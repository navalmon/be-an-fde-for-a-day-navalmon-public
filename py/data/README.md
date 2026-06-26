# Data

Public evaluation datasets and JSON schemas for all three FDEBench tasks. Used by the local eval harness at `py/apps/eval/run_eval.py`.

## Layout

```
data/
├── task1/                          # Signal Triage
│   ├── input_schema.json           # POST /triage request schema
│   ├── output_schema.json          # POST /triage response schema
│   ├── sample.json                 # 25 signals, local dev set
│   ├── sample_gold.json            # Gold answers for sample set
│   ├── public_eval_50.json         # 50 signals, public eval set
│   └── public_eval_50_gold.json    # Gold answers for public eval
├── task2/                          # Document Extraction (OCR)
│   ├── input_schema.json           # POST /extract request schema
│   ├── output_schema.json           # POST /extract response schema
│   ├── images/                       # 50 PNG document images referenced by content paths
│   ├── public_eval_50.json          # 50 documents, public eval set (image_path format)
│   └── public_eval_50_gold.json     # Gold answers for public eval
└── task3/                          # Workflow Orchestration
    ├── input_schema.json           # POST /orchestrate request schema
    ├── output_schema.json          # POST /orchestrate response schema
    ├── public_eval_50.json         # 50 workflow instances, public eval set
    └── public_eval_50_gold.json    # Gold answers for public eval
```

> The Task 3 public set used to ship a `public_eval_50_mock_responses.json`
> answer-key file. It has been removed (cohort-2 secrecy fix): it was the
> literal trace the platform's mock tool service returns for every public
> task, which collapsed ~60% of the T3 score (resolution + ordering + tool
> selection) to a dictionary lookup. Local iteration uses the live cohort-2
> mock service, see [docs/eval/fdebench.md](../../docs/eval/fdebench.md).

## What these datasets are for

The public eval sets are the same format as the hidden eval sets the platform uses for scoring. Use them to test your endpoints locally before submitting:

```bash
cd py/apps/eval
python run_eval.py --endpoint http://localhost:8000
```

The harness loads these datasets automatically. Task 1 defaults to the 50-item public eval set; pass `--dataset` and `--gold` to use the 25-item sample set instead.

## Hidden eval sets (not included)

The platform scores your submission against larger hidden datasets:

| Task | Hidden items | Standard | Adversarial |
|------|-------------|----------|-------------|
| Task 1 | 1,000 | 660 | 340 |
| Task 2 | 500 | 359 | 141 |
| Task 3 | 500 | 350 | 150 |

Adversarial items are harder cases designed to test robustness (see
[docs/eval/fdebench.md, Robustness](../../docs/eval/fdebench.md#robustness--30-of-each-task)
for what makes something adversarial per task). The `difficulty` label
is **internal**: your public gold does not carry it. We do this so
candidates optimise for uniform robustness rather than gating
engineering effort on a label they can read at submission time.

## How the public set is sampled

The 50-item public sets are not IID-random; each is a stratified sample
of the corresponding hidden set, drawn with proportional largest-remainder
allocation to keep marginal distributions matched within total-variation
distance ≤ 0.05 of the hidden set. The strata are:

* **Task 1**: `(difficulty × category)` across the 8 categories.
* **Task 2**: schema-complexity bins (max nesting depth, count of
  arrays-of-objects).
* **Task 3**: step-count buckets (workflows are grouped by
  expected-step count then sampled).

We deliberately don't publish the per-stratum counts (publishing them
is itself a partial sub-type leak). The high-level shape (the public
set is a stratified sample, not an IID one) is enough to know that
overfitting the 50 will not improve your hidden-set score.

## Reading your local score (calibration, not ranking)

The 50-item public eval sets shipped here are a **calibration probe**,
not a leaderboard preview. Two things to keep in mind:

1. **Statistical noise on N=50 is real.** A submission scoring 70%
   on the public set has a 95% confidence interval of roughly
   ±12.7 percentage points (Wilson interval, N=50, p=0.7). A run
   that scores 65% on the public set is *not* meaningfully worse
   than a run that scores 75%. Use the local set to catch regressions
   (>15-point drops), not to chase 1–2 point gains.

2. **The hidden set is drawn from a different stratification.** The
   hidden 1000/500/500 sets cover more sub-populations and a higher
   adversarial fraction (target 25–35% per task vs. ~20% in the public
   set). Your absolute public-set score will differ from your hidden
   score even with no model change.

The official leaderboard reports your **percentile within your
cohort** ("top N% of cohort 2"), not the absolute score. Optimise for
robustness across the rubric dimensions (resolution, efficiency,
adversarial, API resilience). Chasing a single absolute number on
the public set is unlikely to translate.

## Schemas

Each task directory contains `input_schema.json` and `output_schema.json` defining the request and response contracts. Your endpoints must accept and return JSON matching these schemas.

## Task 1: `sample.json` and `sample_gold.json`

`sample.json` and `sample_gold.json` are a 25-record tutorial set drawn
from the same generator as `public_eval_50.json` and matching the same
schema. Use them for unit tests and your first end-to-end run; they
are deliberately small so iteration is fast. Like the public set they
do not carry the `difficulty` label.

## Task 3: tool responses come from the mock service

The Task 3 mock tool service returns deterministic responses for every
tool call. Locally, the eval harness starts the cohort-2 mock service
on `localhost:9090`, loads `py/data/task3/public_eval_50_mock_responses.json`,
and rewrites each task's `mock_service_url` (and every tool `endpoint`)
to point at it. In production the platform substitutes its own scenario
URL at submission time. The public input ships
`https://example.invalid/scenario/<task_id>` as a deliberate placeholder
(RFC 6761 reserved domain). Don't hard-code the placeholder host.

> **`public_eval_50_mock_responses.json` is the answer key for the
> public 50.** Your local public T3 score will likely be near 100% on
> any working orchestration loop. Treat it as a calibration probe to
> verify your harness wiring, not a leaderboard preview. Hidden eval
> rewrites every `task_id` per submission via an opaque session
> prefix and serves responses from a remote mock service the
> candidate cannot inspect.

Some adversarial scenarios include tool failures (`status_code: 500`)
and your orchestration logic should handle these gracefully.
