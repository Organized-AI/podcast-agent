"""Structured output with validation and self-correction.

Every LLM step returns JSON that must (1) parse, (2) satisfy a Pydantic schema, and
(3) pass step-specific checks (word counts, verbatim quotes, valid timestamps...). When any
check fails, the specific problems are sent back to the model and it gets another attempt.
This loop works identically across providers, which is why the agent does not depend on
any one vendor's tool-calling or JSON-mode feature.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from podcast_agent.llm.base import Tier

if TYPE_CHECKING:
    from podcast_agent.agent.context import RunContext

T = TypeVar("T", bound=BaseModel)
FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class StructuredOutputError(RuntimeError):
    def __init__(self, step: str, problems: list[str]):
        super().__init__(f"{step}: could not obtain valid output — {'; '.join(problems[:3])}")
        self.step = step
        self.problems = problems


@dataclass
class StructuredResult(Generic[T]):
    value: T
    attempts: int
    unresolved: list[str] = field(default_factory=list)  # validator issues still present after the last attempt


def extract_json(text: str) -> Any:
    """Pull the first JSON object/array out of a model response (tolerates code fences and prose)."""
    text = text.strip()
    candidates = []
    fenced = FENCE.search(text)
    if fenced:
        candidates.append(fenced.group(1).strip())
    candidates.append(text)
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth, in_str, escaped = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError("response did not contain a JSON object")


def call_structured(
    ctx: RunContext,
    *,
    step: str,
    schema: type[T],
    system: str,
    prompt: str,
    tier: Tier = "main",
    max_tokens: int = 1500,
    cache_prefix: str | None = None,
    validate: Callable[[T], list[str]] | None = None,
    task: str | None = None,
    context: dict[str, Any] | None = None,
    max_attempts: int | None = None,
) -> StructuredResult[T]:
    max_attempts = max_attempts or ctx.settings.max_attempts
    messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
    ctx_payload = dict(context or {})
    last_valid: T | None = None
    problems: list[str] = []
    for attempt in range(1, max_attempts + 1):
        ctx_payload["feedback"] = problems
        resp = ctx.llm.complete(
            system=system,
            messages=messages,
            tier=tier,
            max_tokens=max_tokens,
            temperature=ctx.settings.temperature,
            cache_prefix=cache_prefix,
            task=task or step,
            context=ctx_payload,
        )
        ctx.record_usage(resp)
        ctx.trace.llm_call(step, resp, attempt)
        if resp.stop_reason == "max_tokens":
            ctx.trace.warn(step, "response hit the max_tokens limit and may be truncated")
        try:
            value = schema.model_validate(extract_json(resp.text))
            last_valid = value
            problems = validate(value) if validate else []
        except (ValueError, ValidationError) as exc:
            problems = [_short_error(exc)]
        if not problems:
            return StructuredResult(value=value, attempts=attempt)
        ctx.trace.event(
            "self_correct", step,
            f"attempt {attempt}/{max_attempts} rejected — {problems[0]}"
            + (f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""),
            problems=problems,
        )
        messages = messages + [
            {"role": "assistant", "content": resp.text},
            {
                "role": "user",
                "content": "Your previous response had these problems:\n- "
                + "\n- ".join(problems)
                + "\nFix every problem and return the complete corrected JSON only.",
            },
        ]
    if last_valid is not None:
        return StructuredResult(value=last_valid, attempts=max_attempts, unresolved=problems)
    raise StructuredOutputError(step, problems)


def _short_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        errs = exc.errors()
        parts = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in errs[:3]]
        return "schema validation failed: " + "; ".join(parts)
    return f"invalid JSON: {exc}"
