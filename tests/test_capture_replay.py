from skillhex.capture import TurnRecorder
from skillhex.replay import Cassette, ReplayPolicy
from skillhex.models import Episode, ToolCall


def test_recorder_builds_one_episode_per_loaded_skill_and_clears():
    r = TurnRecorder()
    r.skill_loaded("s1", "weather")
    r.tool_call("s1", "c1", "terminal", {"command": "curl x"}, "out", "ok", 12)
    r.tool_call("s1", "c2", "read_file", {"path": "USER.md"}, "Kokomo", "ok", 3)
    eps = r.finish_turn("s1", turn_id="t9", messages=[{"role": "user", "content": "hi"}], model="m", cwd="/w")
    assert len(eps) == 1
    ep = eps[0]
    assert ep.skill == "weather" and ep.turn_id == "t9" and ep.model == "m" and ep.cwd == "/w"
    assert [tc.name for tc in ep.tool_calls] == ["terminal", "read_file"]
    assert ep.tool_calls[0].duration_ms == 12
    assert r.finish_turn("s1", turn_id="t10", messages=[], model="m", cwd=None) == []


def test_recorder_ignores_turns_with_no_skill_unless_forced():
    r = TurnRecorder()
    r.tool_call("s1", "c1", "terminal", {}, "x", "ok", 1)
    assert r.finish_turn("s1", turn_id="t1", messages=[], model="m", cwd=None) == []
    r.tool_call("s1", "c1", "terminal", {}, "x", "ok", 1)
    eps = r.finish_turn("s1", turn_id="t2", messages=[], model="m", cwd=None, force_skill="weather")
    assert eps[0].skill == "weather"


def test_recorder_keeps_sessions_separate():
    r = TurnRecorder()
    r.skill_loaded("a", "weather")
    r.skill_loaded("b", "notes")
    assert r.finish_turn("a", turn_id="t", messages=[], model="m", cwd=None)[0].skill == "weather"
    assert r.finish_turn("b", turn_id="t", messages=[], model="m", cwd=None)[0].skill == "notes"


def test_recorder_truncates_huge_results():
    r = TurnRecorder(max_result_chars=100)
    r.skill_loaded("s", "x")
    r.tool_call("s", "c", "terminal", {}, "y" * 1000, "ok", 1)
    ep = r.finish_turn("s", turn_id="t", messages=[], model="m", cwd=None)[0]
    assert len(ep.tool_calls[0].result) < 200 and "omitted" in ep.tool_calls[0].result


def cassette_episode():
    return Episode(id="e", skill="s", skill_version="v", task_id="t", tool_calls=[
        ToolCall(id="1", name="terminal", args={"command": "curl a"}, result="A1"),
        ToolCall(id="2", name="terminal", args={"command": "curl a"}, result="A2"),
        ToolCall(id="3", name="web_fetch", args={"url": "u"}, result="W"),
    ])


def test_cassette_matches_in_order_and_consumes():
    c = Cassette(cassette_episode())
    assert c.lookup("terminal", {"command": "curl a"}) == "A1"
    assert c.lookup("terminal", {"command": "curl a"}) == "A2"
    assert c.lookup("terminal", {"command": "curl a"}) is None
    assert c.lookup("web_fetch", {"url": "u"}) == "W"
    assert c.lookup("web_fetch", {"url": "other"}) is None


def test_policy_decides_live_replay_or_block():
    c = Cassette(cassette_episode())
    p = ReplayPolicy(c, mode="strict")
    assert p.decide("terminal", {"command": "curl a"}) == ("replay", "A1")
    assert p.decide("read_file", {"path": "x"}) == ("live", None)          # read-only tools run live
    kind, msg = p.decide("terminal", {"command": "rm -rf /"})
    assert kind == "block" and "not in cassette" in msg
    permissive = ReplayPolicy(Cassette(cassette_episode()), mode="permissive")
    assert permissive.decide("terminal", {"command": "rm -rf /"}) == ("live", None)


def test_episode_ids_are_filesystem_safe():
    r = TurnRecorder()
    r.skill_loaded("s", "x")
    ep = r.finish_turn("s", turn_id="2026:abc/def", messages=[], model="m", cwd=None)[0]
    assert ":" not in ep.id and "/" not in ep.id
