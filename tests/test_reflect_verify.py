import json

from skillhex.bank import TestBank
from skillhex.episodes import EpisodeStore
from skillhex.evidence import EvidenceMatrix
from skillhex.hypotheses import HypothesisStore
from skillhex.models import Episode, ToolCall
from skillhex.reflect import LLMReflector
from skillhex.verify import LLMVerifier
from skillhex.search import ReflectionContext, Task
from skillhex.tree import PatchTree


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def complete_json(self, system, user, **kw):
        self.prompts.append((system, user))
        return self.reply


def ctx(tmp_path):
    tree = PatchTree(tmp_path / "tree.json")
    root = tree.add_root("# weather\nUse wttr.in for forecasts.")
    hs = HypothesisStore(tmp_path / "h.json")
    hs.add("wttr.in only returns 3 days", target_behavior="a 14-day source is used")
    eps = EpisodeStore(tmp_path / "eps")
    ep = Episode(id="e0", skill="weather", skill_version="v0", task_id="t",
                 messages=[{"role": "user", "content": "14 day forecast for Kokomo"},
                           {"role": "assistant", "content": "here are 3 days"}],
                 tool_calls=[ToolCall(id="c1", name="terminal", args={"command": "curl wttr.in/Kokomo"}, result="3 days of data " * 50)],
                 outcome="fail", outcome_note="only 3 days, asked for 14")
    eps.save(ep)
    root.episode_id = "e0"
    root.evaluated = True
    m = EvidenceMatrix(tmp_path / "e.db")
    m.record("v0", "t_days", 0)
    bank = TestBank(tmp_path / "bank")
    return ReflectionContext(node=root, tree=tree, hypotheses=hs, matrix=m, bank=bank, episodes=eps,
                             task=Task(id="t", skill="weather", prompt="14 day forecast for Kokomo"))


REFLECTION_REPLY = {
    "decision": "emit_patch",
    "sufficiency": {"is_sufficient": True, "confidence": 0.7, "reason": "clear"},
    "hypothesis_ops": [{"op": "add", "text": "skill never mentions Open-Meteo", "target_behavior": "open-meteo called", "reason": "r"}],
    "active_hypothesis_ids": ["H1", "H2"],
    "patch_candidates": [
        {"patch_operator": "modify", "hypothesis": "H2", "rank": 2, "edit_intent": {"primary_failure_mode": "wrong source"},
         "skill_md": "# weather\nUse Open-Meteo.", "notes": "n"},
        {"patch_operator": "modify", "hypothesis": "H1", "rank": 1, "edit_intent": {"primary_failure_mode": "days"},
         "skill_md": "# weather\nUse NWS.", "notes": "n"},
    ],
    "summary": "two candidates",
}


def test_reflector_maps_reply_and_prompt_has_evidence(tmp_path):
    c = ctx(tmp_path)
    llm = FakeLLM(REFLECTION_REPLY)
    r = LLMReflector(llm)
    res = r.reflect(c, must_emit=True)
    assert res.decision == "emit_patch"
    assert [p["rank"] for p in res.patch_candidates] == [2, 1]
    assert res.patch_candidates[1]["content"] == "# weather\nUse NWS."
    assert res.patch_candidates[1]["hypothesis"] == "H1"
    assert res.hypothesis_ops[0]["op"] == "add"
    system, user = llm.prompts[0]
    assert "Use wttr.in" in user            # current skill
    assert "H1: wttr.in only returns 3 days" in user
    assert "t_days" in user                 # evidence matrix
    assert "curl wttr.in/Kokomo" in user     # transcript
    assert "must_emit_patch=true" in user
    assert "only 3 days, asked for 14" in user   # outcome note


def test_reflector_truncates_long_tool_results(tmp_path):
    c = ctx(tmp_path)
    llm = FakeLLM(REFLECTION_REPLY)
    LLMReflector(llm, max_result_chars=100).reflect(c, must_emit=False)
    _, user = llm.prompts[0]
    assert "3 days of data " * 50 not in user
    assert "must_emit_patch=false" in user


def test_reflector_lists_tried_edits_from_tree(tmp_path):
    c = ctx(tmp_path)
    c.tree.expand(c.node.id, [{"content": "x", "rank": 1, "summary": "tried NWS already", "hypothesis": "H1"}])
    child = c.tree.get(c.tree.root().children[0])
    c.tree.evaluate(child.id, score=0.1, reward=0, episode_id=None)
    llm = FakeLLM(REFLECTION_REPLY)
    LLMReflector(llm).reflect(c, must_emit=False)
    _, user = llm.prompts[0]
    assert "tried NWS already" in user and "score=0.10" in user


VERIFIER_REPLY = {"cases": [
    {"test_id": "t_fourteen_rows", "test_type": "format", "assertion_strength": "hard_contract",
     "oracle_source": "task instruction", "public_support": "user asked for 14 days",
     "summary": "final answer has 14 day rows", "hypothesis_ids": ["H1"],
     "script": "print('SELF_VERIFIER_RESULT=FAIL')"},
]}


def test_verifier_maps_cases_and_includes_feedback(tmp_path):
    c = ctx(tmp_path)
    llm = FakeLLM(VERIFIER_REPLY)
    v = LLMVerifier(llm)
    cases = v.generate(c, c.hypotheses.active(), feedback=["t_old: syntax error"])
    assert len(cases) == 1
    tc = cases[0]
    assert tc.id == "t_fourteen_rows" and tc.assertion_strength == "hard_contract"
    assert tc.hypothesis_ids == ["H1"] and "SELF_VERIFIER_RESULT" in tc.script
    _, user = llm.prompts[0]
    assert "t_old: syntax error" in user
    assert "SKILLHEX_EPISODE" in user and "episode.json" in user
    assert "H1: wttr.in only returns 3 days" in user


def test_verifier_drops_cases_with_missing_fields(tmp_path):
    c = ctx(tmp_path)
    llm = FakeLLM({"cases": [{"test_id": "x"}, VERIFIER_REPLY["cases"][0]]})
    cases = LLMVerifier(llm).generate(c, c.hypotheses.active())
    assert [t.id for t in cases] == ["t_fourteen_rows"]
