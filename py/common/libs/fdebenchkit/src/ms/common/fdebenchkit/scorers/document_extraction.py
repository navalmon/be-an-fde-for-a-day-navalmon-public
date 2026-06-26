# Copyright (c) Microsoft. All rights reserved.
"""Task 2 scorer: OCR Document Extraction (hybrid information + fidelity).

Evaluates structured data extraction from document images against human-verified
gold data from the Omni OCR Benchmark. Uses a two-dimensional scoring approach:

**Information Accuracy (70%)**: Did the model extract the *right* data?
    - Strings  → token F1 after value normalization (strips $, commas, %, etc.)
    - Numbers  → exact match with 1% relative tolerance
    - Bools    → exact match
    - Lists    → soft set F1 with fuzzy element alignment
    - Dicts    → recursive field-mean

**Text Fidelity (30%)**: Did the model preserve *exact* formatting?
    - Strings  → exact match after whitespace/case normalization only
    - Numbers  → exact match (same as information)
    - Bools    → exact match (same as information)
    - Lists    → strict set F1 (no fuzzy matching)
    - Dicts    → recursive field-mean

Per-field composite:
    field_score = 0.7 × information_accuracy + 0.3 × text_fidelity

Document-level: mean of all field scores.

Submission-level resolution (0–100):
    resolution = (0.7 × mean_info + 0.3 × mean_fidelity) × 100
"""

import logging
import re
from collections.abc import Sequence
from typing import Any

from ms.common.fdebenchkit.scorers._utils import best_token_f1
from ms.common.fdebenchkit.scorers._utils import normalize_text
from ms.common.fdebenchkit.scorers._utils import set_f1
from ms.common.fdebenchkit.scorers._utils import token_f1

logger = logging.getLogger(__name__)

# Dimension weights

WEIGHT_INFORMATION = 0.7
WEIGHT_FIDELITY = 0.3

DIMENSION_WEIGHTS: dict[str, float] = {
    "information_accuracy": WEIGHT_INFORMATION,
    "text_fidelity": WEIGHT_FIDELITY,
}

# Type alias for the dual-score tuple
Score = tuple[float, float]  # (information_accuracy, text_fidelity)


# Value normalization for information matching

_CURRENCY_RE = re.compile(r"[$€£¥₹]")
_NUM_COMMA_RE = re.compile(r"(\d),(\d)")


def _normalize_for_information(text: str) -> str:
    """Aggressive normalization for information matching.

    Strips formatting artifacts that don't change meaning:
    currency symbols, digit-grouping commas, percent signs.
    "$1,234.56" and "1234.56" become equivalent.
    """
    s = normalize_text(text)
    s = _CURRENCY_RE.sub("", s)
    # Remove digit-grouping commas: "1,234,567" → "1234567"
    s = _NUM_COMMA_RE.sub(r"\1\2", s)
    s = _NUM_COMMA_RE.sub(r"\1\2", s)  # second pass for "1,234,567"
    s = s.replace("%", "")
    return re.sub(r"\s+", " ", s).strip()


# Helpers


def _harmonic_mean(a: float, b: float) -> float:
    """F1-style harmonic mean of two values."""
    if a + b == 0:
        return 0.0
    return 2 * a * b / (a + b)


def _score_number(predicted: Any, gold: float | int) -> float:
    """Score a numeric value (shared by both dimensions)."""
    try:
        pred_num = float(predicted)
        gold_num = float(gold)
        if gold_num == 0:
            return 1.0 if pred_num == 0 else 0.0
        if abs(pred_num - gold_num) / max(abs(gold_num), 1e-9) < 0.01:
            return 1.0
        return 0.0
    except (ValueError, TypeError):
        return 0.0


# Core recursive scorer (dual-dimension)


def score_value(predicted: Any, gold: Any) -> Score:
    """Score a single value recursively.

    Returns:
        (information_accuracy, text_fidelity), each in [0.0, 1.0].
    """
    # Both null/None → perfect on both dimensions
    if gold is None and predicted is None:
        return (1.0, 1.0)

    # One null, one not → complete miss
    if gold is None or predicted is None:
        return (0.0, 0.0)

    # String
    if isinstance(gold, str):
        pred_str = str(predicted)
        # Information: token F1 on aggressively normalized text
        info = token_f1(
            _normalize_for_information(pred_str),
            _normalize_for_information(gold),
        )
        # Fidelity: exact match on lightly normalized text
        fidelity = 1.0 if normalize_text(pred_str) == normalize_text(gold) else 0.0
        return (info, fidelity)

    # Number (int/float, not bool)
    if isinstance(gold, (int, float)) and not isinstance(gold, bool):
        match = _score_number(predicted, gold)
        return (match, match)

    # Boolean
    if isinstance(gold, bool):
        match = 1.0 if predicted == gold else 0.0
        return (match, match)

    # List
    if isinstance(gold, list):
        return _score_list(
            predicted if isinstance(predicted, list) else [],
            gold,
        )

    # Dict
    if isinstance(gold, dict):
        return score_document(
            predicted if isinstance(predicted, dict) else {},
            gold,
        )

    # Fallback: coerce to string
    return score_value(str(predicted), str(gold))


def _score_list(predicted: list[Any], gold: list[Any]) -> Score:
    """Score two lists (dual dimension).

    String lists use efficient set-based helpers.
    Object/mixed lists use best-match alignment based on the combined score.
    """
    if not gold and not predicted:
        return (1.0, 1.0)
    if not gold or not predicted:
        return (0.0, 0.0)

    # Fast path for primitive string lists
    if all(isinstance(g, str) for g in gold) and all(isinstance(p, str) for p in predicted):
        # Information: fuzzy set F1 on aggressively normalized items
        info_pred = {_normalize_for_information(str(p)) for p in predicted}
        info_gold = {_normalize_for_information(str(g)) for g in gold}
        info = best_token_f1(info_pred, info_gold)
        # Fidelity: strict set F1 on lightly normalized items
        fidelity_pred = {normalize_text(str(p)) for p in predicted}
        fidelity_gold = {normalize_text(str(g)) for g in gold}
        fidelity = set_f1(fidelity_pred, fidelity_gold)
        return (info, fidelity)

    # Object/mixed lists: best-match alignment
    # Use combined weighted score for alignment, then report both dims.
    def _best_match(anchor: Any, candidates: list[Any]) -> Score:
        best_combined = -1.0
        best_info = 0.0
        best_fidelity = 0.0
        for c in candidates:
            i, f = score_value(c, anchor)
            combined = WEIGHT_INFORMATION * i + WEIGHT_FIDELITY * f
            if combined > best_combined:
                best_combined = combined
                best_info = i
                best_fidelity = f
        return (best_info, best_fidelity)

    # Recall: for each gold element, best match in predicted
    recall_info: list[float] = []
    recall_fidelity: list[float] = []
    for g in gold:
        i, f = _best_match(g, predicted)
        recall_info.append(i)
        recall_fidelity.append(f)

    # Precision: for each predicted element, best match in gold
    prec_info: list[float] = []
    prec_fidelity: list[float] = []
    for p in predicted:
        i, f = _best_match(p, gold)
        prec_info.append(i)
        prec_fidelity.append(f)

    info_f1 = _harmonic_mean(
        sum(prec_info) / len(prec_info),
        sum(recall_info) / len(recall_info),
    )
    fidelity_f1 = _harmonic_mean(
        sum(prec_fidelity) / len(prec_fidelity),
        sum(recall_fidelity) / len(recall_fidelity),
    )
    return (info_f1, fidelity_f1)


def score_document(predicted: dict[str, Any], gold: dict[str, Any]) -> Score:
    """Score a predicted JSON document against gold.

    Returns per-field mean of (information_accuracy, text_fidelity).
    Fields in gold but not in predicted → (0, 0). Extra predicted fields → ignored.
    """
    if not gold:
        return (1.0, 1.0) if not predicted else (0.0, 0.0)

    info_scores: list[float] = []
    fidelity_scores: list[float] = []
    for key, gold_value in gold.items():
        if key in ("document_id", "difficulty"):
            continue
        pred_value = predicted.get(key)
        info, fidelity = score_value(pred_value, gold_value)
        info_scores.append(info)
        fidelity_scores.append(fidelity)

    if not info_scores:
        return (1.0, 1.0)

    return (
        sum(info_scores) / len(info_scores),
        sum(fidelity_scores) / len(fidelity_scores),
    )


# Scorer interface matching FDEBench convention


def score_submission(
    candidate_responses: Sequence[dict[str, Any]],
    gold_answers: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Score a full Task 2 submission (FDEBench-compatible interface).

    Returns:
        resolution: 0-100 weighted composite (0.7·info + 0.3·fidelity)
        dimension_scores: {information_accuracy: float, text_fidelity: float}
        documents_scored / documents_errored
        per_document: list of per-doc dimension scores
    """
    if not gold_answers:
        msg = "Gold answer set is empty"
        raise ValueError(msg)

    per_document: list[dict[str, Any]] = []
    errors: list[str] = []

    for gold in gold_answers:
        doc_id = str(gold.get("document_id", ""))
        candidate = next(
            (c for c in candidate_responses if str(c.get("document_id", "")) == doc_id),
            None,
        )
        if candidate is None:
            errors.append(f"Missing response for document {doc_id}")
            per_document.append(
                {
                    "document_id": doc_id,
                    "information_accuracy": 0.0,
                    "text_fidelity": 0.0,
                }
            )
            continue

        info, fidelity = score_document(candidate, gold)
        per_document.append(
            {
                "document_id": doc_id,
                "information_accuracy": info,
                "text_fidelity": fidelity,
            }
        )

    error_ids = {e.split()[-1] for e in errors}
    scored_docs = [d for d in per_document if d["document_id"] not in error_ids]

    mean_info = sum(d["information_accuracy"] for d in per_document) / len(per_document) if per_document else 0.0
    mean_fidelity = sum(d["text_fidelity"] for d in per_document) / len(per_document) if per_document else 0.0

    resolution = (WEIGHT_INFORMATION * mean_info + WEIGHT_FIDELITY * mean_fidelity) * 100

    return {
        "resolution": round(resolution, 1),
        "dimension_scores": {
            "information_accuracy": mean_info,
            "text_fidelity": mean_fidelity,
        },
        "documents_scored": len(scored_docs),
        "documents_errored": len(errors),
        "per_document": per_document,
        "errors": errors,
    }
