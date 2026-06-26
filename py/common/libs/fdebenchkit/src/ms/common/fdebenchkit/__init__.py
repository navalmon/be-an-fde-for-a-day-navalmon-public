# Copyright (c) Microsoft. All rights reserved.
"""FDEBench Tier 1 scoring library (participant copy).

Re-exports the scoring contract types and the resolution-result
validator so callers can write::

    from ms.common.fdebenchkit import TaskResolutionResult
    from ms.common.fdebenchkit import validate_resolution_result

instead of reaching into the ``models`` / ``weights`` modules directly.

This is a flat, judge-stripped copy of the platform's
``ms.fde.fdebenchkit.tier1`` namespace; Tier 2 (LLM-as-judge) lives only
in the platform repo because it requires Azure OpenAI credentials and is
not part of the public scoreboard.
"""

from ms.common.fdebenchkit.models import EfficiencyResult as EfficiencyResult
from ms.common.fdebenchkit.models import FDEBenchComposite as FDEBenchComposite
from ms.common.fdebenchkit.models import RobustnessResult as RobustnessResult
from ms.common.fdebenchkit.models import TaskResolutionResult as TaskResolutionResult
from ms.common.fdebenchkit.models import Tier1Score as Tier1Score
from ms.common.fdebenchkit.weights import validate_resolution_result as validate_resolution_result
