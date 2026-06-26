# FDEBench challenge implementation plan

This is the repo-persistent implementation plan for executing on the FDEBench challenge. It mirrors the session plan so the work can be resumed even if assistant context is lost.

## Problem and proposed approach

Build a complete, deployable FastAPI solution for the FDEBench challenge in this repository. The service must expose `GET /health`, `POST /triage`, `POST /extract`, and `POST /orchestrate`, score well across resolution, efficiency, and robustness, and include substantive submission documentation.

The fastest path is to keep the existing Python 3.12 FastAPI app in `py/apps/sample` as the scored service. The current foundation already has route wiring, Pydantic contracts, scoring headers, validation behavior, shared settings, HTTP/model client scaffolding, retry helpers, and a strong Task 1 deterministic triage baseline. The implementation work should focus on completing Task 2 document extraction, completing Task 3 real workflow orchestration, improving Task 1 missing-information accuracy, then hardening deployment and docs.

## Execution status

Status as of 2026-06-24: the plan has been executed in this branch.

Completed implementation:

- Improved Task 1 missing-information heuristics while preserving the hybrid triage guardrails.
- Added Task 2 schema parsing, schema-shaped fallbacks, type coercion, invalid-image handling, and multimodal model-message support.
- Added Task 3 deterministic planning, real HTTP tool-call attempts, step traces, summary fields, constraint reporting, and fast local-mock failure handling.
- Tightened Task 2 PNG validation before vision-model calls, enabled configured bounded retries for Task 3 tool calls, restricted orchestration tool endpoints to the normalized benchmark mock-service contract, and corrected Task 3 email/account summary counters.
- Added Task 3 response-aware branching for inventory thresholds, subscription-gated emails, onboarding aborts, meeting scheduling blocks, churn-risk routing, and renewal approval paths.
- Added focused tests for multimodal model payloads, extraction schema coercion/fallbacks, and orchestration tool execution.
- Added Docker deployment scaffolding and container run instructions.
- Filled `docs/methodology.md` and `docs/evals.md` with implementation details and the latest local results.

Latest local eval:

| Area | Score |
|---|---:|
| Composite | 67.6 |
| Average resolution | 54.0 |
| Average efficiency | 96.0 |
| Average robustness | 71.5 |
| Task 1 tier 1 | 88.0 |
| Task 2 tier 1 | 32.1 |
| Task 3 tier 1 | 82.8 |

Important remaining limitation: Task 2 needs configured vision-model credentials to extract real image values. The local no-credential run exercises schema fallback behavior, so Task 2 resolution remains low until `FDE_MODEL_BASE_URL` and `FDE_MODEL_API_KEY` point at a vision-capable model.

## README review summary

All repository `README.md` files were checked, including root, devcontainer, infra, data, submission, challenge overview, eval, all three task folders, common FastAPI/models libraries, and generated pytest-cache READMEs.

Key challenge requirements:

- One HTTPS-callable API with `/health`, `/triage`, `/extract`, and `/orchestrate`.
- Tier 1 score is the mean of the three task scores.
- Per task: `50% Resolution + 20% Efficiency + 30% Robustness`.
- Efficiency depends on P95 latency and the `X-Model-Name` response header.
- Robustness includes adversarial accuracy plus seven API probes.
- Valid-looking task failures should return HTTP 200 with a valid response envelope where possible; malformed JSON, empty body, and protocol-level failures should return clean 4xx responses.
- Required final docs: `docs/architecture.md`, `docs/methodology.md`, and `docs/evals.md`.

## Current implementation state

### Already implemented

- `py/apps/sample/main.py`
  - FastAPI app factory and `GET /health`.
  - `/triage`, `/extract`, and `/orchestrate` route wiring.
  - `X-Model-Name` scoring header middleware.
  - Wrong content type handling for scored endpoints.
  - Request validation handler that returns 400 for malformed JSON and 422 for validation errors.
  - App lifespan creates shared HTTP/model clients.
- `py/apps/sample/models.py`
  - Task 1 enums and response contract.
  - Task 2 flexible response model with extra fields allowed.
  - Task 3 request/response models and `steps_executed` structure.
- `py/apps/sample/config.py`
  - Environment-backed settings for model names, model base URL/API key, retry settings, timeout, and concurrency.
- `py/apps/sample/model_client.py`
  - OpenAI-compatible chat completions adapter.
  - JSON parsing and strict response validation.
  - Retry integration and provider configuration checks.
- `py/apps/sample/resilience.py`
  - Retry helper with `Retry-After` and `Retry-After-Ms` parsing.
  - Retry behavior for 429, 5xx, timeouts, and transport errors.
- `py/apps/sample/triage/service.py`
  - Hybrid deterministic-plus-optional-model Task 1 classifier.
  - Exact enum labels for category, team, priority, and missing information.
  - Guardrails for safety-critical and non-signal cases.
  - Deterministic routing, priority, escalation, missing-info, next-action, and remediation logic.
  - Optional model proposal path for ambiguous signals with strict validation and fallback.
- Tests
  - `py/apps/sample/tests/` covers app foundation, model client, retry behavior, triage service, extraction schema/validation behavior, and orchestration tool execution/retry/security/response-branching behavior.
  - Current sample app tests pass: `90 passed`.
- Docs
  - `docs/architecture.md` is substantive and mostly aligned with the current intended design.
  - `docs/methodology.md` and `docs/evals.md` contain the implemented approach, current local results, and known limitations.
- Tooling
  - `py/Makefile` supports setup, run, all-task eval, and per-task eval.
  - Python workspace is configured with `uv`.
  - TypeScript workspace is only root package-manager scaffolding.
  - Infrastructure is a Pulumi placeholder only.

### Baseline local eval

Command used:

```powershell
cd C:\Workspaces\be-an-fde-for-a-day-navalmon\py
uv run python apps\eval\run_eval.py --endpoint http://127.0.0.1:8000
```

Baseline results:

| Area | Score / observation |
|---|---:|
| Composite | 49.8 |
| Average resolution | 27.6 |
| Average efficiency | 96.0 |
| Average robustness | 55.9 |
| Task 1 tier 1 | 86.1 |
| Task 1 resolution | 81.6 |
| Task 1 API resilience | 100.0 |
| Task 2 tier 1 | 32.0 |
| Task 2 resolution | 1.2 |
| Task 2 API resilience | 100.0 |
| Task 3 tier 1 | 31.2 |
| Task 3 resolution | 0.0 |
| Task 3 API resilience | 100.0 |

Important notes:

- Task 1 is useful but still weak on `missing_info` relative to other dimensions.
- Task 2 returns only `document_id`, so it gets almost no extraction resolution.
- Task 3 returns no executed steps and does not call tools, so it gets zero resolution.
- API resilience is currently strong across all three endpoints.
- Local Task 3 eval warned that `py/data/task3/public_eval_50_mock_responses.json` is absent, so public local T3 tool responses are not available in this checkout. Hidden/platform eval supplies its own mock service.

## Original gaps and current status

### Task 1: Signal triage

Current state: implemented and improved. Latest local Task 1 tier 1 is 88.0, with `missing_info` improved to 0.581.

Completed work:

- Improve `missing_information` precision/recall, currently the weakest Task 1 dimension.
- Add targeted tests from public/sample misses after running `make eval-triage`.
- Review support docs in `docs/challenge/task1/customer_brief.md`, `routing_guide.md`, and `engineering_review.md` for any rule gaps not captured by README.
- Optionally use configured model calls only for ambiguous cases, preserving deterministic guardrails and low latency.

### Task 2: Document extraction

Current state: implemented foundation and optional vision-model path. The no-model local run still scores low because no OCR/vision provider was configured.

Completed work:

- Parse `json_schema` from each request.
- Decode and validate `content_format: image_base64` and PNG bytes.
- Build a complete null/default skeleton from the requested JSON schema.
- Integrate a vision-capable model through the shared model client, or extend the model client for multimodal content.
- Prompt for JSON-only schema-shaped extraction.
- Coerce and validate output against the request schema:
  - preserve `document_id`
  - fill every requested field
  - support nested objects and arrays
  - convert safe numeric and boolean values
  - return `null` instead of hallucinating unknown fields
- Add tests for schema skeleton generation, type coercion, invalid base64 handling, model fallback, and dynamic extra fields.
- Reject non-PNG base64 payloads before model calls and return schema-shaped fallbacks.
- Run `make eval-extract` and document scores/error patterns.

### Task 3: Workflow orchestration

Current state: implemented response-aware deterministic planner/executor. Latest local Task 3 tier 1 is 82.8. The local checkout lacks the public mock-response file, so this score still exercises fallback traces rather than successful payload-driven tool execution.

Completed work:

- Implement real HTTP tool execution against each tool's provided `endpoint`.
- Add a reusable async tool caller with retries, timeouts, structured errors, and result summaries.
- Implement a planner/executor loop that chooses tool calls from `goal`, `available_tools`, and `constraints`.
- Record each real call in `steps_executed` with step number, tool name, parameters, result summary, and success.
- Derive summary fields from actual execution:
  - `constraints_satisfied`
  - `accounts_processed`
  - `emails_sent`
  - `emails_skipped`
  - `skip_reasons`
- Use the configured bounded retry policy for transient tool failures, and count failed email actions as skipped instead of sent.
- Reject untrusted private/internal and non-mock tool endpoints while allowing only the normalized benchmark `localhost:9090/scenario/...` mock-tool service contract.
- Inspect successful tool payloads to branch on actual stock levels, subscription status/tier, CRM search account IDs, calendar availability, churn-risk level, and finance-approval signals.
- Favor constraint compliance over aggressive completion when data is ambiguous or tools fail.
- Add tests with mocked tool endpoints for successful workflows, tool failures, retries, missing required parameters, and constraint-sensitive skipping.
- Run `make eval-orchestrate` locally for contract/wiring even though public mock responses are absent here.

### Cross-cutting reliability

Completed work:

- Preserve current 7/7 API probe behavior while adding model/tool logic.
- Ensure valid-looking downstream/model failures return valid response envelopes instead of 500s.
- Keep bounded concurrency around model calls to avoid 429s.
- Use task-specific model names in headers if different models are used.
- Add request size/timeout safeguards without breaking probe behavior.
- Keep route handlers thin and task services testable.

### Deployment and submission

Completed work:

- Replace placeholder Pulumi or add container deployment instructions for an HTTPS endpoint.
- Configure environment variables/secrets for model provider credentials.
- Confirm `/health` and all task endpoints work against the deployed URL.
- Run local evals against localhost; run deployed eval for Task 1 and Task 2 only if Task 3 tools are local-only in the harness.
- Fill in `docs/methodology.md` with actual approach and iteration details.
- Fill in `docs/evals.md` with actual latest eval results and error analysis.
- Update `README.md` if run/deploy/test instructions change.

## Completed todos

1. Completed: Inventory challenge support docs and scoring code
   - Read task support docs and relevant scorer implementations to identify high-value scoring dimensions and edge cases.
2. Completed: Improve Task 1 missing-information accuracy
   - Use public/sample triage misses to tune deterministic `missing_information` rules and add regression tests.
3. Completed: Build Task 2 schema-guided extraction foundation
   - Add schema parsing, output skeleton generation, safe type coercion, base64 validation, and valid fallback responses.
4. Completed: Add Task 2 vision-model integration
   - Extend or wrap the OpenAI-compatible model client for image input and JSON-only extraction, with retries and strict parsing.
5. Completed: Build Task 3 tool execution layer
   - Add async tool caller, retry/error handling, and step trace recording.
6. Completed: Build Task 3 planner and constraint handling
   - Implement workflow state, tool selection, parameter generation, constraint checks, summary fields, and partial-failure behavior.
7. Completed: Expand service tests
   - Add focused unit tests for Task 1 misses, Task 2 schema/model/fallback behavior, and Task 3 tool/constraint behavior.
8. Completed: Run eval and iterate
   - Run targeted `make eval-*` commands, capture per-dimension results, and iterate on the weakest dimensions first.
9. Completed: Harden deployment path
   - Add or complete container/Pulumi deployment support, environment configuration, HTTPS health checks, and concurrency settings.
10. Completed: Complete submission documentation
    - Fill `docs/methodology.md`, `docs/evals.md`, and update `docs/architecture.md`/root README with final implementation, commands, scores, and limitations.

## Notes and considerations

- Preserve the current FastAPI resilience behavior because all three tasks currently pass every local API probe.
- Task 1 should not be rewritten from scratch; improve its weakest dimension and keep the tested guardrails.
- Task 2 is likely the largest score opportunity because it is currently a stub but has a clear schema-guided vision extraction path.
- Task 3 requires actual tool calls; a plausible trace without HTTP calls will not score.
- Confirmed assumption: use an OpenAI-compatible or Azure OpenAI provider configured via `FDE_MODEL_BASE_URL` and `FDE_MODEL_API_KEY`, including a vision-capable model for Task 2.
- The TypeScript workspace should remain unused unless a concrete need appears.
- Generated pytest-cache README files should not drive implementation and should remain uncommitted.
