"""Minimal OpenAI-compatible chat client (stdlib only) with JSON extraction.

Used for the reflection and self-verifier roles. Any endpoint that speaks
``/v1/chat/completions`` works: Muse, vLLM, llama.cpp, OpenAI.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


class LLMError(RuntimeError):
    pass


@dataclass
class LLMConfig:
    base_url: str
    api_key: str = ""
    model: str = ""
    reasoning_effort: Optional[str] = None
    temperature: float = 0.2
    max_tokens: int = 12000
    timeout: int = 600


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    m = _FENCE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # find the outermost balanced object
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise LLMError(f"no JSON object in output: {text[:200]!r}")


class OpenAICompatLLM:
    def __init__(self, cfg: LLMConfig, post: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None):
        self.cfg = cfg
        self._post = post or self._http_post
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None, **over) -> "OpenAICompatLLM":
        env = env or dict(os.environ)
        cfg = LLMConfig(
            base_url=over.get("base_url") or env.get("SKILLHEX_BASE_URL") or env.get("OPENAI_BASE_URL", ""),
            api_key=over.get("api_key") or env.get("SKILLHEX_API_KEY") or env.get("OPENAI_API_KEY", ""),
            model=over.get("model") or env.get("SKILLHEX_MODEL", ""),
            reasoning_effort=over.get("reasoning_effort") or env.get("SKILLHEX_REASONING_EFFORT") or None,
        )
        return cls(cfg)

    def _http_post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.cfg.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise LLMError(f"HTTP {e.code}: {e.read()[:500]!r}") from e

    def complete(self, system: str, user: str, extra_messages: Optional[list] = None,
                 max_tokens: Optional[int] = None) -> str:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}] + (extra_messages or [])
        payload: Dict[str, Any] = {"model": self.cfg.model, "messages": messages,
                                   "temperature": self.cfg.temperature, "max_tokens": max_tokens or self.cfg.max_tokens}
        if self.cfg.reasoning_effort:
            payload["reasoning_effort"] = self.cfg.reasoning_effort
        data = self._post(payload)
        u = data.get("usage") or {}
        self.usage["prompt_tokens"] += int(u.get("prompt_tokens", 0) or 0)
        self.usage["completion_tokens"] += int(u.get("completion_tokens", 0) or 0)
        self.usage["calls"] += 1
        try:
            return data["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError) as e:
            raise LLMError(f"unexpected response shape: {str(data)[:300]}") from e

    def complete_json(self, system: str, user: str, **kw) -> Dict[str, Any]:
        text = self.complete(system, user, **kw)
        try:
            return extract_json(text)
        except LLMError as first:
            repair = [{"role": "assistant", "content": text},
                      {"role": "user", "content": "That was not a single valid JSON object. Return ONLY the JSON object, no prose, no fences."}]
            text2 = self.complete(system, user, extra_messages=repair, **kw)
            try:
                return extract_json(text2)
            except LLMError as second:
                raise LLMError(f"malformed JSON twice: {first}; {second}") from second
