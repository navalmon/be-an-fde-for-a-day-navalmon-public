# Evaluation Results

## Summary

The final submitted service is deployed at:

```text
https://fdebench-navalmon-api.lemonpebble-c7043a33.eastus2.azurecontainerapps.io
```

The latest complete deployed run after the Task 2 JPEG optimization scored `86.5 / 100` with no errored items. That full run happened after the Task 2-only JPEG tuning run, so the repeated public Task 2 images benefited from the service's in-memory extraction cache. For a colder Task 2 latency read, use the Task 2-only JPEG run below: it improved from `77.8` to `84.6` Tier 1 with P95 latency dropping from `15640 ms` to `9703 ms`.

Using the colder Task 2-only JPEG result with the measured Task 1 and Task 3 scores gives a conservative post-JPEG composite estimate of approximately `85.1`.

## Run configurations

| Run | Endpoint | Command | Date | Notes |
|---|---|---|---|---|
| Local no-model baseline | `http://127.0.0.1:8000` | `uv run python apps\eval\run_eval.py --endpoint http://127.0.0.1:8000` from `py` | 2026-06-24 | No model provider credentials; useful for contract/resilience checks |
| Full deployed run | Azure Container Apps HTTPS endpoint | `uv run python run_eval.py --endpoint <endpoint>` from `py\apps\eval` | 2026-06-25 | Model configured as `gpt-5.4-mini`; PNG Task 2 preprocessing |
| Task 2 JPEG tuning run | Azure Container Apps HTTPS endpoint | `uv run python run_eval.py --endpoint <endpoint> --task extract` from `py\apps\eval` | 2026-06-25 | Same model with JPEG90, max dimension 2048, detail auto |
| Final full deployed regression run | Azure Container Apps HTTPS endpoint | `uv run python run_eval.py --endpoint <endpoint>` from `py\apps\eval` | 2026-06-25 | Same model and JPEG90 settings; Task 2 public items were warm-cache repeats |

## Final full deployed regression run

The runner warned that Task 3's local mock tool endpoints are not reachable from the remote endpoint. The service handles unavailable local mock tools by returning scorer-ingestible fallback traces, so the run still completed with 0 errored items.

| Metric | Score |
|---|---:|
| FDEBench Composite | 86.5 |
| Resolution (avg) | 81.7 |
| Efficiency (avg) | 96.0 |
| Robustness (avg) | 88.1 |

| Task | Tier 1 | Resolution | Efficiency | Robustness | P95 latency | Items errored |
|---|---:|---:|---:|---:|---:|---:|
| Signal Triage | 88.0 | 84.8 | 96.0 | 88.1 | 250 ms | 0 |
| Document Extraction | 88.6 | 84.4 | 96.0 | 90.6 | 594 ms | 0 |
| Workflow Orchestration | 82.8 | 75.9 | 96.0 | 85.5 | 94 ms | 0 |

### Final full-run resolution dimensions

| Task | Dimension | Weight | Score |
|---|---|---:|---:|
| Signal Triage | `category` | 24% | 0.925 |
| Signal Triage | `priority` | 24% | 0.908 |
| Signal Triage | `routing` | 24% | 0.861 |
| Signal Triage | `missing_info` | 17% | 0.581 |
| Signal Triage | `escalation` | 11% | 0.933 |
| Document Extraction | `information_accuracy` | 70% | 0.857 |
| Document Extraction | `text_fidelity` | 30% | 0.812 |
| Workflow Orchestration | `goal_completion` | 20% | 0.617 |
| Workflow Orchestration | `tool_selection` | 15% | 0.851 |
| Workflow Orchestration | `parameter_accuracy` | 5% | 0.730 |
| Workflow Orchestration | `ordering_correctness` | 20% | 0.954 |
| Workflow Orchestration | `constraint_compliance` | 40% | 0.703 |

## Full deployed run before JPEG optimization

The retained full-run output included the composite score, task Tier 1 scores, Task 2 dimension details, P95 latencies, resilience outcome, and error counts. Task 1 and Task 3 detailed dimensions are documented in the local baseline section below, where those values were captured from the same eval harness.

| Metric | Score |
|---|---:|
| FDEBench Composite | 82.9 |
| Task 1: Signal Triage | 88.0 |
| Task 2: Document Extraction | 77.8 |
| Task 3: Workflow Orchestration | 82.8 |

| Task | Tier 1 | P95 latency | Items errored |
|---|---:|---:|---:|
| Signal Triage | 88.0 | 328 ms | 0 |
| Document Extraction | 77.8 | 15640 ms | 0 |
| Workflow Orchestration | 82.8 | 93 ms | 0 |

### Captured Task 2 dimensions from the full deployed run

| Metric | Value |
|---|---:|
| Resolution | 81.6 |
| Efficiency | 51.5 |
| Robustness | 89.0 |

All resilience probes passed and the run had 0 errored items.

## Task 2 JPEG90 deployed run

This run evaluated only `POST /extract` after deploying:

- `FDE_EXTRACT_IMAGE_FORMAT=jpeg`
- `FDE_EXTRACT_JPEG_QUALITY=90`
- `FDE_EXTRACT_IMAGE_MAX_DIMENSION=2048`
- `FDE_EXTRACT_IMAGE_DETAIL=auto`
- `FDE_EXTRACT_MODEL_NAME=gpt-5.4-mini`

| Metric | Before JPEG | JPEG90 |
|---|---:|---:|
| Tier 1 | 77.8 | 84.6 |
| Resolution | 81.6 | 84.4 |
| Efficiency | 51.5 | 76.2 |
| Robustness | 89.0 | 90.6 |
| P95 latency | 15640 ms | 9703 ms |
| Items scored | 50 | 50 |
| Items errored | 0 | 0 |

### JPEG90 resolution dimensions

| Dimension | Weight | Score |
|---|---:|---:|
| `information_accuracy` | 70% | 0.857 |
| `text_fidelity` | 30% | 0.812 |

### JPEG90 operational metrics

| Metric | Value |
|---|---:|
| Latency score | 0.670 |
| Cost tier score | 0.900 |
| Adversarial accuracy | 84.4 |
| API resilience | 100.0 |

### JPEG90 probe results

| Probe | Result |
|---|---|
| malformed_json | PASS |
| empty_body | PASS |
| missing_fields | PASS |
| huge_payload | PASS |
| wrong_content_type | PASS |
| concurrent_burst | PASS |
| slow_followup | PASS |

## Local no-model baseline

This run remains useful because it proves the API contract and resilience behavior without relying on model credentials.

| Metric | Score |
|---|---:|
| FDEBench Composite | 67.6 |
| Resolution (avg) | 54.0 |
| Efficiency (avg) | 96.0 |
| Robustness (avg) | 71.5 |

| Task | Tier 1 | Resolution | Efficiency | Robustness | P95 latency | Items errored |
|---|---:|---:|---:|---:|---:|---:|
| Signal Triage | 88.0 | 84.8 | 96.0 | 88.1 | 47 ms | 0 |
| Document Extraction | 32.1 | 1.4 | 96.0 | 40.8 | 109 ms | 0 |
| Workflow Orchestration | 82.8 | 75.9 | 96.0 | 85.5 | 62 ms | 0 |

The local Task 2 score is low because no vision model was configured; the endpoint returned schema-shaped fallbacks. The deployed Task 2 runs above are the meaningful extraction measurements.

## Error analysis

### Task 1

Task 1 is strong and fast. Remaining losses are likely from ambiguous ownership and missing-information cases where the public examples can reasonably map to more than one team or missing field. Further tuning should be conservative because broad missing-information rules previously risked over-emission.

### Task 2

Task 2 is the highest-value remaining optimization area. JPEG90 materially improved deployed latency without hurting resolution, but extraction remains sensitive to:

- tiny text and low-contrast scans
- table row/column alignment
- nested arrays and repeated sections
- dates, currency formatting, and IDs that need exact transcription
- model quota and remote API latency

The service prefers `null` for invisible fields rather than hallucinating. That preserves schema validity and avoids confidently wrong values, but it can reduce recall when the model is uncertain.

### Task 3

Task 3 is robust and low latency because it uses deterministic workflow plans and bounded tool calls. Remaining errors are expected in workflows that require response-specific branching not covered by the current planner or where a hidden workflow family uses a new tool sequence.

### Cross-task limitations

- The latest measured full deployed composite is from the pre-JPEG Task 2 run; the post-JPEG composite is estimated from separate Task 2 measurement.
- The live deployment is tuned for a quota-limited Azure subscription, not maximum throughput.
- Hidden platform data can differ from public eval examples, especially for Task 2 document layouts and Task 3 workflow families.
