"""Persistent skill-patch tree with evidence-guided selection (paper §3.2).

* Expansion prior from ordinal ranks (eq. 8): P(v,u) = softmax(-beta (rho_u - 1)).
* Max-backup (eq. 9): Q(v) = max(s(v), max_u Q(u)).
* PUCT selection (eq. 10) with global min-max normalised Q and first-play
  urgency for unevaluated children (parent's normalised Q minus ``fpu``).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

from .episodes import _atomic_write


@dataclass
class Node:
    id: str
    content: str
    parent: Optional[str] = None
    children: List[str] = field(default_factory=list)
    rank: int = 1
    prior: float = 1.0
    summary: str = ""
    hypothesis: str = ""
    visits: int = 0            # N(parent, self): times the edge into this node was selected
    evaluated: bool = False
    score: Optional[float] = None
    reward: Optional[int] = None
    episode_id: Optional[str] = None
    q: Optional[float] = None
    exhausted: bool = False


class PatchTree:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.nodes: Dict[str, Node] = {}
        self.root_id: Optional[str] = None
        self._next = 0
        if self.path.exists():
            data = json.loads(self.path.read_text())
            self.root_id = data["root"]
            self._next = data["next"]
            for d in data["nodes"]:
                self.nodes[d["id"]] = Node(**d)

    def _save(self) -> None:
        payload = {"root": self.root_id, "next": self._next, "nodes": [asdict(n) for n in self.nodes.values()]}
        _atomic_write(self.path, json.dumps(payload, indent=2))

    def _new_id(self) -> str:
        nid = f"v{self._next}"
        self._next += 1
        return nid

    def get(self, nid: str) -> Node:
        return self.nodes[nid]

    def root(self) -> Node:
        return self.nodes[self.root_id]

    def add_root(self, content: str) -> Node:
        n = Node(id=self._new_id(), content=content, summary="original skill")
        self.nodes[n.id] = n
        self.root_id = n.id
        self._save()
        return n

    def expand(self, parent_id: str, candidates: List[dict], beta: float = 1.0) -> List[str]:
        parent = self.get(parent_id)
        ranked = sorted(candidates, key=lambda c: c.get("rank", 1))
        weights = [math.exp(-beta * (c.get("rank", i + 1) - 1)) for i, c in enumerate(ranked)]
        z = sum(weights) or 1.0
        ids = []
        for c, w in zip(ranked, weights):
            n = Node(id=self._new_id(), content=c["content"], parent=parent_id, rank=c.get("rank", 1),
                     prior=w / z, summary=c.get("summary", ""), hypothesis=c.get("hypothesis", ""))
            self.nodes[n.id] = n
            parent.children.append(n.id)
            ids.append(n.id)
        self._save()
        return ids

    def evaluate(self, nid: str, score: float, reward: int, episode_id: Optional[str]) -> None:
        n = self.get(nid)
        n.evaluated, n.score, n.reward, n.episode_id = True, float(score), int(reward), episode_id
        self._backup(nid)
        self._save()

    def _backup(self, nid: str) -> None:
        cur: Optional[str] = nid
        while cur is not None:
            n = self.get(cur)
            vals = [n.score] if n.score is not None else []
            vals += [self.get(c).q for c in n.children if self.get(c).q is not None]
            n.q = max(vals) if vals else None
            cur = n.parent

    def mark_exhausted(self, nid: str) -> None:
        self.get(nid).exhausted = True
        self._save()

    def _viable(self, nid: str) -> bool:
        n = self.get(nid)
        if not n.exhausted:
            return True
        return any(self._viable(c) for c in n.children)

    def _norm(self) -> tuple[float, float]:
        qs = [n.q for n in self.nodes.values() if n.q is not None]
        if not qs:
            return 0.0, 1.0
        lo, hi = min(qs), max(qs)
        return lo, (hi if hi > lo else lo + 1.0)

    def _qbar(self, nid: str, lo: float, hi: float) -> float:
        q = self.get(nid).q
        return 0.0 if q is None else (q - lo) / (hi - lo)

    def select(self, c_puct: float = 1.4, fpu: float = 0.1) -> Optional[str]:
        """Walk from the root by eq. 10 until an unevaluated or unexpanded node; bump edge visits."""
        if self.root_id is None or not self._viable(self.root_id):
            return None
        lo, hi = self._norm()
        cur = self.root_id
        while True:
            n = self.get(cur)
            if not n.evaluated:
                return cur
            viable = [c for c in n.children if self._viable(c)]
            if not viable:
                return None if n.exhausted else cur
            n_total = sum(self.get(c).visits for c in viable)
            parent_qbar = self._qbar(cur, lo, hi)
            best, best_u = None, -math.inf
            for c in viable:
                child = self.get(c)
                qbar = self._qbar(c, lo, hi) if child.evaluated else max(0.0, parent_qbar - fpu)
                u = qbar + c_puct * child.prior * math.sqrt(max(1, n_total)) / (1 + child.visits)
                if u > best_u:
                    best, best_u = c, u
            self.get(best).visits += 1
            cur = best

    def best(self) -> Optional[Node]:
        done = [n for n in self.nodes.values() if n.evaluated]
        if not done:
            return None
        return max(done, key=lambda n: ((n.reward or 0), n.score or 0.0, -int(n.id[1:])))

    def evaluated_nodes(self) -> List[Node]:
        return [n for n in self.nodes.values() if n.evaluated]

    def depth(self, nid: str) -> int:
        d, cur = 0, self.get(nid).parent
        while cur is not None:
            d, cur = d + 1, self.get(cur).parent
        return d

    def render(self) -> str:
        lines = []

        def walk(nid: str, indent: int) -> None:
            n = self.get(nid)
            mark = "★" if n.reward == 1 else ("✓" if n.evaluated else "·")
            s = f"{n.score:.2f}" if n.score is not None else "-"
            lines.append(f"{'  ' * indent}{mark} {n.id} s={s} q={'-' if n.q is None else f'{n.q:.2f}'} "
                         f"p={n.prior:.2f} n={n.visits}{' exhausted' if n.exhausted else ''} {n.summary[:60]}")
            for c in n.children:
                walk(c, indent + 1)

        if self.root_id:
            walk(self.root_id, 0)
        return "\n".join(lines)
