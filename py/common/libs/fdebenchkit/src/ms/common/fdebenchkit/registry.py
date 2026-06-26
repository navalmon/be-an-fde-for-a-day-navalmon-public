"""Task registry for FDEBench Tier 1 functional scoring.

Each task declares its endpoint, identifier key, response contract, and
dimension weights. The runner uses this registry to load datasets,
run preflight validation, and dispatch to the correct deterministic scorer.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ms.common.fdebenchkit.scorers import document_extraction as scoring_document_extraction
from ms.common.fdebenchkit.scorers import ticket_triage as ticket_triage_scoring
from ms.common.fdebenchkit.scorers import workflow_orchestration as scoring_workflow_orchestration

ScorerFn = Callable[[list[dict[str, Any]], list[dict[str, Any]]], dict[str, Any]]


@dataclass(frozen=True)
class TaskDefinition:
    """Static configuration for one deterministic benchmark task."""

    task_id: str
    label: str
    endpoint_path: str
    request_id_key: str
    item_label: str
    response_required_keys: frozenset[str]
    dimension_weights: dict[str, float]
    scorer: ScorerFn
    # Per-task latency normalization thresholds.
    #
    # Latency scoring is a 50/50 blend of normalized P50 and P95 against
    # the four per-task thresholds below. Calibration rationale:
    #   * P50 best  = budget-derived target for a well-engineered solution
    #     (one LLM call, mini model, same region, warm pool; see
    #     ``learnings/l1improvements.md`` §2.2.3).
    #   * P50 worst = 4 × P50 best (replaces the prior 10× ramp that put
    #     realistic submissions on the flat tail).
    #   * P95 best  = 3 × P50 best (Azure OpenAI empirical tail/median
    #     ratio).
    #   * P95 worst ≈ cohort-1 P75 of observed P95 (so the slowest
    #     quartile of real submissions sits in the discriminating part
    #     of the ramp). Source: ``analysis/out/tasks_anon.csv``
    #     (snapshot 2026-04-26).
    #
    # Defaults below match Task 1 (Triage); sub-classes override them.
    latency_p50_best_ms: float = 400.0
    latency_p50_worst_ms: float = 1600.0
    latency_p95_best_ms: float = 1200.0
    latency_p95_worst_ms: float = 4500.0

    @property
    def latency_best_ms(self) -> float:  # pragma: no cover - back-compat shim
        """Deprecated: returns ``latency_p95_best_ms`` for legacy callers."""
        return self.latency_p95_best_ms

    @property
    def latency_worst_ms(self) -> float:  # pragma: no cover - back-compat shim
        """Deprecated: returns ``latency_p95_worst_ms`` for legacy callers."""
        return self.latency_p95_worst_ms


@dataclass(frozen=True)
class TaskRun:
    """Concrete dataset bundle for one task run."""

    definition: TaskDefinition
    input_items: list[dict[str, Any]]
    gold_items: list[dict[str, Any]]

    @property
    def smoke_request(self) -> dict[str, Any]:
        """Return the first candidate request payload for preflight validation."""
        if not self.input_items:
            msg = f"Task {self.definition.task_id} has no input items"
            raise ValueError(msg)
        return self.input_items[0]


TASK_DEFINITIONS: dict[str, TaskDefinition] = {
    "ticket_triage": TaskDefinition(
        task_id="ticket_triage",
        label="Task 1: Ticket Triage",
        endpoint_path="/triage",
        request_id_key="ticket_id",
        item_label="ticket",
        response_required_keys=frozenset(
            {
                "ticket_id",
                "category",
                "priority",
                "assigned_team",
                "needs_escalation",
                "missing_information",
                "next_best_action",
                "remediation_steps",
            }
        ),
        dimension_weights={
            "category": ticket_triage_scoring.WEIGHT_CATEGORY,
            "priority": ticket_triage_scoring.WEIGHT_PRIORITY,
            "routing": ticket_triage_scoring.WEIGHT_ROUTING,
            "missing_info": ticket_triage_scoring.WEIGHT_MISSING_INFO,
            "escalation": ticket_triage_scoring.WEIGHT_ESCALATION,
        },
        scorer=ticket_triage_scoring.score_submission,
        # Calibrated from cohort-1 empirical distribution (n = 22 successful
        # runs, ``analysis/out/tasks_anon.csv``):
        #   min P95 = 1 458 ms  → P95 best 1 500 (fastest known earns ~1.0)
        #   P75 P95 = 4 208 ms  → P95 worst 4 200 (slowest quartile in ramp)
        #   P50 best/worst = P95 best / 3, ×4 (AOAI tail/median ratio).
        latency_p50_best_ms=500.0,
        latency_p50_worst_ms=2000.0,
        latency_p95_best_ms=1500.0,
        latency_p95_worst_ms=4200.0,
    ),
    "document_extraction": TaskDefinition(
        task_id="document_extraction",
        label="Task 2: Document Extraction",
        endpoint_path="/extract",
        request_id_key="document_id",
        item_label="document",
        response_required_keys=frozenset({"document_id"}),
        dimension_weights=scoring_document_extraction.DIMENSION_WEIGHTS,
        scorer=scoring_document_extraction.score_submission,
        # Calibrated from cohort-1 empirical distribution (n = 20 successful
        # runs, ``analysis/out/tasks_anon.csv``):
        #   min P95 = 7 107 ms  → P95 best 7 100 (fastest known earns ~1.0)
        #   P75 P95 = 18 900 ms → P95 worst 19 000 (slowest quartile in ramp).
        # P95 best was previously the budget number (5 500 ms), but no
        # cohort-1 submission landed below 7 107 ms; grounding in measured
        # data is fairer than aspirational vision-mini budget math.
        # Revisit after first cohort-2 calibration run (D-7).
        latency_p50_best_ms=2400.0,
        latency_p50_worst_ms=7200.0,
        latency_p95_best_ms=7100.0,
        latency_p95_worst_ms=19000.0,
    ),
    "workflow_orchestration": TaskDefinition(
        task_id="workflow_orchestration",
        label="Task 3: Workflow Orchestration",
        endpoint_path="/orchestrate",
        request_id_key="task_id",
        item_label="workflow",
        response_required_keys=frozenset({"task_id", "status", "steps_executed"}),
        dimension_weights=scoring_workflow_orchestration.DIMENSION_WEIGHTS,
        scorer=scoring_workflow_orchestration.score_submission,
        # Calibrated from cohort-1 empirical distribution (n = 20 successful
        # runs, ``analysis/out/tasks_anon.csv``). The orchestrate distribution
        # is bimodal: 4 of 6 participants hit ≤ 1 300 ms P95 in their best
        # run (parallel tool calls / efficient orchestration), 2 stayed
        # > 5 600 ms (sequential-only). Anchors:
        #   median P95 of fastest-per-participant = 1 200 ms
        #     → P95 best 1 500 (a slight cushion above the median
        #       fastest; rewards the parallel-tool pattern that 4 of 6
        #       demonstrated, with margin for cohort-2 variance)
        #   P75 P95 = 7 960 ms → P95 worst 8 000 (sequential-only sits
        #     in the discriminating part of the ramp instead of clamping).
        latency_p50_best_ms=500.0,
        latency_p50_worst_ms=2700.0,
        latency_p95_best_ms=1500.0,
        latency_p95_worst_ms=8000.0,
    ),
}


def get_task_definition(task_id: str) -> TaskDefinition:
    """Return one task definition or raise for an unknown task id."""
    try:
        return TASK_DEFINITIONS[task_id]
    except KeyError as exc:
        msg = f"Unknown functional scorer task: {task_id}"
        raise ValueError(msg) from exc
