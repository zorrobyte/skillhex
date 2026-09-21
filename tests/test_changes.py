import time

from skillhex.changes import ChangeLog


def test_changes_append_and_recent_newest_first(tmp_path):
    log = ChangeLog(tmp_path / "changes.jsonl")
    log.append(skill="notes", kind="applied", decision="apply (official pass)", run="runs/a", score=1.0, official=1)
    log.append(skill="weather", kind="staged", decision="apply", run="runs/b", score=0.9, official=0, pending_id="ab12")
    log.append(skill="notes", kind="undone", decision="user undo", run=None)
    recent = log.recent(limit=2)
    assert [r["skill"] for r in recent] == ["notes", "weather"] and recent[0]["kind"] == "undone"
    assert len(log.recent(limit=10)) == 3
    assert ChangeLog(tmp_path / "missing.jsonl").recent() == []


def test_recent_honours_a_time_window(tmp_path):
    log = ChangeLog(tmp_path / "c.jsonl")
    log.append(skill="old", kind="applied", decision="d", run=None, at=time.time() - 30 * 86400)
    log.append(skill="new", kind="applied", decision="d", run=None)
    assert [r["skill"] for r in log.recent(days=7)] == ["new"]


def test_render_for_prompt_is_empty_without_changes_and_bounded_with_them(tmp_path):
    log = ChangeLog(tmp_path / "c.jsonl")
    assert log.render_for_prompt() == ""
    for i in range(50):
        log.append(skill=f"skill-{i}", kind="applied", decision="apply (evidence score 0.90 ≥ 0.8, no checker available)", run="r")
    text = log.render_for_prompt(days=7, limit=5, max_chars=1500)
    assert "skill-49" in text and "skill-0" not in text and len(text) <= 1500
    assert "undo" in text.lower()
