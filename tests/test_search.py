"""Algorithm 1 end to end with scripted components (no LLM, no host)."""
from pathlib import Path

from skillhex.bank import TestCase
from skillhex.models import Episode, ToolCall
from skillhex.search import SkillSearch, SearchConfig, Task, ReflectionResult, ExecResult


ORACLE = 'import json,os\nep=json.load(open(os.path.join(os.environ["SKILLHEX_EPISODE"],"episode.json")))\nok=any("open-meteo" in tc["result"] for tc in ep["tool_calls"])\nprint("SELF_VERIFIER_RESULT="+("PASS" if ok else "FAIL"))\n'
ALWAYS_PASS = 'print("SELF_VERIFIER_RESULT=PASS")\n'
FM = "---\nname: weather\ndescription: Forecasts.\n---\n"


class FakeExecutor:
    """Reward 1 iff the skill mentions open-meteo. Records calls."""
    def __init__(self):
        self.calls = 0

    def execute(self, skill_content: str, task: Task, node_id: str) -> ExecResult:
        self.calls += 1
        good = "open-meteo" in skill_content
        ep = Episode(id=f"ep_{node_id}", skill=task.skill, skill_version=node_id, task_id=task.id,
                     messages=[{"role": "user", "content": task.prompt}, {"role": "assistant", "content": "done"}],
                     tool_calls=[ToolCall(id="c", name="terminal", args={}, result="open-meteo 16 days" if good else "wttr 3 days")])
        return ExecResult(episode=ep, reward=1 if good else 0)


class ScriptedReflector:
    """Root: propose [wrong NWS patch (rank 1), right open-meteo patch (rank 2)].
    Any NWS node: keep digging into NWS (wrong) so the branch stalls."""
    def __init__(self, seed_hyps=True):
        self.calls = []
        self.seed_hyps = seed_hyps

    def reflect(self, ctx, must_emit: bool) -> ReflectionResult:
        self.calls.append((ctx.node.id, must_emit))
        ops = []
        if ctx.node.id == ctx.tree.root_id and not ctx.hypotheses.all() and self.seed_hyps:
            ops = [{"op": "add", "text": "wttr.in only returns 3 days", "target_behavior": "a 14-day source is used"},
                   {"op": "add", "text": "NWS has 14 days", "target_behavior": "nws returns 14"}]
        if "nws" in ctx.node.content:
            cands = [{"content": ctx.node.content + " more nws", "rank": 1, "summary": "more NWS", "hypothesis": "H2"}]
        else:
            cands = [{"content": FM + "skill: use nws", "rank": 1, "summary": "NWS", "hypothesis": "H2"},
                     {"content": FM + "skill: use open-meteo", "rank": 2, "summary": "Open-Meteo", "hypothesis": "H1"}]
        return ReflectionResult(decision="emit_patch", hypothesis_ops=ops, active_hypothesis_ids=["H1", "H2"],
                                patch_candidates=cands)


class ScriptedVerifier:
    def __init__(self):
        self.calls = 0

    def generate(self, ctx, active, feedback=None):
        self.calls += 1
        if self.calls > 1:
            return []
        return [TestCase(id="t_open_meteo_used", hypothesis_ids=["H1"], assertion_strength="deterministic_oracle",
                         summary="a tool result came from open-meteo", script=ORACLE)]


def make_search(tmp_path, reflector=None, verifier=None, executor=None, **cfg):
    task = Task(id="t1", skill="weather", prompt="14 day forecast", cwd=str(tmp_path))
    return SkillSearch(
        workdir=tmp_path / "work", task=task, initial_skill=FM + "skill: use wttr",
        reflector=reflector or ScriptedReflector(), verifier=verifier or ScriptedVerifier(),
        executor=executor or FakeExecutor(), config=SearchConfig(**cfg),
    )


def test_finds_passing_patch_by_backtracking_to_preserved_sibling(tmp_path):
    ex = FakeExecutor()
    s = make_search(tmp_path, executor=ex, K=5, L=1)
    result = s.run()
    assert result.passed
    assert "open-meteo" in result.best.content
    assert ex.calls <= 6  # initial + K


def test_initial_episode_seeds_root_without_executor_call(tmp_path):
    ex = FakeExecutor()
    s = make_search(tmp_path, executor=ex, K=5, L=1)
    root_ep = Episode(id="ep0", skill="weather", skill_version="v0", task_id="t1",
                      messages=[{"role": "user", "content": "14 day forecast"}],
                      tool_calls=[ToolCall(id="c", name="terminal", args={}, result="wttr 3 days")], outcome="fail")
    result = s.run(initial_episode=root_ep)
    assert result.passed
    assert ex.calls <= 5
    assert s.tree.root().episode_id == "ep0"


def test_budget_exhausted_returns_best_evaluated_node(tmp_path):
    class NeverRight(FakeExecutor):
        def execute(self, skill_content, task, node_id):
            r = super().execute(skill_content.replace("open-meteo", "x"), task, node_id)
            return r
    ex = NeverRight()
    s = make_search(tmp_path, executor=ex, K=2, L=1)
    result = s.run()
    assert not result.passed
    assert result.best is not None and result.best.evaluated
    assert ex.calls == 3  # root + K


def test_new_test_replays_across_all_evaluated_nodes(tmp_path):
    s = make_search(tmp_path, K=3, L=1)
    s.run()
    m = s.matrix
    assert "t_open_meteo_used" in m.tests()
    for n in s.tree.evaluated_nodes():
        assert m.get(n.id, "t_open_meteo_used") is not None, n.id


def test_refuting_a_hypothesis_prunes_its_tests(tmp_path):
    class RefutingReflector(ScriptedReflector):
        def reflect(self, ctx, must_emit):
            r = super().reflect(ctx, must_emit)
            if ctx.node.id != ctx.tree.root_id and ctx.hypotheses.get("H1").state == "active" and "t_open_meteo_used" in ctx.bank_ids:
                r.hypothesis_ops = [{"op": "refute", "hypothesis_id": "H1", "reason": "nope"}]
            return r
    s = make_search(tmp_path, reflector=RefutingReflector(), K=3, L=1)
    s.run()
    assert s.hypotheses.get("H1").state == "refuted"
    assert "t_open_meteo_used" not in [c.id for c in s.bank.list()]
    assert "t_open_meteo_used" not in s.matrix.tests()


def test_node_with_no_candidates_is_exhausted_and_search_ends(tmp_path):
    class Silent:
        def reflect(self, ctx, must_emit):
            return ReflectionResult(decision="need_more_evidence", hypothesis_ops=[], active_hypothesis_ids=[], patch_candidates=[])
    class NoTests:
        def generate(self, ctx, active, feedback=None):
            return []
    s = make_search(tmp_path, reflector=Silent(), verifier=NoTests(), K=3, L=2)
    result = s.run()
    assert not result.passed
    assert s.tree.root().exhausted


def test_invalid_tests_are_rejected_with_feedback_and_not_added(tmp_path):
    class BadThenGood:
        def __init__(self):
            self.feedback = []
        def generate(self, ctx, active, feedback=None):
            self.feedback.append(feedback)
            if feedback:
                return []
            return [TestCase(id="t_bad", hypothesis_ids=["H1"], assertion_strength="deterministic_oracle", summary="b", script="def x(:")]
    v = BadThenGood()
    s = make_search(tmp_path, verifier=v, K=1, L=1, V=2)
    s.run()
    assert [c.id for c in s.bank.list()] == []
    assert any(fb and "t_bad" in str(fb) for fb in v.feedback)


def test_candidates_failing_lint_are_not_expanded(tmp_path):
    class BadCandidates(ScriptedReflector):
        def reflect(self, ctx, must_emit):
            r = super().reflect(ctx, must_emit)
            r.patch_candidates = [{"content": "# no frontmatter", "rank": 1, "summary": "bad", "hypothesis": "H1"},
                                  {"content": "---\nname: weather\ndescription: ok.\n---\nuse open-meteo", "rank": 2, "summary": "good", "hypothesis": "H1"}]
            return r
    ex = FakeExecutor()
    s = make_search(tmp_path, reflector=BadCandidates(), executor=ex, K=3, L=1)
    result = s.run()
    assert result.passed
    contents = [n.content for n in s.tree.nodes.values()]
    assert "# no frontmatter" not in contents


def test_early_stop_on_evidence_score_when_no_reward(tmp_path):
    class NoReward(FakeExecutor):
        def execute(self, content, task, node_id):
            r = super().execute(content, task, node_id)
            r.reward = 0
            return r
    ex = NoReward()
    s = make_search(tmp_path, executor=ex, K=5, L=1, early_stop_score=0.9)
    result = s.run()
    assert not result.passed
    assert result.best is not None and result.best.score >= 0.9 and "open-meteo" in result.best.content
    assert ex.calls < 6
