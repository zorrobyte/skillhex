"""LLM-backed self-verifier test generator (paper Appendix A, "System Prompt for Self-Verifier")."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .bank import TestCase, STRENGTHS
from .hypotheses import Hypothesis
from .reflect import render_transcript, render_bank, PROMPTS
from .search import ReflectionContext


class LLMVerifier:
    def __init__(self, llm, max_result_chars: int = 2500, max_tokens: int = 16000):
        self.llm = llm
        self.max_result_chars = max_result_chars
        self.max_tokens = max_tokens
        self.system = (PROMPTS / "verifier.md").read_text()

    def build_user_prompt(self, ctx: ReflectionContext, active: List[Hypothesis], feedback: Optional[List[str]]) -> str:
        ep = ctx.node_episode() or ctx.node_episode(ctx.tree.root())
        hyps = "\n".join(f"{h.id}: {h.text}\n    observable: {h.target_behavior}" for h in active) or "(none)"
        parts = [
            f"# TASK INSTRUCTION\n{ctx.task.prompt}",
            f"# SKILL UNDER TEST ({ctx.node.id})\n```markdown\n{ctx.node.content}\n```",
            f"# ACTIVE HYPOTHESES TO PROBE\n{hyps}",
            f"# RECORDED ATTEMPT (episode.json will contain exactly these messages and tool_calls)\n{render_transcript(ep, self.max_result_chars)}",
            f"# EXISTING TESTS (do not duplicate)\n{render_bank(ctx)}",
        ]
        if feedback:
            parts.append("# VALIDATOR FEEDBACK ON YOUR PREVIOUS DRAFTS (repair or replace these; do not resend rejected ids unchanged)\n" + "\n".join(feedback))
        parts.append("Write the tests now. Remember: read $SKILLHEX_EPISODE/episode.json, print SELF_VERIFIER_RESULT=PASS or FAIL.")
        return "\n\n".join(parts)

    def generate(self, ctx: ReflectionContext, active: List[Hypothesis],
                 feedback: Optional[List[str]] = None) -> List[TestCase]:
        data = self.llm.complete_json(self.system, self.build_user_prompt(ctx, active, feedback), max_tokens=self.max_tokens)
        out: List[TestCase] = []
        for c in data.get("cases") or []:
            tid, script = c.get("test_id"), c.get("script")
            hyps = [h for h in (c.get("hypothesis_ids") or []) if isinstance(h, str)]
            strength = c.get("assertion_strength") or ""
            if not (tid and script and hyps and strength in STRENGTHS):
                continue
            out.append(TestCase(id=str(tid), hypothesis_ids=hyps, assertion_strength=strength,
                                summary=str(c.get("summary") or ""), script=str(script),
                                test_type=str(c.get("test_type") or "other"), oracle_source=str(c.get("oracle_source") or ""),
                                public_support=str(c.get("public_support") or ""), diagnosis=dict(c.get("diagnosis") or {})))
        return out
