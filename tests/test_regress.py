import json
from pathlib import Path

from skillhex.bank import TestBank, TestCase
from skillhex.episodes import EpisodeStore
from skillhex.models import Episode, ToolCall
from skillhex.regress import SkillBank, similarity, rollback_skill

PASS = 'print("SELF_VERIFIER_RESULT=PASS")'
FAIL = 'print("SELF_VERIFIER_RESULT=FAIL")'


def test_similarity_is_word_jaccard():
    assert similarity("count my notes including archived", "count my notes including archived ones") > 0.7
    assert similarity("weather in kokomo", "count my notes") < 0.2


def test_skill_bank_persists_run_tests_with_task_fingerprint(tmp_path):
    run_bank = TestBank(tmp_path / "run" / "tests")
    run_bank.add(TestCase(id="t_hard", hypothesis_ids=["H1"], assertion_strength="hard_contract", summary="s", script=PASS))
    sb = SkillBank(tmp_path / "banks", "notes-cli")
    sb.absorb(run_bank, task_prompt="count notes including archived", run="r1")
    assert [c.id for c in sb.bank.list()] == ["t_hard"]
    meta = json.loads((tmp_path / "banks" / "notes-cli" / "bank.json").read_text())
    assert meta["tasks"][0]["prompt"] == "count notes including archived"


def test_regress_runs_only_for_similar_tasks_and_reports_hard_failures(tmp_path):
    run_bank = TestBank(tmp_path / "run" / "tests")
    run_bank.add(TestCase(id="t_hard", hypothesis_ids=["H1"], assertion_strength="hard_contract", summary="s", script=FAIL))
    run_bank.add(TestCase(id="t_soft", hypothesis_ids=["H1"], assertion_strength="proxy_quality", summary="s", script=PASS))
    sb = SkillBank(tmp_path / "banks", "notes-cli")
    sb.absorb(run_bank, task_prompt="count notes including archived", run="r1")
    store = EpisodeStore(tmp_path / "eps")
    ep = Episode(id="e1", skill="notes-cli", skill_version="v", task_id="t",
                 messages=[{"role": "user", "content": "count all my notes including archived ones"}])
    store.save(ep)
    res = sb.regress(ep, store.dir("notes-cli", "e1"))
    assert res is not None and res["hard_failures"] == ["t_hard"] and res["results"]["t_soft"] == 1
    unrelated = Episode(id="e2", skill="notes-cli", skill_version="v", task_id="t",
                        messages=[{"role": "user", "content": "what's the weather in kokomo"}])
    store.save(unrelated)
    assert sb.regress(unrelated, store.dir("notes-cli", "e2")) is None


def test_rollback_restores_previous_skill(tmp_path):
    hh = tmp_path / "hermes"
    d = hh / "skills" / "notes-cli"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("patched")
    (d / "SKILL.md.skillhex-prev").write_text("original")
    assert rollback_skill(hh, "notes-cli") is True
    assert (d / "SKILL.md").read_text() == "original"
    assert not (d / "SKILL.md.skillhex-prev").exists()
    assert rollback_skill(hh, "notes-cli") is False


def test_absorb_keeps_only_listed_tests(tmp_path):
    run_bank = TestBank(tmp_path / "run" / "tests")
    run_bank.add(TestCase(id="t_good", hypothesis_ids=["H1"], assertion_strength="hard_contract", summary="s", script=PASS))
    run_bank.add(TestCase(id="t_never_satisfied", hypothesis_ids=["H1"], assertion_strength="hard_contract", summary="s", script=FAIL))
    sb = SkillBank(tmp_path / "banks", "notes-cli")
    sb.absorb(run_bank, task_prompt="p", run="r", keep={"t_good"})
    assert [c.id for c in sb.bank.list()] == ["t_good"]
