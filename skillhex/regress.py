"""Per-skill persistent test bank and regression on reuse.

After a run, accepted tests are absorbed into ``<home>/banks/<skill>/`` with
the task prompt they were written for. When the skill is loaded again for a
similar task, the bank re-runs on the new episode. A hard-constraint failure
is a regression flag; combined with a user "fail" verdict it triggers rollback.
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .bank import TestBank, HARD_STRENGTHS
from .episodes import _atomic_write
from .models import Episode

_WORD = re.compile(r"[a-z0-9]+")


def similarity(a: str, b: str) -> float:
    wa, wb = set(_WORD.findall(a.lower())), set(_WORD.findall(b.lower()))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


class SkillBank:
    def __init__(self, banks_root: Path | str, skill: str, min_similarity: float = 0.5):
        self.root = Path(banks_root) / skill
        self.skill = skill
        self.bank = TestBank(self.root / "tests")
        self.meta_path = self.root / "bank.json"
        self.min_similarity = min_similarity

    def meta(self) -> Dict[str, Any]:
        if self.meta_path.exists():
            return json.loads(self.meta_path.read_text())
        return {"skill": self.skill, "tasks": []}

    def exists(self) -> bool:
        return bool(self.bank.list())

    def absorb(self, run_bank: TestBank, task_prompt: str, run: str, keep: Optional[set] = None) -> int:
        """Persist run tests as the skill's regression suite. ``keep`` restricts to tests some version
        actually satisfied; a test nothing ever passed is more likely wrong than a contract."""
        kept = []
        for case in run_bank.list():
            if keep is not None and case.id not in keep:
                continue
            self.bank.add(case)
            kept.append(case.id)
        m = self.meta()
        m["tasks"].append({"prompt": task_prompt, "run": run, "at": time.time(), "tests": kept})
        _atomic_write(self.meta_path, json.dumps(m, indent=2))
        return len(kept)

    def applies_to(self, prompt: str) -> bool:
        return any(similarity(t.get("prompt", ""), prompt) >= self.min_similarity for t in self.meta().get("tasks", []))

    def regress(self, ep: Episode, episode_dir: Path) -> Optional[Dict[str, Any]]:
        if not self.exists() or not self.applies_to(ep.user_prompt):
            return None
        results = {c.id: self.bank.run(c.id, episode_dir) for c in self.bank.list()}
        hard = [c.id for c in self.bank.list() if c.assertion_strength in HARD_STRENGTHS and results.get(c.id) == 0]
        rec = {"skill": self.skill, "episode": ep.id, "at": time.time(), "results": results, "hard_failures": hard}
        with open(self.root / "regressions.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")
        return rec


def rollback_skill(hermes_home: Path, skill: str) -> bool:
    from .executors.hermes import find_skill_dir
    d = find_skill_dir(skill, Path(hermes_home))
    if d is None:
        return False
    prev = d / "SKILL.md.skillhex-prev"
    if not prev.exists():
        return False
    before = None
    ledger = None
    try:
        import os
        os.environ.setdefault("HERMES_HOME", str(hermes_home))
        from tools import skill_ledger as ledger  # type: ignore
        before = ledger.capture_before(d, skill=skill) if hasattr(ledger, "capture_before") else None
    except Exception:  # noqa: BLE001
        ledger = None
    shutil.copy2(prev, d / "SKILL.md")
    prev.unlink()
    try:
        if ledger is not None and hasattr(ledger, "record_mutation"):
            ledger.record_mutation("patch", skill, before=before, after_root=d, evidence={"skillhex": "rollback"})
    except Exception:  # noqa: BLE001
        pass
    return True
