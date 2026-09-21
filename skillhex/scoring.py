"""Node score s(v).

The paper gates semantic metrics on hard constraints but leaves the formula
open. Self-verifier tests are LLM-written and can be wrong, so this
implementation scores against the whole evidence matrix:

* Regression gate: a hard test the ORIGINAL skill satisfied and this version
  fails zeroes the score. Hard tests the original also fails are votes, not
  gates (an unsatisfiable "contract" is more likely a bad test).
* Ranking uses only discriminative columns (tests with both outcomes across
  evaluated versions); constant columns carry no information for ranking.
* Weights: hard 2.0, deterministic oracle 1.0, proxy 0.5, diagnostic 0.
* An official pass (reward 1) scores 1.
"""
from __future__ import annotations

from typing import Dict, Iterable, Optional

from .bank import TestCase, HARD_STRENGTHS, SEMANTIC_WEIGHTS
from .evidence import EvidenceMatrix

NEUTRAL = 0.5
HARD_WEIGHT = 2.0


def _weight(case: TestCase) -> float:
    if case.assertion_strength in HARD_STRENGTHS:
        return HARD_WEIGHT
    return SEMANTIC_WEIGHTS.get(case.assertion_strength, 0.0)


def score_row(row: Dict[str, int], cases: Iterable[TestCase], reward: Optional[int] = None) -> float:
    """Single-row scoring (no other versions to compare against): hard tests gate."""
    if reward == 1:
        return 1.0
    by_id = {c.id: c for c in cases}
    num = den = 0.0
    for tid, result in row.items():
        c = by_id.get(tid)
        if c is None:
            continue
        if c.assertion_strength in HARD_STRENGTHS:
            if result == 0:
                return 0.0
            continue
        w = SEMANTIC_WEIGHTS.get(c.assertion_strength)
        if w is None:
            continue
        num += w * result
        den += w
    return num / den if den else NEUTRAL


def column_stats(matrix: EvidenceMatrix) -> Dict[str, Dict[str, object]]:
    out: Dict[str, Dict[str, object]] = {}
    for tid in matrix.tests():
        col = matrix.column(tid)
        passes = sum(1 for v in col.values() if v == 1)
        out[tid] = {"passes": passes, "rows": len(col), "satisfiable": passes > 0,
                    "discriminative": 0 < passes < len(col)}
    return out


def score_node(node: str, matrix: EvidenceMatrix, cases: Iterable[TestCase], reward: Optional[int] = None,
               root: str = "v0") -> float:
    if reward == 1:
        return 1.0
    cases = list(cases)
    row = matrix.row(node)
    if len(matrix.nodes()) < 2:
        return score_row(row, cases, reward)
    by_id = {c.id: c for c in cases}
    stats = column_stats(matrix)
    root_row = matrix.row(root)
    num = den = 0.0
    for tid, result in row.items():
        c = by_id.get(tid)
        if c is None:
            continue
        if c.assertion_strength in HARD_STRENGTHS and node != root and root_row.get(tid) == 1 and result == 0:
            return 0.0
        if not stats.get(tid, {}).get("discriminative"):
            continue
        w = _weight(c)
        num += w * result
        den += w
    return num / den if den else NEUTRAL
