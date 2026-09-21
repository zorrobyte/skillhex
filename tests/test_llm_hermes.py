"""Reviewer routed through Hermes's own auxiliary client, so any provider Hermes can auth (Muse subscription
plugin, Anthropic OAuth, Codex) works without a base_url + Bearer key."""
import json
import os
import types

import pytest

import agent.auxiliary_client as aux
from skillhex.evolve import build_llm
from skillhex.llm import HermesAuxLLM


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in list(os.environ):
        if k.startswith(("SKILLHEX_", "OPENAI_")):
            monkeypatch.delenv(k, raising=False)


def _fake_call_llm(record):
    def call_llm(task=None, *, messages, max_tokens=None, temperature=None, **kw):
        record.append({"task": task, "messages": messages, "max_tokens": max_tokens})
        msg = types.SimpleNamespace(content='{"ok": true}', reasoning=None)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)],
                                     usage=types.SimpleNamespace(prompt_tokens=11, completion_tokens=3))
    return call_llm


def test_hermes_aux_llm_routes_by_task_and_counts_usage(monkeypatch):
    rec = []
    monkeypatch.setattr(aux, "call_llm", _fake_call_llm(rec))
    llm = HermesAuxLLM(task="skillhex_reflector")
    assert llm.complete_json("sys", "user", max_tokens=99) == {"ok": True}
    assert rec[0]["task"] == "skillhex_reflector" and rec[0]["messages"][0]["role"] == "system" and rec[0]["max_tokens"] == 99
    assert llm.usage == {"prompt_tokens": 11, "completion_tokens": 3, "calls": 1}


def test_build_llm_uses_hermes_routing_for_a_provider_only_profile(tmp_path, monkeypatch):
    hh = tmp_path / "hermes"
    hh.mkdir()
    (hh / "config.yaml").write_text("model:\n  provider: muse-code\n  default: muse-spark-1.3\n")
    monkeypatch.setattr(aux, "call_llm", _fake_call_llm([]))
    llm = build_llm(hh)
    assert isinstance(llm, HermesAuxLLM)
    assert os.environ.get("HERMES_HOME") == str(hh)


def test_build_llm_still_honours_an_explicit_custom_endpoint(tmp_path, monkeypatch):
    hh = tmp_path / "hermes"
    hh.mkdir()
    (hh / "config.yaml").write_text("model:\n  provider: muse-code\n  default: muse-spark-1.3\n")
    monkeypatch.setenv("SKILLHEX_BASE_URL", "https://x.example/v1")
    monkeypatch.setenv("SKILLHEX_MODEL", "m")
    llm = build_llm(hh)
    assert not isinstance(llm, HermesAuxLLM) and llm.cfg.base_url == "https://x.example/v1"
