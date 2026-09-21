"""On-disk episode store: ``<root>/<skill>/<episode_id>/episode.json``.

The directory is the cassette a self-verifier test reads. Keeping one JSON
file per episode (rather than a database) is deliberate: tests run as
subprocesses with only the stdlib.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import List, Optional

from .models import Episode

EPISODE_FILE = "episode.json"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class EpisodeStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)

    def dir(self, skill: str, episode_id: str) -> Path:
        return self.root / skill / episode_id

    def save(self, ep: Episode) -> Path:
        d = self.dir(ep.skill, ep.id)
        _atomic_write(d / EPISODE_FILE, json.dumps(ep.to_dict(), indent=2, sort_keys=True))
        return d

    def load(self, skill: str, episode_id: str) -> Episode:
        p = self.dir(skill, episode_id) / EPISODE_FILE
        return Episode.from_dict(json.loads(p.read_text()))

    def exists(self, skill: str, episode_id: str) -> bool:
        return (self.dir(skill, episode_id) / EPISODE_FILE).exists()

    def set_outcome(self, skill: str, episode_id: str, outcome: str,
                    source: str, note: Optional[str] = None) -> Episode:
        if outcome not in ("pass", "fail"):
            raise ValueError(f"outcome must be pass|fail, got {outcome!r}")
        ep = self.load(skill, episode_id)
        ep.outcome, ep.outcome_source, ep.outcome_note = outcome, source, note
        self.save(ep)
        return ep

    def list(self, skill: str, outcome: Optional[str] = None, unevolved: bool = False) -> List[Episode]:
        base = self.root / skill
        if not base.is_dir():
            return []
        eps = []
        for d in base.iterdir():
            if (d / EPISODE_FILE).exists():
                ep = self.load(skill, d.name)
                if (outcome is None or ep.outcome == outcome) and not (unevolved and ep.evolved_run):
                    eps.append(ep)
        return sorted(eps, key=lambda e: (e.created_at, e.id))

    def pending_skills(self) -> List[str]:
        """Skills with at least one failed episode no evolution run has consumed yet."""
        return [s for s in self.skills() if self.list(s, outcome="fail", unevolved=True)]

    def mark_evolved(self, skill: str, episode_ids: List[str], run: str) -> None:
        for eid in episode_ids:
            if self.exists(skill, eid):
                ep = self.load(skill, eid)
                ep.evolved_run = run
                self.save(ep)

    def skills(self) -> List[str]:
        if not self.root.is_dir():
            return []
        return sorted(d.name for d in self.root.iterdir() if d.is_dir() and not d.name.startswith("."))
