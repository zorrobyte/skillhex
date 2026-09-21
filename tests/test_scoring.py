from skillhex.bank import TestCase
from skillhex.scoring import score_row


def tc(tid, strength):
    return TestCase(id=tid, hypothesis_ids=["H1"], assertion_strength=strength, summary=tid)


CASES = [tc("hard", "hard_contract"), tc("pre", "environment_preflight"),
         tc("oracle", "deterministic_oracle"), tc("proxy", "proxy_quality"), tc("diag", "diagnostic")]


def test_any_hard_failure_scores_zero():
    row = {"hard": 0, "pre": 1, "oracle": 1, "proxy": 1, "diag": 1}
    assert score_row(row, CASES) == 0.0


def test_weighted_mean_of_semantic_tests_ignores_diagnostic():
    row = {"hard": 1, "pre": 1, "oracle": 1, "proxy": 0, "diag": 0}
    # oracle weight 1.0 pass, proxy weight 0.5 fail -> 1.0 / 1.5
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
