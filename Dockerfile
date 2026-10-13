# syntax=docker/dockerfile:1.7
# ---- build: install the package into an isolated virtualenv --------------------------------
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m venv /opt/venv && /opt/venv/bin/pip install ".[aws]"

# ---- runtime: slim, non-root, no build tooling ---------------------------------------------
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    LLM_CACHE_DIR=/cache/llm \
    COLUMNS=140
RUN useradd --create-home --uid 10001 agent
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY data ./data
RUN mkdir -p /app/output /app/queue /cache && chown -R agent:agent /app/output /app/queue /cache
USER agent
EXPOSE 8080
ENTRYPOINT ["podcast-agent"]
# Default: process every transcript in data/transcripts and write reports + logs to /app/output
CMD ["run", "data/transcripts", "--out", "output"]
