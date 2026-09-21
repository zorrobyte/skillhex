"""Hermes plugin: evidence-gated skill evolution (SkillHEX).

Hooks used
  on_skill_lifecycle  which skill was loaded this turn
  post_tool_call      record every tool call and result (the cassette)
  post_llm_call       finalize the turn into an Episode per loaded skill
  pre_llm_call        judge the user's next message: did the previous skill-guided answer fail?
  pre_tool_call       cassette replay inside evaluation attempts
  on_session_end      schedule a background evolution run for skills with failed episodes

Nothing here ever asks the user for anything. Winning patches are applied
behind the evidence gate; every write goes through the skill ledger so it
can be rolled back.
"""
from __future__ import annotations

import hashlib
import json
import re
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from skillhex.capture import TurnRecorder
from skillhex.episodes import EpisodeStore
from skillhex.llm import extract_json
from skillhex.models import Episode
from skillhex.outcome import classify_followup, heuristic_followup
from skillhex.replay import Cassette, ReplayPolicy
from skillhex.regress import SkillBank, rollback_skill
from skillhex.session_state import SessionStore
from skillhex.workspace import snapshot_workspace

log = logging.getLogger("skillhex.plugin")

_ctx = None
_recorder = TurnRecorder()
_store: Optional[EpisodeStore] = None
_home: Optional[Path] = None
_hermes_home: Optional[Path] = None
_replay: Optional[ReplayPolicy] = None
_last_by_session: Dict[str, Dict[str, Any]] = {}
_sessions: Optional[SessionStore] = None
_lock = threading.Lock()


# ---------------------------------------------------------------------------- helpers
def _cfg(key: str, default: Any) -> Any:
    try:
        v = _ctx.get_config(key, None) if _ctx else None
    except Exception:  # noqa: BLE001
        v = None
    return default if v is None else v


class _HostLLM:
    """Adapter: PluginContext.llm -> the tiny complete_json interface the core uses."""

    def __init__(self, ctx):
        self.ctx = ctx

    def complete_json(self, system: str, user: str, max_tokens: Optional[int] = None, **_):
        res = self.ctx.llm.complete([{"role": "system", "content": system}, {"role": "user", "content": user}],
                                    max_tokens=max_tokens or 600, purpose="skillhex.outcome")
        return extract_json(res.text)


def _skill_version(skill: str) -> str:
    try:
        from skillhex.executors.hermes import find_skill_dir
        d = find_skill_dir(skill, _hermes_home) if _hermes_home else None
        if d and (d / "SKILL.md").exists():
            return hashlib.sha256((d / "SKILL.md").read_bytes()).hexdigest()[:12]
    except Exception:  # noqa: BLE001
        log.debug("skillhex: version lookup failed", exc_info=True)
    return ""


def _user_text(content: Any) -> str:
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content or "")


def _write_pending_state() -> None:
    """A tiny status file the CLI and the evolve process can read without importing Hermes."""
    try:
        summary = {"updated_at": time.time(), "skills": {}}
        for s in _store.skills():
            eps = _store.list(s)
            summary["skills"][s] = {"episodes": len(eps), "failed": len([e for e in eps if e.outcome == "fail"]),
                                    "pending": len([e for e in eps if e.outcome is None])}
        (_home / "status.json").write_text(json.dumps(summary, indent=2))
    except Exception:  # noqa: BLE001
        log.debug("skillhex: status write failed", exc_info=True)


_snapshots: Dict[str, Optional[str]] = {}


def _snapshot_for(session_id: str) -> Optional[str]:
    """Copy the working directory the first time a skill loads in a session (small workspaces only),
    so evaluation attempts can start from the pre-attempt state."""
    if session_id in _snapshots:
        return _snapshots[session_id]
    dest = None
    try:
        d = snapshot_workspace(os.getcwd(), _home / "snapshots" / re.sub(r"[^A-Za-z0-9_.-]+", "-", session_id)[:80])
        dest = str(d) if d else None
    except Exception:  # noqa: BLE001
        log.debug("skillhex: snapshot failed", exc_info=True)
    _snapshots[session_id] = dest
    return dest


# ---------------------------------------------------------------------------- hooks
def _on_skill_lifecycle(action: str = "", skill_name: str = "", session_id: str = "", task_id: str = "", **kw):
    if action == "loaded" and skill_name:
        _recorder.skill_loaded(session_id or "", skill_name, task_id or None)
        if _sessions is not None and session_id:
            _sessions.add_skill(session_id, skill_name)
        _snapshot_for(session_id or "_")


def _on_post_tool_call(tool_name: str = "", args: Any = None, result: Any = None, session_id: str = "",
                       tool_call_id: str = "", status: str = "", duration_ms: Any = None, error_message: str = "", **kw):
    if tool_name in ("skill_view", "skills_list", "skill_manage"):
        return
    res = result if result is not None else (error_message or "")
    try:
        dur = int(duration_ms) if duration_ms is not None else None
    except (TypeError, ValueError):
        dur = None
    _recorder.tool_call(session_id or "", tool_call_id or "", tool_name, args if isinstance(args, dict) else {"raw": args},
                        res, status or "ok", dur)


def _on_pre_tool_call(tool_name: str = "", args: Any = None, **kw):
    if _replay is None:
        return None
    kind, payload = _replay.decide(tool_name, args if isinstance(args, dict) else {})
    if kind == "replay":
        return {"action": "block", "message": payload}
    if kind == "block":
        return {"action": "block", "message": payload}
    return None


def _on_pre_llm_call(session_id: str = "", user_message: Any = None, is_first_turn: bool = False, **kw):
    """The user's next message is the outcome signal for the previous skill-guided turn."""
    if not session_id or is_first_turn:
        return None
    last = _last_by_session.get(session_id) or (_sessions.last(session_id) if _sessions else None)
    if not last or last.get("resolved"):
        return None
    text = _user_text(user_message).strip()
    if not text:
        return None
    verdict, reason = "unknown", ""
    if _cfg("classify_followups", True) and _ctx is not None:
        try:
            verdict, reason = classify_followup(_HostLLM(_ctx), last["prompt"], last["answer"], text)
        except Exception:  # noqa: BLE001
            verdict, reason = heuristic_followup(text), "heuristic"
    else:
        verdict, reason = heuristic_followup(text), "heuristic"
    if verdict in ("pass", "fail"):
        for skill, ep_id in last["episodes"]:
            try:
                _store.set_outcome(skill, ep_id, verdict, source="followup", note=f"{reason}: {text[:300]}")
            except Exception:  # noqa: BLE001
                log.debug("skillhex: set_outcome failed", exc_info=True)
        last["resolved"] = True
        _last_by_session[session_id] = last
        if _sessions is not None:
            _sessions.resolve(session_id)
        _write_pending_state()
        if verdict == "fail":
            for skill in last.get("regressed", []):
                if rollback_skill(_hermes_home, skill):
                    log.warning("skillhex: rolled back %s (bank regression + user fail)", skill)
                    with open(_home / "rollbacks.jsonl", "a") as f:
                        f.write(json.dumps({"skill": skill, "at": time.time(), "note": text[:200]}) + "\n")
            _maybe_schedule_evolution([s for s, _ in last["episodes"] if s not in last.get("regressed", [])])
    return None


def _on_post_llm_call(session_id: str = "", turn_id: str = "", conversation_history: Any = None,
                      assistant_response: Any = None, user_message: Any = None, model: str = "", **kw):
    force = os.environ.get("SKILLHEX_FORCE_SKILL") or None
    if _sessions is not None and session_id:
        for name in _sessions.skills(session_id):
            _recorder.skill_loaded(session_id, name)
    messages = list(conversation_history or [])
    eps = _recorder.finish_turn(session_id or "", turn_id or None, messages, model, os.getcwd(), force_skill=force)
    if not eps:
        return None
    saved = []
    for ep in eps:
        ep.skill_version = _skill_version(ep.skill)
        ep.workspace_snapshot = _snapshots.get(session_id or "_")
        try:
            _store.save(ep)
            saved.append((ep.skill, ep.id))
        except Exception:  # noqa: BLE001
            log.debug("skillhex: episode save failed", exc_info=True)
    regressed = []
    for ep in eps:
        try:
            rec = SkillBank(_home / "banks", ep.skill).regress(ep, _store.dir(ep.skill, ep.id))
            if rec and rec["hard_failures"]:
                regressed.append(ep.skill)
                log.warning("skillhex: regression on %s: hard failures %s", ep.skill, rec["hard_failures"])
        except Exception:  # noqa: BLE001
            log.debug("skillhex: regression check failed", exc_info=True)
    prompt_text = _user_text(user_message) or eps[0].user_prompt
    answer_text = str(assistant_response or eps[0].final_response)
    _last_by_session[session_id or ""] = {"episodes": saved, "prompt": prompt_text, "answer": answer_text, "resolved": False,
                                          "regressed": regressed}
    if _sessions is not None and session_id:
        _sessions.set_last(session_id, saved, prompt_text, answer_text, regressed)
    _write_pending_state()
    return None


def _on_session_end(session_id: str = "", **kw):
    _maybe_schedule_evolution(None)
    return None


# ---------------------------------------------------------------------------- evolution scheduling
def _maybe_schedule_evolution(skills: Optional[list]) -> None:
    if not _cfg("auto_evolve", True) or os.environ.get("SKILLHEX_CAPTURE_DIR"):
        return  # never recurse from inside an evaluation attempt
    try:
        pending = [s for s in (skills or _store.skills()) if _store.list(s, outcome="fail")
                   and not (_home / "runs" / f"{s}.evolved.json").exists()]
    except Exception:  # noqa: BLE001
        return
    if not pending:
        return
    lock = _home / "evolve.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime < 3600:
        return
    lock.write_text(str(os.getpid()))
    cmd = [sys.executable, "-m", "skillhex.evolve", "--home", str(_home), "--hermes-home", str(_hermes_home), "--auto",
           "--min-score", str(_cfg("min_score_to_apply", 0.8)), "--replay-mode", str(_cfg("replay_mode", "permissive"))]
    logf = open(_home / "evolve.log", "a")
    try:
        subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, start_new_session=True, cwd=str(_home))
        log.info("skillhex: evolution scheduled for %s", pending)
    except Exception:  # noqa: BLE001
        lock.unlink(missing_ok=True)
        log.warning("skillhex: could not start evolution", exc_info=True)


# ---------------------------------------------------------------------------- commands
def _slash(raw_args: str = "") -> str:
    parts = (raw_args or "").split(None, 1)
    sub = parts[0].lower() if parts else "status"
    note = parts[1] if len(parts) > 1 else ""
    if sub in ("ok", "fail"):
        pending = list(_last_by_session.items()) + (_sessions.all_unresolved() if _sessions else [])
        for sid, last in pending:
            if last.get("resolved"):
                continue
            for skill, ep_id in last.get("episodes", []):
                _store.set_outcome(skill, ep_id, "pass" if sub == "ok" else "fail", source="user", note=note or None)
            last["resolved"] = True
            if _sessions is not None:
                _sessions.resolve(sid)
        _write_pending_state()
        if sub == "fail":
            _maybe_schedule_evolution(None)
        return f"skillhex: marked last skill-guided turn as {sub}"
    if sub == "evolve":
        _maybe_schedule_evolution(None)
        return "skillhex: evolution scheduled (see skillhex/evolve.log)"
    lines = [f"skillhex home: {_home}"]
    for s in _store.skills():
        eps = _store.list(s)
        lines.append(f"- {s}: {len(eps)} episodes, {len([e for e in eps if e.outcome == 'fail'])} failed, "
                     f"{len([e for e in eps if e.outcome is None])} pending")
    return "\n".join(lines)


def _cli_setup(sub) -> None:
    sub.add_argument("action", nargs="?", default="status", choices=["status", "evolve", "episodes", "report"])
    sub.add_argument("--skill")
    sub.add_argument("--task-prompt", help="override the task prompt used for evaluation attempts")
    sub.add_argument("--checker", help="path to a checker script (exit 0 = pass) for the task")
    sub.add_argument("--cwd", help="workspace directory the task runs in")
    sub.add_argument("--budget", type=int, default=5)


def _cli_handler(args) -> int:
    from skillhex.evolve import cli_entry
    return cli_entry(args, home=_home, hermes_home=_hermes_home,
                     min_score=float(_cfg("min_score_to_apply", 0.8)), replay_mode=str(_cfg("replay_mode", "permissive")))


# ---------------------------------------------------------------------------- register
def register(ctx) -> None:
    global _ctx, _store, _home, _hermes_home, _replay, _sessions
    _ctx = ctx
    _hermes_home = Path(os.environ.get("HERMES_HOME") or "~/.hermes").expanduser()
    _home = Path(os.environ.get("SKILLHEX_CAPTURE_DIR") or (_hermes_home / "skillhex")).expanduser()
    _home.mkdir(parents=True, exist_ok=True)
    _store = EpisodeStore(_home / "episodes")
    _sessions = SessionStore(_home / "sessions")
    rp = os.environ.get("SKILLHEX_REPLAY")
    if rp and (Path(rp) / "episode.json").exists():
        try:
            ep = Episode.from_dict(json.loads((Path(rp) / "episode.json").read_text()))
            _replay = ReplayPolicy(Cassette(ep), mode=os.environ.get("SKILLHEX_REPLAY_MODE") or str(_cfg("replay_mode", "permissive")))
        except Exception:  # noqa: BLE001
            log.warning("skillhex: could not load replay cassette %s", rp, exc_info=True)
    ctx.register_hook("on_skill_lifecycle", _on_skill_lifecycle)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("post_llm_call", _on_post_llm_call)
    ctx.register_hook("on_session_end", _on_session_end)
    ctx.register_command("skillhex", handler=_slash, description="skillhex status | ok | fail <note> | evolve")
    try:
        ctx.register_cli_command("skillhex", help="Evidence-gated skill evolution", setup_fn=_cli_setup, handler_fn=_cli_handler)
    except Exception:  # noqa: BLE001
        log.debug("skillhex: CLI registration unavailable", exc_info=True)
