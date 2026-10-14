"""Reasoning trace: human-readable console log + machine-readable JSONL.

Every plan, thought, tool call, observation, self-correction and verdict goes through
`Tracer.event`. The console view is what you watch live; `trace.jsonl` is what you would
ship to an observability stack (each record carries run_id / episode_id / step).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape

KIND_STYLE: dict[str, tuple[str, str]] = {
    "info": ("·", "white"),
    "ingest": ("▸", "cyan"),
    "plan": ("◆", "bold magenta"),
    "step": ("▶", "bold cyan"),
    "llm": ("⇄", "dim"),
    "thought": ("💭", "italic yellow"),
    "action": ("🔧", "bold blue"),
    "observation": ("👁 ", "green"),
    "verdict": ("⚖️ ", "bold"),
    "self_correct": ("↻", "bold yellow"),
    "warn": ("⚠", "bold yellow"),
    "error": ("✖", "bold red"),
    "result": ("✔", "bold green"),
}


STEP_LABEL = {
    "normalize_transcript": "normalize",
    "extract_notes": "notes",
    "extract_claims": "claims",
    "verify_claims": "verify",
}


class Tracer:
    def __init__(
        self,
        run_id: str,
        log_dir: str | Path | None = None,
        console: Console | None = None,
        verbose: bool = True,
    ):
        self.run_id = run_id
        self.console = console or Console(highlight=False, soft_wrap=False)
        self.verbose = verbose
        self.episode_id: str | None = None
        self._jsonl = None
        self._plain = None
        self.jsonl_path: Path | None = None
        self.events_written = 0
        if log_dir:
            path = Path(log_dir)
            path.mkdir(parents=True, exist_ok=True)
            self.jsonl_path = path / "trace.jsonl"
            self._jsonl = self.jsonl_path.open("a", encoding="utf-8")
            self._plain = (path / "run.log").open("a", encoding="utf-8")

    # ------------------------------------------------------------------ core
    def event(self, kind: str, step: str, message: str, *, indent: int = 0, **data: Any) -> None:
        now = datetime.now(UTC)
        record = {
            "ts": now.isoformat(timespec="milliseconds"),
            "run_id": self.run_id,
            "episode_id": self.episode_id,
            "step": step,
            "kind": kind,
            "message": message,
        }
        if data:
            record["data"] = _jsonable(data)
        if self._jsonl:
            self._jsonl.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._jsonl.flush()
            self.events_written += 1
        if kind == "llm" and not self.verbose:
            return
        icon, style = KIND_STYLE.get(kind, ("·", "white"))
        pad = "   " * indent
        clock = now.astimezone().strftime("%H:%M:%S")
        label = f"{STEP_LABEL.get(step, step):<14.14}"
        plain_line = f"{clock} {label} {pad}{icon} {message}"
        self.console.print(
            f"[dim]{clock}[/dim] [cyan]{escape(label)}[/cyan] {pad}[{style}]{escape(icon)} {escape(message)}[/{style}]",
            overflow="fold",
        )
        if self._plain:
            self._plain.write(plain_line + "\n")
            self._plain.flush()

    def rule(self, title: str) -> None:
        self.console.rule(f"[bold]{escape(title)}")
        if self._plain:
            self._plain.write(f"\n{'━' * 8} {title} {'━' * max(4, 90 - len(title))}\n")
        if self._jsonl:
            self._jsonl.write(
                json.dumps({"ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
                            "run_id": self.run_id, "episode_id": self.episode_id, "kind": "section",
                            "message": title}, ensure_ascii=False) + "\n"
            )
            self._jsonl.flush()
            self.events_written += 1

    # ------------------------------------------------------------------ helpers
    def info(self, step: str, message: str, **data: Any) -> None:
        self.event("info", step, message, **data)

    def warn(self, step: str, message: str, **data: Any) -> None:
        self.event("warn", step, message, **data)

    def error(self, step: str, message: str, **data: Any) -> None:
        self.event("error", step, message, **data)

    def llm_call(self, step: str, resp: Any, attempt: int = 1) -> None:
        if resp.cached:
            msg = f"{resp.model} · response cache hit"
        else:
            approx = "~" if resp.estimated else ""
            cache = f" (+{resp.cache_read_tokens:,} cached)" if resp.cache_read_tokens else ""
            msg = (f"{resp.model} · {approx}{resp.input_tokens:,}{cache} → {approx}{resp.output_tokens:,} tok"
                   f" · {resp.latency_ms / 1000:.1f}s")
        if attempt > 1:
            msg += f" · attempt {attempt}"
        self.event("llm", step, msg, model=resp.model, input_tokens=resp.input_tokens,
                   output_tokens=resp.output_tokens, cache_read_tokens=resp.cache_read_tokens,
                   latency_ms=resp.latency_ms, cached=resp.cached, attempt=attempt)

    def close(self) -> None:
        for fh in (self._jsonl, self._plain):
            if fh:
                fh.close()


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
