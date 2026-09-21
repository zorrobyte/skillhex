import math

import pytest

from skillhex.tree import PatchTree, Node


def cands(n):
    return [{"content": f"skill v{i}", "rank": i, "summary": f"patch {i}", "hypothesis": f"H{i}"} for i in range(1, n + 1)]


def test_expand_assigns_softmax_priors_over_ordinal_rank(tmp_path):
    t = PatchTree(tmp_path / "tree.json")
    root = t.add_root("original")
    ids = t.expand(root.id, cands(3), beta=1.0)
    priors = [t.get(i).prior for i in ids]
    assert priors[0] > priors[1] > priors[2]
    assert math.isclose(sum(priors), 1.0)
    expected0 = 1 / (1 + math.exp(-1) + math.exp(-2))
    assert math.isclose(priors[0], expected0)


def test_select_returns_root_when_root_is_unevaluated(tmp_path):
    t = PatchTree(tmp_path / "tree.json")
    root = t.add_root("original")
    assert t.select() == root.id


def test_select_returns_root_when_evaluated_but_unexpanded(tmp_path):
    t = PatchTree(tmp_path / "tree.json")
    root = t.add_root("original")
    t.evaluate(root.id, score=0.1, reward=0, episode_id="e0")
    assert t.select() == root.id


def test_select_prefers_highest_prior_among_fresh_children(tmp_path):
    t = PatchTree(tmp_path / "tree.json")
    root = t.add_root("original")
    t.evaluate(root.id, score=0.1, reward=0, episode_id="e0")
    ids = t.expand(root.id, cands(3), beta=1.0)
    assert t.select() == ids[0]


def test_max_backup_never_lowers_parent_value(tmp_path):
    t = PatchTree(tmp_path / "tree.json")
    root = t.add_root("original")
    t.evaluate(root.id, score=0.3, reward=0, episode_id="e0")
    a, b = t.expand(root.id, cands(2), beta=1.0)
    t.evaluate(a, score=0.8, reward=0, episode_id="e1")
    assert t.get(root.id).q == pytest.approx(0.8)
    t.evaluate(b, score=0.1, reward=0, episode_id="e2")
    assert t.get(root.id).q == pytest.approx(0.8)


def test_search_jumps_back_to_preserved_sibling_when_branch_stalls(tmp_path):
    """Paper Fig. 5: an unproductive branch is abandoned for the lower-ranked sibling at the root."""
    t = PatchTree(tmp_path / "tree.json")
    root = t.add_root("original")
    t.evaluate(root.id, score=0.2, reward=0, episode_id="e0")
    a, b = t.expand(root.id, cands(2), beta=1.0)
    picked = []
    for step in range(6):
        v = t.select()
        picked.append(v)
        if v == b:
            break
        if not t.get(v).evaluated:
            t.evaluate(v, score=0.2, reward=0, episode_id=f"e{step}")
        else:
            t.expand(v, cands(1), beta=1.0)
    assert b in picked, picked


def test_exhausted_nodes_are_skipped_and_all_exhausted_returns_none(tmp_path):
    t = PatchTree(tmp_path / "tree.json")
    root = t.add_root("original")
    t.evaluate(root.id, score=0.2, reward=0, episode_id="e0")
    a, b = t.expand(root.id, cands(2), beta=1.0)
    t.evaluate(a, score=0.1, reward=0, episode_id="e1")
    t.mark_exhausted(a)
    assert t.select() == b
    t.evaluate(b, score=0.1, reward=0, episode_id="e2")
    t.mark_exhausted(b)
    t.mark_exhausted(root.id)
    assert t.select() is None


def test_best_prefers_reward_then_score(tmp_path):
    t = PatchTree(tmp_path / "tree.json")
    root = t.add_root("original")
    t.evaluate(root.id, score=0.9, reward=0, episode_id="e0")
    a, b = t.expand(root.id, cands(2), beta=1.0)
    t.evaluate(a, score=0.4, reward=1, episode_id="e1")
    assert t.best().id == a
    assert t.get(b).evaluated is False


def test_persists_and_reloads(tmp_path):
    p = tmp_path / "tree.json"
    t = PatchTree(p)
    root = t.add_root("original")
    ids = t.expand(root.id, cands(2), beta=0.5)
    t.evaluate(ids[0], score=0.5, reward=0, episode_id="e1")
    t2 = PatchTree(p)
    assert t2.get(ids[0]).score == 0.5
    assert t2.get(root.id).children == ids
    assert t2.get(ids[1]).content == "skill v2"


def test_visit_counts_increment_on_select(tmp_path):
    t = PatchTree(tmp_path / "tree.json")
    root = t.add_root("original")
    t.evaluate(root.id, score=0.2, reward=0, episode_id="e0")
    a, b = t.expand(root.id, cands(2), beta=1.0)
    first = t.select()
    assert t.get(first).visits == 1
