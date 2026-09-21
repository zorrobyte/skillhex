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


# ---------------------------------------------------------------- UI: prompt section, slash, tool, skill
def _record_change(mod, skill="notes", kind="applied"):
    from skillhex.changes import ChangeLog
    ChangeLog(mod._home / "changes.jsonl").append(skill=skill, kind=kind, decision="apply (official pass)", run="runs/x",
                                                   score=1.0, official=1)


def test_prompt_section_is_empty_until_something_changed_then_names_the_skill(tmp_path):
    mod, ctx, hh = load_plugin(tmp_path)
    section = ctx.sections["skillhex.changes"]
    assert section({"session_id": "s"}) == ""
    _record_change(mod)
    text = section({"session_id": "s"})
    assert "notes" in text and "undo" in text.lower()


def test_slash_status_show_runs_and_undo(tmp_path):
    mod, ctx, hh = load_plugin(tmp_path)
    d = _skill_with_backup(hh)
    _record_change(mod)
    cmd = ctx.commands["skillhex"]
    status = cmd("")
    assert "notes" in status and "applied" in status
    shown = cmd("show notes")
    assert "-original" in shown and "+patched" in shown
    assert "no runs" in cmd("runs").lower()
    out = cmd("undo notes")
    assert "restored" in out.lower() and (d / "SKILL.md").read_text().endswith("original")
    assert "nothing to undo" in cmd("undo notes").lower()
    from skillhex.changes import ChangeLog
    assert ChangeLog(mod._home / "changes.jsonl").recent()[0]["kind"] == "undone"


def test_agent_tool_can_report_show_undo_and_mark(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mod, ctx, hh = load_plugin(tmp_path, config={"classify_followups": False})
    tool = ctx.tools["skillhex"]
    d = _skill_with_backup(hh)
    _record_change(mod)
    assert "notes" in tool({"action": "status"})
    assert "+patched" in tool({"action": "show", "skill": "notes"})
    assert "restored" in tool({"action": "undo", "skill": "notes"}).lower()
    skill_turn(mod, ctx, "s1")
    assert "fail" in tool({"action": "mark", "verdict": "fail", "note": "wrong count"})
    assert [e.outcome for e in mod._store.list("notes")] == ["fail"]
    assert "unknown action" in tool({"action": "explode"}).lower()


def test_bundled_guide_skill_is_registered(tmp_path):
    mod, ctx, hh = load_plugin(tmp_path)
    assert "guide" in ctx.skills and ctx.skills["guide"].read_text().startswith("---\nname: skillhex-guide")
