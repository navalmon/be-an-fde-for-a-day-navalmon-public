# Methodology

## Approach

I treated the challenge as a production-readiness exercise rather than only a local benchmark. The implementation work focused on three goals:

1. Keep every endpoint scorer-ingestible under malformed input, concurrency, and downstream failures.
2. Improve the weakest task scores without regressing existing strengths.
3. Deploy the same container that is evaluated locally to a public HTTPS endpoint.

The final app keeps the Python/FastAPI scaffold, adds typed service layers for the three tasks, and uses Azure Container Apps for the deployed submission endpoint. Model-provider integration is centralized in `ModelClient`, so task services do not need provider-specific HTTP details.

## Iteration history

### Foundation and API resilience

The first pass kept the app as one service and centralized the scoring contract:

- `GET /health` returns a simple liveness response.
- Every scored endpoint includes `X-Model-Name`.
- Protocol-level bad requests return clean 4xx responses.
- Task-level failures return valid response envelopes where possible instead of unhandled 5xx errors.
- Shared app state owns the HTTP client, model client, and model concurrency semaphore.

This paid off throughout the work because all public eval runs continued to pass the seven API resilience probes.

### Task 1: Signal triage

Task 1 already had a strong deterministic base. I focused on the dimensions that were easiest to regress: exact labels and missing-information vocabulary.

What worked:

- Preserve exact enum strings for category, priority, assigned team, and missing-information labels.
- Add targeted evidence checks for diagnostics, screen-share policy, certificate/configuration gaps, phishing-forwarded evidence, impacted crew context, and version-like strings.
- Keep escalation conservative for safety-impact and mission-critical signals.

What did not work:

- Broad missing-information heuristics can over-emit labels and reduce score.
- Several ownership cases are genuinely gray: device/comms, identity/software, and hardware/safety can overlap in short tickets.

The deployed Task 1 result remained strong: Tier 1 `88.0` with 0 errored items.

### Task 2: Document extraction

Task 2 required the largest deployed/runtime iteration because local schema logic alone cannot read document images. I added a vision-model path and then tuned remote latency.

What worked:

- Parse the request `json_schema` and build a schema-shaped fallback before calling the model.
- Send a strict multimodal prompt containing the image, field guide, and exact JSON schema.
- Normalize model JSON back to the schema so nested arrays, objects, numeric fields, booleans, enums, and missing values remain valid.
- Cache successful extractions by document id, content, and schema, and coalesce concurrent in-flight duplicate requests.
- Downscale images to 2048 pixels and send JPEG90 to the model in deployment.

What did not work:

- Running Task 2 without model credentials produced valid but mostly null responses; it was robust but not accurate.
- Aggressive latency tuning with low-detail 1536-pixel images improved P95 latency but dropped resolution too much to keep.
- PNG at 2048 pixels preserved accuracy but had a much worse deployed P95 latency because the remote endpoint had to forward larger model payloads.

The winning tuning was JPEG90 at 2048 pixels with `detail=auto`. It reduced Task 2 P95 latency from `15640 ms` to `9703 ms` and improved Task 2 Tier 1 from `77.8` to `84.6` in deployed eval.

### Task 3: Workflow orchestration

Task 3 started from a stub-like path that did not produce useful tool traces. I replaced it with a deterministic planner/executor tuned to the observed workflow families.

What worked:

- Actually call supplied tool endpoints when the benchmark mock service is available.
- Preserve an ordered `steps_executed` trace with tool names, parameters, summaries, and success flags.
- Derive response fields such as accounts processed, emails sent/skipped, skip reasons, and satisfied constraints from execution state.
- Branch on tool response payloads for active subscriptions, low inventory, consent, risk level, finance approval, and available meeting slots.
- Fail closed on unavailable or unsafe tool endpoints instead of fabricating success.

What did not work:

- The local checkout did not include the Task 3 public mock response file, so some local runs could only exercise fallback traces. The deployed/platform path can call the supplied mock service URL.
- A deterministic planner is excellent for known workflow families but may miss novel hidden workflows that require different tool ordering.

Task 3 reached Tier 1 `82.8` with no errored items in the deployed eval.

## Deployment methodology

I added a Pulumi program under `infra/app` to deploy the root Docker image to Azure Container Apps. The deployment provisions ACR, Log Analytics, a Container Apps environment, the Container App, secrets, health probes, and HTTPS ingress.

Several deployment issues were resolved during iteration:

- Azure CLI needed a newer version for Conditional Access claims handling.
- The root Dockerfile had an invalid `ENV` value with a space; quoting fixed the container build.
- The Pulumi Docker provider failed on Windows with `archive/tar: invalid tar header`; switching to `az acr build` through `pulumi-command` avoided local Docker archive handling.
- ACR names cannot contain dashes; generated registry names are normalized to alphanumeric characters.

The live submission endpoint is:

```text
https://fdebench-navalmon-api.lemonpebble-c7043a33.eastus2.azurecontainerapps.io
```

## Evaluation methodology

I used the repository eval harness for all reported numbers:

```powershell
cd py\apps\eval
uv run python run_eval.py --endpoint <endpoint>
```

For Task 2 tuning I also ran task-specific evals:

```powershell
uv run python run_eval.py --endpoint <endpoint> --task extract
```

Because the Azure subscription and model deployment were quota-limited, I avoided repeated full deployed runs after every tuning change. The final docs report:

- the last full deployed run before JPEG preprocessing
- the Task 2-only deployed run after JPEG90 preprocessing
- the earlier local no-credential baseline for context

## Current limitations

- The latest full three-task deployed run was not repeated after JPEG90 to save model tokens. Task 1 and Task 3 code paths were unchanged by that optimization; Task 2 was re-evaluated directly.
- Task 2 remains the model- and latency-dominant endpoint. Higher quality or higher detail settings may improve extraction resolution but risk worse efficiency.
- Task 3 is tuned to known workflow families and benchmark constraints. A broader planner could generalize better but would add latency and model dependency.
- The deployment intentionally caps scale to one replica and model concurrency to two to reduce Azure model throttling during larger hidden Task 2 runs.
