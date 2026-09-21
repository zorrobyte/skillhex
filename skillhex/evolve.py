"""Evolution runner: turns failed episodes into a search, applies the winner
behind the evidence gate, and writes a human-readable report.

Runs as its own process (``python -m skillhex.evolve``) so it survives the
host CLI exiting, and so it never competes with a live turn.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .episodes import EpisodeStore
from .executors.hermes import HermesExecutor, find_skill_dir
from .llm import OpenAICompatLLM, LLMConfig, HermesAuxLLM
from .models import Episode
from .reflect import LLMReflector
from .regress import SkillBank
from .search import SkillSearch, SearchConfig, Task, SearchResult
from .verify import LLMVerifier
from .changes import ChangeLog
from .report import write_artifacts, render_html

log = logging.getLogger("skillhex.evolve")

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def _read_env_file(p: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _load_config(hermes_home: Path) -> Dict[str, Any]:
    cfg_path = hermes_home / "config.yaml"
    if yaml and cfg_path.exists():
        try:
            return yaml.safe_load(cfg_path.read_text()) or {}
        except Exception:  # noqa: BLE001
            log.warning("skillhex: could not parse %s", cfg_path, exc_info=True)
    return {}


def _aux_block(cfg: Dict[str, Any], task: str, env: Dict[str, str]) -> Dict[str, Any]:
    """auxiliary.<task> as Hermes reads it: blank/auto fields mean inherit; key_env resolves a secret."""
    aux = cfg.get("auxiliary") or {}
    block = dict(aux.get(task) or {}) if isinstance(aux, dict) else {}
    out: Dict[str, Any] = {}
    for k in ("provider", "model", "base_url", "api_key", "api_mode", "reasoning_effort"):
        v = str(block.get(k) or "").strip()
        if v and v.lower() != "auto":
            out[k] = v
    key_env = str(block.get("key_env") or block.get("api_key_env") or "").strip()
    if "api_key" not in out and key_env and env.get(key_env):
        out["api_key"] = env[key_env]
    return out


def _runtime_env(hermes_home: Path) -> Dict[str, str]:
    env = dict(_read_env_file(hermes_home / ".env"))
    env.update(os.environ)          # the process environment wins over the profile's .env
    return env


REFLECTOR_TASK = "skillhex_reflector"
EXECUTOR_TASK = "skillhex_executor"


def resolve_reflector(hermes_home: Path) -> LLMConfig:
    """Reflection + self-verification model. Precedence: SKILLHEX_* env > auxiliary.skillhex_reflector
    (config.yaml, the block `hermes model` edits) > the profile's main model."""
    env = _runtime_env(hermes_home)
    cfg = _load_config(hermes_home)
    main = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    aux = _aux_block(cfg, REFLECTOR_TASK, env)
    model = env.get("SKILLHEX_MODEL") or aux.get("model") or main.get("default")
    base = env.get("SKILLHEX_BASE_URL") or aux.get("base_url") or main.get("base_url") or env.get("OPENAI_BASE_URL", "")
    key = env.get("SKILLHEX_API_KEY") or aux.get("api_key") or env.get("OPENAI_API_KEY", "")
    effort = env.get("SKILLHEX_REASONING_EFFORT") or aux.get("reasoning_effort") or main.get("reasoning_effort")
    if not (base and model):
        raise SystemExit("skillhex: no reflector model configured (set auxiliary.skillhex_reflector in config.yaml, "
                         "SKILLHEX_BASE_URL/SKILLHEX_MODEL, or a custom main model)")
    return LLMConfig(base_url=str(base), api_key=str(key or ""), model=str(model), reasoning_effort=effort or None)


def build_llm(hermes_home: Path, role: str = "reflector"):
    """Reviewer backend. An explicit custom endpoint (SKILLHEX_* env, or a base_url in the
    auxiliary.skillhex_reflector block) uses the stdlib OpenAI-compatible client. Otherwise, when Hermes is
    importable, route through Hermes's auxiliary client by task name so every provider Hermes can
    authenticate works (subscription plugins, OAuth, pooled credentials). Last resort: the main model's
    custom base_url."""
    os.environ["HERMES_HOME"] = str(hermes_home)
    env = _runtime_env(hermes_home)
    cfg = _load_config(hermes_home)
    aux = _aux_block(cfg, REFLECTOR_TASK, env)
    if env.get("SKILLHEX_BASE_URL") or env.get("SKILLHEX_MODEL") or aux.get("base_url"):
        return OpenAICompatLLM(resolve_reflector(hermes_home))
    if HermesAuxLLM.available():
        return HermesAuxLLM(task=REFLECTOR_TASK)
    return OpenAICompatLLM(resolve_reflector(hermes_home))


def executor_model_from_env() -> Optional[Dict[str, Any]]:
    """SKILLHEX_EXECUTOR_MODEL / _BASE_URL / _PROVIDER: a cheaper (or weaker) model for evaluation attempts."""
    model = os.environ.get("SKILLHEX_EXECUTOR_MODEL")
    if not model:
        return None
    over: Dict[str, Any] = {"default": model}
    if os.environ.get("SKILLHEX_EXECUTOR_BASE_URL"):
        over["base_url"] = os.environ["SKILLHEX_EXECUTOR_BASE_URL"]
        over["provider"] = os.environ.get("SKILLHEX_EXECUTOR_PROVIDER", "custom")
        over["api_mode"] = "chat_completions"
    return over


def resolve_executor_model(hermes_home: Path) -> Optional[Dict[str, Any]]:
    """Model override for evaluation attempts (a `model:` block patch for the scratch profile).
    Precedence: SKILLHEX_EXECUTOR_* env > auxiliary.skillhex_executor > none (the main model)."""
    over = executor_model_from_env()
    if over:
        return over
    aux = _aux_block(_load_config(hermes_home), EXECUTOR_TASK, _runtime_env(hermes_home))
    if not aux.get("model"):
        return None
    over = {"default": aux["model"]}
    if aux.get("base_url"):
        over["base_url"] = aux["base_url"]
        over["provider"] = aux.get("provider", "custom")
        over["api_mode"] = aux.get("api_mode", "chat_completions")
    elif aux.get("provider"):
        over["provider"] = aux["provider"]
    if aux.get("api_key"):
        over["api_key"] = aux["api_key"]
    return over


_TRUTHY = {"on", "true", "yes", "1", "approve", "enabled"}


def write_approval_enabled(hermes_home: Path) -> bool:
    """skills.write_approval from config.yaml, coerced the way tools/write_approval.py does."""
    v = (_load_config(hermes_home).get("skills") or {}).get("write_approval", False)
    return v is True or (isinstance(v, str) and v.strip().lower() in _TRUTHY)


def _stage_skill(hermes_home: Path, skill: str, content: str, evidence: Dict[str, Any]) -> str:
    """Stage the winner as a pending skill write in Hermes's own record shape
    (<HERMES_HOME>/pending/skills/<id>.json), so /skills pending | diff <id> | approve <id> handle it."""
    import uuid
    pid = uuid.uuid4().hex[:8]
    desc = ""
    for line in content.splitlines():
        if line.startswith("description:"):
            desc = line.split(":", 1)[1].strip().strip("'\"")[:140]
            break
    score = evidence.get("score")
    gist = (f"skillhex: rewrite '{skill}'" + (f" — {desc}" if desc else "")
            + f" (evidence {score:.2f}" if isinstance(score, (int, float)) else f"skillhex: rewrite '{skill}' (")
    gist += ", official pass)" if evidence.get("official") == 1 else ")"
    evidence["pending_id"] = pid
    record = {"id": pid, "subsystem": "skills", "action": "edit", "summary": gist, "origin": "background_review",
              "created_at": time.time(), "payload": {"action": "edit", "name": skill, "content": content},
              "skillhex": evidence}
    p = hermes_home / "pending" / "skills" / f"{pid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2))
    os.replace(tmp, p)
    return f"staged for approval (skills.write_approval is on): pending id {pid} -> /skills diff {pid} | /skills approve {pid}"


def ownership_refusal(hermes_home: Path, skill: str, skill_dir: Optional[Path]) -> Optional[str]:
    """Mirror Hermes's own rule for autonomous writes (tools/skill_manager_guards.py): pinned, external,
    protected built-in, hub-installed, bundled, or not curator-managed (created_by != agent, i.e. user-owned)
    skills are off-limits. Returns the reason, or None when the write is allowed. Unknown when Hermes is not
    importable (nothing to check against)."""
    os.environ["HERMES_HOME"] = str(hermes_home)
    try:
        from tools import skill_usage
    except Exception:  # noqa: BLE001
        return None
    try:
        rec = skill_usage.load_usage().get(skill)
        if isinstance(rec, dict) and rec.get("pinned"):
            return "pinned"
        for pred, label in ((skill_usage.is_protected_builtin, "protected built-in"),
                            (skill_usage.is_hub_installed, "hub-installed"), (skill_usage.is_bundled, "bundled")):
            try:
                if pred(skill):
                    return label
            except Exception:  # noqa: BLE001
                continue
        if skill_dir is not None:
            try:
                from agent.skill_utils import is_external_skill_path
                if is_external_skill_path(skill_dir):
                    return "external (skills.external_dirs)"
            except Exception:  # noqa: BLE001
                pass
        if not skill_usage._is_curator_managed_record(rec):
            return "user-owned (not curator-managed; `hermes curator adopt <skill>` opts it in)"
    except Exception:  # noqa: BLE001
        log.debug("ownership check failed", exc_info=True)
        return "ownership could not be verified"
    return None


def _apply_skill(hermes_home: Path, skill: str, content: str, evidence: Dict[str, Any], *,
                 expected_hash: Optional[str] = None, apply_to_user_skills: bool = False) -> str:
    """Write the winning SKILL.md into the profile, through the Hermes ledger when importable.
    Stage for approval instead when: the user gated skill writes; the skill is one Hermes would refuse to
    edit autonomously; or the file changed while the run was evaluating."""
    if write_approval_enabled(hermes_home):
        return _stage_skill(hermes_home, skill, content, evidence)
    src = find_skill_dir(skill, hermes_home)
    if not apply_to_user_skills:
        why = ownership_refusal(hermes_home, skill, src)
        if why:
            evidence["ownership"] = why
            return _stage_skill(hermes_home, skill, content, evidence) + f" (not applied automatically: {why} skill)"
    if expected_hash and src is not None and (src / "SKILL.md").exists():
        import hashlib
        now = hashlib.sha256((src / "SKILL.md").read_bytes()).hexdigest()
        if now != expected_hash:
            evidence["changed_during_run"] = True
            return _stage_skill(hermes_home, skill, content, evidence) + " (not applied automatically: the skill changed during the run)"
    profile_dir = hermes_home / "skills" / skill
    if src is None or not str(src.resolve()).startswith(str(hermes_home.resolve())):
        # bundled or missing skill: materialise a profile-level override (profile skills win by name)
        profile_dir.mkdir(parents=True, exist_ok=True)
        if src is not None:
            for child in src.iterdir():
                if child.name != "SKILL.md" and not (profile_dir / child.name).exists():
                    shutil.copytree(child, profile_dir / child.name) if child.is_dir() else shutil.copy2(child, profile_dir / child.name)
        target = profile_dir
    else:
        target = src
    md = target / "SKILL.md"
    before = None
    try:
        os.environ.setdefault("HERMES_HOME", str(hermes_home))
        from tools import skill_ledger  # type: ignore
        before = skill_ledger.capture_before(target, skill=skill) if hasattr(skill_ledger, "capture_before") else None
    except Exception:  # noqa: BLE001
        skill_ledger = None  # type: ignore
    backup = md.with_suffix(".md.skillhex-prev")
    if md.exists():
        shutil.copy2(md, backup)
    md.write_text(content)
    try:
        if skill_ledger is not None and hasattr(skill_ledger, "record_mutation"):
            skill_ledger.set_ledger_actor("agent") if hasattr(skill_ledger, "set_ledger_actor") else None
            skill_ledger.record_mutation("patch", skill, before=before, after_root=target, evidence=evidence)
            return f"applied via ledger -> {md}"
    except Exception:  # noqa: BLE001
        log.debug("ledger record failed", exc_info=True)
    return f"applied (backup at {backup.name}) -> {md}"


def _grade_pending_with_checker(store: EpisodeStore, skill: str, checker: str) -> None:
    """Episodes captured live have no verdict yet; a task checker can grade them from their workspace."""
    import subprocess
    for ep in store.list(skill):
        if ep.outcome is not None or not ep.cwd or not Path(ep.cwd).is_dir():
            continue
        env = dict(os.environ, SKILLHEX_EPISODE=str(store.dir(skill, ep.id)), SKILLHEX_WORKSPACE=ep.cwd)
        try:
            proc = subprocess.run([sys.executable, checker], capture_output=True, text=True, timeout=120, env=env, cwd=ep.cwd)
        except subprocess.TimeoutExpired:
            continue
        store.set_outcome(skill, ep.id, "pass" if proc.returncode == 0 else "fail", source="checker",
                          note=(proc.stdout + proc.stderr).strip()[-300:])


def _report(run_dir: Path, skill: str, task: Task, result: SearchResult, search: SkillSearch, decision: str, llm) -> Path:
    lines = [f"# skillhex run — {skill}", "", f"task: {task.prompt[:500]}", f"outcome: {'PASSED official check' if result.passed else 'no official pass'}",
             f"decision: {decision}", f"executor attempts: {result.executor_calls}", f"reflector usage: {llm.usage}", "",
             "## Hypotheses", search.hypotheses.summary(), "", "## Evidence matrix", "```", result.matrix_text, "```", "",
             "## Patch tree", "```", result.tree_text, "```", ""]
    if result.best is not None:
        lines += [f"## Best node {result.best.id} (score {result.best.score:.2f}, official {'PASS' if result.best.reward == 1 else 'FAIL'})",
                  "```markdown", result.best.content, "```"]
    p = run_dir / "REPORT.md"
    p.write_text("\n".join(lines))
    return p


def evolve_skill(home: Path, hermes_home: Path, skill: str, *, episode: Optional[Episode] = None,
                 task_prompt: Optional[str] = None, checker: Optional[str] = None, cwd: Optional[str] = None,
                 budget: int = 5, min_score: float = 0.8, replay_mode: str = "permissive", apply: bool = True,
                 executor_model: Optional[Dict[str, Any]] = None, llm=None, executor=None,
                 reflector=None, verifier=None, apply_to_user_skills: bool = False) -> Dict[str, Any]:
    store = EpisodeStore(home / "episodes")
    if episode is None and checker:
        _grade_pending_with_checker(store, skill, checker)
    consumed: list = [episode.id] if episode is not None else []
    if episode is None:
        failed = store.list(skill, outcome="fail", unevolved=True)
        if not failed and task_prompt is None:
            return {"skill": skill, "status": "nothing_to_do"}
        episode = failed[-1] if failed else None
        consumed = [e.id for e in failed]
    skill_dir = find_skill_dir(skill, hermes_home)
    if skill_dir is None:
        return {"skill": skill, "status": "skill_not_found"}
    initial = (skill_dir / "SKILL.md").read_text()
    import hashlib
    initial_hash = hashlib.sha256(initial.encode()).hexdigest()
    prompt = task_prompt or (episode.user_prompt if episode else "")
    task = Task(id=f"{skill}-{int(time.time())}", skill=skill, prompt=prompt, cwd=cwd or (episode.cwd if episode else None),
                meta={"checker": checker} if checker else {})
    run_dir = home / "runs" / f"{skill}-{time.strftime('%Y%m%d-%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    llm = llm or build_llm(hermes_home)
    replay_dir = store.dir(skill, episode.id) if episode and store.exists(skill, episode.id) else None
    executor_model = executor_model or resolve_executor_model(hermes_home)
    executor = executor or HermesExecutor(hermes_home, skill, run_dir, replay_episode_dir=replay_dir, replay_mode=replay_mode,
                                          model_override=executor_model, root_episode=episode)
    cfg = SearchConfig(K=budget, early_stop_score=None if checker else min_score)
    search = SkillSearch(run_dir / "search", task, initial, reflector or LLMReflector(llm), verifier or LLMVerifier(llm), executor, cfg)
    seed = None
    if episode is not None:
        seed = Episode.from_dict(episode.to_dict())
        seed.id = episode.id
    log.info("evolving %s (budget %d, checker=%s, replay=%s)", skill, budget, bool(checker), bool(replay_dir))
    result = search.run(initial_episode=seed)
    root = search.tree.root()
    best = result.best
    decision = "keep original"
    if best is not None and best.id != root.id:
        beats_root = (best.score or 0) > (root.score or 0)
        if result.passed:
            decision = "apply (official pass)"
        elif beats_root and (best.score or 0) >= min_score and task.meta.get("checker") is None:
            decision = f"apply (evidence score {best.score:.2f} ≥ {min_score}, no checker available)"
        else:
            decision = f"keep original (best {best.score:.2f} vs root {root.score or 0:.2f}, passed={result.passed})"
    try:
        satisfied = {t for t in search.matrix.tests()
                     if (best is not None and search.matrix.get(best.id, t) == 1) or search.matrix.get(root.id, t) == 1}
        SkillBank(home / "banks", skill).absorb(search.bank, task_prompt=task.prompt, run=str(run_dir), keep=satisfied)
    except Exception:  # noqa: BLE001
        log.debug("bank absorb failed", exc_info=True)
    applied = None
    evidence = {"run": str(run_dir), "node": best.id if best else None, "score": best.score if best else None,
                "official": best.reward if best else None, "decision": decision}
    if apply and decision.startswith("apply"):
        applied = _apply_skill(hermes_home, skill, best.content, evidence, expected_hash=initial_hash,
                               apply_to_user_skills=apply_to_user_skills)
        ChangeLog(home / "changes.jsonl").append(
            skill=skill, kind="staged" if applied.startswith("staged") else "applied", decision=decision, run=str(run_dir),
            score=best.score, official=best.reward, pending_id=evidence.get("pending_id"))
    report = _report(run_dir, skill, task, result, search, decision + (f"; {applied}" if applied else ""), llm)
    write_artifacts(run_dir, skill=skill, task_prompt=task.prompt, decision=decision, passed=result.passed,
                    executor_calls=result.executor_calls, matrix_text=result.matrix_text, tree_text=result.tree_text,
                    hypotheses=search.hypotheses.summary(), root_content=initial,
                    best_content=best.content if best else initial, best_id=best.id if best else root.id,
                    best_score=best.score if best else root.score, root_score=root.score, llm_usage=llm.usage, applied=applied)
    html_report = render_html(run_dir)
    summary = {"skill": skill, "status": "done", "passed": result.passed, "decision": decision, "applied": applied,
               "best": best.id if best else None, "best_score": best.score if best else None, "root_score": root.score,
               "executor_calls": result.executor_calls, "report": str(report), "html": str(html_report), "run": str(run_dir),
               "llm_usage": llm.usage}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    # every failure this run could have learned from is consumed; a later failure schedules a new run
    store.mark_evolved(skill, consumed, run=str(run_dir))
    return summary


def cli_entry(args, home: Path, hermes_home: Path, min_score: float = 0.8, replay_mode: str = "permissive",
              apply_to_user_skills: bool = False) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", stream=sys.stderr)
    store = EpisodeStore(home / "episodes")
    action = getattr(args, "action", "status")
    if action == "status":
        if not store.skills():
            print(f"skillhex: no skill-guided turns captured yet (home {home})")
        for s in store.skills():
            eps = store.list(s)
            print(f"{s}: {len(eps)} episodes, {len([e for e in eps if e.outcome == 'fail'])} failed, {len([e for e in eps if e.outcome is None])} pending")
        for p in sorted((home / "runs").glob("*/summary.json")) if (home / "runs").exists() else []:
            d = json.loads(p.read_text())
            print(f"run {p.parent.name}: {d.get('decision')} (attempts {d.get('executor_calls')})")
        return 0
    if action == "episodes":
        for s in ([args.skill] if args.skill else store.skills()):
            for e in store.list(s):
                print(f"{s} {e.id} outcome={e.outcome} src={e.outcome_source} tools={len(e.tool_calls)} :: {e.user_prompt[:80]}")
        return 0
    if action == "report":
        runs = sorted((home / "runs").glob("*/REPORT.md")) if (home / "runs").exists() else []
        if args.skill:
            runs = [r for r in runs if r.parent.name.startswith(args.skill + "-")]
        if not runs:
            print("no runs yet")
            return 0
        run_dir = runs[-1].parent
        if getattr(args, "open", False):
            html_path = run_dir / "report.html"
            if not html_path.exists() and (run_dir / "artifacts.json").exists():
                html_path = render_html(run_dir)
            import webbrowser
            webbrowser.open(html_path.as_uri())
            print(f"opened {html_path}")
        else:
            print(runs[-1].read_text())
        return 0
    if action == "undo":
        from .regress import rollback_skill
        if not args.skill:
            print("--skill required", file=sys.stderr)
            return 2
        if rollback_skill(hermes_home, args.skill):
            ChangeLog(home / "changes.jsonl").append(skill=args.skill, kind="undone", decision="user undo (cli)", run=None)
            print(f"restored the previous SKILL.md for {args.skill}")
            return 0
        print(f"nothing to undo for {args.skill} (no skillhex backup)")
        return 1
    if action == "evolve":
        if not args.skill:
            print("--skill required", file=sys.stderr)
            return 2
        res = evolve_skill(home, hermes_home, args.skill, task_prompt=args.task_prompt, checker=args.checker, cwd=args.cwd,
                           budget=args.budget, min_score=min_score, replay_mode=replay_mode,
                           apply_to_user_skills=apply_to_user_skills)
        print(json.dumps(res, indent=2))
        return 0
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="skillhex.evolve")
    ap.add_argument("--home", required=True)
    ap.add_argument("--hermes-home", required=True)
    ap.add_argument("--skill")
    ap.add_argument("--auto", action="store_true", help="evolve every skill with unresolved failed episodes")
    ap.add_argument("--task-prompt")
    ap.add_argument("--checker")
    ap.add_argument("--cwd")
    ap.add_argument("--budget", type=int, default=5)
    ap.add_argument("--min-score", type=float, default=0.8)
    ap.add_argument("--replay-mode", default="permissive")
    ap.add_argument("--no-apply", action="store_true")
    ap.add_argument("--apply-to-user-skills", action="store_true",
                    help="also write skills Hermes treats as user-owned (default: stage them for approval)")
    a = ap.parse_args(argv)
    home, hh = Path(a.home), Path(a.hermes_home)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    lock = home / "evolve.lock"
    try:
        store = EpisodeStore(home / "episodes")
        skills = [a.skill] if a.skill else store.pending_skills()
        for s in skills:
            res = evolve_skill(home, hh, s, task_prompt=a.task_prompt, checker=a.checker, cwd=a.cwd, budget=a.budget,
                               min_score=a.min_score, replay_mode=a.replay_mode, apply=not a.no_apply,
                               apply_to_user_skills=a.apply_to_user_skills)
            print(json.dumps(res, indent=2))
    finally:
        lock.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
