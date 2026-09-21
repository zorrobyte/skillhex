"""Evidence matrix M[v, j] in SQLite (paper eq. 5-7).

Rows are evaluated skill-patch nodes, columns are validated tests. A new
test is replayed on every cached node output (a new column); a new node is
run against every existing test (a new row). Refuting a hypothesis drops the
columns only that hypothesis referenced.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


class EvidenceMatrix:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.execute("CREATE TABLE IF NOT EXISTS cells (node TEXT, test TEXT, result INTEGER, PRIMARY KEY(node, test))")
        self.db.commit()

    def record(self, node: str, test: str, result: int) -> None:
        self.db.execute("INSERT OR REPLACE INTO cells VALUES (?,?,?)", (node, test, int(result)))
        self.db.commit()

    def get(self, node: str, test: str) -> Optional[int]:
        row = self.db.execute("SELECT result FROM cells WHERE node=? AND test=?", (node, test)).fetchone()
        return None if row is None else row[0]

    def row(self, node: str) -> Dict[str, int]:
        return {t: r for t, r in self.db.execute("SELECT test, result FROM cells WHERE node=? ORDER BY test", (node,))}

    def column(self, test: str) -> Dict[str, int]:
        return {n: r for n, r in self.db.execute("SELECT node, result FROM cells WHERE test=? ORDER BY node", (test,))}

    def nodes(self) -> List[str]:
        return [n for (n,) in self.db.execute("SELECT DISTINCT node FROM cells ORDER BY node")]

    def tests(self) -> List[str]:
        return [t for (t,) in self.db.execute("SELECT DISTINCT test FROM cells ORDER BY test")]

    def drop_tests(self, tests: Iterable[str]) -> None:
        self.db.executemany("DELETE FROM cells WHERE test=?", [(t,) for t in tests])
        self.db.commit()

    def missing(self, nodes: Iterable[str], tests: Iterable[str]) -> List[Tuple[str, str]]:
        have = {(n, t) for n, t in self.db.execute("SELECT node, test FROM cells")}
        return [(n, t) for n in nodes for t in tests if (n, t) not in have]

    def render(self, node_labels: Optional[Dict[str, str]] = None, tests: Optional[List[str]] = None) -> str:
        labels = node_labels or {}
        tests = tests or self.tests()
        nodes = self.nodes()
        width = max([len(labels.get(n, n)) for n in nodes] + [4])
        head = " " * width + "  " + "  ".join(tests)
        lines = [head]
        for n in nodes:
            r = self.row(n)
            cells = []
            for t in tests:
                v = r.get(t)
                mark = "✓" if v == 1 else "✗" if v == 0 else "·"
                cells.append(mark.center(len(t)))
            lines.append(labels.get(n, n).ljust(width) + "  " + "  ".join(cells))
        return "\n".join(lines)
