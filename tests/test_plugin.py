import os

from plugin_harness import load_plugin, skill_turn, FakeLLM


def test_followup_without_signal_is_judged_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    llm = FakeLLM()  # always "unknown"
    mod, ctx, hh = load_plugin(tmp_path, llm=llm)
    skill_turn(mod, ctx, "s1")
    ctx.hooks["pre_llm_call"](session_id="s1", user_message="what about the archive?")
    ctx.hooks["pre_llm_call"](session_id="s1", user_message="and yesterday?")
    ctx.hooks["pre_llm_call"](session_id="s1", user_message="ok another thing")
    assert llm.calls == 1, "the next message is the signal; later messages must not be re-judged"
    eps = mod._store.list("notes")
    assert len(eps) == 1 and eps[0].outcome is None


def test_slash_fail_targets_the_most_recent_turn_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mod, ctx, hh = load_plugin(tmp_path, config={"classify_followups": False})
    skill_turn(mod, ctx, "older", prompt="count notes", skill="notes")
    skill_turn(mod, ctx, "newer", prompt="weather in Oslo", skill="weather")
    out = ctx.commands["skillhex"]("fail it printed the wrong city")
    assert "weather" in out
    assert [e.outcome for e in mod._store.list("weather")] == ["fail"]
    assert [e.outcome for e in mod._store.list("notes")] == [None]


def test_slash_ok_overrides_an_automatic_unknown_verdict(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mod, ctx, hh = load_plugin(tmp_path, config={"classify_followups": False})
    skill_turn(mod, ctx, "s1")
    ctx.hooks["pre_llm_call"](session_id="s1", user_message="hmm")   # heuristic: unknown, turn is now judged
    ctx.commands["skillhex"]("ok")
    assert [e.outcome for e in mod._store.list("notes")] == ["pass"]
