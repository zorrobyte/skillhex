from skillhex.session_state import SessionStore


def test_loaded_skills_persist_across_instances(tmp_path):
    s = SessionStore(tmp_path / "sessions")
    s.add_skill("sid1", "notes-cli")
    s.add_skill("sid1", "weather")
    s2 = SessionStore(tmp_path / "sessions")
    assert s2.skills("sid1") == ["notes-cli", "weather"]
    assert s2.skills("other") == []


def test_last_turn_roundtrip_and_resolve(tmp_path):
    s = SessionStore(tmp_path / "sessions")
    s.set_last("sid1", episodes=[("notes-cli", "ep1")], prompt="count", answer="5", regressed=[])
    last = SessionStore(tmp_path / "sessions").last("sid1")
    assert last["episodes"] == [["notes-cli", "ep1"]] and last["prompt"] == "count" and last["resolved"] is False
    s.resolve("sid1")
    assert SessionStore(tmp_path / "sessions").last("sid1")["resolved"] is True


def test_session_ids_are_sanitised_and_missing_is_none(tmp_path):
    s = SessionStore(tmp_path / "sessions")
    s.add_skill("a/b:c", "x")
    assert s.skills("a/b:c") == ["x"]
    assert s.last("nope") is None


def test_most_recent_turn_across_sessions_even_if_resolved(tmp_path):
    s = SessionStore(tmp_path / "sessions")
    s.set_last("a", episodes=[("notes", "e1")], prompt="p", answer="a", regressed=[])
    s.set_last("b", episodes=[("weather", "e2")], prompt="p", answer="a", regressed=[])
    s.resolve("b")
    sid, last = s.most_recent()
    assert sid == "b" and last["episodes"] == [["weather", "e2"]]
    assert SessionStore(tmp_path / "none").most_recent() == (None, None)
