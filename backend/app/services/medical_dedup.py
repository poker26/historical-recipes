"""Indication-vocabulary dedup — now a thin shim over the generic ``vocab_dedup``.

The dedup logic that used to live here was generalized (so it also collapses the
ACTION and COMPOUND vocabularies, which differ in scalar-vs-array linking and which
surface-form fields they carry) into ``app.services.vocab_dedup``. This module is kept
for its import path: callers still do ``from app.services.medical_dedup import
dedup_indications``. See ``vocab_dedup`` for the engine, the merge-signal discipline,
and the review-first ``apply`` contract.
"""

from app.services.vocab_dedup import (  # noqa: F401
    INDICATION_SPEC,
    dedup_indications,
    dedup_vocabulary,
)
