import pytest

from skillhex.hypotheses import HypothesisStore, Hypothesis


def test_add_assigns_sequential_ids_and_active_state(tmp_path):
    hs = HypothesisStore(tmp_path / "h.json")
    h1 = hs.add("wttr.in returns only 3 days", target_behavior="a 14 day call returns 14 rows")
    h2 = hs.add("skill never reads USER.md for location")
    assert (h1.id, h2.id) == ("H1", "H2")
    assert h1.state == "active"
    assert [h.id for h in hs.active()] == ["H1", "H2"]


def test_refine_rewrites_text_keeps_id_and_tests(tmp_path):
    hs = HypothesisStore(tmp_path / "h.json")
    h = hs.add("vague")
    hs.link_test("H1", "t_a")
    hs.refine("H1", "precise", target_behavior="obs")
    got = hs.get("H1")
    assert got.text == "precise" and got.target_behavior == "obs"
    assert got.tests == ["t_a"]
    assert got.state == "active"


def test_refute_marks_and_returns_orphaned_tests(tmp_path):
    hs = HypothesisStore(tmp_path / "h.json")
    hs.add("a")
    hs.add("b")
    hs.link_test("H1", "t_shared")
    hs.link_test("H2", "t_shared")
    hs.link_test("H1", "t_only")
    orphaned = hs.refute("H1", reason="open-meteo returns 16 days")
    assert orphaned == ["t_only"]
    assert hs.get("H1").state == "refuted"
    assert [h.id for h in hs.active()] == ["H2"]


def test_persists_across_instances(tmp_path):
    p = tmp_path / "h.json"
    HypothesisStore(p).add("x")
    assert HypothesisStore(p).get("H1").text == "x"


def test_apply_ops_from_reflection(tmp_path):
    hs = HypothesisStore(tmp_path / "h.json")
    hs.add("old")
    result = hs.apply_ops([
        {"op": "add", "text": "new one", "target_behavior": "tb", "reason": "r"},
        {"op": "refine", "hypothesis_id": "H1", "text": "old refined"},
        {"op": "refute", "hypothesis_id": "H2", "reason": "nope"},
    ])
    assert hs.get("H1").text == "old refined"
    assert hs.get("H2").state == "refuted"
    assert result["added"] == ["H2"] and result["refuted"] == ["H2"]


def test_unknown_id_raises(tmp_path):
    hs = HypothesisStore(tmp_path / "h.json")
    with pytest.raises(KeyError):
        hs.refine("H9", "x")
