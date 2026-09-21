import json

import pytest

from skillhex.llm import OpenAICompatLLM, LLMConfig, LLMError


def fake_post(responses):
    calls = []
    it = iter(responses)

    def post(payload):
        calls.append(payload)
        return {"choices": [{"message": {"content": next(it)}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    post.calls = calls
    return post


def cfg():
    return LLMConfig(base_url="https://x/v1", api_key="k", model="m", reasoning_effort="low")


def test_complete_json_parses_fenced_json_and_sends_model_and_effort():
    post = fake_post(['```json\n{"a": 1}\n```'])
    llm = OpenAICompatLLM(cfg(), post=post)
    assert llm.complete_json("sys", "user") == {"a": 1}
    payload = post.calls[0]
    assert payload["model"] == "m" and payload["reasoning_effort"] == "low"
    assert payload["messages"][0] == {"role": "system", "content": "sys"}
    assert payload["messages"][1]["content"] == "user"


def test_complete_json_repairs_malformed_output_once():
    post = fake_post(['{"a": 1', '{"a": 1}'])
    llm = OpenAICompatLLM(cfg(), post=post)
    assert llm.complete_json("sys", "user") == {"a": 1}
    assert len(post.calls) == 2
    repair = post.calls[1]["messages"]
    assert any('{"a": 1' in str(m.get("content")) for m in repair)


def test_complete_json_raises_after_repair_fails():
    post = fake_post(["nope", "still nope"])
    llm = OpenAICompatLLM(cfg(), post=post)
    with pytest.raises(LLMError):
        llm.complete_json("sys", "user")


def test_usage_is_accumulated():
    post = fake_post(['{"a":1}'])
    llm = OpenAICompatLLM(cfg(), post=post)
    llm.complete_json("s", "u")
    assert llm.usage["prompt_tokens"] == 1 and llm.usage["calls"] == 1


def test_from_env_reads_openai_vars(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk")
    monkeypatch.setenv("SKILLHEX_MODEL", "mm")
    llm = OpenAICompatLLM.from_env()
    assert llm.cfg.base_url == "https://api.example/v1" and llm.cfg.model == "mm"


def test_extract_json_finds_object_inside_prose():
    from skillhex.llm import extract_json
    assert extract_json('Sure! {"x": [1,2]} done') == {"x": [1, 2]}
