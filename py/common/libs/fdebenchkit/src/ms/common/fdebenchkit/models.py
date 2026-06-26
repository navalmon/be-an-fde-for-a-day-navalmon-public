# Copyright (c) Microsoft. All rights reserved.
"""FDEBench data models: the scoring contract.

All task scorers must produce a ``TaskResolutionResult``.
The Tier 1 framework computes ``Tier1Score`` from resolution +
efficiency + robustness. The ``FDEBenchComposite`` aggregates
across tasks into a single benchmark score.

Every model is immutable (FrozenBaseModel) to guarantee that
score objects cannot be mutated after creation.
"""

from ms.common.models.base import FrozenBaseModel


class TaskResolutionResult(FrozenBaseModel):
    """Result from a task-specific resolution scorer.

    Every task scorer (ticket_triage, document_extraction,
    workflow_orchestration) must return this structure.

    Invariants:
      - ``resolution`` is in [0, 100]
      - ``dimension_scores`` values are in [0.0, 1.0]
      - ``dimension_weights`` values are positive and sum to 1.0
      - ``resolution ≈ sum(w_i × d_i) × 100``
    """

    task_id: str
    resolution: float
    dimension_scores: dict[str, float]
    dimension_weights: dict[str, float]
    items_scored: int
    items_errored: int


class EfficiencyResult(FrozenBaseModel):
    """Efficiency score (task-agnostic, computed from HTTP metrics).

    Formula:
      efficiency     = 0.60 × latency_score + 0.40 × cost_score
      latency_score  = 0.5 × latency_p50_score + 0.5 × latency_p95_score

    Both sub-scores are in [0.0, 1.0]. ``latency_p50_score`` and
    ``latency_p95_score`` are reported separately so the leaderboard can
    show "your tail is fine but your typical request is slow" vs the
    other way round.
    """

    latency_score: float
    latency_p50_score: float
    latency_p95_score: float
    cost_score: float
    efficiency: float
    latency_p50_ms: float
    latency_p95_ms: float
    cost_per_item_usd: float


class RobustnessResult(FrozenBaseModel):
    """Robustness score (task-agnostic).

    Formula:
      robustness = 0.60 × adversarial_accuracy + 0.40 × api_resilience

    Both sub-scores are in [0.0, 1.0].
    """

    adversarial_accuracy: float
    api_resilience: float
    robustness: float
    probes_passed: int
    probes_total: int


class Tier1Score(FrozenBaseModel):
    """Tier 1 score for a single task.

    Formula:
      tier1 = 0.50 × resolution + 0.20 × efficiency + 0.30 × robustness

    All sub-scores are 0–100. The composite is 0–100.
    """

    task_id: str
    resolution: float
    efficiency: float
    robustness: float
    tier1: float


class FDEBenchComposite(FrozenBaseModel):
    """FDEBench composite score across all tasks.

    Formula (default):
      fdebench = mean(task1_tier1, task2_tier1, task3_tier1)

    Alternative (worst-task):
      fdebench = min(task1_tier1, task2_tier1, task3_tier1)
    """

    task_scores: list[Tier1Score]
    aggregation: str  # "mean" or "min"
    fdebench: float
