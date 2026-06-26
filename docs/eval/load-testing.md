# Load Testing & Performance Evaluation

Your Efficiency score (20% of each task) depends on P95 latency. Before submitting, stress-test your deployed API to know where you stand.

## Quick benchmarks with the eval harness

The built-in eval harness already measures latency per request. Run it to get your baseline:

```bash
cd py

# Full eval, includes latency P95 and efficiency score
make eval

# Single task
make eval-triage
make eval-extract
make eval-orchestrate
```

The output includes P95 latency, model cost tier, and the computed efficiency score.

## Targeted load testing with hey

[hey](https://github.com/rakyll/hey) is a lightweight HTTP load generator. Install it and fire requests at your API to test throughput and concurrency handling.

```bash
# Install
brew install hey   # macOS
# or: go install github.com/rakyll/hey@latest

# Prepare a payload file
cat > /tmp/triage_payload.json << 'EOF'
{
  "ticket_id": "SIG-LOAD-001",
  "subject": "Intermittent comm relay failure on deck 7",
  "description": "Subspace relay drops every 20 minutes. Crew reports latency spikes during peak hours.",
  "reporter": {"name": "Lt. Chen", "email": "chen@contoso.com", "department": "Communications"},
  "created_at": "2026-04-14T10:00:00Z",
  "channel": "bridge_terminal",
  "attachments": []
}
EOF

# Baseline: serial requests (check single-request latency)
hey -n 10 -c 1 -m POST \
  -H "Content-Type: application/json" \
  -D /tmp/triage_payload.json \
  http://localhost:8000/triage

# Concurrency test: 20 concurrent requests (matches the burst probe)
hey -n 50 -c 20 -m POST \
  -H "Content-Type: application/json" \
  -D /tmp/triage_payload.json \
  http://localhost:8000/triage

# Sustained load: 2 minutes at 5 req/s
hey -z 120s -q 5 -c 5 -m POST \
  -H "Content-Type: application/json" \
  -D /tmp/triage_payload.json \
  http://localhost:8000/triage
```

### Task 2 (Extract): inlined-base64 payload

Your `/extract` always receives `content_format: "image_base64"`. The eval
harness inlines images from `py/data/task2/images/` as base64 before POSTing,
so payloads are larger (~500 KB–15 MB per request, dominated by the PNG
size) than the on-disk JSON would suggest. Build a real payload from the
eval data with the same inlining the harness performs:

```bash
# Inline the first eval item as the wire-format payload
cd py
python3 -c "
import base64, json, pathlib
task2 = pathlib.Path('data/task2')
items = json.load((task2/'public_eval_50.json').open())
item = dict(items[0])
img = (task2/item['content']).read_bytes()
item['content_format'] = 'image_base64'
item['content'] = base64.b64encode(img).decode()
with open('/tmp/extract_payload.json', 'w') as f:
    json.dump(item, f)
print(f'Payload size: {len(json.dumps(item))//1024} KB  ({len(img)//1024} KB raw image)')
"

# Serial baseline (extract is slow, vision model)
hey -n 5 -c 1 -m POST \
  -H "Content-Type: application/json" \
  -D /tmp/extract_payload.json \
  http://localhost:8000/extract
```

### Task 3 (Orchestrate): multi-step with mock tools

Orchestration requests call external tool endpoints. Start the mock tool service first:

```bash
# Terminal 1: start mock tools
cd py && make mock-tools

# Terminal 2: load test
cat > /tmp/orchestrate_payload.json << 'EOF'
{
  "task_id": "LOAD-001",
  "goal": "Check inventory levels and send restock alerts",
  "available_tools": [
    {"name": "inventory_check", "description": "Check stock levels", "endpoint": "http://localhost:9090/inventory_check", "parameters": [{"name": "item_id", "type": "string", "description": "Item ID", "required": true}]}
  ],
  "constraints": ["Must check all items before alerting"],
  "mock_service_url": "http://localhost:9090"
}
EOF

hey -n 10 -c 2 -m POST \
  -H "Content-Type: application/json" \
  -D /tmp/orchestrate_payload.json \
  http://localhost:8000/orchestrate
```

## Load testing a deployed API

Test your actual Azure deployment to catch network latency, cold starts, and scaling behavior:

```bash
# Replace with your deployed URL
export API_URL=https://your-app.azurecontainerapps.io

# Health check
curl -s "$API_URL/health"

# Cold start test: wait 60s, then hit it
sleep 60 && hey -n 1 -c 1 -m POST \
  -H "Content-Type: application/json" \
  -D /tmp/triage_payload.json \
  "$API_URL/triage"

# Burst test: 20 concurrent (probe #6 threshold)
hey -n 20 -c 20 -m POST \
  -H "Content-Type: application/json" \
  -D /tmp/triage_payload.json \
  "$API_URL/triage"

# Full eval against deployed endpoint
cd py/apps/eval
python run_eval.py --endpoint "$API_URL"
```

> **Task 2 (Extract) image delivery.** Your `/extract` always receives
> `content_format: "image_base64"`. The shipped public set on disk uses
> `content_format: "image_path"` so the JSON file stays small (~80 KB),
> but `run_eval.py` inlines each PNG from `py/data/task2/images/` as
> base64 before POSTing, so your endpoint never sees the path form. The
> platform's hidden-eval scoring does the same translation server-side
> (downloading from blob storage and applying per-submission anti-
> reconstruction perturbation, cohort-2 WS1.1) so the wire format is
> uniform across local-dev and production. Override the harness with
> `--inline-t2-images=no` only when debugging the on-disk payload
> shape; the reference solution drops anything other than
> `image_base64`.

## What to look for

| Metric | Target | Why |
|--------|--------|-----|
| P95 latency (triage) | < 2 s | Per-task scoring: 500 ms = 1.0, 5,000 ms = 0.0 |
| P95 latency (extract) | < 10 s | Per-task scoring: 2,000 ms = 1.0, 20,000 ms = 0.0 |
| P95 latency (orchestrate) | < 5 s | Per-task scoring: 1,000 ms = 1.0, 10,000 ms = 0.0 |
| Concurrent burst (20 reqs) | ≥ 18 pass | Probe #6: if you fail this, you lose ~6% robustness |
| Cold start | < 15 s | Probe #7: first request after idle must succeed |
| Error rate under load | < 5% | Errors reduce your resolution score |

## Common performance bottlenecks

| Problem | Symptom | Fix |
|---------|---------|-----|
| Cold start timeout | First request after idle fails | Set min replicas ≥ 1 in Container Apps |
| Azure OpenAI 429s | Spiky latency, intermittent 500s | Add retry with exponential backoff, use multiple endpoints |
| Large prompts | High P95 on triage | Trim system prompt, use structured output instead of free-form |
| Large image in memory | OOM on extract | Stream/decode the image once; don't keep multiple in-memory copies |
| Sequential tool calls | Slow orchestration | Parallelize independent tool calls where constraints allow |
| No connection reuse | High latency variance | Use a singleton `AsyncClient` / `AsyncOpenAI`, don't recreate per request |

## Efficiency scoring reference

```
efficiency = 0.60 × latency_score + 0.40 × cost_score

latency_score (per-task thresholds):
  Triage:      P95 ≤ 500 ms → 1.0,  P95 ≥ 5,000 ms  → 0.0
  Extract:     P95 ≤ 2,000 ms → 1.0, P95 ≥ 20,000 ms → 0.0
  Orchestrate: P95 ≤ 1,000 ms → 1.0, P95 ≥ 10,000 ms → 0.0
  In between   → linear interpolation

cost_score: based on X-Model-Name header
  nano (gpt-4.1-nano)     → 1.0
  mini (gpt-4.1-mini)     → 0.9
  standard (gpt-4o)       → 0.75
  full (gpt-5-pro, o3)    → 0.5
  premium (o1, o3-pro)    → 0.3
```

A solution using `gpt-4.1-mini` with P95 at 1.5 s on triage scores:
- Latency: `(5000 - 1500) / (5000 - 500) = 0.78`
- Cost: `0.9`
- Efficiency: `0.60 × 0.78 + 0.40 × 0.9 = 0.83` → **83/100**
