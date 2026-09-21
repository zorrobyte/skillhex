from skillhex.bank import TestCase
from skillhex.evidence import EvidenceMatrix
from skillhex.scoring import score_row, score_node, column_stats


def tc(tid, strength):
    return TestCase(id=tid, hypothesis_ids=["H1"], assertion_strength=strength, summary=tid)


CASES = [tc("hard", "hard_contract"), tc("pre", "environment_preflight"),
         tc("oracle", "deterministic_oracle"), tc("proxy", "proxy_quality"), tc("diag", "diagnostic")]


def test_any_hard_failure_scores_zero():
    row = {"hard": 0, "pre": 1, "oracle": 1, "proxy": 1, "diag": 1}
    assert score_row(row, CASES) == 0.0


def test_weighted_mean_of_semantic_tests_ignores_diagnostic():
    row = {"hard": 1, "pre": 1, "oracle": 1, "proxy": 0, "diag": 0}
    assert abs(score_row(row, CASES) - (1.0 / 1.5)) < 1e-9


def test_no_semantic_tests_is_neutral_half():
    row = {"hard": 1, "pre": 1}
    assert score_row(row, CASES[:2]) == 0.5


def test_unrun_tests_are_ignored():
    row = {"hard": 1, "oracle": 1}
    assert score_row(row, CASES) == 1.0


def test_reward_one_dominates():
    row = {"hard": 0}
    assert score_row(row, CASES, reward=1) == 1.0


# ---- matrix-aware scoring --------------------------------------------------------

def matrix(tmp_path, cells):
    m = EvidenceMatrix(tmp_path / "e.db")
    for node, test, r in cells:
        m.record(node, test, r)
    return m


def test_unsatisfiable_hard_test_does_not_gate(tmp_path):
    """A 'hard' test no version passes is suspect: it must not zero every candidate."""
    m = matrix(tmp_path, [("v0", "hard", 0), ("v1", "hard", 0), ("v0", "oracle", 0), ("v1", "oracle", 1)])
    assert score_node("v1", m, CASES) > score_node("v0", m, CASES)
    assert score_node("v1", m, CASES) == 1.0


def test_satisfiable_hard_test_still_gates(tmp_path):
    m = matrix(tmp_path, [("v0", "hard", 1), ("v1", "hard", 0), ("v0", "oracle", 0), ("v1", "oracle", 1)])
    assert score_node("v1", m, CASES) == 0.0


def test_constant_columns_carry_no_ranking_weight(tmp_path):
    m = matrix(tmp_path, [("v0", "oracle", 1), ("v1", "oracle", 1), ("v0", "proxy", 0), ("v1", "proxy", 1)])
    assert score_node("v1", m, CASES) == 1.0
    assert score_node("v0", m, CASES) == 0.0


def test_single_row_falls_back_to_row_scoring(tmp_path):
    m = matrix(tmp_path, [("v0", "hard", 0), ("v0", "oracle", 1)])
    assert score_node("v0", m, CASES) == 0.0


def test_column_stats_reports_satisfiable_and_discriminative(tmp_path):
    m = matrix(tmp_path, [("v0", "a", 0), ("v1", "a", 0), ("v0", "b", 0), ("v1", "b", 1)])
    st = column_stats(m)
    assert st["a"] == {"passes": 0, "rows": 2, "satisfiable": False, "discriminative": False}
    assert st["b"] == {"passes": 1, "rows": 2, "satisfiable": True, "discriminative": True}
