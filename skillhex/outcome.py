"""Automatic outcome signals. The acting model never grades itself: the bit
comes from a checker, or from what the *user* said next."""
from __future__ import annotations

import re
from typing import Tuple

_FAIL = re.compile(r"\b(wrong|incorrect|not right|that's not|thats not|didn'?t work|doesn'?t work|not what i|try again|"
                   r"still (broken|wrong|not)|no[,.!]|nope|you missed|failed|error|isn'?t right|fix (it|this))\b", re.I)
_PASS = re.compile(r"\b(thanks|thank you|thx|perfect|great|nice|awesome|works|that'?s (it|right|correct)|looks good|lgtm)\b", re.I)

SYSTEM = ("You classify whether a user's follow-up message indicates that the assistant's previous answer FAILED "
          "the user's intent (wrong, incomplete, needed redoing) or SUCCEEDED, or gives no signal. "
          "Only the user's words count; never judge the answer's quality yourself. "
          'Return JSON: {"verdict": "fail"|"pass"|"unknown", "reason": str}')


def heuristic_followup(text: str) -> str:
    t = (text or "").strip()
    if _FAIL.search(t):
        return "fail"
    if len(t) <= 80 and _PASS.search(t):
        return "pass"
    return "unknown"


def classify_followup(llm, prev_prompt: str, prev_answer: str, next_message: str) -> Tuple[str, str]:
    user = (f"PREVIOUS USER REQUEST:\n{prev_prompt[:2000]}\n\nASSISTANT'S PREVIOUS ANSWER (excerpt):\n{prev_answer[:2000]}\n\n"
            f"USER'S NEXT MESSAGE:\n{next_message[:2000]}")
    try:
        data = llm.complete_json(SYSTEM, user, max_tokens=300)
        v = str(data.get("verdict", "")).lower()
        if v not in ("fail", "pass", "unknown"):
            v = "unknown"
        return v, str(data.get("reason", ""))
    except Exception as e:  # noqa: BLE001
        return heuristic_followup(next_message), f"heuristic ({e.__class__.__name__})"
