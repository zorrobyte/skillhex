"""Executor that re-runs a task in a fresh, isolated Hermes profile holding one
candidate skill version (the paper's fresh-container-per-attempt).

The scratch profile enables the skillhex plugin so the attempt is captured
as an episode, and (optionally) replays recorded tool results from the root
episode's cassette so evaluation has no side effects.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..episodes import EpisodeStore
from ..models import Episode
from ..search import ExecResult, Task
from ..workspace import restore_pristine

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

MAX_WORKSPACE_BYTES = 50 * 1024 * 1024


def find_hermes_bin() -> str:
    cand = Path(sys.executable).parent / "hermes"
    if cand.exists():
        return str(cand)
    return shutil.which("hermes") or "hermes"


def find_skill_dir(skill: str, hermes_home: Path, extra_roots: Optional[List[Path]] = None) -> Optional[Path]:
    """Locate the directory holding SKILL.md for ``skill`` (profile skills first, then bundled)."""
    roots = [hermes_home / "skills"] + list(extra_roots or [])
    try:
        import hermes_constants  # noqa: F401
        repo = Path(hermes_constants.__file__).parent
        roots += [repo / "skills", repo / "optional-skills"]
    except Exception:  # noqa: BLE001
        pass
    for root in roots:
        if not root.is_dir():
            continue
        direct = root / skill / "SKILL.md"
        if direct.exists():
            return direct.parent
        for md in root.glob("**/SKILL.md"):
            if md.parent.name == skill:
                return md.parent
            head = md.read_text(errors="ignore")[:800]
            if f"name: {skill}\n" in head:
                return md.parent
    return None


_JUNK = shutil.ignore_patterns(".git", "node_modules", ".venv", "__pycache__", ".skillhex*")


def _ignore_links_and_junk(root: Path):
    """Skip junk dirs and every symlink: a link can point outside the workspace at real files."""
    def ignore(d, names):
        out = set(_JUNK(d, names))
        for n in names:
            if (Path(d) / n).is_symlink():
                out.add(n)
        return out
    return ignore


_SECRET_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)", re.I)


def _dir_size(p: Path) -> int:
    total = 0
    for f in p.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
            if total > MAX_WORKSPACE_BYTES:
                break
    return total


class HermesExecutor:
    def __init__(self, hermes_home: Path | str, skill: str, runs_dir: Path | str, *,
                 hermes_bin: Optional[str] = None, replay_episode_dir: Optional[Path] = None,
                 replay_mode: str = "permissive", max_turns: int = 30, run_budget: int = 600,
                 timeout: int = 900, model_override: Optional[Dict[str, Any]] = None,
                 python: Optional[str] = None, root_episode: Optional[Episode] = None):
        self.hermes_home = Path(hermes_home).expanduser()
        self.skill = skill
        self.runs_dir = Path(runs_dir).expanduser().resolve()   # attempts run with cwd=workspace; paths must survive that
        self.hermes_bin = hermes_bin or find_hermes_bin()
        self.replay_episode_dir = Path(replay_episode_dir) if replay_episode_dir else None
        self.replay_mode = replay_mode
        self.max_turns, self.run_budget, self.timeout = max_turns, run_budget, timeout
        self.model_override = model_override or {}
        self.python = python or sys.executable
        self.source_skill_dir = find_skill_dir(skill, self.hermes_home)
        self.last_attempt_dir: Optional[Path] = None
        self.root_episode = root_episode

    # ---- scratch profile -----------------------------------------------------------
    def _scratch_config(self) -> Dict[str, Any]:
        cfg: Dict[str, Any] = {}
        src = self.hermes_home / "config.yaml"
        if yaml and src.exists():
            cfg = yaml.safe_load(src.read_text()) or {}
        cfg.setdefault("skills", {})
        cfg["skills"]["auto_load"] = [self.skill]
        cfg["skills"]["creation_nudge_interval"] = 0
        cfg["skills"]["write_approval"] = True
        cfg.setdefault("memory", {})["nudge_interval"] = 0
        cfg.setdefault("auxiliary", {}).setdefault("background_review", {})["enabled"] = False
        cfg.setdefault("curator", {})["enabled"] = False
        cfg.setdefault("updates", {})["check"] = False
        plugins = cfg.setdefault("plugins", {})
        enabled = list(plugins.get("enabled") or [])
        if "skillhex" not in enabled:
            enabled.append("skillhex")
        plugins["enabled"] = enabled
        plugins.setdefault("entries", {}).setdefault("skillhex", {}).setdefault("settings", {})["auto_evolve"] = False
        if self.model_override:
            cfg.setdefault("model", {})
            if not isinstance(cfg["model"], dict):
                cfg["model"] = {}
            cfg["model"].update({k: v for k, v in self.model_override.items() if k != "api_key"})
        return cfg

    def _prepare(self, skill_content: str, node_id: str, task: Task) -> Dict[str, Path]:
        attempt = self.runs_dir / "attempts" / f"{node_id}-{uuid.uuid4().hex[:6]}"
        home = attempt / "home"
        (home / "skills" / self.skill).mkdir(parents=True)
        if self.source_skill_dir and self.source_skill_dir.exists():
            for child in self.source_skill_dir.iterdir():
                if child.name == "SKILL.md" or child.name.startswith("."):
                    continue
                dst = home / "skills" / self.skill / child.name
                shutil.copytree(child, dst) if child.is_dir() else shutil.copy2(child, dst)
        (home / "skills" / self.skill / "SKILL.md").write_text(skill_content)
        env_lines = (self.hermes_home / ".env").read_text().splitlines() if (self.hermes_home / ".env").exists() else []
        key = (self.model_override or {}).get("api_key")
        if key:   # a custom executor endpoint keys through OPENAI_API_KEY in the scratch profile
            env_lines = [l for l in env_lines if not l.startswith("OPENAI_API_KEY=")] + [f"OPENAI_API_KEY={key}"]
        (home / ".env").write_text("\n".join(env_lines) + ("\n" if env_lines else ""))
        # every base-profile plugin (provider plugins such as a subscription auth plugin must be present)
        plugins_src = self.hermes_home / "plugins"
        if plugins_src.is_dir():
            (home / "plugins").mkdir(exist_ok=True)
            for child in plugins_src.iterdir():
                if child.name.startswith(".") or not (child.is_dir() or child.is_symlink()):
                    continue
                os.symlink(child.resolve(), home / "plugins" / child.name)
        for name in ("auth.json",):   # provider credentials Hermes keeps outside .env
            if (self.hermes_home / name).exists():
                shutil.copy2(self.hermes_home / name, home / name)
        cfg = self._scratch_config()
        (home / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False) if yaml else json.dumps(cfg))
        workspace = attempt / "workspace"
        snap = self.root_episode.workspace_snapshot if self.root_episode else None
        source = snap if snap and Path(snap).is_dir() else task.cwd
        if source and Path(source).is_dir() and _dir_size(Path(source)) <= MAX_WORKSPACE_BYTES:
            shutil.copytree(source, workspace, symlinks=False, ignore=_ignore_links_and_junk(Path(source)))
            if not snap and self.root_episode is not None:
                restore_pristine(workspace, self.root_episode)
        else:
            workspace.mkdir(parents=True)
        (attempt / "capture").mkdir()
        self.last_attempt_dir = attempt
        return {"attempt": attempt, "home": home, "workspace": workspace, "capture": attempt / "capture"}

    def _attempt_env(self, paths: Dict[str, Path]) -> Dict[str, str]:
        """Inherited environment minus anything that looks like a credential. The scratch profile's own
        .env carries what the attempt needs; nothing else from the parent process should leak into a run
        that executes tools with --yolo."""
        env = {k: v for k, v in os.environ.items() if not _SECRET_ENV.search(k) or k in ("HERMES_HOME",)}
        env.update({"HERMES_HOME": str(paths["home"]), "SKILLHEX_CAPTURE_DIR": str(paths["capture"]),
                    "SKILLHEX_FORCE_SKILL": self.skill, "HERMES_NO_UPDATE_CHECK": "1"})
        if self.replay_episode_dir:
            env["SKILLHEX_REPLAY"] = str(self.replay_episode_dir)
            env["SKILLHEX_REPLAY_MODE"] = self.replay_mode
        return env

    # ---- run ---------------------------------------------------------------------
    def execute(self, skill_content: str, task: Task, node_id: str) -> ExecResult:
        paths = self._prepare(skill_content, node_id, task)
        env = self._attempt_env(paths)
        cmd = [self.hermes_bin, "chat", "-Q", "-q", task.prompt, "--yolo", "--in", str(paths["workspace"]),
               "--max-turns", str(self.max_turns), "--run-budget", str(self.run_budget)]
        started = time.time()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout, env=env, cwd=str(paths["workspace"]))
            stdout, stderr, rc = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as e:
            stdout, stderr, rc = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""), "timeout", -1
        (paths["attempt"] / "stdout.txt").write_text(stdout or "")
        (paths["attempt"] / "stderr.txt").write_text(stderr or "")
        store = EpisodeStore(paths["capture"] / "episodes")
        captured = store.list(self.skill)
        if captured:
            ep = captured[-1]
        else:
            ep = Episode(id=f"ep-{node_id}-{uuid.uuid4().hex[:4]}", skill=self.skill, skill_version=node_id, task_id=task.id,
                         messages=[{"role": "user", "content": task.prompt}, {"role": "assistant", "content": (stdout or "").strip()}])
        ep.id = f"ep-{node_id}-{uuid.uuid4().hex[:4]}"
        ep.cwd = str(paths["workspace"])
        ep.model = ep.model or str(self.model_override.get("default") or "")
        reward = self._check(task, ep, paths["workspace"], store.dir(self.skill, captured[-1].id) if captured else None)
        (paths["attempt"] / "result.json").write_text(json.dumps({"reward": reward, "rc": rc, "seconds": round(time.time() - started, 1),
                                                                   "tool_calls": len(ep.tool_calls)}, indent=2))
        return ExecResult(episode=ep, reward=reward)

    def _check(self, task: Task, ep: Episode, workspace: Path, episode_dir: Optional[Path]) -> int:
        checker = task.meta.get("checker")
        if not checker:
            ep.outcome_source = "no_checker"
            return 0
        tmp_dir = episode_dir
        if tmp_dir is None:
            tmp_dir = workspace / ".skillhex-episode"
            EpisodeStore(tmp_dir.parent).save(ep) if False else None
            tmp_dir.mkdir(exist_ok=True)
            (tmp_dir / "episode.json").write_text(json.dumps(ep.to_dict()))
        env = dict(os.environ, SKILLHEX_EPISODE=str(tmp_dir), SKILLHEX_WORKSPACE=str(workspace))
        if task.meta.get("expected") is not None:
            env["SKILLHEX_EXPECTED"] = str(task.meta["expected"])
        try:
            proc = subprocess.run([self.python, str(checker)], capture_output=True, text=True, timeout=120, env=env, cwd=str(workspace))
        except subprocess.TimeoutExpired:
            ep.outcome_note = "checker timeout"
            return 0
        ep.outcome_source = "checker"
        ep.outcome_note = (proc.stdout + proc.stderr).strip()[-500:]
        return 1 if proc.returncode == 0 else 0
