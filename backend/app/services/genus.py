"""Genus tier (RFC-reference-granularity): assemble a HUB genus row (``rank='genus'``)
for each Russian noun-token realized by ≥2 corpus species, grounded/validated by the
latin genus, and parent the confirmed members via ``parent_id``.

Grouping is by the RUSSIAN noun-token (``вишн``); the LATIN genus only validates —
it drops folk impostors (физалис «жидовская вишня» = Physalis ≠ the Prunus cherries)
and reveals genuinely poly-generic tokens (перец = Piper/Capsicum/Polygonum), because
the latin genus is COARSER than the folk category (Prunus = вишня+слива+черёмуха), so
it must never be the grouper. See the RFC for the (a)–(d) typology.

Idempotent: find-or-create the genus row, first-claim ``parent_id`` (largest genus
wins, ``parent_id IS NULL`` guard), so a re-run resumes rather than duplicating. A
full RE-derive (after latin repair improves the grounding) needs the parents cleared
first — by design a plain re-run only fills gaps.
"""
from collections import Counter, defaultdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.plant_matching import (
    _latin_key, normalize, _stem, _is_adjective,
    _PART_WORDS, _STOPWORDS, _MIN_KEY_TOKEN,
)

# «растение»/«трава» as a head noun is the literal word "plant" — never a genus.
_DROP_WORDS = {"растение", "растения", "растений", "растению", "растениях",
               "растениями", "трава", "травы", "трав"}

def genus_noun_keys(name: str | None) -> set[str]:
    """Hardened genus-noun stems of a name (adjectives + «растение» + part-words
    dropped). The token a bare generic mention («вишня») would be reduced to.

    Adjective detection is the (now broadened) ``_is_adjective`` from plant_matching
    — single source of truth, so the genus build and the matcher agree on what a
    noun head is (notably both keep «зверобой», whose «-ой» must NOT read as an adj)."""
    out: set[str] = set()
    for tok in normalize(name).split():
        if (not tok or tok in _PART_WORDS or tok in _STOPWORDS or tok in _DROP_WORDS
                or tok.isdigit() or _is_adjective(tok)):
            continue
        st = _stem(tok)
        if len(st) >= _MIN_KEY_TOKEN and not st.isdigit():
            out.add(st)
    return out


# Established genus synonyms / recent segregates → accepted genus, so a name stored
# under either spelling counts as ONE genus (else they look poly). Small + curated;
# grows as the dry-run surfaces more. (A full GBIF accepted-genus pass is the rigorous
# version — this is the pragmatic core that kills the loudest false splits.)
_GENUS_SYN = {
    "cerasus": "prunus", "padus": "prunus", "amygdalus": "prunus",
    "armeniaca": "prunus", "microcerasus": "prunus", "laurocerasus": "prunus",
    "chamomilla": "matricaria",
    "persicaria": "polygonum", "bistorta": "polygonum", "fallopia": "polygonum",
    "reynoutria": "polygonum",
    "jacobaea": "senecio", "oreomecon": "papaver", "phedimus": "sedum",
    "micranthes": "saxifraga", "pseudognaphalium": "gnaphalium",
    "omalotheca": "gnaphalium", "pentanema": "inula",
}


def acc_genus(name_latin: str | None) -> str | None:
    """Accepted latin genus of a name, or None. Single-letter «genera» (OCR
    abbreviations like «A. absinthium») collapse to None — not a distinct genus."""
    k = _latin_key(name_latin)
    if not k:
        return None
    g = k.split()[0]
    if len(g) <= 1:
        return None
    return _GENUS_SYN.get(g, g)


# A member is (id, name, name_latin).
def classify(members: list) -> tuple[str, str | None, set[str]]:
    """Return (class, dominant_genus, ids_of_dominant_members).

    A   — one accepted latin genus across members.
    A+  — a strongly dominant genus (≥3× the runner-up): minority = OCR/impostor,
          dropped from the genus (kept as their own species).
    C   — several comparable latin genera (genuinely poly-generic) → no genus row.
    D   — no latin on any member → no genus row.
    """
    pairs = [(m, acc_genus(m[2])) for m in members]
    dist = Counter(g for _, g in pairs if g)
    if not dist:
        return "D", None, set()
    if len(dist) == 1:
        dom = next(iter(dist))
        return "A", dom, {m[0] for m, g in pairs if g == dom}
    top = dist.most_common()
    if top[0][1] >= 3 * top[1][1]:
        dom = top[0][0]
        return "A+", dom, {m[0] for m, g in pairs if g == dom}
    return "C", None, set()


def _surface_noun(name: str | None, token: str) -> str | None:
    """The surface (un-stemmed) noun word in `name` whose stem == token, so the
    genus row can show «Вишня» (nominative) rather than the stem «вишн»."""
    for w in normalize(name).split():
        if w in _PART_WORDS or w in _STOPWORDS or w in _DROP_WORDS or _is_adjective(w):
            continue
        if _stem(w) == token:
            return w
    return None


def _display_name(members: list, token: str) -> str:
    """Most common surface form of the genus noun across members, capitalized."""
    c = Counter()
    for _, nm, _ in members:
        sw = _surface_noun(nm, token)
        if sw:
            c[sw] += 1
    w = c.most_common(1)[0][0] if c else token
    return w[:1].upper() + w[1:]


def _is_bare_genus(name: str | None, token: str) -> bool:
    """True if `name` is essentially JUST the genus noun (one significant noun ==
    token, no epithet) — such a card is PROMOTED to the genus row instead of
    spawning a duplicate (e.g. an existing «Полынь»/«Борец»/«Вишня» card)."""
    sig = [w for w in normalize(name).split()
           if w not in _PART_WORDS and w not in _STOPWORDS and w not in _DROP_WORDS
           and not _is_adjective(w) and not w.isdigit() and len(_stem(w)) >= _MIN_KEY_TOKEN]
    return len(sig) == 1 and _stem(sig[0]) == token


async def build_genus_tier(db: AsyncSession, dry_run: bool = True, progress=None) -> dict:
    """Create/realize the genus tier. Commits per genus when ``dry_run`` is False so
    the wrapping activity resumes cleanly. ``progress(done, total, genera)`` heartbeats."""
    rows = (await db.execute(text(
        "SELECT id, name, name_latin FROM plants WHERE rank='species' AND kingdom='растение'"
    ))).all()
    key2sp: dict[str, list] = defaultdict(list)
    for r in rows:
        for k in genus_noun_keys(r.name):
            key2sp[k].append((str(r.id), r.name, r.name_latin))
    generic = {k: v for k, v in key2sp.items() if len({x[0] for x in v}) >= 2}

    # Largest genus first → first-claim parent wins for a multi-noun species.
    items = sorted(generic.items(), key=lambda kv: -len({x[0] for x in kv[1]}))
    total = len(items)
    out = {"tokens": total, "genera": 0, "promoted": 0, "created": 0,
           "members_parented": 0, "ambiguous_C": 0, "nolatin_D": 0, "samples": []}

    done = 0
    for token, mem in items:
        uniq = list({x[0]: x for x in mem}.values())
        cls, dom, dom_ids = classify(uniq)
        done += 1
        if progress:
            progress(done, total, out["genera"])
        if cls == "C":
            out["ambiguous_C"] += 1
            continue
        if cls == "D":
            out["nolatin_D"] += 1
            continue
        dom_members = [m for m in uniq if m[0] in dom_ids]
        if len(dom_members) < 2:
            continue
        name = _display_name(dom_members, token)
        latin = dom[:1].upper() + dom[1:]

        if dry_run:
            out["genera"] += 1
            out["members_parented"] += len(dom_members)
            if len(out["samples"]) < 40:
                out["samples"].append({"token": token, "name": name,
                                       "latin": latin, "n": len(dom_members)})
            continue

        # Find-or-create the genus row (idempotent re-run). Always a NEW row — never
        # promote a member, since overwriting a bare card's name_latin to the bare
        # genus would destroy its binomial. A bare «Полынь» species simply becomes a
        # member of the «Полынь» genus; the matcher prefers rank='genus' for generics.
        existing = (await db.execute(text(
            "SELECT id FROM plants WHERE rank='genus' AND lower(name)=lower(:n) AND name_latin=:l"
        ), {"n": name, "l": latin})).first()
        if existing:
            gid = existing[0]
        else:
            gid = (await db.execute(text(
                "INSERT INTO plants (id, name, name_latin, rank, kingdom) "
                "VALUES (gen_random_uuid(), :n, :l, 'genus', 'растение') RETURNING id"
            ), {"n": name, "l": latin})).scalar()
            out["created"] += 1

        child_ids = [m[0] for m in dom_members if str(m[0]) != str(gid)]
        if child_ids:
            r = await db.execute(text(
                "UPDATE plants SET parent_id=:g WHERE id = ANY(:ids) AND parent_id IS NULL"
            ), {"g": gid, "ids": child_ids})
            out["members_parented"] += r.rowcount
        await db.commit()
        out["genera"] += 1
    return out
