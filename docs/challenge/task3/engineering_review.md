# Task 3: Engineering Review

What judges look for in your orchestration code. Tier 2 doesn't affect the leaderboard, but it's how finalists get picked.

## Code Quality (25% of Tier 2)

| Dimension | What judges look for |
|---|---|
| Structure (25%) | Planner separated from executor. Tool dispatch in its own module. Workflow state machine cleanly implemented, not one giant async function. |
| Type safety (20%) | Tool definitions, step results, and workflow state typed. `steps_executed` is a list of typed `StepResult` models, not raw dicts. Constraint checks type-safe. |
| Error handling (15%) | Per-step error handling, not one-failure-crashes-all. Tool timeouts handled. Distinction between retryable and terminal failures. |
| Testing (25%) | Tests for planning logic, constraint checking, and error recovery. Mock tool responses used to verify workflow execution without real HTTP calls. |
| Readability (15%) | Planner prompt clearly structured. Execution loop readable. Constraint definitions self-documenting. |

## Architecture Design (30% of Tier 2)

| Dimension | What judges look for |
|---|---|
| AI pipeline (30%) | Clear plan → execute → evaluate loop. Planning prompt separate from execution logic. Support for both sequential and parallel tool execution. Re-planning mechanism when tool results are unexpected. |
| Decomposition (25%) | Planner → Executor → Tool Client → State Manager layering. Each component has a clear interface. Tool dispatch is polymorphic (new tools don't require code changes). |
| API design (20%) | `POST /orchestrate` returns a meaningful execution trace. Intermediate results included. Response schema consistent whether the workflow completed, partially completed, or failed. |
| Trade-off reasoning (15%) | Awareness of planning-depth vs. cost. Single-pass planning vs. iterative. Parallel vs. sequential execution. Model selection for planning vs. tool-call parsing. |
| Scalability (10%) | Can handle workflows with many tool calls. Token budget management across multiple LLM turns. Tool calls rate-limited to avoid overwhelming endpoints. |

## Engineering Maturity (20% of Tier 2)

| Dimension | What judges look for |
|---|---|
| Deployment (30%) | Same as all tasks. Orchestration may need additional config for tool endpoint URLs. |
| Config and secrets (25%) | Tool endpoint URLs configurable. LLM settings in env vars. Timeout and retry config externalized. |
| Observability (20%) | Per-step logging with tool name + parameters + result. Workflow-level tracing. Execution time per step logged. |
| Security (25%) | Tool parameters treated as potentially sensitive. Tool endpoint URLs validated. No arbitrary code execution from LLM-generated plans. |

## Tips

- This is the most architecture-heavy task.
- Upfront planning is cheaper but iterative re-planning handles surprises better.
- Parallelize independent tool calls when you can; it makes a real latency difference.
- Consider a two-model strategy: capable model for planning, cheaper model for straightforward tool calls.
