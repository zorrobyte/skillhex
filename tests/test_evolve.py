import json
from pathlib import Path

from skillhex.episodes import EpisodeStore
from skillhex.evolve import evolve_skill
from skillhex.models import Episode, ToolCall
from skillhex.search import ReflectionResult, ExecResult
from test_search import ScriptedReflector, ScriptedVerifier, FakeExecutor


class NoLLM:
    usage = {"calls": 0}


def setup_home(tmp_path, skill_text="skill: use wttr"):
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
    assert (hh / "skills" / "weather" / "SKILL.md.skillhex-prev").read_text() == "skill: use wttr"
    report = Path(res["report"]).read_text()
    assert "Evidence matrix" in report and "Patch tree" in report
    assert (home / "runs" / "weather.evolved.json").exists()


def test_no_pass_and_low_score_keeps_original(tmp_path):
    home, hh = setup_home(tmp_path)

    class NeverRight(FakeExecutor):
        def execute(self, content, task, node_id):
            return super().execute(content.replace("open-meteo", "x"), task, node_id)

    res = evolve_skill(home, hh, "weather", budget=2, llm=NoLLM(), executor=NeverRight(),
                       reflector=ScriptedReflector(), verifier=ScriptedVerifier())
    assert not res["passed"] and res["decision"].startswith("keep")
    assert (hh / "skills" / "weather" / "SKILL.md").read_text() == "skill: use wttr"


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
    (bundled / "SKILL.md").write_text("skill: use wttr")
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
    assert (bundled / "SKILL.md").read_text() == "skill: use wttr"


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
