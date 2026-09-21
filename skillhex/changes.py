"""What skillhex changed, for humans and for the agent.

One JSONL line per event (applied / staged / undone / rolled_back). The plugin renders the
recent entries into a system-prompt section so the agent can tell the user what changed
since they last looked and act on "undo that".
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class ChangeLog:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def append(self, *, skill: str, kind: str, decision: str, run: Optional[str], at: Optional[float] = None,
               **extra: Any) -> Dict[str, Any]:
        rec = {"skill": skill, "kind": kind, "decision": decision, "run": run, "at": at or time.time(), **extra}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        return rec

    def all(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def recent(self, days: Optional[float] = None, limit: int = 10) -> List[Dict[str, Any]]:
        cutoff = time.time() - days * 86400 if days else None
        recs = [r for r in self.all() if cutoff is None or r.get("at", 0) >= cutoff]
        recs.sort(key=lambda r: r.get("at", 0), reverse=True)
        return recs[:limit]

    @staticmethod
    def describe(rec: Dict[str, Any]) -> str:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(rec.get("at", 0)))
        kind = rec.get("kind", "")
        s = f"{when}  {rec.get('skill')}: {kind}"
        if kind == "staged" and rec.get("pending_id"):
            s += f" (pending id {rec['pending_id']}, awaiting /skills approve)"
        d = rec.get("decision")
        if d and kind in ("applied", "staged"):
            s += f" — {d}"
        if rec.get("note"):
            s += f" — {rec['note']}"
        return s

    def render_for_prompt(self, days: float = 7, limit: int = 8, max_chars: int = 2500) -> str:
        recs = self.recent(days=days, limit=limit)
        if not recs:
            return ""
        head = ("The skillhex plugin evolves skills automatically: when a skill-guided answer turned out wrong, it "
                "tested candidate rewrites in throwaway profiles and kept the one the evidence supported. Recent changes "
                "(newest first):")
        tail = ("If the user asks what changed or why, explain from this list (the `skillhex` tool gives details: "
                "action=show|status). If they want a change reverted, call the `skillhex` tool with action=undo and the "
                "skill name; if they say a skill-guided answer was right or wrong, action=mark with verdict=ok|fail.")
        lines = [head]
        for r in recs:
            lines.append("- " + self.describe(r))
        lines.append(tail)
        text = "\n".join(lines)
        while len(text) > max_chars and len(lines) > 3:
            lines.pop(-2)
            text = "\n".join(lines)
        return text[:max_chars]
