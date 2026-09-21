from pathlib import Path
import textwrap

from skillhex.bank import TestBank, TestCase, STRENGTHS
from skillhex.episodes import EpisodeStore
from skillhex.models import Episode, ToolCall


PASS_SCRIPT = textwrap.dedent("""
    import json, os, sys
    ep = json.load(open(os.path.join(os.environ["SKILLHEX_EPISODE"], "episode.json")))
    ok = any(tc["name"] == "terminal" for tc in ep["tool_calls"])
    print("SELF_VERIFIER_RESULT=" + ("PASS" if ok else "FAIL"))
""")

FAIL_SCRIPT = 'print("SELF_VERIFIER_RESULT=FAIL")'
NO_OUTPUT_SCRIPT = 'print("hello")'
SYNTAX_ERROR_SCRIPT = 'def x(:\n  pass'
HANG_SCRIPT = 'import time\ntime.sleep(30)'


def ep(tmp_path, eid="e1", calls=None):
    store = EpisodeStore(tmp_path / "eps")
    e = Episode(id=eid, skill="s", skill_version="v", task_id="t",
                tool_calls=calls if calls is not None else [ToolCall(id="c", name="terminal", args={}, result="x")])
    store.save(e)
    return store, e


def case(tid="t_terminal_used", strength="deterministic_oracle", script=PASS_SCRIPT, hyps=("H1",)):
    return TestCase(id=tid, hypothesis_ids=list(hyps), assertion_strength=strength,
                    summary="terminal tool was used", script=script)


def test_add_writes_script_and_case_json(tmp_path):
    bank = TestBank(tmp_path / "bank")
    bank.add(case())
    assert (tmp_path / "bank" / "t_terminal_used" / "test.py").read_text() == PASS_SCRIPT
    assert bank.get("t_terminal_used").assertion_strength == "deterministic_oracle"
    assert [c.id for c in bank.list()] == ["t_terminal_used"]


def test_run_returns_1_for_pass_and_0_for_fail(tmp_path):
    store, e = ep(tmp_path)
    bank = TestBank(tmp_path / "bank")
    bank.add(case("t_pass", script=PASS_SCRIPT))
    bank.add(case("t_fail", script=FAIL_SCRIPT))
    assert bank.run("t_pass", store.dir("s", "e1")) == 1
    assert bank.run("t_fail", store.dir("s", "e1")) == 0


def test_run_reads_the_given_episode(tmp_path):
    store, _ = ep(tmp_path, "with_terminal")
    store.save(Episode(id="no_terminal", skill="s", skill_version="v", task_id="t", tool_calls=[]))
    bank = TestBank(tmp_path / "bank")
    bank.add(case())
    assert bank.run("t_terminal_used", store.dir("s", "with_terminal")) == 1
    assert bank.run("t_terminal_used", store.dir("s", "no_terminal")) == 0


def test_validate_rejects_syntax_error_and_missing_result_line(tmp_path):
    store, e = ep(tmp_path)
    bank = TestBank(tmp_path / "bank")
    ok, msg = bank.validate(case("t_bad", script=SYNTAX_ERROR_SCRIPT), store.dir("s", "e1"))
    assert not ok and "syntax" in msg.lower()
    ok, msg = bank.validate(case("t_silent", script=NO_OUTPUT_SCRIPT), store.dir("s", "e1"))
    assert not ok and "SELF_VERIFIER_RESULT" in msg
    ok, msg = bank.validate(case("t_ok", script=PASS_SCRIPT), store.dir("s", "e1"))
    assert ok


def test_validate_rejects_unknown_strength(tmp_path):
    store, e = ep(tmp_path)
    bank = TestBank(tmp_path / "bank")
    ok, msg = bank.validate(case("t_x", strength="vibes"), store.dir("s", "e1"))
    assert not ok and "assertion_strength" in msg
    assert "hard_contract" in STRENGTHS


def test_run_times_out_as_fail(tmp_path):
    store, e = ep(tmp_path)
    bank = TestBank(tmp_path / "bank", timeout=1)
    bank.add(case("t_hang", script=HANG_SCRIPT))
    assert bank.run("t_hang", store.dir("s", "e1")) == 0


def test_remove_deletes_test_dir(tmp_path):
    bank = TestBank(tmp_path / "bank")
    bank.add(case())
    bank.remove("t_terminal_used")
    assert bank.list() == []
    assert not (tmp_path / "bank" / "t_terminal_used").exists()


def test_bank_with_relative_root_still_runs_scripts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store, e = ep(tmp_path)
    bank = TestBank("relbank")
    ok, msg = bank.validate(case("t_rel", script=PASS_SCRIPT), store.dir("s", "e1"))
    assert ok, msg
    bank.add(case("t_rel", script=PASS_SCRIPT))
    assert bank.run("t_rel", store.dir("s", "e1")) == 1


def test_self_verifier_scripts_run_with_a_throwaway_home(tmp_path):
    from skillhex.bank import TestBank, TestCase
    bank = TestBank(tmp_path / "bank")
    ep = tmp_path / "ep"
    ep.mkdir()
    (ep / "episode.json").write_text("{}")
    case = TestCase(id="t_home", hypothesis_ids=["H1"], assertion_strength="diagnostic", summary="d",
                    script="import os\nprint('HOME=' + os.environ['HOME'])\nprint('SELF_VERIFIER_RESULT=PASS')\n")
    bank.add(case)
    out = bank.run_capture(case.id, ep)
    home_line = [l for l in out.splitlines() if l.startswith("HOME=")][0]
    assert str(Path.home()) not in home_line and "SELF_VERIFIER_RESULT=PASS" in out
