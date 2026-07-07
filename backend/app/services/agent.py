"""Consumer agent orchestration for nastoiki.pro.

The conversational core behind ``POST /api/agent/chat``. Three moving parts:

1. **topic-gate** — a cheap (32b) classifier that runs BEFORE the expensive loop.
   Off-topic messages get a canned in-character redirect and never reach the
   235b tool-loop. This is the main token-saver (guardrail §1).
2. **system prompt** — the «Корни, Травы, Дистиллят» / Олег Покровский persona,
   scope limits, grounding + safety rules, and the soft master-class CTA.
3. **agent loop** — streamed tool-calling over the curated recipe-first tool set
   (``agent_tools.TOOLS``). Bounded by MAX_TOOL_ROUNDS and a per-answer token cap.

The loop is an async generator of small event dicts the router serialises to SSE:
  {"type": "tool", "name": <tool>}    — a tool call started (UX hint)
  {"type": "delta", "text": <str>}    — a streamed answer token
  {"type": "error", "text": <str>}    — a soft failure message
  {"type": "done"}                    — end of turn
"""

import asyncio
import json
import logging

from app.config import settings
from app.services import agent_llm
from app.services.agent_tools import TOOLS, run_tool

logger = logging.getLogger("agent")

# Two-tier models for latency: a fast, cheap model drives the tool-gathering
# rounds (non-streamed), and a stronger model writes the final answer the user
# reads. Both call OpenAI directly via trusttunnel on prod (see agent_llm.py).
ANSWER_MODEL = settings.agent_answer_model
TOOL_MODEL = settings.agent_tool_model
GATE_MODEL = settings.agent_tool_model

# Kept for call-site compatibility; OpenAI ignores this knob.
NO_REASONING = None

# Ceilings that bound cost/latency on the 235b loop.
MAX_TOOL_ROUNDS = 5          # tool round-trips before we force a final answer
MAX_HISTORY_MESSAGES = 16    # trailing conversation turns kept in context
ANSWER_MAX_TOKENS = 1200     # per-answer output cap

# Short, branded link the agent can reproduce verbatim without mangling a long
# UTM query. nastoiki.pro/mk 302-redirects to qtickets with utm_medium=agent.
QTICKETS_URL = "https://nastoiki.pro/mk"

# In-character redirect for off-topic messages (no LLM call — pure guardrail).
OFF_TOPIC_REPLY = (
    "Это уже не по моей части. Я разбираюсь в старинных русских настойках, "
    "травах и кореньях. Спросите меня, из чего собрать добрую горькую, с чем "
    "лучше сочетать корень дягиля или что нынче цветёт по опушкам и годится для "
    "наливки — об этом расскажу с удовольствием и покажу, где почитать."
)

# Cheap substring bypass so obvious recipe/drink questions never hit the LLM gate
# (which can misfire on historical drink names like «водка ночных стражей»).
_ON_TOPIC_HINTS = (
    "рецепт", "насто", "налив", "водк", "горьк", "бальзам", "ликёр", "ликер",
    "дистил", "трав", "корен", "корень", "гриб", "сбор", "цветёт", "цветет",
    "эфирн", "напит", "румк", "дегуст", "мастер", "привет", "здравств",
)


def _heuristic_on_topic(user_message: str) -> bool:
    text = user_message.lower()
    return any(hint in text for hint in _ON_TOPIC_HINTS)


def _system_prompt(today: str, location: str | None) -> str:
    loc = location.strip() if location and location.strip() else None
    loc_line = (
        f"Локация собеседника: {loc}. Используй её для сезонных советов «что "
        f"собрать рядом сейчас» (инструмент find_observations_nearby + месяц)."
        if loc else
        "Локация собеседника не указана — если нужна для совета «что растёт рядом / "
        "что собрать сейчас», спроси город или район."
    )
    return f"""Ты — мастер и рассказчик старинных русских настоек, голос проекта «Корни, Травы, Дистиллят». За тобой стоит Олег Покровский и его алхимическая лаборатория в деревне Пронино под Серпуховом, где по дореволюционным книгам (1792–1803 гг., Альмединген, Брейтенбах, Штритер и др.) воссоздают горькие настойки, наливки, ликёры, бальзамы и хлебные дистилляты.

ГОЛОС. Умудрённый опытом мастер-настойщик: спокойный, уверенный, сдержанно тёплый, без сюсюканья и без театральных восклицаний. Говоришь как человек, который тридцать лет варил по книгам, а не как рассказчик на ярмарке. Лёгкий юмор и уместные эмодзи 😊 — да. Кредо: «Современные настойки — фастфуд, а тинктуры — медленная магия». Не занудствуй, говори живо и по делу. ЗАПРЕЩЕНО начинать ответ (и любую реплику) с «Ах», «Ох», «Эх», «Ой», «Ого» и похожих междометий — это звучит шаблонно и не по-мастерски. Входи сразу в суть: факт, рецепт, совет; варьируй заходы.

КЛАССИКИ. Изредка, когда к месту, можешь вспомнить мысль любого русского писателя XVIII–XIX века — Пушкин, Лермонтов, Тургенев, Салтыков-Щедрин, Островский, Некрасов, кто угодно из той эпохи; варьируй, не зацикливайся на одних и тех же. Но ты не библиотекарь, а старый мастер, и память с годами подводит. Если вставляешь такую отсылку, обрамляй её по-человечески, вскользь, одной живой фразой: «кажется, у Гоголя где-то было…», «если мне не изменяет память, у Пушкина было примерно так», «вроде бы Салтыков-Щедрин…», «если я не ошибаюсь». Не выдавай это за дословную цитату и не пиши канцелярских оговорок («данная цитата может быть неточной», «я не несу ответственности» и т.п.) — это робот, а не мастер. Не чаще одной такой отсылки на ответ и только когда она правда украшает мысль.

ТЕМА (строго). Только: старинные рецепты напитков (настойки/наливки/ликёры/бальзамы/дистилляты/воды/эфирные масла), растения·грибы·коренья и зачем они в рецепте, их сочетания, сбор и заготовка, что растёт рядом и в какой сезон, историческая питейная культура и посуда. На вопросы вне темы — коротко и в характере верни разговор к настойкам, не расписывай.

ОПОРА НА ДАННЫЕ. У тебя есть инструменты к источник-корпусу оцифрованных книг. Пользуйся ими для конкретики: рецепты — search_recipes/get_recipe/plant_recipes; растения и «зачем ингредиент, когда собирать» — search_plants/get_plant; сочетания — plant_pairings; «что рядом и когда» — find_observations_nearby; лечебный ракурс — plants_for_condition (историческая справка!). Конкретные рецепты и факты называй с книгой и годом. Не выдумывай источники и не приписывай книгам того, чего в них нет. Живой рассказ и разумные подсказки — можно; фактические утверждения — из корпуса.

ВАЖНО ПРО ИНСТРУМЕНТЫ. Вызывай их МОЛЧА — без промежуточных реплик вроде «сейчас найду» или «давайте посмотрим». Не описывай процесс поиска. Обычно достаточно 1–2 вызовов. Для рецепта хватает найти сам рецепт (search_recipes → get_recipe) и объяснить его состав из своих знаний — НЕ ищи каждый ингредиент отдельным get_plant. get_plant вызывай, только когда вопрос именно про конкретное растение (его сбор, свойства, что из него делают). Не вызывай один и тот же инструмент повторно. Как только собрал достаточно данных, ОБЯЗАТЕЛЬНО дай пользователю полный текстовый ответ — никогда не заканчивай ход молчанием.

РАЗВОРОТ РАЗГОВОРА (ПОЭТАПНО, не всё сразу). На запрос рецепта: найди сам рецепт (search_recipes → get_recipe) и выдай его — состав, пропорции, способ, источник и год. НЕ разбирай каждый ингредиент через get_plant: в рецепте бывает 20 и больше трав, это долго и не нужно. Заверши коротким живым приглашением копнуть глубже — предложи рассказать подробнее про одну-две травы из рецепта (например: «Хотите, расскажу про дягиль или калган из этого рецепта — чем они хороши, когда и где их собирать?»). И только КОГДА пользователь сам спросит про конкретное растение — углубляйся: тогда get_plant (свойства, сбор), plant_pairings (сочетания), find_observations_nearby (что растёт рядом сейчас, с учётом месяца и локации). Так разговор идёт шаг за шагом, а пользователь сам выбирает, куда нырнуть.

БЕЗОПАСНОСТЬ. Это историческая и культурная справка, НЕ медицинский совет — так и говори, если спрашивают «от чего лечит». Напитки — для взрослых, всегда про умеренность и культуру пития. При сборе предупреждай о ядовитых двойниках (классика: дягиль путают с борщевиком — трогать осторожно!) и о ядовитых растениях.

МАСТЕР-КЛАССЫ. Где это уместно и естественно (человек хочет «сделать вживую», просит показать процесс, загорелся рецептом) — тепло, без навязчивости и не чаще раза за разговор, пригласи на мастер-класс в алхимическую лабораторию в Пронино. Ссылку давай ТОЧНО и ДОСЛОВНО такую: {QTICKETS_URL} — не меняй в ней ни символа, не добавляй параметров, не разбивай её. НЕ выдумывай названия, даты, программы и цены конкретных занятий — актуальное расписание человек увидит по ссылке.

ЯЗЫК. Пиши на живом, полном русском языке — мы слова не экономим. Не сжимай мысль телеграфным стилем через тире и пропуски союзов и глаголов. Плохо (робо-язык): «2 июля — корень рано, суши траву». Хорошо (живой язык): «2 июля. В этот день ещё рано собирать корни, но можно сушить траву». Разворачивай мысль в нормальные предложения со связками; тире не должно заменять глагол или союз. Авторское тире в прозе уместно, запрещено именно сжатие ради экономии символов.

Сегодня: {today}. {loc_line}
Отвечай на русском, живым разговорным языком, полными предложениями."""


def _tool_system(today: str, location: str | None) -> str:
    """Short system prompt for the fast tool-selection phase. Deliberately lean —
    the 32b tool model doesn't need the full persona/voice/CTA/safety prose (that
    goes to the answer model in phase 2); a compact instruction here roughly halves
    time-to-first-tool-status."""
    loc = f" Локация пользователя: {location.strip()}." if location and location.strip() else ""
    return (
        "Твоя единственная задача — выбрать и вызвать инструменты, чтобы собрать данные "
        "для ответа на вопрос пользователя о старинных русских настойках, наливках, "
        "травах, кореньях, грибах, рецептах напитков, эфирных маслах и сборе растений. "
        "Вызывай инструменты молча, без текста.\n"
        "ГЛАВНОЕ ПРАВИЛО ПОИСКА: в параметр q подставляй КЛЮЧЕВЫЕ СЛОВА ПОЛЬЗОВАТЕЛЯ "
        "ДОСЛОВНО, в том же виде и падеже, как он написал. НЕ приводи к «каноническому» "
        "названию, НЕ меняй падеж/число/род, НЕ переводи, НЕ перефразируй. Пример: на "
        "«дай рецепт водки ночных стражей» → search_recipes(q='водки ночных стражей'), "
        "а НЕ q='Ночные стражи'. Поиск по подстроке буквальный — любая правка ломает матч.\n"
        "НЕ добавляй фильтры (category, kind и т.п.), которых пользователь явно не "
        "называл — они сужают выдачу и отсекают нужное. Для рецепта обычно достаточно "
        "одного search_recipes (в карточке уже есть текст рецепта с источником). Если по "
        "точному запросу ПУСТО — попробуй semantic_search с тем же вопросом (он устойчив "
        "к формулировке). get_plant / plant_pairings / find_observations_nearby — ТОЛЬКО "
        "когда вопрос именно про конкретное растение (свойства, сбор, что растёт рядом). "
        "Обычно хватает 1–2 вызовов; не повторяй один и тот же вызов. Если инструменты не "
        f"нужны — не вызывай ничего. Сегодня: {today}.{loc}"
    )


async def is_on_topic(user_message: str) -> bool:
    """Cheap topic-gate. Returns True unless the message is clearly unrelated to
    настойки/травы/рецепты/сбор. Biased slightly permissive: false negatives hurt
    UX more than the occasional off-topic leak, and clearly-off cases (code,
    politics, math, celebrities) are what we actually want to drop."""
    if not user_message or not user_message.strip():
        return True
    prompt = (
        "Ты — фильтр тематики ассистента по старинным русским настойкам, травам, "
        "кореньям, грибам, рецептам напитков, сбору растений, эфирным маслам и "
        "питейной культуре. Верни СТРОГО JSON {\"on_topic\": true|false}. "
        "true — для приветствий, вопросов «кто ты / что умеешь» и всего, что хоть "
        "как-то про растения/грибы/травы/рецепты/напитки/сбор/сезон/посуду/историю "
        "питья. false — ТОЛЬКО для явно посторонних тем (программирование, политика, "
        "математика, знаменитости, крипта, техподдержка гаджетов и т.п.). Сомневаешься — true."
    )
    try:
        # reasoning OFF → the gate returns in ~1s instead of ~3-4s. Runs
        # concurrently with the first tool call, so this is on the critical path.
        msg = await agent_llm.chat_completion_raw(
            [{"role": "system", "content": prompt},
             {"role": "user", "content": user_message[:800]}],
            model=GATE_MODEL, temperature=0.0, max_tokens=30, reasoning=NO_REASONING,
        )
        content = (msg.get("content") or "").lower()
        if "false" in content and "true" not in content:
            return False
        return True
    except Exception as e:  # noqa: BLE001 — gate must never hard-fail the request
        logger.warning(f"topic gate failed ({e}); allowing through")
    return True


def _trim_history(messages: list[dict]) -> list[dict]:
    """Keep only the trailing turns to bound context/cost."""
    if len(messages) <= MAX_HISTORY_MESSAGES:
        return messages
    return messages[-MAX_HISTORY_MESSAGES:]


async def run_agent(history: list[dict], today: str, location: str | None = None):
    """Drive one assistant turn. `history` is the running [{role, content}] list
    (client-held; server is stateless). Yields SSE event dicts."""
    user_turns = [m for m in history if m.get("role") == "user"]
    last_user = user_turns[-1]["content"] if user_turns else ""

    convo = _trim_history([m for m in history if m.get("role") in ("user", "assistant")])
    # The model conversation must start with a user turn — drop any leading
    # assistant turns (e.g. the client's UI greeting) so the provider doesn't choke.
    while convo and convo[0].get("role") == "assistant":
        convo.pop(0)

    # Run the topic-gate CONCURRENTLY with the first tool call so its ~2-3s latency
    # is hidden behind phase 1 instead of adding to the dead air before the first
    # status reaches the client. Obvious drink/herb questions skip the LLM gate.
    if _heuristic_on_topic(last_user):
        async def _allow() -> bool:
            return True
        gate_task = asyncio.create_task(_allow())
    else:
        gate_task = asyncio.create_task(is_on_topic(last_user))

    # ── Phase 1: fast tool-gathering (mini, NON-streamed, LEAN prompt) ──────
    # Phase 1 gets only a short tool-selection system prompt — NOT the full persona
    # (~1500 tokens) + which the 32b would slog through on every call. The full
    # persona goes to the answer model in phase 2. This roughly halves time-to-first
    # tool status. The user never sees phase-1 text — just the «Листаю…» spinner.
    p1_base = [{"role": "system", "content": _tool_system(today, location)}]
    p1_base += convo
    tool_msgs: list[dict] = []
    used_tools = False

    for round_i in range(MAX_TOOL_ROUNDS):
        try:
            call = asyncio.create_task(agent_llm.chat_completion_raw(
                p1_base + tool_msgs, model=TOOL_MODEL, tools=TOOLS,
                temperature=0.2, max_tokens=1024, reasoning=NO_REASONING,
            ))
            if round_i == 0:
                # Guardrail §1: resolve the gate before spending anything visible.
                if not await gate_task:
                    call.cancel()
                    logger.info("agent: off-topic message, canned redirect")
                    yield {"type": "delta", "text": OFF_TOPIC_REPLY}
                    yield {"type": "done"}
                    return
            msg = await call
        except Exception as e:  # noqa: BLE001
            logger.error(f"agent tool phase failed: {e}")
            break
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            break  # fast model has gathered enough — hand off to the answer model
        used_tools = True
        tool_msgs.append({
            "role": "assistant",
            "content": msg.get("content") or "",
            "tool_calls": tool_calls,
        })
        for tc in tool_calls:
            name = tc.get("function", {}).get("name", "")
            raw_args = tc.get("function", {}).get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            except json.JSONDecodeError:
                args = {}
            yield {"type": "tool", "name": name}
            logger.info(f"agent tool call: {name} args={args}")
            result = await run_tool(name, args)
            tool_msgs.append({
                "role": "tool",
                "tool_call_id": tc.get("id") or name,
                "content": result,
            })

    # ── Phase 2: quality answer (gpt-4.1, STREAMED, FULL persona) ─────────────
    # Full persona + the conversation + everything phase 1 gathered. A trailing USER
    # nudge (not system — models expect system only at the start and can fall silent
    # on a trailing system turn) tells the big model to write the answer now.
    p2 = [{"role": "system", "content": _system_prompt(today, location)}] + convo + tool_msgs
    if used_tools:
        p2.append({
            "role": "user",
            "content": "Теперь ответь мне сам — полно, связно и в своём стиле, по уже "
                       "собранным данным, с источником и годом, где уместно. Инструменты "
                       "больше не вызывай.",
        })
    answered = False
    try:
        async for ev in agent_llm.stream_completion(
            p2, model=ANSWER_MODEL, tools=None,
            temperature=0.5, max_tokens=ANSWER_MAX_TOKENS,
        ):
            if ev["type"] == "text":
                answered = True
                yield {"type": "delta", "text": ev["text"]}
    except Exception as e:  # noqa: BLE001
        logger.error(f"agent synthesis failed: {e}")
        if not answered:
            yield {"type": "error", "text": "Прошу прощения, в лаборатории что-то забарахлило. Пожалуйста, повторите вопрос чуть позже. 😊"}
            yield {"type": "done"}
            return

    if not answered:
        yield {"type": "delta", "text": "Прошу прощения, с ходу собрать ответ не вышло. "
                                        "Задайте вопрос, пожалуйста, чуть иначе — и я всё расскажу. 😊"}
    yield {"type": "done"}
