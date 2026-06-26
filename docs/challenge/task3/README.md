# Task 3: Workflow Orchestration

`POST /orchestrate`

Given a goal, a set of tools, and a list of constraints, execute the workflow and return what happened. Actually call the tools: the benchmark checks real execution, not just a plan that looks reasonable.

Read the background:

- [customer_brief.md](customer_brief.md): what the customer needs
- [execution_guide.md](execution_guide.md): how to think about execution
- [engineering_review.md](engineering_review.md): what judges look for

## Request contract

Input fields:

- `task_id`
- `goal`
- `available_tools`
- `constraints`

`available_tools[].parameters` is a list of parameter objects:

```json
{
  "name": "filter",
  "type": "string",
  "description": "Search filter expression",
  "required": true
}
```

See [../../../py/data/task3/input_schema.json](../../../py/data/task3/input_schema.json) for the formal schema.

## Response contract

Required output fields:

- `task_id`
- `status`
- `steps_executed`

Common additional fields include:

- `constraints_satisfied`
- `accounts_processed`
- `emails_sent`
- `emails_skipped`
- `skip_reasons`

See [../../../py/data/task3/output_schema.json](../../../py/data/task3/output_schema.json) for the formal schema.

## Resolution scoring

```
resolution = (0.20 x goal_completion + 0.15 x tool_selection + 0.05 x parameter_accuracy + 0.20 x ordering_correctness + 0.40 x constraint_compliance) x 100
```

| Dimension | Weight | Metric |
|---|---|---|
| `goal_completion` | 20% | End-state match |
| `tool_selection` | 15% | Set F1 on tools used |
| `parameter_accuracy` | 5% | Per-call parameter match |
| `ordering_correctness` | 20% | Dependency satisfaction |
| `constraint_compliance` | 40% | Outcome assertions |

## How tool calls work

Each workflow scenario includes an `available_tools` array. Each tool has a `name`, `description`, `endpoint`, and `parameters`. Your code should actually call these tool endpoints via HTTP; the benchmark scores real execution, not a simulated plan.

During local testing, the eval harness starts a mock tool service automatically on `localhost:9090`. It rewrites all tool endpoint URLs in the test data to point at this local service, and adds a `mock_service_url` field to each request. The mock service returns deterministic canned responses from `py/data/task3/public_eval_50_mock_responses.json`.

During platform scoring, the platform does the same rewriting: it points tool URLs at a production mock service and adds the `mock_service_url` field. Your code doesn't need to know the difference.

### What your `/orchestrate` endpoint receives at scoring time

```json
{
  "task_id": "TASK-0015",
  "goal": "Analyze churn risk for ...",
  "available_tools": [
    {
      "name": "crm_search",
      "endpoint": "http://localhost:9090/scenario/TASK-0015/crm_search",
      "parameters": [...]
    }
  ],
  "constraints": ["..."],
  "mock_service_url": "http://localhost:9090/scenario/TASK-0015"
}
```

Your code should either:
- Call each tool's `endpoint` directly (the URL is already rewritten), **or**
- Use `mock_service_url` + `/{tool_name}` to construct tool URLs yourself

Both approaches work identically. The tool endpoints return JSON with realistic data (CRM records, inventory levels, email confirmations, etc.).

### Calling tools: an example

```python
# Option A: Use the tool's endpoint directly
for tool in task["available_tools"]:
    resp = httpx.post(tool["endpoint"], json={"filter": "...", "limit": 100})

# Option B: Build URL from mock_service_url
base = task["mock_service_url"]
resp = httpx.post(f"{base}/crm_search", json={"filter": "...", "limit": 100})
```

## Local testing

The eval harness handles everything automatically:

```bash
cd py/apps/eval
python run_eval.py --endpoint http://localhost:8000 --task orchestrate
```

This will:
1. Start a mock tool service on port 9090 (loads `py/data/task3/public_eval_50_mock_responses.json`)
2. Rewrite all tool URLs in the test data to `http://localhost:9090/scenario/{task_id}/{tool_name}`
3. Call your `POST /orchestrate` endpoint with each scenario
4. Score your results against the gold answers
5. Shut down the mock service

You can also run the mock service manually for ad-hoc testing:

```bash
# Terminal 1: start mock tools
cd py/apps/eval
python mock_tool_service.py

# Terminal 2: start your solution
uvicorn my_app:app --port 8000

# Terminal 3: test a single call
curl -s http://localhost:9090/health
# → {"status":"ok","scenarios_loaded":"50"}

curl -X POST http://localhost:9090/scenario/TASK-0015/crm_search -H "Content-Type: application/json" -d '{}'
# → {"accounts": [{"account_id": "ACC-0015-0", ...}, ...]}
```

> **Calibration only: your local public T3 score will be near 100%.**
>
> The shipped `py/data/task3/public_eval_50_mock_responses.json` is the
> deterministic answer key for the 50 public scenarios. The local mock
> service replays it byte-for-byte, so a working orchestration loop will
> almost always achieve a perfect score on the public set. **Use this to
> verify your harness wiring (your endpoint speaks the contract, parses
> the responses, satisfies basic constraints), not as a leaderboard
> preview.**
>
> Hidden eval rewrites every `task_id` per submission via an opaque
> session prefix and serves responses from a remote mock service the
> candidate cannot inspect. Your hidden score depends on actually
> *executing* the workflow correctly, not on memorising the public
> answer key.

## What's hard

Multiple valid plans exist. Parameters need to be computed, not copied from the goal. Constraint violations tank your score even if the trace looks clean. Some workflows have ambiguous goals or failing tools. The benchmark tests what you actually did, not what you said you'd do.

## Tips

- Call the tool endpoints via HTTP. Don't simulate responses; the scorer checks what you actually called.
- Small verifiable steps beat one opaque jump.
- Handle failures explicitly. Crashing on a 503 is not recovery.
