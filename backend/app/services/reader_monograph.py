# -*- coding: utf-8 -*-
"""Layer 2 — the precomputed «reader monograph» generator (docs/RFC-reader-monograph).

Pipeline (RFC §3): the deterministic `_field_view` aggregation → (1) DISTILL with a
hard cap (top-N uses, top-M compound groups) into a compact, ranked, quote-carrying
facts pack → (2) strictly-ABSTRACTIVE LLM polish (merge morpho/synonym dups, fix OCR,
write verdict/description/summaries/note/lead_fact/cautions, one best quote per action
— INVENT NOTHING) → (3) PUBLISH-GATE (P0 ungrounded_claim/identity_conflict block;
P1 residual_ocr/dup_not_merged/stub_leak warn) → (4) STORE with generated_from_hash.

The deterministic SKELETON (photo, recipe refs, sources, identity) is carried through
untouched; the LLM only rewrites text fields. Default view + MCP get_plant stay RAW.
"""
import hashlib
import json
import re

from app.services.llm import chat_completion_json

N_USES = 12
M_COMPOUNDS = 15
MODEL_TAG = "qwen3-235b"


# ──────────────────────────────────────────────────────── (1) distillation

def distill(fv: dict, n_uses: int = N_USES, m_compounds: int = M_COMPOUNDS) -> dict:
    """Cap the field-view aggregation into a compact facts pack for the LLM. Keeps
    the top-N consensus-ranked uses + top-M compound groups (already source-count
    ranked upstream), each carrying its verbatim quote + source so the LLM stays
    grounded. `_`-prefixed keys are deterministic skeleton carried through to the
    final monograph WITHOUT touching the LLM (and excluded from the input hash)."""
    uses_all = fv.get("uses") or []
    uses = sorted(uses_all, key=lambda u: -(u.get("source_count") or 0))[:n_uses]
    slim_uses = [{
        "action": u.get("action"),
        "action_id": u.get("action_id"),
        "indications": u.get("indications") or [],
        "quote": u.get("quote"),
        "source_count": u.get("source_count"),
    } for u in uses]

    comps_all = fv.get("compound_groups") or []
    comps = comps_all[:m_compounds]
    slim_comps = [{
        "group": c.get("group"),
        "examples": [(e.get("name") if isinstance(e, dict) else e)
                     for e in (c.get("examples") or [])][:6],
    } for c in comps]

    return {
        "id": fv["id"], "name": fv.get("name"), "name_modern": fv.get("name_modern"),
        "name_latin": fv.get("name_latin"), "family": fv.get("family"),
        "is_toxic": fv.get("is_toxic"), "roles": fv.get("roles") or [],
        "description": fv.get("description"), "parts_used": fv.get("parts_used"),
        "uses": slim_uses, "compounds": slim_comps,
        "cautions": fv.get("cautions"), "harvest": fv.get("harvest"),
        "culinary": fv.get("culinary"), "sources": fv.get("sources") or [],
        # canonical biotope keys → LLM writes habitat.summary prose (facing/hashed:
        # a biotope change should refresh the summary).
        "habitat_biotopes": [b["key"] for b in
                             ((fv.get("habitat") or {}).get("biotopes") or [])],
        # forager-safety: level + twin are HASHED (a level/twin change must refresh
        # the verdict) and shown to the LLM so the verdict prose matches — but the
        # authoritative structured block is carried verbatim below (_safety), never
        # reworded by the LLM (safety-critical).
        "safety_level": (fv.get("safety") or {}).get("level"),
        "deadly_twin": (fv.get("safety") or {}).get("deadly_twin"),
        # ── deterministic skeleton (NOT LLM-touched, NOT hashed) ──
        "_safety": fv.get("safety"),
        "_photo": {k: fv.get(k) for k in
                   ("photo_url", "photo_attribution", "photo_license", "photo_source")},
        "_family_latin": fv.get("family_latin"), "_kingdom": fv.get("kingdom"),
        "_recipes": fv.get("recipes") or [], "_recipes_total": fv.get("recipes_total") or 0,
        "_uses_total": len(uses_all), "_compounds_total": len(comps_all),
        "_habitat": fv.get("habitat"),     # structured chips + regions (carried verbatim)
    }


def _facing(distilled: dict) -> dict:
    """The LLM-facing subset (drops the carried-through `_` skeleton)."""
    return {k: v for k, v in distilled.items() if not k.startswith("_")}


def input_hash(distilled: dict) -> str:
    """sha256 of the LLM-facing facts — the idempotency key (§7). Skeleton changes
    (a new photo) don't force a costly regen; only the facts that shape text do."""
    blob = json.dumps(_facing(distilled), ensure_ascii=False, sort_keys=True)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ──────────────────────────────────────────────────────── (2) LLM polish

_SYS = (
    "Ты — редактор ботанического справочника. Тебе дан ДИСТИЛЛИРОВАННЫЙ набор "
    "ОБОСНОВАННЫХ фактов об одном растении (отранжированные действия с индикациями и "
    "вербатим-цитатами+источником, группы состава, части, описание, кулинария, "
    "предостережения). Задача — переписать это в приятный ЧИТАТЕЛЬСКИЙ монограф для "
    "обычного человека (не агента). Строго:\n"
    "• Склей морфологические и синонимичные дубли действий и групп состава (различие "
    "только в падеже/числе/очевидном синониме → одно: «успокаивающее»=«седативное», "
    "«улучшает/улучшающее/улучшение» → одно).\n"
    "• Почини очевидный OCR в видимом тексте (нишек→шишек, лупулинъ→лупулин, рех-→трёх-).\n"
    "• Нормализуй части до коротких канонических форм.\n"
    "• Напиши человеческим языком: verdict (одна честная фраза-вердикт; если роль "
    "edible И toxic — объясни нюанс в одной фразе, как со съедобными побегами и "
    "одурманивающими шишками хмеля), description, по каждому действию summary, по "
    "• БЕЗОПАСНОСТЬ ЕДОКА: тебе дан safety_level (0=нет данных, 1=съедобно, "
    "2=условно съедобно, 3=опасно/дозозависимо — не еда, 4=СМЕРТЕЛЬНО ядовито) и "
    "deadly_twin. Вердикт ОБЯЗАН соответствовать уровню и НЕ смягчать опасность; "
    "при level 3-4 прямо предупреди, что есть нельзя; при deadly_twin обязательно "
    "упомяни «опасный двойник — {twin}, не перепутай». Сам уровень НЕ меняй.\n"
    "каждой группе состава note, lead_fact (живой факт ТОЛЬКО из реальной цитаты "
    "источника), cautions.text.\n"
    "• habitat.summary — короткая проза «где растёт» из habitat_biotopes (НЕ добавляй "
    "биотопы не из списка; пусто → null).\n"
    "• Выбери по ОДНОЙ лучшей цитате на действие (с дозировкой/способом, если есть).\n"
    "ЖЁСТКО (анти-галлюцинация):\n"
    "• НЕ вводи НИ ОДНОГО факта, которого нет во входе. Перефразирование/реорганизация "
    "— да; новые свойства/показания/вещества — НЕТ.\n"
    "• Никаких медицинских инструкций/доз «от себя» — только из цитат.\n"
    "• lead_fact только из реального текста источника, с source. Нет живого факта — "
    "поле опусти (НЕ выдумывай).\n"
    "• Сохраняй привязку к источникам (quote.source).\n"
    "• Заглушки запрещены: блок либо с реальным контентом, либо отсутствует.\n"
    "Верни СТРОГО JSON:\n"
    "{\"verdict\": str, \"roles\": [str], \"is_toxic\": bool, \"description\": str|null, "
    "\"lead_fact\": {\"text\": str, \"source\": str}|null, "
    "\"uses\": [{\"action\": str, \"summary\": str, \"indications\": [str], "
    "\"quote\": {\"text\": str, \"source\": str}|null, \"source_count\": int}], "
    "\"compounds\": [{\"group\": str, \"note\": str}], "
    "\"cautions\": {\"text\": str, \"toxic_parts\": [str]}|null, "
    "\"habitat\": {\"summary\": str|null}, "
    "\"parts_used\": [str], \"culinary\": [{\"use\": str, \"part\": str|null}]|null}"
)


async def polish(distilled: dict) -> dict:
    """Run the abstractive LLM polish. Returns the candidate text fields (§4/§5).
    Rich plants (12 uses + 15 compounds, each with prose) produce a large JSON, so
    max_tokens is generous — too small truncates and the salvage yields a list."""
    user = json.dumps(_facing(distilled), ensure_ascii=False)
    out = await chat_completion_json(
        [{"role": "system", "content": _SYS}, {"role": "user", "content": user}],
        task="plant_extraction", temperature=0.2, max_tokens=6000)
    if not isinstance(out, dict):
        raise ValueError(f"LLM polish returned {type(out).__name__}, expected dict (likely truncated)")
    return out


def assemble(distilled: dict, polished: dict, gen_hash: str, reviewed: bool = False) -> dict:
    """Merge the LLM text fields with the deterministic skeleton into the §4 contract.
    Drops empty blocks (no stubs). Recipe refs / photo / sources are carried verbatim."""
    out: dict = {
        "id": distilled["id"], "name": distilled.get("name"),
        "name_latin": distilled.get("name_latin"), "family": distilled.get("family"),
        "family_latin": distilled.get("_family_latin"), "kingdom": distilled.get("_kingdom"),
        **{k: v for k, v in distilled.get("_photo", {}).items() if v},
        "verdict": (polished.get("verdict") or "").strip() or None,
        "roles": polished.get("roles") or distilled.get("roles") or [],
        "is_toxic": bool(polished.get("is_toxic", distilled.get("is_toxic"))),
        # authoritative forager-safety verdict — carried VERBATIM from our classifier,
        # the LLM never rewrites the level/twin (shown first in the reader card).
        "safety": distilled.get("_safety"),
        "description": (polished.get("description") or "").strip() or None,
        "uses": [u for u in (polished.get("uses") or []) if u.get("action")],
        "compounds": [c for c in (polished.get("compounds") or []) if c.get("group")],
        "parts_used": polished.get("parts_used") or None,
        "culinary": polished.get("culinary") or None,
        "recipes": distilled.get("_recipes") or None,
        "recipes_total": distilled.get("_recipes_total") or 0,
        "sources": distilled.get("sources") or [],
        # «ещё N» pointers to the raw endpoint for the curious (§12 Q3)
        "uses_total": distilled.get("_uses_total"),
        "compounds_total": distilled.get("_compounds_total"),
        "generated_from_hash": gen_hash, "model": MODEL_TAG, "reviewed": reviewed,
    }
    lf = polished.get("lead_fact")
    if isinstance(lf, dict) and (lf.get("text") or "").strip():
        out["lead_fact"] = {"text": lf["text"].strip(), "source": lf.get("source")}
    c = polished.get("cautions")
    if isinstance(c, dict) and (c.get("text") or "").strip():
        out["cautions"] = {"text": c["text"].strip(), "toxic_parts": c.get("toxic_parts") or []}
    h = distilled.get("harvest")
    if h and any(h.values()):
        out["harvest"] = h
    hab = distilled.get("_habitat")
    if isinstance(hab, dict) and (hab.get("biotopes") or hab.get("regions")):
        hsum = (polished.get("habitat") or {}).get("summary") if isinstance(polished.get("habitat"), dict) else None
        out["habitat"] = {
            "biotopes": hab.get("biotopes") or [],
            "regions": hab.get("regions") or [],
            "summary": (hsum or "").strip() or None,
        }
    return {k: v for k, v in out.items() if v is not None}


# ──────────────────────────────────────────────────────── (3) publish-gate

# Characteristic residual-OCR signatures in VISIBLE text (RFC §8 P1).
_OCR_PAT = re.compile(r"ѣ|[a-zA-Z][а-яё]|[а-яё][a-zA-Z]|нишек|иллик|ъ\b|рех-пят")
_STUB_PAT = re.compile(r"появится позже|появятся позже|appears later|coming soon|TODO|заглушк", re.I)
_norm = lambda s: re.sub(r"[^а-яёa-z]", "", (s or "").lower().replace("ё", "е"))


def _visible_text(mono: dict) -> str:
    parts = [mono.get("verdict") or "", mono.get("description") or ""]
    parts += [u.get("summary") or "" for u in mono.get("uses", [])]
    parts += [c.get("note") or "" for c in mono.get("compounds", [])]
    if mono.get("lead_fact"):
        parts.append(mono["lead_fact"].get("text") or "")
    if mono.get("cautions"):
        parts.append(mono["cautions"].get("text") or "")
    return " ".join(parts)


def publish_gate(mono: dict, distilled: dict) -> list[dict]:
    """Validate a candidate against its distilled input. Returns findings; any P0
    blocks publication (reviewed stays false → not served). RFC §8."""
    findings: list[dict] = []
    in_sources = {s.strip().lower() for s in (distilled.get("sources") or []) if s}
    in_use_count = len(distilled.get("uses") or [])

    # P0 ungrounded_claim — no invented uses; every cited source traces to the input.
    if len(mono.get("uses", [])) > max(in_use_count, 1):
        findings.append({"check": "ungrounded_claim", "severity": "P0",
                         "detail": f"uses {len(mono['uses'])} > input {in_use_count}"})
    bad_src = [u["quote"]["source"] for u in mono.get("uses", [])
               if u.get("quote") and (u["quote"].get("source") or "").strip().lower()
               and (u["quote"]["source"].strip().lower() not in in_sources)]
    if mono.get("lead_fact") and (mono["lead_fact"].get("source") or "").strip().lower() \
            and mono["lead_fact"]["source"].strip().lower() not in in_sources:
        bad_src.append(mono["lead_fact"]["source"])
    if bad_src:
        findings.append({"check": "ungrounded_claim", "severity": "P0",
                         "detail": f"source(s) not in input: {bad_src[:3]}"})

    # P0 identity_conflict — edible+toxic must be RECONCILED in the verdict (Хмель):
    # the one-sentence verdict has to address BOTH the edible sense and the
    # caution sense (any reasonable phrasing — «съедобен»/«в кулинарии»/«побеги
    # едят» on one side, «ядовито»/«осторожно»/«большие дозы» on the other).
    roles = set(mono.get("roles") or [])
    if "edible" in roles and (mono.get("is_toxic") or "toxic" in roles):
        v = (mono.get("verdict") or "").lower()
        edible_sense = any(w in v for w in (
            "съедоб", "кулинар", "едят", "едят", "пищ", "овощ", "салат",
            "употребл", "побеги", "заварива", " чай"))
        caution_sense = any(w in v for w in (
            "ядов", "токсич", "осторож", "ограничен", "передозир", "доз",
            "нельзя", "противопоказ", "одурман", "вред", "больш"))
        if not (v and edible_sense and caution_sense):
            findings.append({"check": "identity_conflict", "severity": "P0",
                             "detail": "edible+toxic not reconciled in verdict"})

    # P1 residual_ocr — visible text still carries OCR signatures.
    vt = _visible_text(mono)
    if _OCR_PAT.search(vt):
        m = _OCR_PAT.search(vt)
        findings.append({"check": "residual_ocr", "severity": "P1",
                         "detail": f"OCR pattern near «…{vt[max(0, m.start()-12):m.start()+12]}…»"})

    # P1 dup_not_merged — obvious MORPHO dups survived (case/number variants:
    # one is a prefix of the other with a tiny tail, e.g. «успокаивающ»/«успокаивающее»).
    # Bare substring-anywhere is too loose (flags unrelated «желчегонное» pairs) — use
    # prefix + small length delta so only true morphological variants trip it.
    def _morpho_dup(a: str, b: str) -> bool:
        if not a or not b:
            return False
        if a == b:
            return True
        short, lng = (a, b) if len(a) <= len(b) else (b, a)
        return len(short) >= 5 and lng.startswith(short) and (len(lng) - len(short)) <= 3
    for field, key in (("uses", "action"), ("compounds", "group")):
        labels = [_norm(x.get(key)) for x in mono.get(field, []) if x.get(key)]
        hit = False
        for i in range(len(labels)):
            for j in range(i + 1, len(labels)):
                if _morpho_dup(labels[i], labels[j]):
                    findings.append({"check": "dup_not_merged", "severity": "P1",
                                     "detail": f"{field}: «{labels[i]}» ≈ «{labels[j]}»"})
                    hit = True
                    break
            if hit:
                break

    # P1 stub_leak — placeholder phrase in a visible field.
    if _STUB_PAT.search(vt):
        findings.append({"check": "stub_leak", "severity": "P1", "detail": "placeholder phrase"})

    return findings


def has_p0(findings: list[dict]) -> bool:
    return any(f.get("severity") == "P0" for f in findings)
