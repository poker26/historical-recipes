from datetime import datetime

from sqlalchemy import String, Integer, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class InatTaxonCache(Base):
    """Latin name → iNaturalist Russian common name + taxon_id, cached.

    The identify flow needs a Russian name for EVERY candidate species, including
    ones not in our corpus (whose name would otherwise show as the English
    PlantNet common name — confusing next to corpus rows that are Russian). iNat's
    ``preferred_common_name`` at ``locale=ru`` is the canonical source, but it is
    a live external call under a ≤60 req/min budget, so we cache the resolution
    keyed on the genus+species :func:`plant_matching._latin_key` (the SAME key the
    herbarium dedups on — "Gratiola officinalis L." and "gratiola officinalis"
    collapse to one row).

    A row is written only for a DEFINITIVE iNat answer (a taxon match, or a
    confirmed no-match). Transient failures (HTTP error / 429) are never cached,
    so the next identify retries. ``name_ru`` / ``taxon_id`` are nullable: a
    cached null means "iNat has no Russian name / no taxon for this Latin" — a
    real answer we should not keep re-querying.
    """

    __tablename__ = "inat_taxon_cache"

    latin_key: Mapped[str] = mapped_column(String(120), primary_key=True)  # genus+species, lowercased, author-stripped
    name_ru: Mapped[str | None] = mapped_column(Text)                       # iNat preferred_common_name @ locale=ru (None = none exists)
    taxon_id: Mapped[int | None] = mapped_column(Integer)                   # iNat taxon id (None = no taxon matched)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
