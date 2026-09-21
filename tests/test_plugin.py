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


def test_plugin_registers_reviewer_and_executor_as_hermes_auxiliary_tasks(tmp_path):
    mod, ctx, hh = load_plugin(tmp_path)
    assert set(ctx.aux_tasks) == {"skillhex_reflector", "skillhex_executor"}
    assert "review" in ctx.aux_tasks["skillhex_reflector"]["description"].lower()


def _failed_episode(mod, skill="notes"):
    from skillhex.models import Episode
    ep = Episode(id="e1", skill=skill, skill_version="v", task_id="t", outcome="fail", outcome_source="user",
                 messages=[{"role": "user", "content": "count notes"}])
    mod._store.save(ep)


def test_budget_setting_is_passed_to_the_background_run(tmp_path, monkeypatch):
    mod, ctx, hh = load_plugin(tmp_path, config={"auto_evolve": True, "budget": 3})
    _failed_episode(mod)
    seen = {}

    class P:
        def __init__(self, cmd, **kw):
            seen["cmd"] = cmd
    monkeypatch.setattr(mod.subprocess, "Popen", P)
    mod._maybe_schedule_evolution(["notes"])
    assert "--budget" in seen["cmd"] and seen["cmd"][seen["cmd"].index("--budget") + 1] == "3"


def _skill_with_backup(hh, name="notes"):
    d = hh / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: notes\n---\npatched")
    (d / "SKILL.md.skillhex-prev").write_text("---\nname: notes\n---\noriginal")
    return d


def test_auto_rollback_can_be_disabled(tmp_path):
    mod, ctx, hh = load_plugin(tmp_path, config={"auto_rollback": False})
    d = _skill_with_backup(hh)
    mod._on_fail({"episodes": [["notes", "e1"]], "regressed": ["notes"]}, "wrong")
    assert (d / "SKILL.md").read_text().endswith("patched")
    mod2, ctx2, hh2 = load_plugin(tmp_path / "b")
    d2 = _skill_with_backup(hh2)
    mod2._on_fail({"episodes": [["notes", "e1"]], "regressed": ["notes"]}, "wrong")
    assert (d2 / "SKILL.md").read_text().endswith("original")


def test_bank_similarity_and_snapshot_limit_settings(tmp_path, monkeypatch):
    mod, ctx, hh = load_plugin(tmp_path, config={"bank_min_similarity": 0.9, "snapshot_max_bytes": 1})
    assert mod._bank_for("notes").min_similarity == 0.9
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "big.txt").write_text("more than one byte")
    monkeypatch.chdir(ws)
    assert mod._snapshot_for("sid") is None
