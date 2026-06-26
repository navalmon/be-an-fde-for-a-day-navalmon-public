FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

COPY py/pyproject.toml py/uv.lock ./py/
COPY py/common ./py/common
COPY py/apps/sample ./py/apps/sample

WORKDIR /app/py
RUN uv sync --frozen --package sample --no-dev

ENV PYTHONPATH=/app/py/common/libs/models/src:/app/py/common/libs/fdebenchkit/src:/app/py/common/libs/fastapi/src
ENV FDE_SERVICE_NAME="FDEBench API"

WORKDIR /app/py/apps/sample
EXPOSE 8000

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
