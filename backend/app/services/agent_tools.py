"""Curated tool surface for the consumer agent (nastoiki.pro).

A recipe-first subset of the MCP tool set, exposed to the conversational LLM as
OpenAI-style function definitions. Dispatch mirrors ``app/mcp/server.py``: every
tool is a thin wrapper over the existing FastAPI routers reached over the
internal docker network (``settings.internal_api_url``), so all query logic
stays in one place and this layer never touches SQL.

Two knobs bound LLM cost on the pricey 235b loop:
- only the tools a настойки-assistant actually needs are exposed (no admin/
  compound-association/identify surface — those bloat the prompt and the loop);
- tool results are size-capped (``_cap``) before they re-enter the context, so a
  giant get_plant monograph can't balloon the conversation token by token.
"""

import json
import logging

import httpx

from app.config import settings

logger = logging.getLogger("agent.tools")

API = settings.internal_api_url.rstrip("/")

# Result leanness is the TOOLS' job now (RFC-mcp-agent-fit): get_plant uses
# ?view=agent, searches pass a limit. So this cap is only a defensive backstop
# against a pathological result, NOT the primary mechanism — set high enough that
# a well-behaved agent-fit payload never trips it (a slimmed monograph is ~25 KB).
_RESULT_CAP = 60000
_SEARCH_LIMIT = 8


async def _request(method: str, path: str, *, params: dict | None = None,
                   json_body: dict | None = None) -> dict | list:
    """One HTTP round-trip to the internal REST API (see mcp/server.py._request)."""
    if params:
        params = {k: v for k, v in params.items() if v is not None}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.request(method, f"{API}{path}", params=params, json=json_body)
    except httpx.HTTPError as e:
        logger.warning(f"internal API {method} {path} failed: {type(e).__name__}: {e}")
        return {"error": f"backend request failed: {e}"}
    if resp.status_code == 404:
        return {"error": "not found"}
    if resp.status_code >= 400:
        return {"error": f"backend HTTP {resp.status_code}", "detail": resp.text[:300]}
    try:
        return resp.json()
    except ValueError:
        return {"error": "backend returned non-JSON body"}


# ─────────────────────────────── dispatch fns ───────────────────────────────


async def _search_recipes(q=None, category=None, domain=None, home_doable=True, kind=None):
    return await _request("GET", "/api/recipes/", params={
        "q": q, "category": category, "domain": domain,
        "home_doable": home_doable, "kind": kind, "limit": _SEARCH_LIMIT,
    })


async def _get_recipe(recipe_id):
    return await _request("GET", f"/api/recipes/{recipe_id}")


async def _search_plants(q=None, compound=None, action=None, indication=None,
                         family=None, toxic=None, edibility=None, kingdom=None):
    return await _request("GET", "/api/plants/", params={
        "q": q, "compound": compound, "action": action, "indication": indication,
        "family": family, "is_toxic": toxic, "edibility": edibility, "kingdom": kingdom,
        "limit": _SEARCH_LIMIT,
    })


async def _get_plant(plant_id):
    # ?view=agent = the lean, deduped, valid-JSON monograph (RFC-mcp-agent-fit);
    # the default view is a 1+ MB raw dump that can't fit an LLM context.
    return await _request("GET", f"/api/plants/{plant_id}", params={"view": "agent"})


async def _plant_recipes(plant_id, kind=None, limit=12):
    return await _request("GET", f"/api/plants/{plant_id}/recipes",
                          params={"kind": kind, "limit": limit})


async def _plant_pairings(plant_id, category=None, limit=15):
    return await _request("GET", f"/api/plants/{plant_id}/pairings",
                          params={"category": category, "limit": limit})


async def _plants_for_condition(condition, kingdom=None, toxic=None, limit=12):
    base = {"kingdom": kingdom, "is_toxic": toxic, "limit": _SEARCH_LIMIT}
    by_ind = await _request("GET", "/api/plants/", params={**base, "indication": condition})
    by_act = await _request("GET", "/api/plants/", params={**base, "action": condition})
    merged: dict[str, dict] = {}
    for rows in (by_ind, by_act):
        if isinstance(rows, list):
            for p in rows:
                pid = p.get("id")
                if pid and pid not in merged:
                    merged[pid] = p
    if not merged and isinstance(by_ind, dict) and "error" in by_ind:
        return by_ind
    plants = list(merged.values())[:limit]
    return {"condition": condition, "count": len(plants), "plants": plants}


async def _semantic_search(query, collection=None, limit=8):
    return await _request("POST", "/api/search/", json_body={
        "query": query, "collection": collection, "limit": limit, "mode": "hybrid",
    })


async def _search_oils(q=None, limit=30):
    return await _request("GET", "/api/oils", params={"q": q, "limit": limit})


async def _oils_for_condition(condition, limit=30):
    return await _request("GET", "/api/oils/for-condition",
                          params={"condition": condition, "limit": limit})


async def _get_oil(oil_id):
    return await _request("GET", f"/api/oils/{oil_id}")


async def _observations_nearby(plant_id, region=None, lat=None, lng=None,
                               radius_km=50.0, limit=15):
    params: dict = {"radius_km": radius_km, "limit": limit}
    if region:
        params["place"] = region
    if lat is not None:
        params["lat"] = lat
    if lng is not None:
        params["lng"] = lng
    return await _request("GET", f"/api/plants/{plant_id}/observations", params=params)


DISPATCH = {
    "search_recipes": _search_recipes,
    "get_recipe": _get_recipe,
    "search_plants": _search_plants,
    "get_plant": _get_plant,
    "plant_recipes": _plant_recipes,
    "plant_pairings": _plant_pairings,
    "plants_for_condition": _plants_for_condition,
    "semantic_search": _semantic_search,
    "search_oils": _search_oils,
    "oils_for_condition": _oils_for_condition,
    "get_oil": _get_oil,
    "find_observations_nearby": _observations_nearby,
}


# ──────────────────────────── OpenAI tool schemas ────────────────────────────

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_recipes",
            "description": (
                "Найти рецепты (настойки, наливки, ликёры, бальзамы, дистилляты, "
                "воды, масла) по свободному тексту/категории. Возвращает карточки "
                "с id, названием, категорией, книгой+годом, текстом. Полный текст — "
                "через get_recipe(id). home_doable=true по умолчанию отсекает "
                "мусор (обрывки, промышленные процедуры)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {"type": "string", "description": "свободный текст (напр. «зверобой», «английская горькая», «дягиль перец»)"},
                    "category": {"type": "string", "description": "категория: настойка/наливка/ликёр/бальзам/водка/масло…"},
                    "kind": {"type": "string", "enum": ["medicinal", "food", "cosmetic", "other"]},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recipe",
            "description": "Полный рецепт: дословный original_text + нормализованный текст + список ингредиентов (связанных с растениями) + книга/автор/год. Источник для цитирования.",
            "parameters": {
                "type": "object",
                "properties": {"recipe_id": {"type": "string"}},
                "required": ["recipe_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_plants",
            "description": (
                "Поиск по гербарию растений И грибов (все фасеты по AND). Возвращает "
                "карточки (id, name, name_latin, family, is_toxic, parts_used, "
                "фото). Полный монограф — get_plant(id)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {"type": "string", "description": "название (совр./латынь/старинное), напр. «дягиль», «калган», «полынь горькая»"},
                    "family": {"type": "string", "description": "семейство (рус/лат)"},
                    "toxic": {"type": "boolean"},
                    "kingdom": {"type": "string", "enum": ["растение", "гриб"]},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_plant",
            "description": (
                "Полный монограф растения/гриба: идентичность (названия, латынь, "
                "семейство, токсичность, фото iNat) + ВСЕ факты по источникам "
                "(лекарственные применения, вещества, СБОР/harvest, места обитания, "
                "кулинария) с дословным текстом и книгой+годом + связанные рецепты. "
                "Отсюда берутся факты «когда/что собирать» и «почему ингредиент нужен»."
            ),
            "parameters": {
                "type": "object",
                "properties": {"plant_id": {"type": "string"}},
                "required": ["plant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plant_recipes",
            "description": "«Что сделать из этого растения» — курированные, ранжированные, реально-выполнимые рецепты с этим растением (с откатом на род). Каждый с текстом+источником. step_by_step=настоящий рецепт vs заметка о дозировке.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plant_id": {"type": "string"},
                    "kind": {"type": "string", "enum": ["medicinal", "food", "cosmetic", "other"]},
                },
                "required": ["plant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plant_pairings",
            "description": (
                "«С чем это растение исторически СОЧЕТАЕТСЯ» — растения-компаньоны, "
                "ранжированные по совместной встречаемости в рецептах, с рецептами-"
                "доказательствами. Это grounded co-occurrence, НЕ утверждение об "
                "эффективности. specific:true = особое сродство."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plant_id": {"type": "string"},
                    "category": {"type": "string", "description": "форма препарата, напр. «настойка»"},
                },
                "required": ["plant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plants_for_condition",
            "description": "Растения/грибы для СОСТОЯНИЯ, как скажет человек (симптом/болезнь/старинное название/действие). Резолвит по обеим осям (показания с архаично→совр. мостом + действие). Историческая справка, НЕ мед.совет.",
            "parameters": {
                "type": "object",
                "properties": {
                    "condition": {"type": "string"},
                    "kingdom": {"type": "string", "enum": ["растение", "гриб"]},
                    "toxic": {"type": "boolean"},
                },
                "required": ["condition"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "semantic_search",
            "description": "Гибридный смысловой поиск по корпусу для вопросов, ответ на которые в свободной прозе, а не в поле (напр. «чем красили настойки в синий»). Возвращает ранжированные хиты с id — далее get_plant/get_recipe.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "collection": {"type": "string", "enum": ["recipes_v2", "plants_v2", "sections_v1"]},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_oils",
            "description": "Поиск эфирных масел (отдельный столп от гербария). Каждое связано с растением-источником. Далее get_oil(id).",
            "parameters": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "oils_for_condition",
            "description": "Эфирные масла для состояния (аналог plants_for_condition). Ароматерапия — слабая доказательность; подавать как исторически засвидетельствованное применение, не совет.",
            "parameters": {
                "type": "object",
                "properties": {"condition": {"type": "string"}},
                "required": ["condition"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_oil",
            "description": "Полный монограф эфирного масла: идентичность, растение-источник, part/extraction/aroma_profile + факты применения с дословным текстом.",
            "parameters": {
                "type": "object",
                "properties": {"oil_id": {"type": "string"}},
                "required": ["oil_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_observations_nearby",
            "description": (
                "Живые наблюдения iNaturalist растения/гриба — «где найти, насколько "
                "часто встречается, КОГДА (сезонность по месяцам)». Живые внешние "
                "данные (не корпус): атрибуцию iNat нужно показать. Сначала нужен "
                "plant_id (через search_plants). Задавай ЛИБО region (название места), "
                "ЛИБО lat+lng. Используй для «что растёт рядом со мной сейчас»."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plant_id": {"type": "string"},
                    "region": {"type": "string", "description": "название места (район/город/область)"},
                    "lat": {"type": "number"},
                    "lng": {"type": "number"},
                    "radius_km": {"type": "number"},
                },
                "required": ["plant_id"],
            },
        },
    },
]


def _cap(result) -> str:
    """Serialize a tool result to JSON. Defensive backstop only: if a payload is
    still pathologically large (the tools should already be agent-fit), truncate
    so it can't blow up the context. A well-behaved result never trips this."""
    try:
        text = json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(result)
    if len(text) > _RESULT_CAP:
        logger.warning(f"tool result {len(text)} chars > cap {_RESULT_CAP}; truncating (tool not agent-fit?)")
        text = text[:_RESULT_CAP] + "… [результат обрезан]"
    return text


async def run_tool(name: str, arguments: dict) -> str:
    """Execute one tool call and return a capped JSON string for the model."""
    fn = DISPATCH.get(name)
    if fn is None:
        return json.dumps({"error": f"unknown tool {name}"}, ensure_ascii=False)
    try:
        result = await fn(**(arguments or {}))
    except TypeError as e:
        return json.dumps({"error": f"bad arguments for {name}: {e}"}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001 — never let a tool crash the loop
        logger.warning(f"tool {name} raised {type(e).__name__}: {e}")
        return json.dumps({"error": f"tool {name} failed: {e}"}, ensure_ascii=False)
    return _cap(result)
