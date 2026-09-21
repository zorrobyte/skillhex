"""Falsifiable failure-cause hypotheses, per skill (paper §3.1).

Each hypothesis h_i = (d_i, q_i, sigma_i, C_i): description, observable
behaviour that would support or refute it, lifecycle state, linked tests.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .episodes import _atomic_write

STATES = ("active", "refuted")


@dataclass
class Hypothesis:
    id: str
    text: str
    target_behavior: str = ""
    state: str = "active"
    tests: List[str] = field(default_factory=list)
    reason: str = ""
    refuted_reason: str = ""


class HypothesisStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._items: Dict[str, Hypothesis] = {}
        self._order: List[str] = []
        self._next = 1
        if self.path.exists():
            data = json.loads(self.path.read_text())
            self._next = data.get("next", 1)
            for d in data.get("hypotheses", []):
                h = Hypothesis(**d)
                self._items[h.id] = h
                self._order.append(h.id)

    def _save(self) -> None:
        payload = {"next": self._next, "hypotheses": [asdict(self._items[i]) for i in self._order]}
        _atomic_write(self.path, json.dumps(payload, indent=2))

    def add(self, text: str, target_behavior: str = "", reason: str = "") -> Hypothesis:
        hid = f"H{self._next}"
        self._next += 1
        h = Hypothesis(id=hid, text=text, target_behavior=target_behavior, reason=reason)
        self._items[hid] = h
        self._order.append(hid)
        self._save()
        return h

    def get(self, hid: str) -> Hypothesis:
        if hid not in self._items:
            raise KeyError(hid)
        return self._items[hid]

    def all(self) -> List[Hypothesis]:
        return [self._items[i] for i in self._order]

    def active(self) -> List[Hypothesis]:
        return [h for h in self.all() if h.state == "active"]

    def refine(self, hid: str, text: str, target_behavior: Optional[str] = None) -> Hypothesis:
        h = self.get(hid)
        h.text = text
        if target_behavior is not None:
            h.target_behavior = target_behavior
        self._save()
        return h

    def link_test(self, hid: str, test_id: str) -> None:
        h = self.get(hid)
        if test_id not in h.tests:
            h.tests.append(test_id)
        self._save()

    def refute(self, hid: str, reason: str = "") -> List[str]:
        """Mark refuted; return tests no remaining active hypothesis references (eq. 7)."""
        h = self.get(hid)
        h.state = "refuted"
        h.refuted_reason = reason
        still_used = {t for o in self.active() for t in o.tests}
        orphaned = [t for t in h.tests if t not in still_used]
        self._save()
        return orphaned

    def apply_ops(self, ops: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        """Apply reflection-emitted hypothesis operations. Returns ids touched per op kind."""
        result: Dict[str, List[str]] = {"added": [], "refined": [], "refuted": [], "orphaned_tests": []}
        for op in ops:
            kind = op.get("op")
            if kind == "add":
                h = self.add(op.get("text", ""), op.get("target_behavior", ""), op.get("reason", ""))
                result["added"].append(h.id)
            elif kind == "refine":
                self.refine(op["hypothesis_id"], op.get("text", ""), op.get("target_behavior"))
                result["refined"].append(op["hypothesis_id"])
            elif kind == "refute":
                orphaned = self.refute(op["hypothesis_id"], op.get("reason", ""))
                result["refuted"].append(op["hypothesis_id"])
                result["orphaned_tests"].extend(orphaned)
            elif kind == "drop_test" and op.get("test_id"):
                for h in self.all():
                    if op["test_id"] in h.tests:
                        h.tests.remove(op["test_id"])
                self._save()
                result.setdefault("dropped_tests", []).append(op["test_id"])
        return result

    def summary(self) -> str:
        lines = []
        for h in self.all():
            flag = "" if h.state == "active" else f" [{h.state}: {h.refuted_reason}]"
            lines.append(f"{h.id}: {h.text}{flag}\n    observable: {h.target_behavior}\n    tests: {', '.join(h.tests) or '-'}")
        return "\n".join(lines) or "(none)"
