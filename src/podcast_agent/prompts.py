"""All prompts in one place so they can be reviewed, versioned and diffed like code."""

from __future__ import annotations

import json

from podcast_agent.models import Episode, format_seconds

PROMPT_VERSION = "2026-10-16.1"


def episode_context(ep: Episode) -> str:
    """Shared, cacheable prefix: identical for every transcript-level step of one episode."""
    guests = ", ".join(ep.guests) or "none listed"
    return f"""You are part of an agentic pipeline that turns raw podcast transcripts into publishable show notes and a fact-check report for an advertising agency's content team.

The transcript may contain filler words, transcription errors, missing punctuation and mis-heard names. Interpret it charitably, but never add facts that are not in it.

<episode id="{ep.episode_id}">
title: {ep.title}
host: {ep.host or "unknown"}
guests: {guests}
length: ~{format_seconds(ep.duration_seconds)} · {ep.word_count} words · {len(ep.utterances)} lines
</episode>

<transcript>
{ep.render()}
</transcript>"""


# --------------------------------------------------------------------------- planner

PLANNER_SYSTEM = (
    "You are the planning module of an autonomous podcast-processing agent. You decide which capabilities "
    "to run, in what order and with which parameters, and you justify each choice in one short sentence. "
    "Respond with JSON only."
)


def planner_prompt(profile: dict, catalog: dict, threshold: int, max_claims_cap: int) -> str:
    return f"""Plan the processing of one podcast episode.

Episode profile:
{json.dumps(profile, indent=2)}

Capability catalog:
{json.dumps(catalog, indent=2)}

Rules
- Deliverables: a summary, key notes (top 5 takeaways, quotes with timestamps, topic tags) and a fact-check table. Every capability marked "required": true must appear.
- Respect "requires": a capability must come after the capabilities it requires.
- Put normalize_transcript first when the profile shows filler or repeated words; otherwise you may leave it out.
- summarize.strategy is "map_reduce" when word_count > {threshold}, otherwise "single_pass".
- extract_claims.max_claims is between {min(3, max_claims_cap)} and {max_claims_cap}; scale it with claim_signals (numbers, years, organisations, predictions).
- verify_claims.max_steps_per_claim is between 2 and 6; verify_claims.tools is a subset of the available tools.

Return JSON:
{{"goal": "<one sentence>", "steps": [{{"step": "<capability>", "reason": "<why, one sentence>", "params": {{}}}}]}}"""


# --------------------------------------------------------------------------- summary

SUMMARY_SYSTEM = "You are a senior podcast producer who writes crisp, accurate show-note summaries. Respond with JSON only."


def summary_prompt(min_words: int, max_words: int) -> str:
    return f"""Task: write the episode summary for the show notes.

Requirements
- Length: {min_words}–{max_words} words. This is a hard limit; count carefully.
- Cover the topics discussed in the order they come up, and the opinions and conclusions shared. Attribute each point to the speaker who made it, by name.
- Report what the speakers say, not how they say it: don't describe tone, mood or attitude ("cautiously", "jokes", "light-hearted", "concedes", "skeptical", "hopeful") unless a speaker says it.
- Add nothing the speakers don't state: no themes or framing of your own, no implications, and no dates, time frames, numbers or comparisons beyond their words.
- If the transcript is short, reach the length by giving each point in full (who said it, the names, figures and dates they used, and how the other speaker replied), never by interpretation.
- Third person, present tense, publishable tone. No timestamps, bullet points or headings.

Return JSON: {{"summary": "<the summary>"}}"""


MAP_SYSTEM = "You condense one segment of a long podcast transcript into faithful notes. Respond with JSON only."


def map_prompt(ep: Episode, segment: str, index: int, total: int) -> str:
    return f"""Episode: "{ep.title}" (host: {ep.host}; guests: {", ".join(ep.guests) or "n/a"}).
This is segment {index} of {total}:

<segment>
{segment}
</segment>

Write 4–8 notes covering who said what: themes, arguments, examples, claims (keep numbers and names exact) and opinions.
Return JSON: {{"notes": ["...", "..."]}}"""


def reduce_prompt(ep: Episode, segment_notes: list[list[str]], min_words: int, max_words: int) -> str:
    blocks = "\n\n".join(
        f"Segment {i + 1}:\n" + "\n".join(f"- {n}" for n in notes) for i, notes in enumerate(segment_notes)
    )
    return f"""Episode: "{ep.title}" (host: {ep.host}; guests: {", ".join(ep.guests) or "n/a"}).
Below are chronological notes from every segment of the episode.

{blocks}

{summary_prompt(min_words, max_words)}"""


# --------------------------------------------------------------------------- key notes

NOTES_SYSTEM = (
    "You are a show-notes editor and social media producer. You extract the most useful, accurate "
    "takeaways and quotable moments from a transcript. Respond with JSON only."
)

NOTES_PROMPT = """Task: extract key notes for the show notes and social media.

Return JSON with exactly these keys:
{
  "takeaways": [{"text": "...", "timestamp": "MM:SS"}],
  "quotes": [{"text": "...", "speaker": "...", "timestamp": "MM:SS"}],
  "topics": ["..."]
}

Rules
- takeaways: exactly 5, most important first. Each is one standalone sentence (max 30 words) that a listener could act on or remember; attribute opinions to the speaker. timestamp = the line that best supports it, copied exactly as written in the transcript.
- quotes: 3 to 5 quotes suitable for social media. Each quote MUST be verbatim — a contiguous span of a single transcript line (6–40 words). You may start or end mid-line, but do not change, fix, reorder or join words. speaker = the full name shown on that line; timestamp = that line's timestamp exactly as written. Prefer vivid, standalone lines; avoid greetings and questions.
- topics: 5–8 tag-style labels in lowercase kebab-case (e.g. "async-communication"), specific to this episode."""


# --------------------------------------------------------------------------- claims

CLAIMS_SYSTEM = (
    "You are a fact-checking editor. You decide which statements in a transcript must be checked before "
    "publication. Respond with JSON only."
)


def claims_prompt(max_claims: int) -> str:
    return f"""Task: identify the statements in this episode that should be fact-checked before publishing.

Include
- factual claims about the world: organisations, products, regulations, events, science, markets, dates, numbers;
- predictions and personal anecdotes that listeners might take as fact (label them so; they are usually unverifiable).
Exclude pure opinions, advice, questions and pleasantries.

For each claim
- "claim": one standalone sentence that makes sense out of context (resolve pronouns, name the subject, keep the speaker's meaning and any numbers or dates exactly);
- "type": factual | statistical | historical | prediction | anecdotal;
- "time_sensitive": true if the claim's truth can change over time (schedules, counts, "currently", "recently", dates);
- "speaker": full name as shown; "timestamp": exactly as written; "quote": verbatim span from that line that supports the claim.

Return at most {max_claims} claims, most check-worthy first:
{{"claims": [{{"claim": "...", "type": "factual", "time_sensitive": false, "speaker": "...", "timestamp": "...", "quote": "..."}}]}}"""


# --------------------------------------------------------------------------- verification (ReAct)


def verify_system(today: str, max_steps: int, tools: list[str]) -> str:
    tool_lines = []
    if "search_knowledge_base" in tools:
        tool_lines.append(
            '- search_knowledge_base {"query": str}: the agency\'s curated fact base. Trusted; every entry has an as-of date.'
        )
    if "web_search" in tools:
        tool_lines.append(
            '- web_search {"query": str}: web results with publish dates. Broader but noisier; results may be stale or wrong.'
        )
    tool_lines.append(
        '- finish {"verdict": "verified" | "possibly_outdated_or_inaccurate" | "unverifiable", "confidence": 0.0-1.0, '
        '"rationale": str, "evidence_ids": [str]}'
    )
    tools_block = "\n".join(tool_lines)
    return f"""You are a meticulous fact-checker for an advertising agency's content team. Today's date is {today}.
You verify ONE claim at a time by calling tools, and you answer every turn with exactly one JSON object:
{{"action": "<tool name>", "input": {{...}}, "purpose": "<one short sentence for the audit log: what this call should establish>"}}
Keep the purpose to one short sentence about the call itself.

Tools
{tools_block}

Verdict policy
- verified: reliable, current evidence supports the claim as stated.
- possibly_outdated_or_inaccurate: evidence contradicts the claim, shows it overstated, or shows it was true once but has changed.
- unverifiable: no adequate evidence was found, or the claim is a personal anecdote or a prediction about the future. A prediction whose deadline has already passed IS checkable — check it.

Working rules
- Start with the knowledge base. Use web search when the knowledge base is insufficient or the claim is time-sensitive.
- Write focused search queries (key entities + the specific fact), not the whole claim.
- You may make at most {max_steps} tool calls before you must finish; finish as soon as the evidence is sufficient.
- The rationale is 1–3 sentences and cites evidence ids like [E2]. evidence_ids lists the ids you relied on.
- Do not rely on memory alone. If you must, say so in the rationale and keep confidence at or below 0.6.
- confidence = how strongly the evidence supports your verdict (not how likely the claim is to be true)."""


def verify_opening(claim, episode_title: str) -> str:
    sensitive = ", time-sensitive" if claim.time_sensitive else ""
    return (
        f'Claim {claim.id} ({claim.type}{sensitive}): "{claim.claim}"\n'
        f'Said by {claim.speaker} at [{claim.timestamp}] in the episode "{episode_title}": "{claim.quote}"\n'
        "Begin."
    )
