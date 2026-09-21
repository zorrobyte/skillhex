import os
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
import harness  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def test_load_fixtures_reads_every_fixture_dir():
    fx = {f.name: f for f in harness.load_fixtures(ROOT / "eval" / "fixtures")}
    assert {"notes", "csv-report", "log-errors", "version-bump", "config-units"} <= set(fx)
    f = fx["csv-report"]
    assert f.skill == "csv-report" and f.expected == "275.50" and f.checker.exists() and (f.workspace / "sales.csv").exists()
    assert f.skill_md.read_text().startswith("---\nname: csv-report")


def test_no_skill_content_keeps_the_name_and_drops_the_procedure():
    text = harness.no_skill_content("csv-report", "---\nname: csv-report\ndescription: d\n---\n# body\n1. do wrong thing\n")
    assert text.startswith("---\nname: csv-report") and "wrong thing" not in text


class FakeExec:
    """Reward 1 iff the skill content contains 'GOOD'."""
    def __init__(self):
        self.calls = 0

    def execute(self, content, task, node_id):
        self.calls += 1
        from skillhex.search import ExecResult
        from skillhex.models import Episode
        return ExecResult(episode=Episode(id=f"e{self.calls}", skill=task.skill, skill_version=node_id, task_id=task.id),
                          reward=1 if "GOOD" in content else 0)


def test_measure_runs_n_attempts_and_returns_pass_rate():
    ex = FakeExec()
    from skillhex.search import Task
    r = harness.measure(ex, Task(id="t", skill="s", prompt="p"), "GOOD skill", n=3, label="after")
    assert r == {"label": "after", "n": 3, "passes": 3, "rate": 1.0} and ex.calls == 3
    assert harness.measure(ex, Task(id="t", skill="s", prompt="p"), "bad", n=2, label="before")["rate"] == 0.0


def test_render_table_and_summary(tmp_path):
    rows = [{"fixture": "a", "before": {"rate": 0.0, "n": 2}, "no_skill": {"rate": 0.5, "n": 2}, "after": {"rate": 1.0, "n": 2},
             "evolve": {"decision": "apply (official pass)", "executor_calls": 2, "reviewer_tokens": 20000}},
            {"fixture": "b", "before": {"rate": 0.0, "n": 2}, "no_skill": {"rate": 0.0, "n": 2}, "after": {"rate": 0.0, "n": 2},
             "evolve": {"decision": "keep original", "executor_calls": 5, "reviewer_tokens": 30000}}]
    md = harness.render_table(rows)
    assert "| a |" in md and "0/2" in md and "2/2" in md and "keep original" in md
    assert "mean pass rate" in md and "before 0%" in md and "after 50%" in md


def test_make_home_copies_profile_and_forces_auto_apply(tmp_path):
    import yaml
    base = tmp_path / "base"
    base.mkdir()
    (base / "config.yaml").write_text("model:\n  default: m\nskills:\n  write_approval: true\n")
    (base / ".env").write_text("OPENAI_API_KEY=k\n")
    fx = harness.load_fixtures(ROOT / "eval" / "fixtures")[0]
    hh = harness.make_home(base, tmp_path / "home", fx)
    cfg = yaml.safe_load((hh / "config.yaml").read_text())
    assert cfg["model"]["default"] == "m" and cfg["skills"]["write_approval"] is False
    assert (hh / ".env").read_text() == "OPENAI_API_KEY=k\n"
    assert (hh / "skills" / fx.skill / "SKILL.md").read_text() == fx.skill_md.read_text()


def test_version_bump_checker_requires_the_package_json_edit(tmp_path):
    import subprocess, sys, shutil
    fx = ROOT / "eval" / "fixtures" / "version-bump"
    ws = tmp_path / "ws"
    shutil.copytree(fx / "workspace", ws)
    (ws / "answer.txt").write_text("1.4.10\n")
    rc = subprocess.run([sys.executable, str(fx / "checker.py")], env={**os.environ, "SKILLHEX_WORKSPACE": str(ws)}).returncode
    assert rc == 1, "answer.txt right but package.json untouched must fail"
    (ws / "package.json").write_text((ws / "package.json").read_text().replace("1.4.9", "1.4.10"))
    assert subprocess.run([sys.executable, str(fx / "checker.py")], env={**os.environ, "SKILLHEX_WORKSPACE": str(ws)}).returncode == 0


def test_make_home_marks_the_fixture_skill_as_agent_created(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (base / "config.yaml").write_text("model:\n  default: m\n")
    fx = harness.load_fixtures(ROOT / "eval" / "fixtures")[0]
    hh = harness.make_home(base, tmp_path / "home", fx)
    usage = json.loads((hh / "skills" / ".usage.json").read_text())
    assert usage[fx.skill]["created_by"] == "agent"


def test_fixtures_carry_a_heldout_variant_with_its_own_expected_value():
    for f in harness.load_fixtures(ROOT / "eval" / "fixtures"):
        assert f.heldout_workspace is not None and f.heldout_workspace.is_dir(), f.name
        assert f.heldout_expected and f.heldout_expected != f.expected, f.name


def test_heldout_task_points_the_checker_at_the_variant_answer():
    f = [x for x in harness.load_fixtures(ROOT / "eval" / "fixtures") if x.name == "config-units"][0]
    t = harness.heldout_task(f)
    assert t.cwd == str(f.heldout_workspace) and t.meta["expected"] == f.heldout_expected and t.meta["checker"] == str(f.checker)


def test_checkers_honour_an_expected_override(tmp_path):
    import subprocess, sys, shutil
    for f in harness.load_fixtures(ROOT / "eval" / "fixtures"):
        ws = tmp_path / f.name
        shutil.copytree(f.heldout_workspace, ws)
        (ws / "answer.txt").write_text(f.heldout_expected + "\n")
        if f.name == "version-bump":
            (ws / "package.json").write_text((ws / "package.json").read_text().replace('"2.0.9"', '"2.0.10"'))
        env = {**os.environ, "SKILLHEX_WORKSPACE": str(ws), "SKILLHEX_EXPECTED": f.heldout_expected}
        assert subprocess.run([sys.executable, str(f.checker)], env=env, capture_output=True).returncode == 0, f.name
        env["SKILLHEX_EXPECTED"] = f.expected
        assert subprocess.run([sys.executable, str(f.checker)], env=env, capture_output=True).returncode == 1, f.name
