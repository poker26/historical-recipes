import uuid
from datetime import datetime

from sqlalchemy import String, Text, Boolean, Float, DateTime, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DataQualityFinding(Base):
    """One durable data-quality finding emitted by a validator («линтер гербария»).

    A *finding* is the product of a pure-read validator that spotted an instance
    of a known failure class (identity incoherence, alias collision, monograph-as-
    recipe, …). Findings persist across sweeps so we get dedup + trend + sticky
    triage state, instead of a fresh wall of text each run:

    * dedup on ``(check_id, entity_id)`` — re-running a check updates the row,
      doesn't duplicate it;
    * a human ``dismiss`` (false positive) or ``confirmed`` sticks — the next
      sweep won't resurrect it to ``open``;
    * a finding whose problem is gone (not re-emitted by its check) is aged to
      ``stale``, so the table reflects the live state.

    ``entity_id`` is text (not an FK) so one table can hold findings for plants,
    recipes, oils, books and even qdrant point ids uniformly. Fixing is a separate
    explicit step — a validator never mutates.
    """

    __tablename__ = "data_quality_findings"
    __table_args__ = (UniqueConstraint("check_id", "entity_id", name="uq_dqf_check_entity"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    check_id: Mapped[str] = mapped_column(String(60))        # e.g. "alias.collision"
    severity: Mapped[str] = mapped_column(String(2))         # P0 / P1 / P2
    entity_type: Mapped[str] = mapped_column(String(20))     # plant | recipe | oil | book | qdrant
    entity_id: Mapped[str] = mapped_column(Text)             # UUID-as-text or other id

    title: Mapped[str] = mapped_column(Text)                 # human one-liner
    evidence: Mapped[dict | None] = mapped_column(JSONB)         # what we observed
    suggested_fix: Mapped[dict | None] = mapped_column(JSONB)    # structured, machine-applicable where possible
    auto_fixable: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # open → flagged; confirmed/dismissed → human triage (sticky); fixed → resolved;
    # stale → no longer emitted (problem gone) and not human-touched.
    status: Mapped[str] = mapped_column(String(12), default="open", server_default="open")

    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_by: Mapped[str | None] = mapped_column(String(60))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)

    # LLM adjudication layer (RFC-data-quality-llm): an LLM verdict on whether this
    # candidate is a real problem, with a grounded reason + a suggested action.
    # Cached on the row — re-runs skip already-adjudicated findings.
    llm_verdict: Mapped[str | None] = mapped_column(String(20))    # real | false_positive | uncertain
    llm_confidence: Mapped[float | None] = mapped_column(Float)    # 0..1
    llm_action: Mapped[str | None] = mapped_column(String(40))     # strip_alias | keep | delete | merge | …
    llm_reasoning: Mapped[str | None] = mapped_column(Text)        # short, must cite the finding's data
    llm_model: Mapped[str | None] = mapped_column(String(60))
    llm_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
