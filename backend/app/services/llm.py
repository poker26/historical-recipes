"""OpenRouter LLM client with per-task model selection and JSON mode support."""

import asyncio
import json
import logging
import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Retry transient failures: provider returning empty content (finish_reason=None),
# rate limits (429) and upstream errors (5xx). Backoff in seconds per attempt.
MAX_RETRIES = 5
RETRY_BACKOFF = [5, 15, 30, 60]
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class _RetryableLLMError(Exception):
    """Internal: a transient LLM failure worth retrying."""

MODELS = {
    "default": settings.llm_model_default,
    "ocr_fallback": settings.llm_model_ocr_fallback,
    "ocr_hard": settings.llm_model_ocr_hard,
    "lightweight": settings.llm_model_lightweight,
    # Wizard-specific tasks — use large-context models
    "structure_analysis": settings.llm_model_long_context,  # qwen3-235b-2507 (262K context)
    "recipe_extraction": settings.llm_model_long_context,   # qwen3-235b-2507 (precise extraction)
    "plant_extraction": settings.llm_model_long_context,    # qwen3-235b-2507 (herbalism monographs)
    "text_cleanup": settings.llm_model_default,          # qwen3-235b (good Russian)
    "translation": settings.llm_model_default,           # qwen3-235b (pre-reform Russian)
    "ingredient_matching": settings.llm_model_lightweight,  # qwen3-32b (many small calls)
}


async def chat_completion(
    messages: list[dict],
    task: str = "default",
    temperature: float = 0.3,
    max_tokens: int = 4096,
    json_mode: bool = False,
) -> str:
    """Send a chat completion request to OpenRouter."""
    model = MODELS.get(task, MODELS["default"])

    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if json_mode:
        body["response_format"] = {"type": "json_object"}

    # Log request info
    prompt_chars = sum(len(m.get("content", "")) if isinstance(m.get("content"), str) else 0 for m in messages)
    logger.info(f"LLM request: model={model}, task={task}, prompt_chars={prompt_chars}, max_tokens={max_tokens}")

    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return await _do_request(body, model)
        except _RetryableLLMError as e:
            last_err = e
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
                logger.warning(f"LLM transient failure ({e}); retry {attempt+1}/{MAX_RETRIES-1} in {delay}s")
                await asyncio.sleep(delay)
            else:
                logger.error(f"LLM failed after {MAX_RETRIES} attempts: {e}")
    raise ValueError(f"LLM request failed after {MAX_RETRIES} attempts: {last_err}")


async def _do_request(body: dict, model: str) -> str:
    """Single OpenRouter call. Raises _RetryableLLMError on transient failures."""
    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post(
            f"{settings.openrouter_base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
            },
            json=body,
        )

    if response.status_code >= 400:
        # Surface OpenRouter's error body — it explains *why* (credits, data
        # policy, model access, disabled key). raise_for_status() hides it.
        body_text = response.text[:1000]
        if response.status_code in RETRYABLE_STATUS:
            raise _RetryableLLMError(f"HTTP {response.status_code}: {body_text}")
        logger.error(f"LLM HTTP {response.status_code}: {body_text}")
        raise ValueError(f"LLM API error HTTP {response.status_code}: {body_text}")

    # OpenRouter sometimes returns 200 OK with a truncated/malformed body
    # (interrupted stream, partial proxy response). Parsing that raw would raise
    # JSONDecodeError that bubbles past all content-salvage logic and fails the
    # whole multi-chunk activity. Treat a broken transport body as transient and
    # retry just this single call.
    try:
        data = response.json()
    except json.JSONDecodeError as e:
        snippet = response.text[:500]
        logger.warning(f"LLM returned unparseable body (HTTP 200): {e}; body[:500]={snippet!r}")
        raise _RetryableLLMError(f"malformed response body: {e}")

    # Check for errors in response body
    if "error" in data:
        error_msg = data["error"].get("message", str(data["error"]))
        logger.error(f"LLM error: {error_msg}")
        raise ValueError(f"LLM API error: {error_msg}")

    content = data["choices"][0]["message"]["content"]

    # Null/empty content (e.g. finish_reason=None) — usually a transient provider hiccup
    if content is None or content == "":
        finish_reason = data["choices"][0].get("finish_reason", "unknown")
        logger.warning(f"LLM returned empty content, finish_reason={finish_reason}")
        raise _RetryableLLMError(f"empty response (finish_reason={finish_reason})")

    logger.info(f"LLM response: {len(content)} chars, finish_reason={data['choices'][0].get('finish_reason')}")
    return content


async def chat_completion_json(
    messages: list[dict],
    task: str = "default",
    temperature: float = 0.1,
    max_tokens: int = 16384,
) -> dict | list:
    """Chat completion that parses response as JSON.

    Uses json_mode when available, falls back to extracting JSON from markdown code blocks.
    """
    raw = await chat_completion(
        messages, task=task, temperature=temperature,
        max_tokens=max_tokens, json_mode=True,
    )

    # Try direct JSON parse
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass

    # Try extracting from markdown code block
    for fence in ("```json", "```"):
        if fence in raw:
            try:
                start = raw.index(fence) + len(fence)
                end = raw.index("```", start)
                return json.loads(raw[start:end].strip())
            except (json.JSONDecodeError, ValueError):
                pass

    # Try finding a complete JSON array or object
    for char, end_char in [("[", "]"), ("{", "}")]:
        if char in raw and end_char in raw:
            try:
                start = raw.index(char)
                end = raw.rindex(end_char) + 1
                return json.loads(raw[start:end])
            except json.JSONDecodeError:
                pass

    # Last resort: response was truncated (hit max_tokens mid-array). Salvage
    # every complete object from the array so we keep what the model produced
    # instead of losing the whole call.
    salvaged = _salvage_json_objects(raw)
    if salvaged:
        logger.warning(f"Salvaged {len(salvaged)} objects from truncated/invalid JSON response")
        return salvaged

    raise ValueError(f"Could not parse JSON from LLM response: {raw[:500]}...")


def _salvage_json_objects(raw: str) -> list:
    """Recover complete objects from a (possibly truncated) JSON array.

    Finds the first '[' — covers both a bare top-level array and an array nested
    in a wrapper like {"recipes": [...]} — then decodes objects one at a time,
    stopping at the first incomplete one. Returns the list of recovered objects.
    """
    start = raw.find("[")
    if start == -1:
        return []
    decoder = json.JSONDecoder()
    objects: list = []
    i = start + 1
    n = len(raw)
    while i < n:
        while i < n and raw[i] in " \t\r\n,":
            i += 1
        if i >= n or raw[i] == "]":
            break
        try:
            obj, end = decoder.raw_decode(raw, i)
        except json.JSONDecodeError:
            break  # truncated/incomplete object — stop here
        objects.append(obj)
        i = end
    return objects
