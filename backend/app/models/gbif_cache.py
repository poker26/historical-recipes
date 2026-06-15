from datetime import datetime

from sqlalchemy import String, Integer, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class GbifTaxonCache(Base):
    """latin_key → GBIF name-match result, cached (external-truth backbone).

    The data-quality identity checks need an authoritative kingdom + accepted name
    for each binomial. GBIF's `/species/match` gives exactly that (no auth, fast),
    but it's a live external call, so we cache the answer keyed on the SAME
    `plant_matching._latin_key` the herbarium dedups on (genus+species, lowercased,
    author-stripped) — so one resolve covers every card that shares the binomial.

    A NONE match (the latin resolves to no taxon — OCR garbage / made-up name) is
    a real, definitive answer and IS cached (so we don't re-query it). `kingdom`
    etc. are nullable for that case.
    """

    __tablename__ = "gbif_taxon_cache"

    latin_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    match_type: Mapped[str | None] = mapped_column(String(20))   # EXACT|FUZZY|HIGHERRANK|NONE
    confidence: Mapped[int | None] = mapped_column(Integer)
    kingdom: Mapped[str | None] = mapped_column(String(30))      # Plantae|Fungi|Animalia|… (None on NONE)
    canonical: Mapped[str | None] = mapped_column(Text)          # accepted/canonical scientific name
    rank: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str | None] = mapped_column(String(20))       # ACCEPTED|SYNONYM|DOUBTFUL…
    usage_key: Mapped[int | None] = mapped_column(Integer)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
