"""OpenRouter LLM client with per-task model selection and JSON mode support."""

import json
import httpx

from app.config import settings

MODELS = {
    "default": settings.llm_model_default,
    "ocr_fallback": settings.llm_model_ocr_fallback,
    "ocr_hard": settings.llm_model_ocr_hard,
    "lightweight": settings.llm_model_lightweight,
    # Wizard-specific tasks — use large-context models
    "structure_analysis": settings.llm_model_ocr_hard,  # gemini-2.5-pro (1M context)
    "recipe_extraction": settings.llm_model_ocr_hard,   # gemini-2.5-pro (precise extraction)
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
    """Send a chat completion request to OpenRouter.

    Args:
        messages: Chat messages in OpenAI format.
        task: Task key to select model from MODELS dict.
        temperature: Sampling temperature.
        max_tokens: Maximum tokens in response.
        json_mode: If True, request JSON response format.

    Returns:
        The assistant's response text.
    """
    model = MODELS.get(task, MODELS["default"])

    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if json_mode:
        body["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=300) as client:
        response = await client.post(
            f"{settings.openrouter_base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
            },
            json=body,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


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
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code block
    if "```json" in raw:
        start = raw.index("```json") + 7
        end = raw.index("```", start)
        return json.loads(raw[start:end].strip())
    if "```" in raw:
        start = raw.index("```") + 3
        end = raw.index("```", start)
        return json.loads(raw[start:end].strip())

    # Try finding JSON array or object
    for char, end_char in [("[", "]"), ("{", "}")]:
        if char in raw:
            start = raw.index(char)
            end = raw.rindex(end_char) + 1
            return json.loads(raw[start:end])

    raise ValueError(f"Could not parse JSON from LLM response: {raw[:200]}...")
