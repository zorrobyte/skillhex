"""Per-session turn recorder: accumulates tool calls between host hooks and
emits one Episode per skill that was loaded during the turn."""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import Episode, ToolCall


@dataclass
class _TurnState:
    skills: List[str] = field(default_factory=list)
    tool_calls: List[ToolCall] = field(default_factory=list)
    task_id: Optional[str] = None


class TurnRecorder:
    def __init__(self, max_result_chars: int = 20000):
        self._sessions: Dict[str, _TurnState] = {}
        self.max_result_chars = max_result_chars

    def _state(self, session_id: str) -> _TurnState:
        return self._sessions.setdefault(session_id or "_", _TurnState())

    def skill_loaded(self, session_id: str, skill_name: str, task_id: Optional[str] = None) -> None:
        st = self._state(session_id)
        if skill_name not in st.skills:
            st.skills.append(skill_name)
        st.task_id = st.task_id or task_id

    def loaded_skills(self, session_id: str) -> List[str]:
        return list(self._state(session_id).skills)

    def tool_call(self, session_id: str, tool_call_id: str, name: str, args: Dict[str, Any],
                  result: Any, status: str, duration_ms: Optional[int]) -> None:
        text = result if isinstance(result, str) else str(result)
        if len(text) > self.max_result_chars:
            half = self.max_result_chars // 2
            text = text[:half] + f"\n…[{len(text) - self.max_result_chars} chars omitted]…\n" + text[-half:]
        self._state(session_id).tool_calls.append(
            ToolCall(id=str(tool_call_id or uuid.uuid4().hex[:8]), name=str(name), args=dict(args or {}),
                     result=text, status=str(status or "ok"), duration_ms=duration_ms))

    def merge(self, from_key: str, into: str) -> None:
        """Fold a bucket keyed some other way (e.g. by task id, when the host fired a lifecycle event
        without a session id) into the session's bucket."""
        src = self._sessions.pop(from_key, None)
        if src is None:
            return
        dst = self._state(into)
        for s in src.skills:
            if s not in dst.skills:
                dst.skills.append(s)
        dst.tool_calls.extend(src.tool_calls)
        dst.task_id = dst.task_id or src.task_id

    def finish_turn(self, session_id: str, turn_id: Optional[str], messages: List[Dict[str, Any]],
                    model: Optional[str], cwd: Optional[str], force_skill: Optional[str] = None,
                    prompt: Optional[str] = None, fallback_skills: Optional[List[str]] = None) -> List[Episode]:
        st = self._sessions.pop(session_id or "_", None)
        if st is None:
            return []
        skills = st.skills or ([force_skill] if force_skill else []) or list(fallback_skills or [])
        eps = []
        stamp = re.sub(r"[^A-Za-z0-9_.-]+", "-", turn_id or f"{int(time.time())}-{uuid.uuid4().hex[:6]}")[:80]
        for name in skills:
            eps.append(Episode(id=f"{stamp}-{uuid.uuid4().hex[:4]}", skill=name, skill_version="",
                               task_id=st.task_id or "", messages=list(messages), tool_calls=list(st.tool_calls),
                               session_id=session_id, turn_id=turn_id, model=model, cwd=cwd, prompt=prompt or None))
        return eps
