# syntax=docker/dockerfile:1.7
# ---- build: install exactly what uv.lock pins into an isolated virtualenv ------------------
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never
WORKDIR /src
# Dependencies first so code edits don't invalidate this layer.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --extra aws --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --extra aws --no-editable

# ---- runtime: slim, non-root, no build tooling ---------------------------------------------
FROM python:3.12-slim AS runtime
# Recorded in every run_manifest.json so a run can be traced to the code that produced it:
#   docker build --build-arg GIT_COMMIT=$(git rev-parse --short HEAD) -t podcast-agent .
ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=${GIT_COMMIT} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    LLM_CACHE_DIR=/cache/llm \
    COLUMNS=140
RUN useradd --create-home --uid 10001 agent
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY --chown=agent:agent data ./data
RUN mkdir -p /app/output /app/queue /cache && chown -R agent:agent /app/output /app/queue /cache
USER agent
EXPOSE 8080
ENTRYPOINT ["podcast-agent"]
# Default: process every transcript in data/transcripts and write reports + logs to /app/output
CMD ["run", "data/transcripts", "--out", "output"]
