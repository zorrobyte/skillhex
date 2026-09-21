"""Hierarchical node score s(v): hard constraints gate semantic metrics.

The paper leaves the exact formula open; this is our choice and is
documented as such. A node that passes the official verifier scores 1.
"""
from __future__ import annotations

from typing import Dict, Iterable, Optional

from .bank import TestCase, HARD_STRENGTHS, SEMANTIC_WEIGHTS

NEUTRAL = 0.5


def score_row(row: Dict[str, int], cases: Iterable[TestCase], reward: Optional[int] = None) -> float:
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
