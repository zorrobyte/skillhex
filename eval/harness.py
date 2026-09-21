"""Before/after evaluation of skillhex on deliberately poisoned skills.

For each fixture (workspace + poisoned SKILL.md + checker):
  before    n fresh-profile attempts with the poisoned skill      (pass rate)
  no_skill  n attempts with the skill's procedure removed          (is the task solvable unaided?)
  evolve    one skillhex run (checker-graded, budget K)            (decision, attempts, reviewer tokens)
  after     n attempts with whatever skill the run left in place   (pass rate)

Every attempt is a real Hermes run in an isolated profile, graded by the fixture's checker.
The pure parts (fixture loading, measurement, table rendering) are unit tested; the runner
is orchestration around them.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skillhex.search import Task  # noqa: E402


@dataclass
class Fixture:
    name: str
    dir: Path
    skill: str
    prompt: str
    checker: Path
    workspace: Path
    expected: str
    skill_md: Path


def load_fixtures(root: Path) -> List[Fixture]:
    out = []
    for d in sorted(Path(root).iterdir()):
        task = d / "task.json"
        if not d.is_dir() or not task.exists():
            continue
        t = json.loads(task.read_text())
        skill_md = d / "skill" / "SKILL.md"
        name = skill_md.read_text().split("name:", 1)[1].splitlines()[0].strip()
        out.append(Fixture(name=d.name, dir=d, skill=name, prompt=t["prompt"], checker=(d / t.get("checker", "checker.py")).resolve(),
                           workspace=(d / "workspace").resolve(), expected=str(t.get("expected", "")), skill_md=skill_md))
    return out


def no_skill_content(skill: str, original: str) -> str:
    """Frontmatter only: the skill loads (so capture works) but carries no procedure."""
    fm = original.split("---", 2)
    head = fm[1] if len(fm) >= 3 else f"\nname: {skill}\ndescription: {skill}\n"
    return f"---{head}---\n\n# {skill}\n\nNo procedure is provided. Use your own judgement and the tools available.\n"


def measure(executor, task: Task, content: str, n: int, label: str) -> Dict[str, Any]:
    passes = 0
    for i in range(n):
        r = executor.execute(content, task, f"{label}-{i}")
        passes += 1 if r.reward == 1 else 0
    return {"label": label, "n": n, "passes": passes, "rate": passes / n if n else 0.0}


def _pct(x: float) -> str:
    return f"{round(100 * x):d}%"


def render_table(rows: List[Dict[str, Any]]) -> str:
    lines = ["| fixture | before (poisoned) | no skill | after | evolve decision | attempts | reviewer tokens |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        b, ns, a, ev = r["before"], r.get("no_skill") or {}, r["after"], r.get("evolve") or {}
        def cell(m):
            return f"{m.get('passes', round(m.get('rate', 0) * m.get('n', 0)))}/{m.get('n', 0)}" if m else "—"
        lines.append(f"| {r['fixture']} | {cell(b)} | {cell(ns)} | {cell(a)} | {ev.get('decision', '—')} | "
                     f"{ev.get('executor_calls', '—')} | {ev.get('reviewer_tokens', '—')} |")
    if rows:
        mean = lambda k: sum((r.get(k) or {}).get("rate", 0) for r in rows) / len(rows)  # noqa: E731
        lines.append("")
        lines.append(f"mean pass rate: before {_pct(mean('before'))}, no skill {_pct(mean('no_skill'))}, after {_pct(mean('after'))}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------- real runner
def make_home(base_profile: Path, dest: Path, fixture: Fixture) -> Path:
    """An isolated Hermes home for one fixture: the base profile's config/.env/plugin, this skill only."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    if (base_profile / ".env").exists():
        shutil.copy2(base_profile / ".env", dest / ".env")
    cfg: Dict[str, Any] = {}
    if (base_profile / "config.yaml").exists():
        import yaml
        cfg = yaml.safe_load((base_profile / "config.yaml").read_text()) or {}
    # the evaluation measures automatic application; a staging gate in the base profile would hide it
    cfg.setdefault("skills", {})["write_approval"] = False
    cfg.setdefault("auxiliary", {}).setdefault("background_review", {})["enabled"] = False
    cfg.setdefault("curator", {})["enabled"] = False
    import yaml
    (dest / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    plug = base_profile / "plugins" / "skillhex"
    if plug.exists():
        (dest / "plugins").mkdir()
        os.symlink(plug.resolve(), dest / "plugins" / "skillhex")
    (dest / "skills" / fixture.skill).mkdir(parents=True)
    shutil.copy2(fixture.skill_md, dest / "skills" / fixture.skill / "SKILL.md")
    # the premise is a skill the agent learned: mark it curator-managed so autonomous writes are allowed
    (dest / "skills" / ".usage.json").write_text(json.dumps({fixture.skill: {"created_by": "agent"}}))
    return dest


def run_fixture(fixture: Fixture, base_profile: Path, out_root: Path, n: int = 2, budget: int = 5,
                conditions=("before", "no_skill", "evolve", "after"), log=print) -> Dict[str, Any]:
    from skillhex.evolve import evolve_skill, resolve_executor_model
    from skillhex.executors.hermes import HermesExecutor

    out_root = Path(out_root).resolve()
    hh = make_home(base_profile, out_root / "homes" / fixture.name, fixture)
    home = out_root / "skillhex" / fixture.name
    home.mkdir(parents=True, exist_ok=True)
    task = Task(id=f"{fixture.name}-eval", skill=fixture.skill, prompt=fixture.prompt, cwd=str(fixture.workspace),
                meta={"checker": str(fixture.checker)})
    poisoned = fixture.skill_md.read_text()
    row: Dict[str, Any] = {"fixture": fixture.name, "skill": fixture.skill, "started": time.time()}

    def executor(label):
        return HermesExecutor(hh, fixture.skill, out_root / "attempts" / fixture.name / label,
                              model_override=resolve_executor_model(hh))

    if "before" in conditions:
        row["before"] = measure(executor("before"), task, poisoned, n, "before")
        log(f"[{fixture.name}] before: {row['before']['passes']}/{n}")
    if "no_skill" in conditions:
        row["no_skill"] = measure(executor("no_skill"), task, no_skill_content(fixture.skill, poisoned), n, "no_skill")
        log(f"[{fixture.name}] no_skill: {row['no_skill']['passes']}/{n}")
    if "evolve" in conditions:
        res = evolve_skill(home, hh, fixture.skill, task_prompt=fixture.prompt, checker=str(fixture.checker),
                           cwd=str(fixture.workspace), budget=budget)
        usage = res.get("llm_usage") or {}
        row["evolve"] = {"decision": res.get("decision"), "executor_calls": res.get("executor_calls"),
                         "reviewer_tokens": (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0),
                         "run": res.get("run"), "html": res.get("html"), "passed": res.get("passed")}
        log(f"[{fixture.name}] evolve: {res.get('decision')} ({res.get('executor_calls')} attempts)")
    if "after" in conditions:
        current = (hh / "skills" / fixture.skill / "SKILL.md").read_text()
        row["after"] = measure(executor("after"), task, current, n, "after")
        row["after"]["skill_changed"] = current != poisoned
        log(f"[{fixture.name}] after: {row['after']['passes']}/{n} (skill changed: {current != poisoned})")
    row["seconds"] = round(time.time() - row["started"], 1)
    return row
