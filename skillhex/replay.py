"""Cassette replay: serve recorded tool results to a re-run of the same task so
a candidate skill can be evaluated without repeating side effects."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from .models import Episode

READ_ONLY_TOOLS = {
    "read_file", "search_files", "skills_list", "skill_view", "web_search", "web_extract", "web_fetch",
    "memory", "session_search", "todo", "vision_analyze", "skillhex_status",
}


def _key(name: str, args: Dict[str, Any]) -> str:
    return name + "|" + json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=str)


class Cassette:
    def __init__(self, episode: Episode):
        self._queues: Dict[str, List[str]] = {}
        for tc in episode.tool_calls:
            self._queues.setdefault(_key(tc.name, tc.args), []).append(tc.result)

    def lookup(self, name: str, args: Dict[str, Any]) -> Optional[str]:
        q = self._queues.get(_key(name, args))
        if not q:
            return None
        return q.pop(0)


class ReplayPolicy:
    def __init__(self, cassette: Cassette, mode: str = "permissive"):
        self.cassette = cassette
        self.mode = mode

    def decide(self, name: str, args: Dict[str, Any]) -> Tuple[str, Optional[str]]:
        hit = self.cassette.lookup(name, args)
        if hit is not None:
            return "replay", hit
        if name in READ_ONLY_TOOLS or self.mode == "permissive":
            return "live", None
        return "block", f"[skillhex replay] call not in cassette: {name} {json.dumps(args, default=str)[:200]}"
