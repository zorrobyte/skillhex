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
