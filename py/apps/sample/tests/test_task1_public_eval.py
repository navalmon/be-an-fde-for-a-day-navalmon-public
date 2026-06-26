"""Public Task 1 scoring guard and error-analysis helper."""

import json
import sys
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any
from typing import cast

import pytest
from models import TriageRequest
from triage.service import TriageService

_PY_ROOT = Path(__file__).resolve().parents[3]
_FDEBENCHKIT_SRC = _PY_ROOT / "common" / "libs" / "fdebenchkit" / "src"
if str(_FDEBENCHKIT_SRC) not in sys.path:
    sys.path.insert(0, str(_FDEBENCHKIT_SRC))

_scorer = import_module("ms.common.fdebenchkit.scorers.ticket_triage")
_score_submission = cast(Callable[[list[dict[str, Any]], list[dict[str, Any]]], dict[str, Any]], _scorer.score_submission)
_score_ticket = cast(Callable[[dict[str, Any], dict[str, Any]], dict[str, float]], _scorer.score_ticket)


@pytest.mark.asyncio
async def test_task1_public_eval_resolution_guard() -> None:
    analysis = await analyze_task1_public_eval()

    assert analysis["resolution"] >= 85.0
    assert analysis["dimension_scores"]["category"] >= 0.95
    assert analysis["dimension_scores"]["routing"] >= 0.95


async def analyze_task1_public_eval() -> dict[str, Any]:
    inputs = json.loads((_PY_ROOT / "data" / "task1" / "public_eval_50.json").read_text(encoding="utf-8"))
    golds = json.loads((_PY_ROOT / "data" / "task1" / "public_eval_50_gold.json").read_text(encoding="utf-8"))
    service = TriageService()
    responses = [
        (await service.triage(TriageRequest.model_validate(item))).model_dump(mode="json")
        for item in inputs
    ]
    summary = _score_submission(responses, golds)
    gold_by_id = {str(item["ticket_id"]): item for item in golds}
    misses: list[dict[str, Any]] = []
    for response in responses:
        ticket_id = str(response["ticket_id"])
        gold = gold_by_id[ticket_id]
        scores = _score_ticket(response, gold)
        if scores["total"] < 1.0:
            misses.append(
                {
                    "ticket_id": ticket_id,
                    "total": scores["total"],
                    "scores": scores,
                    "predicted": _scored_fields(response),
                    "gold": _scored_fields(gold),
                }
            )

    misses.sort(key=lambda item: item["total"])
    return {
        "resolution": summary["resolution"],
        "dimension_scores": summary["dimension_scores"],
        "misses": misses,
    }


def _scored_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in ("category", "priority", "assigned_team", "needs_escalation", "missing_information")
    }
