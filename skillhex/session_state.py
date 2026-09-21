"""Per-session state that must survive host process restarts (CLI --resume,
gateway restarts): which skills are loaded in the session, and the last
skill-guided turn awaiting an outcome verdict."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .episodes import _atomic_write

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


class SessionStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)

    def _path(self, sid: str) -> Path:
        return self.root / (_SAFE.sub("-", sid or "_")[:120] + ".json")

    def _read(self, sid: str) -> Dict[str, Any]:
        p = self._path(sid)
        if p.exists():
            try:
                return json.loads(p.read_text())
            except json.JSONDecodeError:
                pass
        return {"skills": [], "last": None, "updated_at": 0}

    def _write(self, sid: str, data: Dict[str, Any]) -> None:
        data["updated_at"] = time.time()
        _atomic_write(self._path(sid), json.dumps(data))

    def skills(self, sid: str) -> List[str]:
        return list(self._read(sid).get("skills") or [])

    def add_skill(self, sid: str, skill: str) -> None:
        d = self._read(sid)
        if skill not in d["skills"]:
            d["skills"].append(skill)
        self._write(sid, d)

    def last(self, sid: str) -> Optional[Dict[str, Any]]:
        return self._read(sid).get("last")

    def set_last(self, sid: str, episodes: List[Tuple[str, str]], prompt: str, answer: str, regressed: List[str]) -> None:
        d = self._read(sid)
        d["last"] = {"episodes": [list(e) for e in episodes], "prompt": prompt, "answer": answer,
                     "regressed": list(regressed), "resolved": False, "at": time.time()}
        self._write(sid, d)

    def resolve(self, sid: str) -> None:
        d = self._read(sid)
        if d.get("last"):
            d["last"]["resolved"] = True
        self._write(sid, d)

    def all_unresolved(self) -> List[Tuple[str, Dict[str, Any]]]:
        out = []
        if self.root.is_dir():
            for p in self.root.glob("*.json"):
                d = json.loads(p.read_text())
                if d.get("last") and not d["last"].get("resolved"):
                    out.append((p.stem, d["last"]))
        return out
