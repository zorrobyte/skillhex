from skillhex.episodes import EpisodeStore
from skillhex.models import Episode


def _ep(i, skill="notes", outcome=None):
    return Episode(id=f"ep{i}", skill=skill, skill_version="v", task_id="t", outcome=outcome,
                   messages=[{"role": "user", "content": "x"}])


def test_pending_skills_are_those_with_unevolved_failures(tmp_path):
    s = EpisodeStore(tmp_path / "eps")
    s.save(_ep(0, "a", "fail"))
    s.save(_ep(1, "b", "pass"))
    s.save(_ep(2, "c", None))
    assert s.pending_skills() == ["a"]
    s.mark_evolved("a", ["ep0"], run="runs/a-1")
    assert s.pending_skills() == []
    assert s.load("a", "ep0").evolved_run == "runs/a-1"
    assert s.list("a", outcome="fail") and s.list("a", outcome="fail", unevolved=True) == []
