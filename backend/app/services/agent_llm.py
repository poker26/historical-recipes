"""LLM primitives for the consumer agent: tool-calling + streaming over OpenAI.

Kept SEPARATE from ``llm.py`` on purpose: the shared client there returns only a
content string and treats empty content as a retryable error, which is wrong for
a tool-calling turn (legitimately ``content=None`` when the model only emits
tool_calls) and has no streaming. This module is self-contained (own httpx/retry,
reads ``settings`` directly, takes an explicit model id) so it adds no coupling to
``llm.py`` and no edits to that (heavily-used) file.

Prod egress to ``api.openai.com`` is geo-blocked from RU datacenters — requests
go through the fleet trusttunnel HTTPS proxy (``tt.begemot26.ru:4431``, server 7).
Set ``AGENT_LLM_PROXY`` in ``.env`` (same URL shape as ``PLANTNET_PROXY``).
"""

import asyncio
import json
import logging

import httpx

from app.config import settings

logger = logging.getLogger("agent.llm")

_MAX_RETRIES = 4
_BACKOFF = [2, 5, 12, 25]
_RETRYABLE = {429, 500, 502, 503, 504}


class _Retry(Exception):
    """Internal: a transient failure worth retrying."""


def _proxy() -> str | None:
    """Trusttunnel forward-proxy for OpenAI. Falls back to PLANTNET_PROXY when the
    agent-specific var is unset — prod uses the same ``tt.begemot26.ru:4431`` URL."""
    url = (settings.agent_llm_proxy or settings.plantnet_proxy or "").strip()
    return url or None


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.openai_api_key}"}


def _completions_url() -> str:
    base = settings.openai_base_url.rstrip("/")
    return f"{base}/chat/completions"


def _body(model: str, messages: list[dict], tools, tool_choice,
          temperature: float, max_tokens: int, stream: bool, reasoning=None) -> dict:
    body: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if stream:
        body["stream"] = True
    if tools:
        body["tools"] = tools
    if tool_choice is not None:
        body["tool_choice"] = tool_choice
    return body


async def chat_completion_raw(
    messages: list[dict],
    model: str,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
    temperature: float = 0.3,
    max_tokens: int = 2048,
    reasoning=None,
) -> dict:
    """Non-streaming completion returning the full assistant ``message`` dict
    (``content`` + ``tool_calls``). Retries transient transport/5xx/429 failures.
    ``reasoning`` is accepted for call-site compatibility but ignored (OpenAI)."""
    body = _body(model, messages, tools, tool_choice, temperature, max_tokens, False, reasoning)
    last_err: Exception | None = None
    proxy = _proxy()
    for attempt in range(_MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=600, proxy=proxy) as client:
                resp = await client.post(_completions_url(), headers=_headers(), json=body)
            if resp.status_code >= 400:
                text = resp.text[:800]
                if resp.status_code in _RETRYABLE:
                    raise _Retry(f"HTTP {resp.status_code}: {text}")
                raise ValueError(f"LLM API error HTTP {resp.status_code}: {text}")
            data = resp.json()
            if "error" in data:
                raise ValueError(f"LLM API error: {data['error']}")
            return data["choices"][0]["message"]
        except (httpx.TransportError, _Retry, json.JSONDecodeError) as e:
            last_err = e
            if attempt < _MAX_RETRIES - 1:
                wait = _BACKOFF[min(attempt, len(_BACKOFF) - 1)]
                logger.warning(f"chat_completion_raw retry {attempt + 1}/{_MAX_RETRIES} in {wait}s: {e}")
                await asyncio.sleep(wait)
    raise ValueError(f"LLM raw failed after {_MAX_RETRIES} attempts: {last_err}")


async def stream_completion(
    messages: list[dict],
    model: str,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
    temperature: float = 0.3,
    max_tokens: int = 2048,
    reasoning=None,
):
    """Stream one completion, yielding event dicts as they arrive:

      {"type": "text", "text": <delta>}                          — content token
      {"type": "final", "content", "tool_calls", "finish_reason"} — once at end

    Tool-call deltas are accumulated by index and surfaced in the final event.
    ``reasoning`` is accepted for call-site compatibility but ignored (OpenAI).
    """
    body = _body(model, messages, tools, tool_choice, temperature, max_tokens, True, reasoning)
    content_parts: list[str] = []
    tool_calls: dict[int, dict] = {}
    finish_reason: str | None = None
    proxy = _proxy()
    last_err: Exception | None = None

    for attempt in range(_MAX_RETRIES):
        content_parts.clear()
        tool_calls.clear()
        finish_reason = None
        try:
            async with httpx.AsyncClient(timeout=600, proxy=proxy) as client:
                async with client.stream(
                    "POST", _completions_url(), headers=_headers(), json=body,
                ) as resp:
                    if resp.status_code >= 400:
                        raw = await resp.aread()
                        if resp.status_code in _RETRYABLE:
                            raise _Retry(f"HTTP {resp.status_code}: {raw[:500]!r}")
                        raise ValueError(f"LLM stream HTTP {resp.status_code}: {raw[:500]!r}")
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        payload = line[len("data:"):].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        choice = choices[0]
                        delta = choice.get("delta") or {}
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]
                        text = delta.get("content")
                        if text:
                            content_parts.append(text)
                            yield {"type": "text", "text": text}
                        for tc in delta.get("tool_calls") or []:
                            idx = tc.get("index", 0)
                            slot = tool_calls.setdefault(
                                idx, {"id": None, "type": "function",
                                      "function": {"name": "", "arguments": ""}}
                            )
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["function"]["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["function"]["arguments"] += fn["arguments"]
            yield {
                "type": "final",
                "content": "".join(content_parts) or None,
                "tool_calls": [tool_calls[i] for i in sorted(tool_calls)],
                "finish_reason": finish_reason,
            }
            return
        except (httpx.TransportError, _Retry) as e:
            last_err = e
            if attempt < _MAX_RETRIES - 1:
                wait = _BACKOFF[min(attempt, len(_BACKOFF) - 1)]
                logger.warning(f"stream_completion retry {attempt + 1}/{_MAX_RETRIES} in {wait}s: {e}")
                await asyncio.sleep(wait)
    raise ValueError(f"LLM stream failed after {_MAX_RETRIES} attempts: {last_err}")
