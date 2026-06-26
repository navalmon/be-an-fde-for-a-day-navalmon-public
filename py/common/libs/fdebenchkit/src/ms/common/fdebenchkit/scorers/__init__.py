"""Scoring package: deterministic scorers for all FDEBench tasks.

Submodules:
  - ticket_triage:        Task 1 classification scoring (5 dimensions)
  - document_extraction:  Task 2 extraction scoring (8 dimensions)
  - workflow_orchestration: Task 3 orchestration scoring (5 dimensions)
  - _utils:               Shared normalization and F1 helpers
"""

from ms.common.fdebenchkit.scorers import document_extraction as document_extraction
from ms.common.fdebenchkit.scorers import ticket_triage as ticket_triage
from ms.common.fdebenchkit.scorers import workflow_orchestration as workflow_orchestration
