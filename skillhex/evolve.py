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
from .llm import OpenAICompatLLM, LLMConfig
from .models import Episode
from .reflect import LLMReflector
from .regress import SkillBank
from .search import SkillSearch, SearchConfig, Task, SearchResult
from .verify import LLMVerifier

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


def build_llm(hermes_home: Path, role: str = "reflector") -> OpenAICompatLLM:
    """Reflection/verifier model: SKILLHEX_* env, else the profile's custom endpoint from config.yaml + .env."""
    env = dict(_read_env_file(hermes_home / ".env"))
    env.update({k: v for k, v in os.environ.items() if k.startswith(("SKILLHEX_", "OPENAI_"))})
    model = env.get("SKILLHEX_MODEL")
    base = env.get("SKILLHEX_BASE_URL")
    key = env.get("SKILLHEX_API_KEY")
    effort = env.get("SKILLHEX_REASONING_EFFORT")
    cfg_path = hermes_home / "config.yaml"
    if yaml and cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
        m = cfg.get("model") or {}
        if isinstance(m, dict):
            model = model or m.get("default")
            base = base or m.get("base_url")
            effort = effort or m.get("reasoning_effort")
    base = base or env.get("OPENAI_BASE_URL", "")
    key = key or env.get("OPENAI_API_KEY", "")
    if not (base and model):
        raise SystemExit("skillhex: no reflector model configured (set SKILLHEX_BASE_URL/SKILLHEX_MODEL or a custom model in config.yaml)")
    return OpenAICompatLLM(LLMConfig(base_url=base, api_key=key, model=model, reasoning_effort=effort or None))


def executor_model_from_env() -> Optional[Dict[str, Any]]:
    """SKILLHEX_EXECUTOR_MODEL / _BASE_URL / _PROVIDER let attempts run on a cheaper (or weaker) model than reflection."""
    model = os.environ.get("SKILLHEX_EXECUTOR_MODEL")
    if not model:
        return None
    over: Dict[str, Any] = {"default": model}
    if os.environ.get("SKILLHEX_EXECUTOR_BASE_URL"):
        over["base_url"] = os.environ["SKILLHEX_EXECUTOR_BASE_URL"]
        over["provider"] = os.environ.get("SKILLHEX_EXECUTOR_PROVIDER", "custom")
        over["api_mode"] = "chat_completions"
    return over


def _apply_skill(hermes_home: Path, skill: str, content: str, evidence: Dict[str, Any]) -> str:
    """Write the winning SKILL.md into the profile, through the Hermes ledger when importable."""
    src = find_skill_dir(skill, hermes_home)
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
                 reflector=None, verifier=None) -> Dict[str, Any]:
    store = EpisodeStore(home / "episodes")
    if episode is None and checker:
        _grade_pending_with_checker(store, skill, checker)
    if episode is None:
        failed = store.list(skill, outcome="fail")
        if not failed and task_prompt is None:
            return {"skill": skill, "status": "nothing_to_do"}
        episode = failed[-1] if failed else None
    skill_dir = find_skill_dir(skill, hermes_home)
    if skill_dir is None:
        return {"skill": skill, "status": "skill_not_found"}
    initial = (skill_dir / "SKILL.md").read_text()
    prompt = task_prompt or (episode.user_prompt if episode else "")
    task = Task(id=f"{skill}-{int(time.time())}", skill=skill, prompt=prompt, cwd=cwd or (episode.cwd if episode else None),
                meta={"checker": checker} if checker else {})
    run_dir = home / "runs" / f"{skill}-{time.strftime('%Y%m%d-%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    llm = llm or build_llm(hermes_home)
    replay_dir = store.dir(skill, episode.id) if episode and store.exists(skill, episode.id) else None
    executor_model = executor_model or executor_model_from_env()
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
    if apply and decision.startswith("apply"):
        applied = _apply_skill(hermes_home, skill, best.content, {"run": str(run_dir), "node": best.id, "score": best.score,
                                                                  "official": best.reward, "decision": decision})
    report = _report(run_dir, skill, task, result, search, decision + (f"; {applied}" if applied else ""), llm)
    summary = {"skill": skill, "status": "done", "passed": result.passed, "decision": decision, "applied": applied,
               "best": best.id if best else None, "best_score": best.score if best else None, "root_score": root.score,
               "executor_calls": result.executor_calls, "report": str(report), "llm_usage": llm.usage}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    if episode is not None:
        marker = home / "runs" / f"{skill}.evolved.json"
        marker.write_text(json.dumps({"episode": episode.id, "run": str(run_dir), "at": time.time()}))
    return summary


def cli_entry(args, home: Path, hermes_home: Path, min_score: float = 0.8, replay_mode: str = "permissive") -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", stream=sys.stderr)
    store = EpisodeStore(home / "episodes")
    action = getattr(args, "action", "status")
    if action == "status":
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
        if runs:
            print(runs[-1].read_text())
        return 0
    if action == "evolve":
        if not args.skill:
            print("--skill required", file=sys.stderr)
            return 2
        res = evolve_skill(home, hermes_home, args.skill, task_prompt=args.task_prompt, checker=args.checker, cwd=args.cwd,
                           budget=args.budget, min_score=min_score, replay_mode=replay_mode)
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
    a = ap.parse_args(argv)
    home, hh = Path(a.home), Path(a.hermes_home)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    lock = home / "evolve.lock"
    try:
        store = EpisodeStore(home / "episodes")
        skills = [a.skill] if a.skill else [s for s in store.skills() if store.list(s, outcome="fail")
                                            and not (home / "runs" / f"{s}.evolved.json").exists()]
        for s in skills:
            res = evolve_skill(home, hh, s, task_prompt=a.task_prompt, checker=a.checker, cwd=a.cwd, budget=a.budget,
                               min_score=a.min_score, replay_mode=a.replay_mode, apply=not a.no_apply)
            print(json.dumps(res, indent=2))
    finally:
        lock.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
