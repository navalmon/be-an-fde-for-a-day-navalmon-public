#!/usr/bin/env python3
"""Lightweight mock tool service for local Task 3 testing.

Serves deterministic tool responses from ``public_eval_50_mock_responses.json``
so your ``POST /orchestrate`` endpoint can make real HTTP calls during local
development and evaluation.

The eval harness (``run_eval.py``) starts this automatically. You can also
run it manually:

    cd py/apps/eval
    python mock_tool_service.py                   # default port 9090
    python mock_tool_service.py --port 9091       # custom port

Then point your orchestration code at ``http://localhost:9090/scenario/{task_id}/{tool_name}``.
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

try:
    from fastapi import FastAPI
    from fastapi import Request
    from fastapi.responses import JSONResponse
except ImportError:
    _FASTAPI_AVAILABLE = False
else:
    _FASTAPI_AVAILABLE = True

try:
    import uvicorn
except ImportError:
    uvicorn = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

app = FastAPI(title="FDEBench Mock Tool Service (local)", version="1.0.0") if _FASTAPI_AVAILABLE else None  # type: ignore[assignment]

# State
# { "TASK-0001": { "crm_search": [{call_index, status_code, response_body}, ...] } }
_MOCK_DATA: dict[str, dict[str, list[dict[str, Any]]]] = {}
# { task_id: { tool_name: call_count } }
_CALL_COUNTERS: dict[str, dict[str, int]] = {}

_SESSION_SEP = "__"


def load_responses(path: Path) -> int:
    """Load mock response data. Returns number of scenarios loaded."""
    if not path.exists():
        logger.error("Mock responses file not found: %s", path)
        return 0
    raw = json.loads(path.read_text(encoding="utf-8"))
    _MOCK_DATA.clear()
    for task_id, responses in raw.items():
        _MOCK_DATA[task_id] = {}
        for resp in responses:
            tool = resp["tool_name"]
            _MOCK_DATA[task_id].setdefault(tool, []).append(resp)
    return len(_MOCK_DATA)


def _resolve_task_id(session_task_id: str) -> str:
    """Strip session prefix: ``sub-xyz__TASK-0001`` → ``TASK-0001``."""
    if _SESSION_SEP in session_task_id:
        return session_task_id.split(_SESSION_SEP, 1)[1]
    return session_task_id


# Routes (only defined when FastAPI is available)

if _FASTAPI_AVAILABLE:

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "scenarios_loaded": str(len(_MOCK_DATA))}

    @app.post("/scenario/{task_id}/{tool_name}")
    async def call_tool(task_id: str, tool_name: str, request: Request) -> Any:
        """Return the next canned response for this tool in this scenario."""
        original = _resolve_task_id(task_id)

        if original not in _MOCK_DATA:
            return JSONResponse(status_code=404, content={"error": f"Unknown scenario: {original}"})

        tool_responses = _MOCK_DATA[original].get(tool_name)
        if not tool_responses:
            return JSONResponse(
                status_code=404,
                content={"error": f"No mock for tool '{tool_name}' in scenario {original}"},
            )

        # Track call count per (task_id, tool_name)
        counters = _CALL_COUNTERS.setdefault(task_id, {})
        idx = counters.get(tool_name, 0)
        counters[tool_name] = idx + 1

        # Find matching call_index or fall back to last response
        matching = [r for r in tool_responses if r.get("call_index") == idx]
        resp = matching[0] if matching else tool_responses[-1]

        status_code = resp["status_code"]
        body = resp["response_body"]
        if status_code != 200:
            return JSONResponse(status_code=status_code, content=body)
        return body

    @app.post("/scenario/{task_id}/reset")
    async def reset_scenario(task_id: str) -> dict[str, str]:
        """Reset call counters for one scenario."""
        _CALL_COUNTERS.pop(task_id, None)
        return {"status": "reset", "task_id": task_id}

    @app.post("/reset")
    async def reset_all() -> dict[str, str]:
        """Reset all call counters."""
        count = len(_CALL_COUNTERS)
        _CALL_COUNTERS.clear()
        return {"status": "reset_all", "sessions_cleared": str(count)}

    @app.post("/session/{prefix}/reset")
    async def reset_session(prefix: str) -> dict[str, str]:
        """Reset counters for all scenarios with a given session prefix."""
        to_remove = [k for k in _CALL_COUNTERS if k.startswith(prefix + _SESSION_SEP) or k == prefix]
        for k in to_remove:
            del _CALL_COUNTERS[k]
        return {"status": "reset", "session": prefix, "keys_removed": str(len(to_remove))}


# Startup & CLI

_DEFAULT_MOCK_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "task3" / "public_eval_50_mock_responses.json"
)


def start(port: int = 9090, mock_path: Path | None = None) -> None:
    """Load data and start the server (blocking)."""
    if not _FASTAPI_AVAILABLE or uvicorn is None:
        print(
            "mock_tool_service requires fastapi and uvicorn.\n"
            "Install them:  uv pip install fastapi uvicorn\n"
            "Or from the workspace root:  cd py && uv sync",
            file=sys.stderr,
        )
        sys.exit(1)
    path = mock_path or _DEFAULT_MOCK_PATH
    loaded = load_responses(path)
    if loaded == 0:
        logger.error("No mock responses loaded from %s, exiting", path)
        sys.exit(1)
    logger.info("Mock tool service: %d scenarios from %s", loaded, path.name)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(message)s")
    parser = argparse.ArgumentParser(description="FDEBench mock tool service for local testing")
    parser.add_argument("--port", type=int, default=9090, help="Port to listen on (default: 9090)")
    parser.add_argument("--mock-path", type=Path, default=None, help="Path to mock responses JSON")
    cli_args = parser.parse_args()
    start(port=cli_args.port, mock_path=cli_args.mock_path)
