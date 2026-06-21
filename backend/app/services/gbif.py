"""GBIF occurrence source for custom quests (RFC-custom-quests).

GBIF aggregates iNaturalist research-grade + herbaria + regional surveys → far more
plant records than iNat alone, with real coordinates and a clean licence (CC0/CC-BY).
Two uses:
  * Layer-1 augmentation — distinct plant species actually recorded IN the quest circle
    (boosts the iNat set ~+50% in populated areas).
  * Layer-2 regional pool — distinct species recorded across the wider REGION, the
    «known here» filter for biotope-expected species where local point-data is empty
    (true глухомань: iNat 0 / local GBIF 0, but the region still holds hundreds).

No API key. We read species names straight off occurrence records (the `species` field
is the binomial), aggregating distinct species + a coarse count, paginating a bounded
number of records (we only need the species list, not every record)."""

import httpx

GBIF_BASE = "https://api.gbif.org/v1"
_PLANTAE_KINGDOM_KEY = 6


async def species_in_bbox(swlat: float, swlng: float, nelat: float, nelng: float,
                          max_records: int = 1000) -> dict[str, int]:
    """{binomial scientific name: occurrence_count} for georeferenced plants inside the
    bbox. Bounded pagination (`max_records`); we want the species set, not all records."""
    out: dict[str, int] = {}
    base = {
        "kingdomKey": _PLANTAE_KINGDOM_KEY,
        "hasCoordinate": "true",
        "hasGeospatialIssue": "false",
        "decimalLatitude": f"{swlat},{nelat}",
        "decimalLongitude": f"{swlng},{nelng}",
        "limit": 300,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        offset = 0
        while offset < max_records:
            try:
                r = await client.get(f"{GBIF_BASE}/occurrence/search",
                                     params=dict(base, offset=offset))
                if r.status_code != 200:
                    break
                data = r.json()
            except (httpx.HTTPError, ValueError):
                break
            results = data.get("results", []) or []
            for rec in results:
                # `species` is populated only when the record is identified to species
                # rank (skips genus-only / family-only records — exactly what we want).
                sp = rec.get("species")
                if sp:
                    out[sp] = out.get(sp, 0) + 1
            if data.get("endOfRecords") or not results:
                break
            offset += len(results)
    return out
