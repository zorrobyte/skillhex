from pathlib import Path

from skillhex.models import Episode, ToolCall
from skillhex.workspace import snapshot_workspace, written_paths, restore_pristine


def test_snapshot_copies_small_workspace_and_skips_big_or_missing(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.txt").write_text("a")
    (ws / ".git").mkdir()
    (ws / ".git" / "HEAD").write_text("x")
    dst = snapshot_workspace(ws, tmp_path / "snaps" / "s1", max_bytes=10_000)
    assert dst is not None and (dst / "a.txt").read_text() == "a" and not (dst / ".git").exists()
    assert snapshot_workspace(tmp_path / "missing", tmp_path / "snaps" / "s2") is None
    (ws / "big.bin").write_bytes(b"0" * 20_000)
    assert snapshot_workspace(ws, tmp_path / "snaps" / "s3", max_bytes=10_000) is None


def test_written_paths_from_write_and_patch_calls():
    ep = Episode(id="e", skill="s", skill_version="v", task_id="t", cwd="/w", tool_calls=[
        ToolCall(id="1", name="write_file", args={"path": "answer.txt", "content": "5"}, result="ok"),
        ToolCall(id="2", name="patch", args={"path": "/w/notes.json"}, result="ok"),
        ToolCall(id="3", name="terminal", args={"command": "ls"}, result="ok"),
    ])
    assert written_paths(ep) == ["answer.txt", "/w/notes.json"]


def test_restore_pristine_removes_files_the_root_attempt_wrote(tmp_path):
    ws = tmp_path / "copy"
    ws.mkdir()
    (ws / "answer.txt").write_text("7")
    (ws / "keep.txt").write_text("k")
    ep = Episode(id="e", skill="s", skill_version="v", task_id="t", cwd=str(ws), tool_calls=[
        ToolCall(id="1", name="write_file", args={"path": str(ws / "answer.txt"), "content": "5"}, result="ok")])
    removed = restore_pristine(ws, ep)
    assert removed == ["answer.txt"] and not (ws / "answer.txt").exists() and (ws / "keep.txt").exists()


def test_episode_workspace_snapshot_field_roundtrips():
    ep = Episode(id="e", skill="s", skill_version="v", task_id="t", workspace_snapshot="/snap")
    assert Episode.from_dict(ep.to_dict()).workspace_snapshot == "/snap"
