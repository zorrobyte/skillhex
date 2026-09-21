"""Model routing: auxiliary.skillhex_reflector / skillhex_executor blocks in config.yaml, env as override,
main model as fallback. Same convention as every other Hermes side task."""
import os

import pytest

from skillhex.evolve import build_llm, executor_model_from_env, resolve_executor_model, resolve_reflector

MAIN = "model:\n  provider: custom\n  default: sonnet\n  base_url: https://main.example/v1\n  api_mode: chat_completions\n"


def _home(tmp_path, extra="", env="OPENAI_API_KEY=main-key\n"):
    hh = tmp_path / "hermes"
    hh.mkdir(exist_ok=True)
    (hh / "config.yaml").write_text(MAIN + extra)
    (hh / ".env").write_text(env)
    return hh


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith(("SKILLHEX_", "OPENAI_", "AUXILIARY_")):
            monkeypatch.delenv(k, raising=False)


def test_reflector_defaults_to_the_main_model(tmp_path):
    cfg = resolve_reflector(_home(tmp_path))
    assert (cfg.model, cfg.base_url, cfg.api_key) == ("sonnet", "https://main.example/v1", "main-key")


def test_reflector_takes_the_auxiliary_block_model_on_the_main_endpoint(tmp_path):
    hh = _home(tmp_path, "auxiliary:\n  skillhex_reflector:\n    model: opus\n")
    cfg = resolve_reflector(hh)
    assert (cfg.model, cfg.base_url, cfg.api_key) == ("opus", "https://main.example/v1", "main-key")


def test_reflector_auxiliary_block_can_name_its_own_endpoint_and_key_env(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEW_KEY", "review-secret")
    hh = _home(tmp_path, "auxiliary:\n  skillhex_reflector:\n    model: opus\n    base_url: https://review.example/v1\n"
                         "    key_env: REVIEW_KEY\n    reasoning_effort: high\n")
    cfg = resolve_reflector(hh)
    assert (cfg.model, cfg.base_url, cfg.api_key, cfg.reasoning_effort) == ("opus", "https://review.example/v1", "review-secret", "high")


def test_env_overrides_the_auxiliary_block(tmp_path, monkeypatch):
    hh = _home(tmp_path, "auxiliary:\n  skillhex_reflector:\n    model: opus\n")
    monkeypatch.setenv("SKILLHEX_MODEL", "muse")
    assert resolve_reflector(hh).model == "muse"
    assert build_llm(hh).cfg.model == "muse"


def test_executor_defaults_to_no_override(tmp_path):
    assert resolve_executor_model(_home(tmp_path)) is None


def test_executor_auxiliary_block_overrides_only_what_it_names(tmp_path):
    hh = _home(tmp_path, "auxiliary:\n  skillhex_executor:\n    model: haiku\n")
    assert resolve_executor_model(hh) == {"default": "haiku"}
    hh = _home(tmp_path, "auxiliary:\n  skillhex_executor:\n    model: qwen\n    base_url: http://gpu:8000/v1\n    provider: custom\n")
    over = resolve_executor_model(hh)
    assert over["default"] == "qwen" and over["base_url"] == "http://gpu:8000/v1" and over["provider"] == "custom"
    assert over["api_mode"] == "chat_completions"


def test_executor_env_still_wins(tmp_path, monkeypatch):
    hh = _home(tmp_path, "auxiliary:\n  skillhex_executor:\n    model: haiku\n")
    monkeypatch.setenv("SKILLHEX_EXECUTOR_MODEL", "qwen")
    assert resolve_executor_model(hh)["default"] == "qwen"
    assert executor_model_from_env()["default"] == "qwen"
