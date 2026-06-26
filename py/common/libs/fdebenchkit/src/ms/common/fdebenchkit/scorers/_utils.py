"""Shared scoring utilities used by all task scorers.

Provides text normalization and set-based F1 helpers that are reused
across space signal triage, document extraction, and workflow orchestration
scoring modules.
"""

import re
from collections.abc import Sequence


def normalize_text(text: str | None) -> str:
    """Lowercase, collapse whitespace, strip punctuation edges.

    Returns empty string for None input.
    """
    if text is None:
        return ""
    text = str(text).strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .;:,")


def set_f1(predicted: set[str], gold: set[str]) -> float:
    """Compute F1 between two sets of normalized strings.

    Returns 1.0 when both sets are empty (agreement on absence).
    """
    if not gold and not predicted:
        return 1.0
    if not gold or not predicted:
        return 0.0

    tp = len(predicted & gold)
    precision = tp / len(predicted)
    recall = tp / len(gold)

    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def token_f1(candidate: str, gold: str) -> float:
    """Token-level F1 between two strings. Fully deterministic.

    Splits both strings into word tokens after normalization,
    then computes precision/recall/F1 on the token sets.
    Friendlier than exact string match for free-text extraction fields:
    "type 2 diabetes mellitus" vs "type 2 diabetes" → 0.86 (not 0.0).

    Returns 1.0 when both are empty, 0.0 when only one is empty.
    """
    c_tokens = set(normalize_text(candidate).split())
    g_tokens = set(normalize_text(gold).split())
    if not g_tokens and not c_tokens:
        return 1.0
    if not g_tokens or not c_tokens:
        return 0.0
    tp = len(c_tokens & g_tokens)
    precision = tp / len(c_tokens)
    recall = tp / len(g_tokens)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def best_token_f1(candidate_items: set[str], gold_items: set[str]) -> float:
    """Soft set F1 using token-level matching between set elements.

    For each gold item, finds the best token F1 match among candidate items.
    For each candidate item, finds the best token F1 match among gold items.
    Computes precision (avg best match per candidate) and recall (avg best
    match per gold), then returns their harmonic mean.

    This replaces exact-string set_f1 for free-text extraction fields where
    minor wording differences should not produce 0.0 scores.

    Returns 1.0 when both sets are empty, 0.0 when only one is empty.
    """
    if not gold_items and not candidate_items:
        return 1.0
    if not gold_items or not candidate_items:
        return 0.0

    # Recall: for each gold item, best match among candidates
    recall_scores = [max(token_f1(c, g) for c in candidate_items) for g in gold_items]
    recall = sum(recall_scores) / len(recall_scores)

    # Precision: for each candidate item, best match among golds
    precision_scores = [max(token_f1(c, g) for g in gold_items) for c in candidate_items]
    precision = sum(precision_scores) / len(precision_scores)

    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def macro_f1(
    candidates: Sequence[str],
    golds: Sequence[str],
    label_set: Sequence[str],
) -> float:
    """Macro-averaged F1 over a closed set of class labels.

    Computes per-class precision, recall, and F1, then averages F1 across
    all classes that appear in either golds or candidates. Classes absent
    from both are excluded from the average.
    """
    candidate_norm = [normalize_text(c) for c in candidates]
    gold_norm = [normalize_text(g) for g in golds]
    label_norm = [normalize_text(label) for label in label_set]

    f1_scores: list[float] = []
    for label in label_norm:
        tp = sum(1 for c, g in zip(candidate_norm, gold_norm, strict=False) if c == label and g == label)
        fp = sum(1 for c, g in zip(candidate_norm, gold_norm, strict=False) if c == label and g != label)
        fn = sum(1 for c, g in zip(candidate_norm, gold_norm, strict=False) if c != label and g == label)

        if tp + fp + fn == 0:
            continue

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        f1_scores.append(f1)

    return sum(f1_scores) / len(f1_scores) if f1_scores else 0.0


def binary_f1(candidates: Sequence[bool], golds: Sequence[bool]) -> float:
    """F1 for the positive class (True) in binary classification.

    Returns 1.0 when there are no positive cases in either gold or
    predictions (perfect agreement on absence).
    """
    tp = sum(1 for c, g in zip(candidates, golds, strict=False) if c and g)
    fp = sum(1 for c, g in zip(candidates, golds, strict=False) if c and not g)
    fn = sum(1 for c, g in zip(candidates, golds, strict=False) if not c and g)

    if tp + fp + fn == 0:
        return 1.0

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0

    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)
