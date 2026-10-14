"""Runtime configuration, read from environment variables (and CLI overrides)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _env_float(name: str, default: float | None) -> float | None:
    value = _env(name)
    if value is None:
        return default
    if value.lower() in {"none", "null", "off"}:
        return None
    return float(value)


def _env_effort(name: str, default: str) -> str | None:
    value = (_env(name, default) or "").lower()
    return None if value in {"none", "off", "default"} else value


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # --- model provider -----------------------------------------------------------------
    provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "auto"))  # auto|anthropic|bedrock|openai|ollama|offline
    anthropic_api_key: str | None = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    anthropic_model: str = field(default_factory=lambda: _env("ANTHROPIC_MODEL", "claude-sonnet-5-5"))
    anthropic_fast_model: str = field(default_factory=lambda: _env("ANTHROPIC_FAST_MODEL", "claude-haiku-5-5"))
    # Claude on Amazon Bedrock: AWS credentials come from the standard chain (env vars, ~/.aws, IAM role / IRSA).
    bedrock_model: str | None = field(default_factory=lambda: _env("BEDROCK_MODEL"))
    bedrock_fast_model: str | None = field(default_factory=lambda: _env("BEDROCK_FAST_MODEL"))
    aws_region: str = field(default_factory=lambda: _env("AWS_REGION", _env("AWS_DEFAULT_REGION", "us-east-1")))
    prompt_cache: bool = field(default_factory=lambda: _env_bool("PROMPT_CACHE", True))
    # Claude effort level (low|medium|high|xhigh|max); "none" sends no effort, for older models.
    anthropic_effort: str | None = field(default_factory=lambda: _env_effort("ANTHROPIC_EFFORT", "low"))
    openai_api_key: str | None = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_base_url: str = field(default_factory=lambda: _env("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    openai_model: str | None = field(default_factory=lambda: _env("OPENAI_MODEL"))
    openai_fast_model: str | None = field(default_factory=lambda: _env("OPENAI_FAST_MODEL"))
    openai_max_tokens_param: str = field(default_factory=lambda: _env("OPENAI_MAX_TOKENS_PARAM", "max_tokens"))
    ollama_base_url: str = field(default_factory=lambda: _env("OLLAMA_BASE_URL", "http://localhost:11434/v1"))
    ollama_model: str = field(default_factory=lambda: _env("OLLAMA_MODEL", "llama3.1:8b"))
    temperature: float | None = field(default_factory=lambda: _env_float("LLM_TEMPERATURE", 0.2))
    llm_timeout_s: float = field(default_factory=lambda: float(_env("LLM_TIMEOUT_S", "90")))

    # --- response cache -----------------------------------------------------------------
    cache_enabled: bool = field(default_factory=lambda: _env_bool("LLM_CACHE", True))
    cache_dir: str = field(default_factory=lambda: _env("LLM_CACHE_DIR", ".cache/llm"))

    # --- tools ----------------------------------------------------------------------------
    search_provider: str = field(default_factory=lambda: _env("SEARCH_PROVIDER", "mock"))  # mock|tavily|none
    tavily_api_key: str | None = field(default_factory=lambda: _env("TAVILY_API_KEY"))
    kb_path: str = field(default_factory=lambda: _env("KB_PATH", "data/knowledge_base.json"))
    mock_web_path: str = field(default_factory=lambda: _env("MOCK_WEB_PATH", "data/mock_web_index.json"))
    mock_search_latency_ms: int = field(default_factory=lambda: int(_env("MOCK_SEARCH_LATENCY_MS", "0")))
    mock_search_failure_rate: float = field(default_factory=lambda: float(_env("MOCK_SEARCH_FAILURE_RATE", "0")))

    # --- task parameters ------------------------------------------------------------------
    summary_min_words: int = 200
    summary_max_words: int = 300
    # "spec": target 200–300 words (accept ±10% for the spec's "~").
    # "adaptive": lower the floor to ~60% of very short transcripts to avoid padding.
    summary_length_mode: str = field(default_factory=lambda: _env("SUMMARY_LENGTH_MODE", "spec"))
    map_reduce_threshold_words: int = field(default_factory=lambda: int(_env("MAP_REDUCE_THRESHOLD_WORDS", "15000")))
    max_claims: int = field(default_factory=lambda: int(_env("MAX_CLAIMS", "8")))
    max_steps_per_claim: int = field(default_factory=lambda: int(_env("MAX_STEPS_PER_CLAIM", "4")))
    max_attempts: int = field(default_factory=lambda: int(_env("MAX_ATTEMPTS", "3")))
    # "Today" for the fact-checker. Pin it (AGENT_TODAY=2026-10-13) for reproducible runs.
    today: str = field(default_factory=lambda: _env("AGENT_TODAY", date.today().isoformat()))

    def resolved_provider(self) -> str:
        if self.provider != "auto":
            return self.provider
        if self.anthropic_api_key:
            return "anthropic"
        if self.openai_api_key and self.openai_model:
            return "openai"
        return "offline"
