# Submission

## How to submit

1. **Fork** this repo (your fork must be **public**)
2. **Build** your solution: all 3 task endpoints + health check
3. **Deploy** somewhere callable via HTTPS (not localhost)
4. **Test** with the eval harness: run `python run_eval.py --endpoint http://localhost:8000` from `py/apps/eval/`
5. **Write** your three docs: `docs/architecture.md`, `docs/methodology.md`, `docs/evals.md`
6. **Push** everything to your public fork
7. **Submit** at **[aka.ms/fde/hackathon](https://aka.ms/fde/hackathon)**

Think of submission like a deployment rollout: can the endpoints be called reliably, is the code legible, do the docs show you understood the problem?

## Required files

```
your-repo/
├── README.md                # How to install, run, and test locally
├── docs/
│   ├── architecture.md      # System design, AI pipeline, tradeoffs
│   ├── methodology.md       # Approach, iteration, what worked and what didn't
│   └── evals.md             # Actual scores, error analysis, honest limitations
└── ...                      # Your code, tests, infrastructure
```

All three docs are mandatory. Missing one affects your Tier 2 (engineering quality) score.

## What gets evaluated

### Tier 1: deterministic (public leaderboard)

Your deployed API is called with a hidden eval set per task (T1: ~1,000, T2: ~500, T3: ~500).

| Dimension | Weight | Description |
|-----------|--------|-------------|
| **Resolution** | 50% | Correct outputs vs. gold answers (task-specific F1/accuracy metrics) |
| **Efficiency** | 20% | Latency (P95) + cost (model tier from `X-Model-Name` header) |
| **Robustness** | 30% | Adversarial accuracy (tagged hard instances) + API resilience (7 probes) |

Per-task scores are averaged into a FDEBench composite (0-100).

FDEBench rewards consistency: solid across all three tasks usually beats amazing on one and weak on the others.

#### What you get back from the platform

After a submission completes you'll see your aggregate task scores and FDEBench composite. You will **not** see per-dimension scores, per-item feedback, agent reasoning traces, or per-probe pass/fail. Use the local runner at `py/apps/eval/run_eval.py` against `public_eval_50.json` for per-dimension introspection.

Eval items are also shuffled per submission, so join responses on `request_id_key`, not on position. See [How Your Solution Is Scored, Platform behaviour you should know](../eval/fdebench.md#platform-behaviour-you-should-know) for the full list.

### Tier 2: LLM-as-judge (judges only)

Four agents read your repo. Scores are **not public**; they inform finalist selection.

| Agent | Weight | Focus |
|-------|--------|-------|
| Code Quality | 25% | Structure, types, error handling, testing, readability |
| Architecture Design | 30% | AI pipeline, decomposition, API design, tradeoffs |
| AI Problem Solving | 25% | Prompt engineering, evaluation methodology, model/cost awareness |
| Engineering Maturity | 20% | Deployment, config/secrets, observability, security |

See the `engineering_review.md` in each [task folder](../challenge/) for what judges look for per task.

## Required documents

See [../challenge/README.md](../challenge/README.md) and the task briefs under [../challenge/](../challenge/) for the challenge context behind each document.

| Document | What we want to see |
|---|---|
| `docs/architecture.md` | System design, data flow per task, AI pipeline, tradeoffs, production readiness |
| `docs/methodology.md` | Approach, time allocation across 3 tasks, what you tried, what failed |
| `docs/evals.md` | Per-task scores, per-dimension breakdown, error analysis, honest limitations |

If you use the local runner at `py/apps/eval/run_eval.py` to gather your numbers, say so in `docs/evals.md` or `docs/methodology.md`. That makes your evaluation process easier to follow.

## Pre-submission checklist

### Endpoints

- [ ] `GET /health` returns HTTP 200
- [ ] `POST /triage` returns valid JSON matching the [triage output schema](../../py/data/task1/output_schema.json)
- [ ] `POST /extract` returns valid JSON matching the [extraction output schema](../../py/data/task2/output_schema.json)
- [ ] `POST /orchestrate` returns valid JSON matching the [orchestration output schema](../../py/data/task3/output_schema.json)
- [ ] All 3 endpoints handle **10 concurrent requests** without errors
- [ ] Each request responds in **under 30 seconds**

### Task 1: Signal Triage

- [ ] All 8 response fields present: `ticket_id`, `category`, `priority`, `assigned_team`, `needs_escalation`, `missing_information`, `next_best_action`, `remediation_steps`
- [ ] `category` values from the 8 valid categories (exact strings)
- [ ] `assigned_team` values from the 7 valid teams (including `"None"`)
- [ ] `missing_information` values from the 16-value constrained vocabulary (exact strings)
- [ ] Eval harness runs against sample signals with gold scoring

### Task 2: Document Extraction

- [ ] Receives `content_format: "image_base64"` and base64-decodes `content` into PNG bytes (the local harness inlines from `py/data/task2/images/`; the platform inlines from blob storage with anti-reconstruction perturbation; your code sees the same payload shape both ways)
- [ ] Reads `json_schema` from the request and returns matching structured JSON
- [ ] Response includes `document_id` plus all fields specified by the document's schema
- [ ] Nested objects and arrays properly structured (tables, lists, sub-objects)

### Task 3: Workflow Orchestration

- [ ] Actually calls the mock tool endpoints provided in `available_tools`
- [ ] Returns execution trace with `steps_executed`, tool calls, parameters, results
- [ ] Reports `constraints_satisfied` indicating which constraints were respected
- [ ] Handles tool failures gracefully (retry, skip, or report; don't crash)

### API Resilience

- [ ] Returns HTTP 400/422 for malformed JSON, empty body, missing fields (not 500)
- [ ] Handles 50KB payloads without timeout or crash
- [ ] Survives 20 concurrent requests (at least 18/20 must succeed)
- [ ] Recovers from 60s idle (cold start)

### Efficiency headers (recommended)

- [ ] Responses include `X-Model-Name` header (required for cost scoring; model tier determines your cost score)

### Documentation and Repo

- [ ] `docs/architecture.md` exists and is substantive
- [ ] `docs/methodology.md` exists and is substantive
- [ ] `docs/evals.md` exists and includes actual numbers
- [ ] Repo is **public** on GitHub
- [ ] README explains how to install, run, and test locally
- [ ] Submitted at **[aka.ms/fde/hackathon](https://aka.ms/fde/hackathon)**
