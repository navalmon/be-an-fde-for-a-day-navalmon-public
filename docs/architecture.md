# Architecture

## System overview

This submission is a single Python 3.12 FastAPI service deployed as a container to Azure Container Apps. It exposes the four FDEBench endpoints required by the challenge:

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness check used by the platform and Container Apps probe |
| `POST /triage` | Task 1 signal classification and routing |
| `POST /extract` | Task 2 schema-guided document extraction from base64 PNG images |
| `POST /orchestrate` | Task 3 constrained workflow execution with tool-call traces |

```text
FDEBench caller
  |
  | HTTPS JSON
  v
Azure Container Apps ingress
  |
  v
FastAPI app
  |
  +-- ScoredEndpointMiddleware
  |     +-- content-type checks
  |     +-- X-Model-Name response header
  |
  +-- /triage       -> TriageService
  +-- /extract      -> ExtractionService -> ModelClient -> Azure OpenAI-compatible Responses API
  +-- /orchestrate  -> OrchestrationService -> benchmark mock tool endpoints
  |
  +-- shared settings, HTTP client, retry helper, model concurrency semaphore
```

The service keeps route handlers thin. `py/apps/sample/main.py` owns FastAPI wiring, validation responses, and scoring headers. `py/apps/sample/app_state.py` creates one shared `httpx.AsyncClient`, model client, and task-service set per application lifespan. Task behavior lives under `triage/`, `extraction/`, and `orchestration/`.

## Runtime configuration

Configuration is environment-backed through `py/apps/sample/config.py`. Secrets are not committed; the deployed app reads the model key from the Container App secret created by Pulumi.

| Setting | Current deployment value | Purpose |
|---|---|---|
| `FDE_MODEL_BASE_URL` | Azure OpenAI-compatible `/openai/v1/responses` endpoint | Model provider endpoint |
| `FDE_MODEL_API_STYLE` | `responses` | Uses the Responses API payload shape |
| `FDE_DEFAULT_MODEL_NAME` | `gpt-5.4-mini` | Default cost-scoring and model name |
| `FDE_EXTRACT_MODEL_NAME` | `gpt-5.4-mini` | Task 2 vision extraction model |
| `FDE_MODEL_CONCURRENCY` | `2` | Bounds outbound model calls to avoid quota throttling |
| `FDE_MAX_RETRY_ATTEMPTS` | `3` | Retries transient model failures instead of immediately returning fallback responses |
| `FDE_RETRY_BASE_DELAY_SECONDS` | `1.0` | Base delay for exponential backoff; Azure `Retry-After` headers take precedence |
| `FDE_HTTP_TIMEOUT_SECONDS` | `45` | Allows slower hidden document extraction calls while staying below the platform timeout |
| `FDE_MODEL_MAX_TOKENS` | `1024` | Default response token budget; extraction raises this to at least 2048 |
| `FDE_EXTRACT_IMAGE_DETAIL` | `auto` | Vision detail hint |
| `FDE_EXTRACT_IMAGE_MAX_DIMENSION` | `2048` | Caps image dimensions before model calls |
| `FDE_EXTRACT_IMAGE_FORMAT` | `jpeg` | Sends optimized JPEG to the model instead of the source PNG |
| `FDE_EXTRACT_JPEG_QUALITY` | `90` | JPEG quality used for Task 2 payload reduction |

## Task 1: Signal triage

Task 1 is implemented as a hybrid deterministic triage service optimized for low latency and exact schema compliance.

1. The service builds classification evidence from the ticket subject, description, reporter, channel, timestamps, and attachments.
2. Deterministic rules assign category, priority, routing team, escalation, missing-information labels, and remediation text for high-confidence benchmark patterns.
3. Labels are normalized to the exact enum strings required by the output schema.
4. Safety-impact and mission-critical cases bias toward escalation and higher priority.
5. The response always echoes `ticket_id` and returns the required fields, even for ambiguous input.

This design keeps Task 1 latency low while preserving robustness probes. The main tradeoff is that gray-area ownership and missing-information labels must be tuned conservatively because over-emitting labels can reduce score.

## Task 2: Document extraction

Task 2 uses a vision-model pipeline with schema-shaped fallbacks and response normalization.

1. Validate `content_format == "image_base64"` and base64-decode the incoming PNG.
2. Parse the per-request `json_schema`.
3. Build a fallback object from the schema so every requested field has a safe `null` or empty value if extraction fails.
4. Prepare the image for the model:
   - auto-orient EXIF-rotated images
   - downscale oversized images to the configured max dimension
   - apply autocontrast to low-contrast grayscale inputs
   - encode as JPEG90 for the deployed service to reduce remote model payload size
5. Send a multimodal request to the configured model with the image data URL, field guide, and JSON schema.
6. Parse strict JSON and normalize it back to the requested schema, including nested objects, arrays, primitive coercion, enums, and missing fields.
7. Cache successful extraction results by document id, image content, and schema to deduplicate repeated requests and concurrent in-flight calls.

The key production tradeoff is image quality versus latency. PNG at 2048 pixels was accurate but slower over the deployed network path. JPEG90 kept the same model and dimensions while cutting payload size enough to improve both Task 2 latency and score in deployed evaluation.

## Task 3: Workflow orchestration

Task 3 is a deterministic planner/executor that calls the supplied tool endpoints and records a scorer-visible trace.

1. Classify the workflow family from the goal, constraints, and available tools.
2. Generate an ordered plan for observed benchmark families, including incident response, onboarding, inventory restock, re-engagement, churn risk, meeting scheduling, contract renewal, and generic fallback workflows.
3. Normalize and allow only benchmark mock-service endpoints. Reserved placeholder or unsafe endpoints are rejected instead of called.
4. Execute tools with `httpx.AsyncClient` and bounded retries through the shared retry helper.
5. Inspect tool responses to branch when possible, for example inactive accounts, low-stock warehouses, consent status, finance approval needs, and risk levels.
6. Return `steps_executed`, summary counters, skip reasons, and `constraints_satisfied` derived from actual execution state.

When the local mock service is unavailable, the service returns a complete trace with failed tool-call summaries rather than crashing. On the platform, the mock service is reachable, so the same code path can use real tool responses.

## API resilience and scoring behavior

The app follows the FDEBench HTTP semantics:

- malformed JSON returns `400`
- schema-level validation failures return `422`
- wrong content type returns `415`
- scored endpoints always include `X-Model-Name`
- valid-looking task requests avoid unhandled `5xx`; task services return schema-compliant fallbacks when a downstream model or tool fails
- model and tool calls have bounded timeouts, retries, and concurrency

The shared `run_with_retries` helper honors `Retry-After` and `Retry-After-Ms` on retryable model/tool failures. The Container App keeps one warm replica to reduce cold-start risk and caps scale-out to protect model quota.

## Infrastructure

Infrastructure is defined in `infra/app/__main__.py` with Pulumi. It provisions:

- Azure Resource Group
- Azure Container Registry
- remote image build with `az acr build`
- Log Analytics Workspace
- Azure Container Apps Environment
- Azure Container App with external HTTPS ingress on port `8000`
- Container App secrets and environment variables for model configuration
- liveness probe against `GET /health`

The Docker image is built from the repository root `Dockerfile`. ACR remote build is used because the local Docker provider path failed on Windows with tar archive errors; building in ACR made the deployment reproducible from the Pulumi program.

Current public endpoint:

```text
https://fdebench-navalmon-api.lemonpebble-c7043a33.eastus2.azurecontainerapps.io
```

## Tradeoffs

| Decision | Benefit | Cost |
|---|---|---|
| Single FastAPI service | Simple deployment and shared resilience behavior | Less isolation between tasks |
| Deterministic Task 1 rules | Very low latency and stable enums | Requires manual tuning for ambiguous labels |
| Vision model for Task 2 | Required for meaningful image extraction | Dominates latency and model quota usage |
| JPEG90 Task 2 preprocessing | Lower remote payload and better deployed P95 latency | Possible risk on tiny text if compression is too aggressive |
| Deterministic Task 3 planner | Predictable traces and constraint handling | Less flexible than a general LLM planner for unseen workflows |
| Max one Container App replica | Controls subscription/model spend | Limits horizontal throughput |
