import json

from skillhex.report import render_html, write_artifacts

ROOT = "---\nname: weather\n---\nuse wttr"
BEST = "---\nname: weather\n---\nuse open-meteo"


def _artifacts(run_dir):
    return write_artifacts(run_dir, skill="weather", task_prompt="14 day forecast <b>", decision="apply (official pass)",
                           passed=True, executor_calls=2, matrix_text="      t1 t2\nroot  ✗  ✓\nv1    ✓  ✓", tree_text="root\n└─ v1 *",
                           hypotheses="H1: wttr caps at 3 days", root_content=ROOT, best_content=BEST, best_id="v1",
                           best_score=1.0, root_score=0.0, llm_usage={"calls": 7}, applied="applied via ledger")


def test_artifacts_roundtrip_and_html_has_diff_matrix_tree_and_escaping(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    p = _artifacts(run)
    assert json.loads(p.read_text())["best_id"] == "v1"
    html = render_html(run)
    assert html.name == "report.html" and html.parent == run
    text = html.read_text()
    assert "weather" in text and "-use wttr" in text and "+use open-meteo" in text
    assert "H1: wttr caps" in text and "root  ✗  ✓" in text and "└─ v1" in text
    assert "&lt;b&gt;" in text and "<b>" not in text.split("<body")[1]


def test_html_renders_from_saved_artifacts_when_only_the_run_dir_is_given(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    _artifacts(run)
    (run / "report.html").unlink(missing_ok=True)
    assert render_html(run).exists()
