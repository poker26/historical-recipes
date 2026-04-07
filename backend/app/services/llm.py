"""OpenRouter LLM client with per-task model selection."""

import httpx

from app.config import settings

MODELS = {
    "default": settings.llm_model_default,
    "ocr_fallback": settings.llm_model_ocr_fallback,
    "ocr_hard": settings.llm_model_ocr_hard,
    "lightweight": settings.llm_model_lightweight,
}


async def chat_completion(
    messages: list[dict],
    task: str = "default",
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> str:
    """Send a chat completion request to OpenRouter.

    Args:
        messages: Chat messages in OpenAI format.
        task: Task key to select model from MODELS dict.
        temperature: Sampling temperature.
        max_tokens: Maximum tokens in response.

    Returns:
        The assistant's response text.
    """
    model = MODELS.get(task, MODELS["default"])

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{settings.openrouter_base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
            },
            json={
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
