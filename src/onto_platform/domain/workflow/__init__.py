"""Lightweight, spiral ontology engineering workflow.

The package is deliberately small: rounds, competency questions (CQs),
reuse decisions, sample instances, acceptance runs and review records are
*platform definitions*, so they live in the generic document repository next
to the ontology itself.  The semantics that must stay stable (what a passing
acceptance run means, when a step is done, what blocks a publish) are pure
functions in :mod:`model`, which makes them easy to test without a runtime.
"""

from .model import (
    CQ_PRIORITIES,
    CQ_STATUSES,
    CQ_TYPES,
    EXPECTED_KINDS,
    QUERY_KINDS,
    ROUND_STATUSES,
    STEP_TITLES,
    acceptance_summary,
    canonical_hash,
    compute_steps,
    evaluate_expectation,
    evaluate_query,
    normalize_cq,
    normalize_reuse,
    normalize_round,
    normalize_sample,
    ontology_fingerprint,
    sample_fingerprint,
)

__all__ = [
    "CQ_PRIORITIES",
    "CQ_STATUSES",
    "CQ_TYPES",
    "EXPECTED_KINDS",
    "QUERY_KINDS",
    "ROUND_STATUSES",
    "STEP_TITLES",
    "acceptance_summary",
    "canonical_hash",
    "compute_steps",
    "evaluate_expectation",
    "evaluate_query",
    "normalize_cq",
    "normalize_reuse",
    "normalize_round",
    "normalize_sample",
    "ontology_fingerprint",
    "sample_fingerprint",
]
