"""Workspace snapshots so evaluation attempts start from the state the
original attempt saw, not the state it left behind."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import List, Optional

from .models import Episode

DEFAULT_MAX_BYTES = 50 * 1024 * 1024
IGNORE = shutil.ignore_patterns(".git", "node_modules", ".venv", "__pycache__", ".skillhex*", "*.sqlite", "*.db-wal")
WRITE_TOOLS = ("write_file", "patch", "create_file", "edit_file")


def _size(p: Path, limit: int) -> int:
    total = 0
    for root, dirs, files in os.walk(p):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", ".venv", "__pycache__")]
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                continue
            if total > limit:
                return total
    return total


def snapshot_workspace(cwd: Path | str | None, dest: Path | str, max_bytes: int = DEFAULT_MAX_BYTES) -> Optional[Path]:
    if not cwd:
        return None
    src, dst = Path(cwd), Path(dest)
    if not src.is_dir() or _size(src, max_bytes) > max_bytes:
        return None
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=True, ignore=IGNORE)
    return dst


def written_paths(ep: Episode) -> List[str]:
    out: List[str] = []
    for tc in ep.tool_calls:
        if tc.name in WRITE_TOOLS:
            p = tc.args.get("path") or tc.args.get("file_path") or tc.args.get("filename")
            if p and p not in out:
                out.append(str(p))
    return out


def restore_pristine(workspace: Path | str, root_episode: Episode) -> List[str]:
    """Fallback when no snapshot exists: delete files the original attempt wrote (copied workspace only)."""
    ws = Path(workspace)
    removed: List[str] = []
    for p in written_paths(root_episode):
        rel = Path(p)
        if rel.is_absolute():
            try:
                rel = rel.relative_to(root_episode.cwd) if root_episode.cwd else rel
            except ValueError:
                rel = Path(rel.name)
        target = ws / rel
        if target.is_file():
            target.unlink()
            removed.append(str(rel))
    return removed
