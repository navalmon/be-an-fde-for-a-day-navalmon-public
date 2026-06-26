"""Deterministic scoring for Task 1: Support Ticket Triage.

Scores 5 classification dimensions with proper statistical metrics:
  1. category      : macro F1 across 8 categories          (24%)
  2. priority      : mean ordinal partial credit (P1-P4)   (24%)
  3. routing       : macro F1 across 7 teams               (24%)
  4. missing_info  : mean per-ticket set F1                 (17%)
  5. escalation    : binary F1 on the positive class        (12%)

All weights sum to 1.0 for FDEBench Tier 1 consistency.
Efficiency (latency + cost) is scored separately by the runner at the
Tier 1 composite level, not baked into per-task resolution.

No free text. No LLM. No randomness. Fully auditable.
"""

from collections.abc import Sequence

from ms.common.fdebenchkit.scorers._utils import binary_f1
from ms.common.fdebenchkit.scorers._utils import macro_f1
from ms.common.fdebenchkit.scorers._utils import normalize_text

# Weights (sum to 1.0 for FDEBench Tier 1 consistency)
# Rescaled from the original 85-point scheme (÷ 0.85) so all tasks
# use the same contract: resolution weights sum to 1.0, efficiency
# is a separate layer.

WEIGHT_CATEGORY = 0.24
WEIGHT_PRIORITY = 0.24
WEIGHT_ROUTING = 0.24
WEIGHT_MISSING_INFO = 0.17
WEIGHT_ESCALATION = 0.11

# Closed label sets

CATEGORIES = (
    "Crew Access & Biometrics",
    "Hull & Structural Systems",
    "Communications & Navigation",
    "Flight Software & Instruments",
    "Threat Detection & Containment",
    "Telemetry & Data Banks",
    "Mission Briefing Request",
    "Not a Mission Signal",
)

TEAMS = (
    "Crew Identity & Airlock Control",
    "Spacecraft Systems Engineering",
    "Deep Space Communications",
    "Mission Software Operations",
    "Threat Response Command",
    "Telemetry & Data Core",
    "None",
)

# Priority distance table

_PRIORITY_ORDER = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}


# Use normalize_text from _utils as the local normalizer
_normalize = normalize_text


# Submission-level aggregate metrics
# macro_f1 and binary_f1 are imported from _utils


# Per-ticket dimension scorers (for transparency / error analysis)


def score_category(candidate: str, gold: str) -> float:
    """Per-ticket category match (0.0 or 1.0). Submission-level uses macro F1."""
    return 1.0 if _normalize(candidate) == _normalize(gold) else 0.0


def score_priority(candidate: str, gold: str) -> float:
    """Priority match with partial credit for off-by-one.

    Returns:
        1.0  exact match
        0.67 off by one level (e.g., P2 vs P3)
        0.0  off by two or more levels, or invalid label
    """
    c_idx = _PRIORITY_ORDER.get(candidate.strip().upper())
    g_idx = _PRIORITY_ORDER.get(gold.strip().upper())

    if c_idx is None or g_idx is None:
        return 0.0

    distance = abs(c_idx - g_idx)
    if distance == 0:
        return 1.0
    if distance == 1:
        return 0.67
    return 0.0


def score_routing(candidate: str, gold: str) -> float:
    """Per-ticket routing match (0.0 or 1.0). Submission-level uses macro F1."""
    return 1.0 if _normalize(candidate) == _normalize(gold) else 0.0


def score_missing_info(candidate: Sequence[str], gold: Sequence[str]) -> float:
    """Set F1 for missing_information field.

    Both inputs are lists of constrained vocabulary terms (fixed label set).
    Returns F1 in [0.0, 1.0]. Empty gold + empty candidate = 1.0.
    """
    c_set = {_normalize(item) for item in candidate}
    g_set = {_normalize(item) for item in gold}

    if not g_set and not c_set:
        return 1.0
    if not g_set or not c_set:
        return 0.0

    true_positives = len(c_set & g_set)
    precision = true_positives / len(c_set) if c_set else 0.0
    recall = true_positives / len(g_set) if g_set else 0.0

    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def score_escalation(candidate: bool, gold: bool) -> float:
    """Per-ticket escalation match (0.0 or 1.0). Submission-level uses binary F1."""
    return 1.0 if candidate == gold else 0.0


def _coerce_bool(value: object) -> bool:
    """Safely coerce a value to boolean.

    Handles string representations like ``"true"``/``"false"`` that
    participants may return from their API.  Python's ``bool("false")``
    is ``True``, so we need explicit string handling.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    if isinstance(value, int):
        return value != 0
    return False


# Per-ticket scorer


def score_ticket(candidate: dict[str, object], gold: dict[str, object]) -> dict[str, float]:
    """Score a single ticket response against its gold answer.

    Returns per-dimension scores (0.0–1.0 each) and a weighted total.
    These are per-ticket accuracy scores used for error analysis.

    Note: the submission-level ``score_submission`` uses macro F1 for
    category and routing, and binary F1 for escalation. The per-ticket
    total here is an approximation useful for identifying which specific
    tickets were answered incorrectly.
    """
    category = score_category(
        str(candidate.get("category", "")),
        str(gold.get("category", "")),
    )
    priority = score_priority(
        str(candidate.get("priority", "")),
        str(gold.get("priority", "")),
    )
    routing = score_routing(
        str(candidate.get("assigned_team", "")),
        str(gold.get("assigned_team", "")),
    )

    candidate_missing = candidate.get("missing_information")
    gold_missing = gold.get("missing_information")
    missing_info = score_missing_info(
        candidate_missing if isinstance(candidate_missing, list) else [],
        gold_missing if isinstance(gold_missing, list) else [],
    )

    candidate_esc = candidate.get("needs_escalation")
    gold_esc = gold.get("needs_escalation")
    escalation = score_escalation(
        _coerce_bool(candidate_esc),
        _coerce_bool(gold_esc),
    )

    total = (
        WEIGHT_CATEGORY * category
        + WEIGHT_PRIORITY * priority
        + WEIGHT_ROUTING * routing
        + WEIGHT_MISSING_INFO * missing_info
        + WEIGHT_ESCALATION * escalation
    )

    return {
        "category": category,
        "priority": priority,
        "routing": routing,
        "missing_info": missing_info,
        "escalation": escalation,
        "total": total,
    }


# Full submission scorer


def score_submission(
    candidate_responses: Sequence[dict[str, object]],
    gold_answers: Sequence[dict[str, object]],
) -> dict[str, object]:
    """Score a full submission and produce the final score.

    Dimension scoring methods (submission level):
      - category:     macro F1 across the 8 category labels
      - priority:     mean per-ticket ordinal partial credit
      - routing:      macro F1 across the 7 team labels
      - missing_info: mean per-ticket set F1
      - escalation:   binary F1 on the positive class (needs_escalation=True)

    Returns:
      - resolution: 0–100 (weighted composite, same key as Tasks 2 and 3)
      - dimension_scores: per-dimension F1 or mean score (0.0–1.0)
      - tickets_scored / tickets_errored
      - per_ticket: full per-ticket breakdown for error analysis
    """
    if not gold_answers:
        msg = "Gold answer set is empty"
        raise ValueError(msg)

    per_ticket: list[dict[str, float]] = []
    errors: list[str] = []

    # Collect parallel arrays for submission-level metrics
    all_category_candidates: list[str] = []
    all_category_golds: list[str] = []
    all_routing_candidates: list[str] = []
    all_routing_golds: list[str] = []
    all_escalation_candidates: list[bool] = []
    all_escalation_golds: list[bool] = []
    all_priority_scores: list[float] = []
    all_missing_info_scores: list[float] = []

    for gold in gold_answers:
        tid = str(gold.get("ticket_id", ""))
        candidate = next(
            (c for c in candidate_responses if str(c.get("ticket_id", "")) == tid),
            None,
        )
        if candidate is None:
            errors.append(f"Missing response for ticket {tid}")
            per_ticket.append(
                {
                    "category": 0.0,
                    "priority": 0.0,
                    "routing": 0.0,
                    "missing_info": 0.0,
                    "escalation": 0.0,
                    "total": 0.0,
                }
            )
            # Missing tickets contribute empty strings / False → guaranteed mismatches
            all_category_candidates.append("")
            all_category_golds.append(_normalize(str(gold.get("category", ""))))
            all_routing_candidates.append("")
            all_routing_golds.append(_normalize(str(gold.get("assigned_team", ""))))
            all_escalation_candidates.append(False)
            all_escalation_golds.append(_coerce_bool(gold.get("needs_escalation")))
            all_priority_scores.append(0.0)
            all_missing_info_scores.append(0.0)
        else:
            ticket_result = score_ticket(candidate, gold)
            per_ticket.append(ticket_result)

            all_category_candidates.append(_normalize(str(candidate.get("category", ""))))
            all_category_golds.append(_normalize(str(gold.get("category", ""))))
            all_routing_candidates.append(_normalize(str(candidate.get("assigned_team", ""))))
            all_routing_golds.append(_normalize(str(gold.get("assigned_team", ""))))
            all_escalation_candidates.append(_coerce_bool(candidate.get("needs_escalation")))
            all_escalation_golds.append(_coerce_bool(gold.get("needs_escalation")))
            all_priority_scores.append(ticket_result["priority"])
            all_missing_info_scores.append(ticket_result["missing_info"])

    n = len(per_ticket)

    # Submission-level dimension scores using proper classification metrics
    category_score = macro_f1(all_category_candidates, all_category_golds, CATEGORIES)
    priority_score = sum(all_priority_scores) / n if n > 0 else 0.0
    routing_score = macro_f1(all_routing_candidates, all_routing_golds, TEAMS)
    missing_info_score = sum(all_missing_info_scores) / n if n > 0 else 0.0
    escalation_score = binary_f1(all_escalation_candidates, all_escalation_golds)

    weighted_total = (
        WEIGHT_CATEGORY * category_score
        + WEIGHT_PRIORITY * priority_score
        + WEIGHT_ROUTING * routing_score
        + WEIGHT_MISSING_INFO * missing_info_score
        + WEIGHT_ESCALATION * escalation_score
    )

    # Scale to 0–100 (resolution, consistent with Tasks 2 and 3)
    resolution = round(weighted_total * 100, 1)

    return {
        "resolution": resolution,
        "tickets_scored": n - len(errors),
        "tickets_errored": len(errors),
        "dimension_scores": {
            "category": round(category_score, 4),
            "priority": round(priority_score, 4),
            "routing": round(routing_score, 4),
            "missing_info": round(missing_info_score, 4),
            "escalation": round(escalation_score, 4),
        },
        "per_ticket": per_ticket,
        "errors": errors,
    }
