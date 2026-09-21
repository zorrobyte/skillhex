import json
from pathlib import Path

import yaml

from skillhex.executors.hermes import HermesExecutor, find_skill_dir
from skillhex.search import Task


def make_home(tmp_path):
    hh = tmp_path / "hermes"
    (hh / "skills" / "notes-cli" / "scripts").mkdir(parents=True)
    (hh / "skills" / "notes-cli" / "SKILL.md").write_text("---\nname: notes-cli\n---\nold")
    (hh / "skills" / "notes-cli" / "scripts" / "helper.py").write_text("print(1)")
    (hh / "config.yaml").write_text(yaml.safe_dump({"model": {"provider": "custom", "default": "m"}, "plugins": {"enabled": ["other"]}}))
    (hh / ".env").write_text("OPENAI_API_KEY=k\n")
    (hh / "plugins").mkdir()
    (hh / "plugins" / "skillhex").mkdir()
    return hh


def test_find_skill_dir_by_dir_name_and_frontmatter(tmp_path):
    hh = make_home(tmp_path)
    assert find_skill_dir("notes-cli", hh) == hh / "skills" / "notes-cli"
    (hh / "skills" / "cat" / "weird").mkdir(parents=True)
    (hh / "skills" / "cat" / "weird" / "SKILL.md").write_text("---\nname: weather\n---\n")
    assert find_skill_dir("weather", hh) == hh / "skills" / "cat" / "weird"
    assert find_skill_dir("nope", hh) is None


def test_prepare_builds_isolated_profile_with_candidate_skill(tmp_path):
    hh = make_home(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "data.txt").write_text("x")
    ex = HermesExecutor(hh, "notes-cli", tmp_path / "runs", model_override={"default": "qwen", "base_url": "http://q/v1"})
    paths = ex._prepare("---\nname: notes-cli\n---\nnew", "v3", Task(id="t", skill="notes-cli", prompt="p", cwd=str(ws)))
    home = paths["home"]
    assert (home / "skills" / "notes-cli" / "SKILL.md").read_text().endswith("new")
    assert (home / "skills" / "notes-cli" / "scripts" / "helper.py").exists()
    assert (home / ".env").read_text() == "OPENAI_API_KEY=k\n"
    assert (home / "plugins" / "skillhex").is_symlink()
    cfg = yaml.safe_load((home / "config.yaml").read_text())
    assert cfg["skills"]["auto_load"] == ["notes-cli"]
    assert cfg["skills"]["creation_nudge_interval"] == 0
    assert cfg["auxiliary"]["background_review"]["enabled"] is False
    assert "skillhex" in cfg["plugins"]["enabled"] and "other" in cfg["plugins"]["enabled"]
    assert cfg["plugins"]["entries"]["skillhex"]["settings"]["auto_evolve"] is False
    assert cfg["model"]["default"] == "qwen" and cfg["model"]["base_url"] == "http://q/v1"
    assert (paths["workspace"] / "data.txt").read_text() == "x"
    assert (ws / "data.txt").exists()  # original untouched


def test_execute_runs_command_and_uses_checker(tmp_path, monkeypatch):
    """Stub the hermes binary with a script that writes answer.txt; checker decides reward."""
    hh = make_home(tmp_path)
    fake_bin = tmp_path / "fake-hermes"
    fake_bin.write_text("#!/bin/sh\necho '7' > \"$(pwd)/answer.txt\"\necho 'done'\n")
    fake_bin.chmod(0o755)
    checker = tmp_path / "check.py"
    checker.write_text("import os,sys; sys.exit(0 if open(os.path.join(os.environ['SKILLHEX_WORKSPACE'],'answer.txt')).read().strip()=='7' else 1)")
    ex = HermesExecutor(hh, "notes-cli", tmp_path / "runs", hermes_bin=str(fake_bin))
    res = ex.execute("skill", Task(id="t", skill="notes-cli", prompt="p", meta={"checker": str(checker)}), "v1")
    assert res.reward == 1
    assert res.episode.outcome_source == "checker"
    assert (ex.last_attempt_dir / "result.json").exists()
    assert res.episode.final_response == "done"


def test_prepare_writes_an_executor_api_key_into_the_scratch_env_not_the_config(tmp_path):
    hh = make_home(tmp_path)
    ex = HermesExecutor(hh, "notes-cli", tmp_path / "runs",
                        model_override={"default": "qwen", "base_url": "http://q/v1", "api_key": "exec-secret"})
    paths = ex._prepare("---\nname: notes-cli\n---\nnew", "v1", Task(id="t", skill="notes-cli", prompt="p", cwd=None))
    env = (paths["home"] / ".env").read_text()
    assert "OPENAI_API_KEY=exec-secret" in env and "OPENAI_API_KEY=k" not in env
    cfg = yaml.safe_load((paths["home"] / "config.yaml").read_text())
    assert "api_key" not in cfg["model"]


def test_relative_runs_dir_yields_absolute_attempt_paths(tmp_path, monkeypatch):
    hh = make_home(tmp_path)
    monkeypatch.chdir(tmp_path)
    ex = HermesExecutor(hh, "notes-cli", "rel-runs")
    paths = ex._prepare("---\nname: notes-cli\n---\nx", "v1", Task(id="t", skill="notes-cli", prompt="p", cwd=None))
    assert paths["workspace"].is_absolute() and paths["attempt"].is_absolute()
    assert str(paths["attempt"]).startswith(str(tmp_path.resolve()))
