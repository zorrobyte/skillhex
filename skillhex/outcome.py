"""Automatic outcome signals. The acting model never grades itself: the bit
comes from a checker, or from what the *user* said next."""
from __future__ import annotations

import re
from typing import Tuple

# Phrases only: a bare "error", "failed" or "no" is a topic word as often as a verdict.
_FAIL = re.compile(r"(\b(that'?s|thats|this is|it'?s|its) (wrong|incorrect|not right|not it)\b|\bnot what i (asked|wanted|meant)\b|"
                   r"\b(didn'?t|doesn'?t|does not|did not) work\b|\btry again\b|\bstill (broken|wrong|not working)\b|"
                   r"\byou (missed|forgot|got it wrong)\b|\bisn'?t right\b|\bfix (it|this|that)\b|\boff by\b|"
                   r"^\s*(no|nope|wrong|incorrect)\b[,.!: ]|\bthat is (wrong|incorrect)\b)", re.I)
_PASS = re.compile(r"\b(thanks|thank you|thx|perfect|great|nice|awesome|that works|works now|that'?s (it|right|correct)|looks good|lgtm)\b", re.I)
_HEDGE = re.compile(r"\b(but|however|although|except|not quite|almost)\b", re.I)

SYSTEM = ("You classify whether a user's follow-up message indicates that the assistant's previous answer FAILED "
          "the user's intent (wrong, incomplete, needed redoing) or SUCCEEDED, or gives no signal. "
          "Only the user's words count; never judge the answer's quality yourself. "
          'Return JSON: {"verdict": "fail"|"pass"|"unknown", "reason": str}')


def heuristic_followup(text: str) -> str:
    t = (text or "").strip()
    if _FAIL.search(t):
        return "fail"
    if len(t) <= 80 and _PASS.search(t) and not _HEDGE.search(t):
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
