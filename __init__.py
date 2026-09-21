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

import sys as _sys
from pathlib import Path as _Path

_ROOT = str(_Path(__file__).resolve().parent)
if _ROOT not in _sys.path:          # the plugin ships the core package beside it
    _sys.path.insert(0, _ROOT)

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
from skillhex.changes import ChangeLog
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


def _changes() -> ChangeLog:
    return ChangeLog(_home / "changes.jsonl")


def _prompt_section(info=None) -> str:
    """Frozen into each new session's system prompt: what skillhex changed recently, and how the agent
    should act on 'what changed?' / 'undo that' / 'that was wrong'."""
    try:
        return _changes().render_for_prompt(days=float(_cfg("changes_window_days", 7)))
    except Exception:  # noqa: BLE001
        log.debug("skillhex: prompt section failed", exc_info=True)
        return ""


def _bank_for(skill: str) -> SkillBank:
    return SkillBank(_home / "banks", skill, min_similarity=float(_cfg("bank_min_similarity", 0.5)))


_snapshots: Dict[str, Optional[str]] = {}


def _snapshot_for(session_id: str) -> Optional[str]:
    """Copy the working directory the first time a skill loads in a session (small workspaces only),
    so evaluation attempts can start from the pre-attempt state."""
    if session_id in _snapshots:
        return _snapshots[session_id]
    dest = None
    try:
        d = snapshot_workspace(os.getcwd(), _home / "snapshots" / re.sub(r"[^A-Za-z0-9_.-]+", "-", session_id)[:80],
                               max_bytes=int(_cfg("snapshot_max_bytes", 50 * 1024 * 1024)))
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
    # the next message is the signal; whatever it said, this turn is judged and never looked at again
    last["resolved"] = True
    _last_by_session[session_id] = last
    if _sessions is not None:
        _sessions.resolve(session_id)
    _write_pending_state()
    if verdict in ("pass", "fail"):
        if verdict == "fail":
            _on_fail(last, text)
    return None


def _on_fail(last: Dict[str, Any], note: str) -> None:
    """A confirmed failure: roll back a patch the bank already flagged, then schedule evolution for the rest."""
    for skill in (last.get("regressed", []) if _cfg("auto_rollback", True) else []):
        if rollback_skill(_hermes_home, skill):
            log.warning("skillhex: rolled back %s (bank regression + user fail)", skill)
            with open(_home / "rollbacks.jsonl", "a") as f:
                f.write(json.dumps({"skill": skill, "at": time.time(), "note": note[:200]}) + "\n")
    _maybe_schedule_evolution([s for s, _ in last["episodes"] if s not in last.get("regressed", [])])


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
            rec = _bank_for(ep.skill).regress(ep, _store.dir(ep.skill, ep.id))
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
        pending = [s for s in (skills or _store.skills()) if _store.list(s, outcome="fail", unevolved=True)]
    except Exception:  # noqa: BLE001
        return
    if not pending:
        return
    lock = _home / "evolve.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime < 3600:
        return
    lock.write_text(str(os.getpid()))
    cmd = [sys.executable, "-m", "skillhex.evolve", "--home", str(_home), "--hermes-home", str(_hermes_home), "--auto",
           "--min-score", str(_cfg("min_score_to_apply", 0.8)), "--replay-mode", str(_cfg("replay_mode", "permissive")),
           "--budget", str(int(_cfg("budget", 5)))]
    logf = open(_home / "evolve.log", "a")
    try:
        subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, start_new_session=True, cwd=str(_home))
        log.info("skillhex: evolution scheduled for %s", pending)
    except Exception:  # noqa: BLE001
        lock.unlink(missing_ok=True)
        log.warning("skillhex: could not start evolution", exc_info=True)


# ---------------------------------------------------------------------------- commands / tool
def _skill_dir(skill: str):
    from skillhex.executors.hermes import find_skill_dir
    return find_skill_dir(skill, _hermes_home)


def _runs_for(skill: Optional[str] = None) -> list:
    out = []
    for p in sorted((_home / "runs").glob("*/summary.json")) if (_home / "runs").exists() else []:
        if skill and not p.parent.name.startswith(skill + "-"):
            continue
        try:
            d = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        d["dir"] = str(p.parent)
        out.append(d)
    return out


def _status() -> str:
    lines = [f"skillhex home: {_home}"]
    skills = _store.skills()
    if not skills:
        lines.append("no skill-guided turns captured yet")
    for s in skills:
        eps = _store.list(s)
        pend = len(_store.list(s, outcome="fail", unevolved=True))
        lines.append(f"- {s}: {len(eps)} episodes, {len([e for e in eps if e.outcome == 'fail'])} failed"
                     f"{f' ({pend} awaiting evolution)' if pend else ''}, {len([e for e in eps if e.outcome is None])} unjudged")
    runs = _runs_for()
    if runs:
        lines.append(f"runs: {len(runs)} (latest: {Path(runs[-1]['dir']).name} — {runs[-1].get('decision')})")
    recent = _changes().recent(limit=5)
    if recent:
        lines.append("recent changes:")
        lines += ["  " + ChangeLog.describe(r) for r in recent]
    lock = _home / "evolve.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime < 3600:
        lines.append("an evolution run is in progress (see skillhex/evolve.log)")
    return "\n".join(lines)


def _show(skill: str) -> str:
    if not skill:
        return "usage: show <skill>"
    d = _skill_dir(skill)
    if d is None:
        return f"skillhex: no skill named {skill}"
    import difflib
    cur = (d / "SKILL.md").read_text() if (d / "SKILL.md").exists() else ""
    prev_p = d / "SKILL.md.skillhex-prev"
    lines = [f"{skill}: {d / 'SKILL.md'}"]
    runs = _runs_for(skill)
    if runs:
        r = runs[-1]
        lines.append(f"last run: {Path(r['dir']).name} — {r.get('decision')} (attempts {r.get('executor_calls')}); "
                     f"report: {r.get('html') or r.get('report')}")
    if prev_p.exists():
        diff = "".join(difflib.unified_diff(prev_p.read_text().splitlines(keepends=True), cur.splitlines(keepends=True),
                                            fromfile="previous/SKILL.md", tofile="current/SKILL.md"))
        lines.append(diff or "(previous version identical)")
    else:
        lines.append("no previous version kept (skillhex has not changed this skill, or the change was undone)")
    return "\n".join(lines)


def _undo(skill: str, who: str = "user") -> str:
    if not skill:
        return "usage: undo <skill>"
    if rollback_skill(_hermes_home, skill):
        _changes().append(skill=skill, kind="undone", decision=f"{who} undo", run=None)
        return f"skillhex: restored the previous SKILL.md for {skill}"
    return f"skillhex: nothing to undo for {skill} (no backup of an earlier version)"


def _runs_text() -> str:
    runs = _runs_for()
    if not runs:
        return "skillhex: no runs yet"
    return "\n".join(f"{Path(r['dir']).name}: {r.get('decision')} (attempts {r.get('executor_calls')}) {r.get('html') or ''}"
                     for r in runs[-20:])


def _mark(verdict: str, note: str = "") -> str:
    sid, last = _sessions.most_recent() if _sessions else (None, None)
    if not last:
        return "skillhex: no skill-guided turn to mark"
    marked = []
    for skill, ep_id in last.get("episodes", []):
        try:
            _store.set_outcome(skill, ep_id, "pass" if verdict == "ok" else "fail", source="user", note=note or None)
            marked.append(skill)
        except Exception:  # noqa: BLE001
            log.debug("skillhex: set_outcome failed", exc_info=True)
    last["resolved"] = True
    _sessions.resolve(sid)
    if sid in _last_by_session:
        _last_by_session[sid]["resolved"] = True
    _write_pending_state()
    if verdict == "fail":
        _on_fail(last, note)
    return f"skillhex: marked the last skill-guided turn ({', '.join(marked) or 'no skill'}) as {verdict}"


_HELP = ("/skillhex            status\n/skillhex show <skill>   diff of the last change + last run\n"
         "/skillhex undo <skill>   restore the previous version\n/skillhex ok|fail [note]  grade the last skill-guided turn\n"
         "/skillhex runs           list evolution runs\n/skillhex evolve         start a run for pending failures now")


def _slash(raw_args: str = "") -> str:
    parts = (raw_args or "").split(None, 1)
    sub = parts[0].lower() if parts else "status"
    rest = parts[1].strip() if len(parts) > 1 else ""
    if sub in ("ok", "fail"):
        return _mark(sub, rest)
    if sub == "show":
        return _show(rest)
    if sub == "undo":
        return _undo(rest)
    if sub == "runs":
        return _runs_text()
    if sub == "evolve":
        _maybe_schedule_evolution(None)
        return "skillhex: evolution scheduled (see skillhex/evolve.log)"
    if sub in ("help", "-h", "--help"):
        return _HELP
    return _status()


_TOOL_SCHEMA = {
    "name": "skillhex",
    "description": ("Inspect or act on skillhex, the plugin that evolves skills after failed skill-guided turns. "
                    "status: what it captured and changed. show: diff of a skill's last change and its run. "
                    "undo: restore a skill's previous version (only when the user asks). "
                    "mark: record the user's verdict (ok|fail) on the most recent skill-guided turn. runs: list runs."),
    "parameters": {"type": "object",
                   "properties": {"action": {"type": "string", "enum": ["status", "show", "undo", "mark", "runs"]},
                                  "skill": {"type": "string", "description": "skill name for show/undo"},
                                  "verdict": {"type": "string", "enum": ["ok", "fail"], "description": "for mark"},
                                  "note": {"type": "string", "description": "for mark: what the user said was wrong"}},
                   "required": ["action"]},
}


def _tool(args: Dict[str, Any], **kw) -> str:
    a = str((args or {}).get("action") or "").lower()
    skill = str((args or {}).get("skill") or "")
    if a == "status":
        return _status()
    if a == "show":
        return _show(skill)
    if a == "undo":
        return _undo(skill, who="agent (on user request)")
    if a == "mark":
        v = str((args or {}).get("verdict") or "").lower()
        if v not in ("ok", "fail"):
            return "skillhex: verdict must be ok or fail"
        return _mark(v, str((args or {}).get("note") or ""))
    if a == "runs":
        return _runs_text()
    return f"skillhex: unknown action {a!r} (status|show|undo|mark|runs)"


def _cli_setup(sub) -> None:
    sub.add_argument("action", nargs="?", default="status", choices=["status", "evolve", "episodes", "report", "undo"])
    sub.add_argument("--skill")
    sub.add_argument("--open", action="store_true", help="report: open the HTML report in a browser")
    sub.add_argument("--task-prompt", help="override the task prompt used for evaluation attempts")
    sub.add_argument("--checker", help="path to a checker script (exit 0 = pass) for the task")
    sub.add_argument("--cwd", help="workspace directory the task runs in")
    sub.add_argument("--budget", type=int, default=None)


def _cli_handler(args) -> int:
    from skillhex.evolve import cli_entry
    if getattr(args, "budget", None) is None:
        args.budget = int(_cfg("budget", 5))
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
    ctx.register_command("skillhex", handler=_slash, args_hint="[status|show <skill>|undo <skill>|ok|fail [note]|runs|evolve]",
                         description="Evidence-gated skill evolution: status, show/undo a change, grade the last turn")
    # The agent is the UI: it can answer "what changed?", revert on request, and record verdicts.
    try:
        ctx.register_tool(name="skillhex", toolset="skillhex", schema=_TOOL_SCHEMA, handler=_tool, emoji="🧬",
                          description=_TOOL_SCHEMA["description"])
    except Exception:  # noqa: BLE001
        log.debug("skillhex: tool registration unavailable", exc_info=True)
    try:
        ctx.register_system_prompt_section("skillhex.changes", _prompt_section, max_chars=3000)
    except Exception:  # noqa: BLE001
        log.debug("skillhex: prompt section registration unavailable", exc_info=True)
    try:
        ctx.register_skill("guide", Path(__file__).resolve().parent / "skills" / "guide" / "SKILL.md",
                           description="How skillhex works and how to inspect, undo, or grade its changes")
    except Exception:  # noqa: BLE001
        log.debug("skillhex: skill registration unavailable", exc_info=True)
    # Model routing uses Hermes's own auxiliary-task convention: both tasks appear in `hermes model`
    # under "Auxiliary models" and live at auxiliary.skillhex_reflector / auxiliary.skillhex_executor.
    # Blank = the main model. Set the reflector to a stronger tier (Sonnet acts, Opus reviews) or the
    # executor to a cheaper/local one (Qwen acts, anything frontier reviews).
    try:
        ctx.register_auxiliary_task(
            "skillhex_reflector", display_name="SkillHEX reviewer",
            description="Reviews a failed skill-guided turn: writes failure hypotheses, self-verifier tests and "
                        "skill patches. Pick a stronger tier than the acting model when you can; blank = main model.",
            defaults={"timeout": 300})
        ctx.register_auxiliary_task(
            "skillhex_executor", display_name="SkillHEX executor",
            description="Runs candidate skills in throwaway profiles during evolution (many cheap attempts). "
                        "Blank = main model; point it at a local/cheaper model to save tokens.",
            defaults={"timeout": 600})
    except Exception:  # noqa: BLE001
        log.debug("skillhex: auxiliary task registration unavailable", exc_info=True)
    try:
        ctx.register_cli_command("skillhex", help="Evidence-gated skill evolution", setup_fn=_cli_setup, handler_fn=_cli_handler)
    except Exception:  # noqa: BLE001
        log.debug("skillhex: CLI registration unavailable", exc_info=True)
