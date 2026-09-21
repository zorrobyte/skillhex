"""Core data types shared by every SkillHEX component.

Everything here is plain dataclasses that round-trip through JSON so that
self-verifier test scripts (which run in a subprocess) can read episodes
without importing this package.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class ToolCall:
    id: str
    name: str
    args: Dict[str, Any]
    result: str
    status: str = "ok"          # "ok" | "error"
    duration_ms: Optional[int] = None


@dataclass
class Episode:
    """One execution of a task with one skill version loaded.

    ``outcome`` is the paper's binary reward R. It is never set by the model
    that produced the episode: it comes from a checker, the user, or a
    classification of the user's next message.
    """
    id: str
    skill: str
    skill_version: str
    task_id: str
    messages: List[Dict[str, Any]] = field(default_factory=list)
    tool_calls: List[ToolCall] = field(default_factory=list)
    outcome: Optional[str] = None       # None | "pass" | "fail"
    outcome_source: Optional[str] = None
    outcome_note: Optional[str] = None
    session_id: Optional[str] = None
    turn_id: Optional[str] = None
    model: Optional[str] = None
    cwd: Optional[str] = None
    workspace_snapshot: Optional[str] = None
    evolved_run: Optional[str] = None   # set once an evolution run has consumed this failure
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Episode":
        d = dict(d)
        d["tool_calls"] = [ToolCall(**tc) for tc in d.get("tool_calls", [])]
        return cls(**d)

    @property
    def user_prompt(self) -> str:
        for m in self.messages:
            if m.get("role") == "user":
                c = m.get("content")
                if isinstance(c, list):
                    return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
                return str(c or "")
        return ""

    @property
    def final_response(self) -> str:
        for m in reversed(self.messages):
            if m.get("role") == "assistant" and m.get("content"):
                return str(m["content"])
        return ""
