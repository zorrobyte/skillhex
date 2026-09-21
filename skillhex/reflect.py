"""LLM-backed Reflector (paper Appendix A, "System Prompt for Reflection")."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Episode
from .search import ReflectionContext, ReflectionResult

PROMPTS = Path(__file__).parent / "prompts"


def render_transcript(ep: Optional[Episode], max_result_chars: int = 1500, max_msg_chars: int = 3000) -> str:
    if ep is None:
        return "(no recorded attempt)"
    lines = [f"USER TASK: {ep.user_prompt[:max_msg_chars]}"]
    for i, tc in enumerate(ep.tool_calls, 1):
        args = json.dumps(tc.args, ensure_ascii=False)
        res = tc.result or ""
        if len(res) > max_result_chars:
            res = res[: max_result_chars // 2] + f"\n…[{len(res) - max_result_chars} chars omitted]…\n" + res[-max_result_chars // 2:]
        lines.append(f"[{i}] {tc.name}({args[:600]}) -> status={tc.status}\n{res}")
    lines.append(f"FINAL ANSWER: {ep.final_response[:max_msg_chars]}")
    lines.append(f"OFFICIAL OUTCOME: {ep.outcome or 'unknown'}" + (f" — note: {ep.outcome_note}" if ep.outcome_note else ""))
    return "\n".join(lines)


def render_tried_edits(ctx: ReflectionContext) -> str:
    rows = []
    for n in ctx.tree.nodes.values():
        if n.id == ctx.tree.root_id:
            continue
        s = f"score={n.score:.2f}" if n.score is not None else "unevaluated"
        r = f" official={'PASS' if n.reward == 1 else 'FAIL'}" if n.evaluated else ""
        rows.append(f"- {n.id} (parent {n.parent}, hyp {n.hypothesis}): {n.summary} — {s}{r}")
    return "\n".join(rows) or "(none yet)"


def render_bank(ctx: ReflectionContext) -> str:
    rows = [f"- {c.id} [{c.assertion_strength}] hyps={','.join(c.hypothesis_ids)}: {c.summary}" for c in ctx.bank.list()]
    return "\n".join(rows) or "(no tests yet)"


class LLMReflector:
    def __init__(self, llm, max_result_chars: int = 1500, max_tokens: int = 16000):
        self.llm = llm
        self.max_result_chars = max_result_chars
        self.max_tokens = max_tokens
        self.system = (PROMPTS / "reflection.md").read_text()

    def build_user_prompt(self, ctx: ReflectionContext, must_emit: bool) -> str:
        ep = ctx.node_episode()
        labels = {n.id: f"{n.id}{' (root)' if n.id == ctx.tree.root_id else ''}" for n in ctx.tree.nodes.values()}
        parts = [
            f"# CONTROL\nmust_emit_patch={'true' if must_emit else 'false'}\nreflection_round={ctx.round}\ncurrent_node={ctx.node.id}",
            f"# TASK INSTRUCTION\n{ctx.task.prompt}",
            f"# CURRENT SKILL ({ctx.node.id})\n```markdown\n{ctx.node.content}\n```",
            f"# RECORDED ATTEMPT WITH THIS SKILL\n{render_transcript(ep, self.max_result_chars)}",
            f"# HYPOTHESES\n{ctx.hypotheses.summary()}",
            f"# TEST BANK\n{render_bank(ctx)}",
            f"# EVIDENCE MATRIX (rows = skill versions, ✓ pass ✗ fail · not run)\n{ctx.matrix.render(node_labels=labels)}",
            f"# TRIED EDITS\n{render_tried_edits(ctx)}",
        ]
        return "\n\n".join(parts)

    def reflect(self, ctx: ReflectionContext, must_emit: bool) -> ReflectionResult:
        data = self.llm.complete_json(self.system, self.build_user_prompt(ctx, must_emit), max_tokens=self.max_tokens)
        cands: List[Dict[str, Any]] = []
        for i, c in enumerate(data.get("patch_candidates") or [], 1):
            md = c.get("skill_md") or c.get("content") or ""
            if not md.strip():
                continue
            intent = c.get("edit_intent") or {}
            summary = intent.get("primary_failure_mode") or c.get("notes") or f"candidate {i}"
            cands.append({"content": md, "rank": int(c.get("rank") or i), "summary": str(summary)[:200],
                          "hypothesis": str(c.get("hypothesis") or ""), "operator": c.get("patch_operator", "modify")})
        decision = data.get("decision") or ("emit_patch" if cands else "need_more_evidence")
        return ReflectionResult(decision=decision, hypothesis_ops=list(data.get("hypothesis_ops") or []),
                                active_hypothesis_ids=list(data.get("active_hypothesis_ids") or []),
                                patch_candidates=cands, summary=str(data.get("summary") or ""))
