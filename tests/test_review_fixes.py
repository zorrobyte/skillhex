"""Fixes for the adversarial review findings (2026-09-21). Plugin-level."""
import json
import os
import sys
from pathlib import Path

from plugin_harness import load_plugin, skill_turn, ROOT


def _failed(mod, skill="notes"):
    from skillhex.models import Episode
    mod._store.save(Episode(id="e1", skill=skill, skill_version="v", task_id="t", outcome="fail", outcome_source="user",
                            messages=[{"role": "user", "content": "count notes"}]))


def test_worker_is_launched_with_the_plugin_root_on_pythonpath(tmp_path, monkeypatch):
    """Hermes installs a plugin's declared deps, never the plugin package itself; the background worker
    must find skillhex/ beside __init__.py without a pip install."""
    mod, ctx, hh = load_plugin(tmp_path, config={"auto_evolve": True})
    _failed(mod)
    seen = {}

    class P:
        pid = 4242
        def __init__(self, cmd, **kw):
            seen["cmd"], seen["env"] = cmd, kw.get("env")
    monkeypatch.setattr(mod.subprocess, "Popen", P)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    mod._maybe_schedule_evolution(["notes"])
    assert seen["env"] is not None and str(ROOT) in seen["env"]["PYTHONPATH"].split(os.pathsep)
    assert seen["env"]["HERMES_HOME"] == str(hh)
    assert (mod._home / "evolve.lock").read_text().strip() == "4242", "lock holds the worker pid, not the parent's"


def test_stale_lock_with_a_dead_pid_does_not_block_scheduling(tmp_path, monkeypatch):
    mod, ctx, hh = load_plugin(tmp_path, config={"auto_evolve": True})
    _failed(mod)
    (mod._home / "evolve.lock").write_text("999999999")   # no such process
    launched = []
    monkeypatch.setattr(mod.subprocess, "Popen", lambda cmd, **kw: launched.append(cmd) or type("P", (), {"pid": 1})())
    mod._maybe_schedule_evolution(["notes"])
    assert launched, "a dead worker's lock must not block for an hour"
    (mod._home / "evolve.lock").write_text(str(os.getpid()))   # a live pid (us)
    mod._maybe_schedule_evolution(["notes"])
    assert len(launched) == 1, "a live worker's lock still blocks"


def test_autoloaded_skill_without_session_id_is_captured_via_task_id(tmp_path, monkeypatch):
    """skills.auto_load / slash-loaded skills fire on_skill_lifecycle with session_id='' and only a task_id
    (agent/skill_commands.py bump_use). The turn end carries the same task_id; join on it."""
    monkeypatch.chdir(tmp_path)
    mod, ctx, hh = load_plugin(tmp_path)
    ctx.hooks["on_skill_lifecycle"](action="loaded", skill_name="notes", session_id="", task_id="T1")
    ctx.hooks["post_tool_call"](tool_name="terminal", args={"command": "ls"}, result="a", session_id="s1", tool_call_id="c1")
    ctx.hooks["post_llm_call"](session_id="s1", task_id="T1", turn_id="t1",
                               conversation_history=[{"role": "user", "content": "count"}, {"role": "assistant", "content": "5"}],
                               assistant_response="5", user_message="count", model="m")
    eps = mod._store.list("notes")
    assert len(eps) == 1 and len(eps[0].tool_calls) == 1


def test_only_skills_loaded_this_turn_are_blamed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mod, ctx, hh = load_plugin(tmp_path, config={"classify_followups": False})
    skill_turn(mod, ctx, "s1", prompt="count notes", skill="notes")
    ctx.hooks["pre_llm_call"](session_id="s1", user_message="ok thanks")
    skill_turn(mod, ctx, "s1", prompt="weather in Oslo", skill="weather")
    assert [e.skill for e in mod._store.list("notes")] == ["notes"]
    assert [e.skill for e in mod._store.list("weather")] == ["weather"]
    # a turn with no new skill load is attributed to the most recently loaded skill only, not every skill ever seen
    ctx.hooks["post_tool_call"](tool_name="terminal", args={}, result="x", session_id="s1", tool_call_id="c9")
    ctx.hooks["post_llm_call"](session_id="s1", turn_id="t3", conversation_history=[{"role": "user", "content": "and tomorrow?"},
                               {"role": "assistant", "content": "rain"}], assistant_response="rain", user_message="and tomorrow?", model="m")
    assert len(mod._store.list("weather")) == 2 and len(mod._store.list("notes")) == 1


def test_episode_prompt_is_this_turns_user_message_not_the_first_in_history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mod, ctx, hh = load_plugin(tmp_path)
    ctx.hooks["on_skill_lifecycle"](action="loaded", skill_name="notes", session_id="s1")
    ctx.hooks["post_llm_call"](session_id="s1", turn_id="t2",
                               conversation_history=[{"role": "user", "content": "First task"}, {"role": "assistant", "content": "done"},
                                                     {"role": "user", "content": "Second task: count notes"}, {"role": "assistant", "content": "5"}],
                               assistant_response="5", user_message="Second task: count notes", model="m")
    ep = mod._store.list("notes")[0]
    assert ep.user_prompt == "Second task: count notes"


def test_tool_mark_targets_the_invoking_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mod, ctx, hh = load_plugin(tmp_path, config={"classify_followups": False})
    skill_turn(mod, ctx, "alice", prompt="count notes", skill="notes")
    skill_turn(mod, ctx, "bob", prompt="weather", skill="weather")     # most recent overall
    out = ctx.tools["skillhex"]({"action": "mark", "verdict": "fail", "note": "wrong"}, session_id="alice")
    assert "notes" in out
    assert [e.outcome for e in mod._store.list("notes")] == ["fail"]
    assert [e.outcome for e in mod._store.list("weather")] == [None]


def test_snapshot_is_retaken_for_each_turn(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.txt").write_text("v1")
    monkeypatch.chdir(ws)
    mod, ctx, hh = load_plugin(tmp_path)
    skill_turn(mod, ctx, "s1")
    snap1 = mod._store.list("notes")[0].workspace_snapshot
    (ws / "a.txt").write_text("v2")
    skill_turn(mod, ctx, "s1")
    snap2 = sorted(mod._store.list("notes"), key=lambda e: e.created_at)[-1].workspace_snapshot
    assert snap1 and snap2 and (Path(snap2) / "a.txt").read_text() == "v2"
