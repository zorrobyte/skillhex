import json
from pathlib import Path

from skillhex.episodes import EpisodeStore
from skillhex.evolve import evolve_skill
from skillhex.models import Episode, ToolCall
from skillhex.search import ReflectionResult, ExecResult
from test_search import ScriptedReflector, ScriptedVerifier, FakeExecutor, FM


class NoLLM:
    usage = {"calls": 0}


def setup_home(tmp_path, skill_text=FM + "skill: use wttr"):
    hh = tmp_path / "hermes"
    (hh / "skills" / "weather").mkdir(parents=True)
    (hh / "skills" / "weather" / "SKILL.md").write_text(skill_text)
    home = tmp_path / "skillhex"
    store = EpisodeStore(home / "episodes")
    ep = Episode(id="ep0", skill="weather", skill_version="x", task_id="t",
                 messages=[{"role": "user", "content": "14 day forecast"}, {"role": "assistant", "content": "3 days"}],
                 tool_calls=[ToolCall(id="c", name="terminal", args={}, result="wttr 3 days")], outcome="fail", outcome_source="followup")
    store.save(ep)
    return home, hh


def test_official_pass_applies_patch_and_writes_report(tmp_path):
    home, hh = setup_home(tmp_path)
    res = evolve_skill(home, hh, "weather", budget=5, llm=NoLLM(), executor=FakeExecutor(),
                       reflector=ScriptedReflector(), verifier=ScriptedVerifier())
    assert res["passed"] and res["decision"].startswith("apply")
    assert "open-meteo" in (hh / "skills" / "weather" / "SKILL.md").read_text()
    assert (hh / "skills" / "weather" / "SKILL.md.skillhex-prev").read_text() == FM + "skill: use wttr"
    report = Path(res["report"]).read_text()
    assert "Evidence matrix" in report and "Patch tree" in report
    # the failed episode is now marked evolved, so it does not schedule a second run
    assert EpisodeStore(home / "episodes").load("weather", "ep0").evolved_run == res["run"]
    assert EpisodeStore(home / "episodes").pending_skills() == []


def test_a_new_failure_after_a_run_is_pending_again(tmp_path):
    home, hh = setup_home(tmp_path)
    store = EpisodeStore(home / "episodes")
    evolve_skill(home, hh, "weather", budget=2, llm=NoLLM(), executor=FakeExecutor(),
                 reflector=ScriptedReflector(), verifier=ScriptedVerifier())
    assert store.pending_skills() == []
    ep = Episode(id="ep1", skill="weather", skill_version="y", task_id="t",
                 messages=[{"role": "user", "content": "forecast for Oslo"}, {"role": "assistant", "content": "?"}],
                 outcome="fail", outcome_source="followup")
    store.save(ep)
    assert store.pending_skills() == ["weather"]
    assert [e.id for e in store.list("weather", outcome="fail", unevolved=True)] == ["ep1"]


def test_no_pass_and_low_score_keeps_original(tmp_path):
    home, hh = setup_home(tmp_path)

    class NeverRight(FakeExecutor):
        def execute(self, content, task, node_id):
            return super().execute(content.replace("open-meteo", "x"), task, node_id)

    res = evolve_skill(home, hh, "weather", budget=2, llm=NoLLM(), executor=NeverRight(),
                       reflector=ScriptedReflector(), verifier=ScriptedVerifier())
    assert not res["passed"] and res["decision"].startswith("keep")
    assert (hh / "skills" / "weather" / "SKILL.md").read_text() == FM + "skill: use wttr"


def test_no_checker_high_evidence_score_applies(tmp_path):
    home, hh = setup_home(tmp_path)

    class NoReward(FakeExecutor):
        """Executor with no checker: reward always 0, but the open-meteo node passes the oracle test."""
        def execute(self, content, task, node_id):
            r = super().execute(content, task, node_id)
            r.reward = 0
            r.episode.outcome_source = "no_checker"
            return r

    res = evolve_skill(home, hh, "weather", budget=5, min_score=0.8, llm=NoLLM(), executor=NoReward(),
                       reflector=ScriptedReflector(), verifier=ScriptedVerifier())
    assert not res["passed"]
    assert res["decision"].startswith("apply"), res
    assert "open-meteo" in (hh / "skills" / "weather" / "SKILL.md").read_text()


def test_bundled_skill_is_overridden_at_profile_level(tmp_path):
    home, hh = setup_home(tmp_path)
    bundled = tmp_path / "bundled" / "weather"
    bundled.mkdir(parents=True)
    (bundled / "SKILL.md").write_text(FM + "skill: use wttr")
    (hh / "skills" / "weather" / "SKILL.md").unlink()
    (hh / "skills" / "weather").rmdir()
    import skillhex.evolve as ev
    import skillhex.executors.hermes as hx
    orig = hx.find_skill_dir
    hx.find_skill_dir = lambda skill, hermes_home, extra_roots=None: (hh / "skills" / skill) if (hh / "skills" / skill / "SKILL.md").exists() else bundled
    ev.find_skill_dir = hx.find_skill_dir
    try:
        res = evolve_skill(home, hh, "weather", budget=5, llm=NoLLM(), executor=FakeExecutor(),
                           reflector=ScriptedReflector(), verifier=ScriptedVerifier())
    finally:
        hx.find_skill_dir = orig
        ev.find_skill_dir = orig
    assert res["decision"].startswith("apply")
    assert "open-meteo" in (hh / "skills" / "weather" / "SKILL.md").read_text()
    assert (bundled / "SKILL.md").read_text() == FM + "skill: use wttr"


def test_checker_grades_pending_episode_before_search(tmp_path):
    home, hh = setup_home(tmp_path)
    store = EpisodeStore(home / "episodes")
    ep = store.load("weather", "ep0")
    ep.outcome = None
    ep.cwd = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir()
    store.save(ep)
    checker = tmp_path / "check.py"
    checker.write_text("import sys; sys.exit(1)")
    res = evolve_skill(home, hh, "weather", checker=str(checker), budget=5, llm=NoLLM(), executor=FakeExecutor(),
                       reflector=ScriptedReflector(), verifier=ScriptedVerifier())
    assert store.load("weather", "ep0").outcome == "fail"
    assert store.load("weather", "ep0").outcome_source == "checker"
    assert res["decision"].startswith("apply")


def test_write_approval_on_stages_the_patch_instead_of_writing_it(tmp_path):
    """When the user gated skill writes (skills.write_approval: true) the winner goes to
    <HERMES_HOME>/pending/skills/<id>.json in Hermes's own record shape, for /skills pending|diff|approve."""
    home, hh = setup_home(tmp_path)
    (hh / "config.yaml").write_text("skills:\n  write_approval: true\n")
    res = evolve_skill(home, hh, "weather", budget=5, llm=NoLLM(), executor=FakeExecutor(),
                       reflector=ScriptedReflector(), verifier=ScriptedVerifier())
    assert res["decision"].startswith("apply")
    assert (hh / "skills" / "weather" / "SKILL.md").read_text() == FM + "skill: use wttr", "must not write directly"
    pending = list((hh / "pending" / "skills").glob("*.json"))
    assert len(pending) == 1
    rec = json.loads(pending[0].read_text())
    assert rec["subsystem"] == "skills" and rec["origin"] == "background_review"
    assert rec["payload"]["action"] == "edit" and rec["payload"]["name"] == "weather"
    assert "open-meteo" in rec["payload"]["content"]
    assert rec["id"] in res["applied"] and "staged" in res["applied"]


def test_evolve_records_the_change_and_writes_an_html_report(tmp_path):
    from skillhex.changes import ChangeLog
    home, hh = setup_home(tmp_path)
    res = evolve_skill(home, hh, "weather", budget=5, llm=NoLLM(), executor=FakeExecutor(),
                       reflector=ScriptedReflector(), verifier=ScriptedVerifier())
    assert (Path(res["run"]) / "report.html").exists() and (Path(res["run"]) / "artifacts.json").exists()
    rec = ChangeLog(home / "changes.jsonl").recent()[0]
    assert rec["skill"] == "weather" and rec["kind"] == "applied" and rec["run"] == res["run"]
    assert res["html"] == str(Path(res["run"]) / "report.html")
