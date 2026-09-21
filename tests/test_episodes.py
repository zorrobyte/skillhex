import json

from skillhex.models import Episode, ToolCall
from skillhex.episodes import EpisodeStore


def make_episode(**over):
    base = dict(
        id="ep1",
        skill="weather",
        skill_version="abc123",
        task_id="t1",
        messages=[{"role": "user", "content": "forecast please"}],
        tool_calls=[
            ToolCall(id="c1", name="terminal", args={"command": "curl wttr.in"}, result="3 days", status="ok")
        ],
    )
    base.update(over)
    return Episode(**base)


def test_save_then_load_roundtrips_episode(tmp_path):
    store = EpisodeStore(tmp_path)
    ep = make_episode()
    path = store.save(ep)
    assert path.is_dir()
    loaded = store.load("weather", "ep1")
    assert loaded == ep


def test_episode_dir_holds_readable_json_for_test_scripts(tmp_path):
    store = EpisodeStore(tmp_path)
    store.save(make_episode())
    data = json.loads((tmp_path / "weather" / "ep1" / "episode.json").read_text())
    assert data["tool_calls"][0]["name"] == "terminal"
    assert data["outcome"] is None


def test_set_outcome_persists_and_lists_failed(tmp_path):
    store = EpisodeStore(tmp_path)
    store.save(make_episode(id="ep1"))
    store.save(make_episode(id="ep2"))
    store.set_outcome("weather", "ep1", "fail", source="user", note="wrong city")
    store.set_outcome("weather", "ep2", "pass", source="checker")
    failed = store.list("weather", outcome="fail")
    assert [e.id for e in failed] == ["ep1"]
    assert failed[0].outcome_note == "wrong city"
    assert store.load("weather", "ep2").outcome_source == "checker"


def test_list_orders_by_created_at(tmp_path):
    store = EpisodeStore(tmp_path)
    store.save(make_episode(id="b", created_at=2.0))
    store.save(make_episode(id="a", created_at=1.0))
    assert [e.id for e in store.list("weather")] == ["a", "b"]


def test_skills_lists_skill_names(tmp_path):
    store = EpisodeStore(tmp_path)
    store.save(make_episode(skill="weather"))
    store.save(make_episode(skill="notes", id="x"))
    assert store.skills() == ["notes", "weather"]
