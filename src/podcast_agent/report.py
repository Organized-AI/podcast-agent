"""Render EpisodeReport objects as Markdown (show notes + fact-check table) and a run index."""

from __future__ import annotations

from collections import Counter

from podcast_agent.models import EpisodeReport, Verdict

SOURCE_LABEL = {"knowledge_base": "KB", "web_search": "Web", "model_knowledge": "Model"}


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def verdict_counts(report: EpisodeReport) -> Counter:
    return Counter(fc.verdict for fc in report.fact_checks)


def render_markdown(r: EpisodeReport) -> str:
    e = r.episode
    out: list[str] = [f"# {e.title}", ""]
    guests = ", ".join(e.guests) or "—"
    out += [f"**Episode** {e.episode_id} · **Host** {e.host or '—'} · **Guests** {guests} · "
            f"**Length** ~{e.duration} ({e.word_count} words)", ""]

    out += ["## Summary", ""]
    if r.summary:
        out += [r.summary.text, "", f"<sub>{r.summary.word_count} words · {r.summary.strategy.replace('_', '-')}</sub>", ""]
    else:
        out += ["_Summary not generated — see warnings below._", ""]

    out += ["## Key notes", ""]
    if r.notes:
        out += ["### 🔹 Top 5 takeaways", ""]
        for i, t in enumerate(r.notes.takeaways, 1):
            ts = f" · `[{t.timestamp}]`" if t.timestamp else ""
            out.append(f"{i}. {t.text}{ts}")
        out += ["", "### 💬 Notable quotes", ""]
        for q in r.notes.quotes:
            out += [f"> “{q.text}”  ", f"> — **{q.speaker}**, `[{q.timestamp}]`", ""]
        out += ["### 🧭 Topics discussed", "", " ".join(f"`#{t}`" for t in r.notes.topics), ""]
    else:
        out += ["_Key notes not generated — see warnings below._", ""]

    out += ["## Fact-check", ""]
    if r.fact_checks:
        out += ["| Claim | Verification | Confidence |", "|---|---|---|"]
        for row in r.fact_check_table:
            out.append(f"| {_cell(row.claim)} | {_cell(row.verification)} | {row.confidence:.2f} |")
        counts = verdict_counts(r)
        out += [
            "",
            f"<sub>{counts[Verdict.VERIFIED]} ✅ verified · {counts[Verdict.OUTDATED]} ⚠️ possibly outdated/inaccurate · "
            f"{counts[Verdict.UNVERIFIABLE]} ❓ unverifiable. Confidence is how strongly the evidence supports the "
            "verdict; unverifiable verdicts are capped at 0.50 and evidence-free verdicts at 0.60.</sub>",
            "",
            "<details><summary>Evidence and reasoning for each claim (speaker, timestamp, sources)</summary>",
            "",
        ]
        for fc in r.fact_checks:
            tools = f" · {fc.tool_calls} tool call(s): {', '.join(fc.tools_used)}" if fc.tool_calls else ""
            out += [
                f"**{fc.claim.id}. {_cell(fc.claim.claim)}**  ",
                f"{fc.verdict.emoji} {fc.verdict.label} · confidence {fc.confidence:.2f} (model reported "
                f"{fc.raw_confidence:.2f}) · method: {fc.method}{tools}  ",
                f"_{fc.claim.type}{', time-sensitive' if fc.claim.time_sensitive else ''}_ · "
                f"“{fc.claim.quote}” — {fc.claim.speaker} `[{fc.claim.timestamp}]`  ",
                f"{fc.rationale}",
                "",
            ]
            for ev in fc.evidence:
                date = f" ({'as of' if ev.source == 'knowledge_base' else 'published'} {ev.date})" if ev.date else ""
                link = f" — <{ev.url}>" if ev.url else ""
                out.append(f"- `[{ev.id}]` {SOURCE_LABEL.get(ev.source, ev.source)} · {ev.title}{date}{link}")
            out.append("")
        out += ["</details>", ""]
    elif any(s.step == "verify_claims" and s.status != "ok" for s in r.steps):
        out += ["_Fact-check not completed — see warnings below._", ""]
    else:
        out += ["_No check-worthy factual claims were found._", ""]

    out += ["## Agent run", ""]
    if r.plan:
        out += [f"**Plan** — {r.plan.goal}", ""]
        for i, s in enumerate(r.plan.steps, 1):
            params = f" `{s.params}`" if s.params else ""
            out.append(f"{i}. `{s.step}`{params} — {s.reason}")
        if r.plan.repair_notes:
            out += ["", "Plan validator: " + "; ".join(r.plan.repair_notes)]
        out.append("")
    out += ["| Step | Status | Time | LLM attempts | Detail |", "|---|---|---|---|---|"]
    for st in r.steps:
        out.append(f"| {st.step} | {st.status} | {st.duration_ms / 1000:.1f}s | {st.attempts} | {_cell(st.detail)} |")
    u = r.usage
    approx = "~" if u.estimated else ""
    out += [
        "",
        f"Provider **{r.provider}** · models {r.models.get('main')} / {r.models.get('fast')} · "
        f"{u.llm_calls} LLM calls ({u.cached_calls} from cache) · {approx}{u.input_tokens:,} input / "
        f"{approx}{u.output_tokens:,} output tokens"
        + (f" · {u.cache_read_tokens:,} prompt-cache read tokens" if u.cache_read_tokens else "")
        + f" · run `{r.run_id}`",
        "",
    ]
    if r.warnings:
        out += ["**Warnings**", ""] + [f"- {w}" for w in r.warnings] + [""]
    return "\n".join(out)


def render_index(reports: list[EpisodeReport], run_id: str) -> str:
    lines = [f"# Podcast agent run `{run_id}`", ""]
    if not reports:
        return "\n".join([*lines, "_No episodes processed._", ""])
    r0 = reports[0]
    lines += [f"Provider **{r0.provider}** ({r0.models.get('main')} / {r0.models.get('fast')})", ""]
    lines += [
        "| Episode | Summary | Takeaways | Quotes | Topics | Claims (✅ / ⚠️ / ❓) | LLM calls | Tokens in / out |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in reports:
        c = verdict_counts(r)
        n = r.notes
        approx = "~" if r.usage.estimated else ""
        lines.append(
            f"| [{_cell(r.episode.title)}]({r.episode.episode_id}.md) | "
            f"{r.summary.word_count if r.summary else '—'} words | {len(n.takeaways) if n else '—'} | "
            f"{len(n.quotes) if n else '—'} | {len(n.topics) if n else '—'} | "
            f"{len(r.fact_checks)} ({c[Verdict.VERIFIED]} / {c[Verdict.OUTDATED]} / {c[Verdict.UNVERIFIABLE]}) | "
            f"{r.usage.llm_calls} | {approx}{r.usage.input_tokens:,} / {approx}{r.usage.output_tokens:,} |"
        )
    lines += ["", "Each episode also has a machine-readable `<episode_id>.json`. The agent's reasoning trace is in "
              "`logs/run.log` (human-readable) and `logs/trace.jsonl` (structured).", ""]
    return "\n".join(lines)


def render_combined(reports: list[EpisodeReport], run_id: str) -> str:
    """One Markdown file with every episode's summary, notes and fact-check table (the PDF's output file)."""
    lines = ["# Podcast show notes and fact-checks", ""]
    if not reports:
        return "\n".join([*lines, "_No episodes processed._", ""])
    r0 = reports[0]
    lines += [
        f"Run `{run_id}` · provider **{r0.provider}** ({r0.models.get('main')} / {r0.models.get('fast')}) · "
        f"{len(reports)} episode(s)",
        "",
        "## Episodes",
        "",
    ]
    for r in reports:
        c = verdict_counts(r)
        lines.append(f"- {r.episode.title} ({r.episode.episode_id}): {len(r.fact_checks)} claims, "
                     f"{c[Verdict.VERIFIED]} ✅ · {c[Verdict.OUTDATED]} ⚠️ · {c[Verdict.UNVERIFIABLE]} ❓")
    lines.append("")
    for r in reports:
        lines += ["---", ""]
        for line in render_markdown(r).splitlines():
            lines.append("#" + line if line.startswith("#") else line)  # nest each episode one level down
    return "\n".join(lines) + "\n"
