"""Deterministic, lexical stand-ins for each LLM task (used by the offline provider).

These exist so the whole agent — planning, validation, self-correction, tool loop, reports —
runs in CI and on a laptop with no API key or network. They are intentionally simple
(extractive summaries, keyword topics, lexical evidence matching) and are NOT a substitute for
a real model: use them to test the plumbing, use Claude/Ollama for real output.
"""

from __future__ import annotations

import re
from collections import Counter
from itertools import pairwise
from typing import Any

from podcast_agent.models import Episode, Utterance
from podcast_agent.text import STOPWORDS, kebab, split_sentences, word_count

# Whole-sentence pleasantries ("Thanks Sarah.", "Great insights, Mark.") vs. leading interjections ("Yes, ...").
PLEASANTRY = re.compile(
    r"(?i)^(thanks|thank you|hi|hey|hello|welcome|great insights|got it|that makes sense|interesting|fair)\b[^.!?]*[.!?]\s*"
)
INTERJECTION = re.compile(r"(?i)^(yes|yeah|right|exactly|absolutely|true|sure|ok|okay|wow|well|so|and|plus|but)\b[,.!]?\s+")
CONTRAST = re.compile(r"(?i)\b(not|but|isn't|doesn't|don't|aren't|instead|more than|rather|never|only)\b")
ANECDOTE = re.compile(r"(?i)^(i|we|my|our)\b|\b(i built|i advised|we reached|i've raised|my experience|i founded)\b")
PREDICTION = re.compile(r"(?i)\b(will|won't|going to|anytime soon|next big thing|by 20\d\d|in the future|might become)\b")
STATISTIC = re.compile(r"\d|%|\bpercent\b|\$|\bbillion\b|\bmillion\b")
FACT_CUES = re.compile(
    r"(?i)\b(fda|nasa|hospitals?|regulat\w*|announced|launch\w*|reviews?|approved|cleared|pandemic|"
    r"x-rays?|chatbots?|startups?|handbooks?|investors?|venture|seed)\b"
)
TIME_CUES = re.compile(r"(?i)\b(19|20)\d{2}\b|\b(recently|currently|today|now|anytime soon|this year|next year)\b")
CONTRADICTION_CUES = re.compile(
    r"(?i)\b(delay\w*|postpon\w*|cancel\w*|terminat\w*|no longer|did not|didn't|never reached|fell short|"
    r"failed to|first autonomous|contrary|however|outdated|superseded|peaked)\b"
)
FILLERISH = {"also", "really", "just", "literally", "actually", "basically", "lot", "big", "next", "sure",
             "keep", "saying", "side", "something", "think", "heard", "honestly", "planning", "recently",
             "talking", "today", "everyone", "listeners", "tuning", "joining", "insights", "excited", "share",
             "discuss", "dive", "diving", "wrap", "thing", "things", "people", "look", "give", "concrete"}


# --------------------------------------------------------------------------- helpers


def short_name(full: str) -> str:
    parts = full.split()
    if len(parts) >= 3 and parts[0].rstrip(".").lower() in {"dr", "prof"}:
        return f"{parts[0]} {parts[-1]}"
    return parts[-1] if len(parts) > 1 else full


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z][a-z'-]+", text.lower().replace("’", "'"))


def keyword_weights(ep: Episode) -> Counter:
    names = {t.lower() for s in ep.speakers for t in re.findall(r"[A-Za-z]+", s)}
    counts: Counter = Counter()
    for u in ep.utterances:
        for w in _words(u.text):
            if w in STOPWORDS or w in FILLERISH or w in names or len(w) < 4:
                continue
            counts[w] += 1
    for w in _words(ep.title):
        if w not in STOPWORDS and len(w) >= 4:
            counts[w] += 2
    return counts


def _score(text: str, weights: Counter) -> float:
    toks = [w for w in _words(text) if w in weights]
    return sum(weights[w] for w in toks) / max(1.0, len(_words(text)) ** 0.5)


def _sentences(ep: Episode) -> list[tuple[Utterance, str]]:
    out: list[tuple[Utterance, str]] = []
    for u in ep.utterances:
        sents = split_sentences(u.text)
        merged: list[str] = []
        for s in sents:  # glue "They've ..." style follow-ups to the sentence they depend on
            if merged and re.match(r"(?i)^(they|it|this|that|these|those)\b", s):
                merged[-1] = f"{merged[-1]} {s}"
            else:
                merged.append(s)
        out.extend((u, s) for s in merged)
    return out


def _strip_pleasantries(text: str) -> str:
    prev = None
    while prev != text:
        prev = text
        text = PLEASANTRY.sub("", text, count=1).strip()
        text = INTERJECTION.sub("", text, count=1).strip()
    return text[:1].upper() + text[1:] if text else text


def entities(text: str, exclude: set[str]) -> list[str]:
    found: list[str] = []
    for sent in split_sentences(text):
        tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", sent)
        for tok in tokens[1:]:
            if (tok[0].isupper() and tok.lower() not in exclude and tok not in {"I", "I'm", "I've"}
                    and tok.lower() not in STOPWORDS):
                found.append(tok)
    return found


# --------------------------------------------------------------------------- plan


def plan(context: dict[str, Any]) -> dict:
    profile, catalog, settings = context["profile"], context["catalog"], context["settings"]
    signals = profile["claim_signals"]
    steps = []
    if profile["filler_or_repeated_words"] > 0:
        steps.append({"step": "normalize_transcript",
                      "reason": f"{profile['filler_or_repeated_words']} filler/repeated tokens detected; clean before quoting.",
                      "params": {}})
    long = profile["word_count"] > settings.map_reduce_threshold_words
    steps.append({"step": "summarize",
                  "reason": f"{profile['word_count']} words {'exceeds' if long else 'fits within'} the single-pass budget.",
                  "params": {"strategy": "map_reduce" if long else "single_pass"}})
    steps.append({"step": "extract_notes", "reason": "Required show-notes deliverable.", "params": {}})
    density = len(signals["named_entities"]) // 2 + signals["years"] + signals["numbers"] // 2 + signals["prediction_cues"] // 2
    max_claims = max(3, min(settings.max_claims, 3 + density))
    steps.append({"step": "extract_claims",
                  "reason": f"Claim signals (entities={len(signals['named_entities'])}, years={signals['years']}, "
                            f"predictions={signals['prediction_cues']}) suggest about {max_claims} check-worthy claims.",
                  "params": {"max_claims": max_claims}})
    tools = catalog["verify_claims"]["params"]["tools"]
    steps.append({"step": "verify_claims",
                  "reason": "Ground each claim in the knowledge base first, then the web when evidence is thin or time-sensitive.",
                  "params": {"max_steps_per_claim": 3 if density < 4 else 4, "tools": tools}})
    return {"goal": f"Publish-ready show notes and a fact-check table for '{profile['title']}'.", "steps": steps}


# --------------------------------------------------------------------------- summary

SECTION_VERBS = {
    "introduction": "opens by noting", "deep dive": "argues", "examples": "points to", "trends": "observes",
    "challenges": "acknowledges", "predictions": "predicts", "future": "looks ahead", "framework": "suggests",
    "pros & cons": "weighs the trade-offs", "closing": "concludes",
}


def summarize(context: dict[str, Any]) -> dict:
    ep: Episode = context["episode"]
    lo, hi = context["min_words"], context["max_words"]
    weights = keyword_weights(ep)
    top = [w for w, _ in weights.most_common(4)]
    guests = ", ".join(ep.guests)
    opening = (f'In "{ep.title}", {ep.host or "the host"} '
               + (f"talks with {guests} " if guests else "")
               + f"about {', '.join(top[:-1])} and {top[-1]}." if len(top) > 1 else f'In "{ep.title}", the hosts talk.')
    last_idx = ep.utterances[-1].idx
    candidates = []
    for u in ep.utterances:
        text = _strip_pleasantries(u.text)
        is_host_conclusion = (u.role == "host" and u.idx >= last_idx - 1 and "?" not in text
                              and "thank" not in text.lower())
        if (u.role == "host" and not is_host_conclusion) or word_count(text) < 6 or text.endswith("?"):
            continue
        candidates.append((u, text, _score(text, weights)))
    budget = hi - word_count(opening) - 10
    chosen, used = [], 0
    for u, text, _ in sorted(candidates, key=lambda c: c[2], reverse=True):
        n = word_count(text) + 4
        if used + n <= budget:
            chosen.append((u, text))
            used += n
    chosen.sort(key=lambda c: c[0].idx)
    mentioned: set[str] = set()
    body = []
    for u, text in chosen:
        name = u.speaker_name if u.speaker_name not in mentioned else short_name(u.speaker_name)
        mentioned.add(u.speaker_name)
        verb = SECTION_VERBS.get((u.section or "").lower(), "adds")
        body.append(f"{name} {verb}: “{text}”")
    summary = opening + " " + " ".join(body)
    if word_count(summary) < lo:  # very short sources: append remaining host framing
        for u in ep.utterances:
            text = _strip_pleasantries(u.text)
            if u.role == "host" and text and word_count(summary) < lo and text not in summary:
                summary += f" {short_name(u.speaker_name)} asks: “{text}”"
    return {"summary": summary}


def summarize_map(context: dict[str, Any]) -> dict:
    seg: list[Utterance] = context["segment"]
    notes = [f"{u.speaker_name}: {split_sentences(u.text)[0]}" for u in seg if word_count(u.text) >= 6][:8]
    return {"notes": notes or ["(no substantive content)"]}


# --------------------------------------------------------------------------- key notes


def notes(context: dict[str, Any]) -> dict:
    ep: Episode = context["episode"]
    weights = keyword_weights(ep)
    sents = [(u, s) for u, s in _sentences(ep) if not PLEASANTRY.match(s)]

    def eligible(role_ok) -> list[tuple[Utterance, str]]:
        return [(u, s) for u, s in sents if role_ok(u) and word_count(s) >= 7 and not s.endswith("?")]

    pool = eligible(lambda u: u.role != "host") or eligible(lambda u: True)
    ranked = sorted(pool, key=lambda p: _score(p[1], weights), reverse=True)
    takeaways, used_utts = [], set()
    for u, s in ranked:
        if u.idx in used_utts:
            continue
        words = s.split()
        text = " ".join(words[:30]).rstrip(",;:") + ("…" if len(words) > 30 else "")
        takeaways.append({"text": f"{short_name(u.speaker_name)}: {text}", "timestamp": u.timestamp})
        used_utts.add(u.idx)
        if len(takeaways) == 5:
            break
    for u, s in eligible(lambda u: True):
        if len(takeaways) == 5:
            break
        if u.idx not in used_utts:
            takeaways.append({"text": f"{short_name(u.speaker_name)}: {s}", "timestamp": u.timestamp})
            used_utts.add(u.idx)

    def quotability(p: tuple[Utterance, str]) -> float:
        u, s = p
        n = word_count(s)
        return _score(s, weights) + (1.5 if CONTRAST.search(s) else 0) + (1.0 if n <= 20 else 0) - (0.5 if u.role == "host" else 0)

    quotes, quoted = [], set()
    for u, s in sorted([p for p in sents if 6 <= word_count(p[1]) <= 40 and not p[1].endswith("?")],
                       key=quotability, reverse=True):
        if u.idx in quoted:
            continue
        quotes.append({"text": s, "speaker": u.speaker_name, "timestamp": u.timestamp})
        quoted.add(u.idx)
        if len(quotes) == 4:
            break

    text = " ".join(u.text for u in ep.utterances)
    words = [w for w in _words(text)]
    bigrams = Counter(
        f"{a}-{b}" for a, b in pairwise(words)
        if a not in STOPWORDS and b not in STOPWORDS and a in weights and b in weights
    )
    tags: list[str] = [b for b, c in bigrams.most_common(3) if c >= 2]
    names = {t.lower() for s in ep.speakers for t in re.findall(r"[A-Za-z]+", s)}
    ents = Counter(e for e in entities(text, names) if len(e) > 2)
    tags += [kebab(e) for e, _ in ents.most_common(2)]
    for w, _ in weights.most_common(12):
        if len(tags) >= 7:
            break
        if not any(w in t for t in tags):
            tags.append(kebab(w))
    return {"takeaways": takeaways, "quotes": quotes, "topics": list(dict.fromkeys(tags))[:8]}


# --------------------------------------------------------------------------- claims

PRIORITY = {"statistical": 0, "factual": 1, "historical": 1, "prediction": 2, "anecdotal": 3}


def extract_claims(context: dict[str, Any]) -> dict:
    ep: Episode = context["episode"]
    max_claims: int = context["max_claims"]
    names = {t.lower() for s in ep.speakers for t in re.findall(r"[A-Za-z]+", s)}
    weights = keyword_weights(ep)
    found: list[dict[str, Any]] = []
    prev_statement: dict[int, str] = {}
    last_statement = ""
    first_idx, last_idx = ep.utterances[0].idx, ep.utterances[-1].idx
    for u in ep.utterances:
        prev_statement[u.idx] = last_statement
        sents = [x for x in split_sentences(u.text) if not x.endswith("?")]
        if sents:
            last_statement = _strip_pleasantries(sents[-1])
    for u, s in _sentences(ep):
        quote = _strip_pleasantries(s)
        core = quote
        if not quote or quote.endswith("?") or word_count(quote) < 6 or re.match(r"(?i)^(welcome|i[’']m\b)", quote):
            continue
        if u.role == "host" and u.idx in (first_idx, last_idx):  # intro/outro framing, not claims
            continue
        if re.match(r"(?i)^(it[’']s|it is|they[’']re|they are|they[’']ve|they)\b(?! seems?)", core) and prev_statement.get(u.idx):
            core = f"{prev_statement[u.idx].rstrip('.')}: {core}"  # resolve the pronoun with the previous line
        has_entity = bool(entities(core, names)) or bool(re.match(r"^[A-Z]{2,}\b", core))
        if PREDICTION.search(core):
            kind = "prediction"
        elif ANECDOTE.search(core):
            kind = "anecdotal"
        elif STATISTIC.search(core):
            kind = "statistical"
        elif has_entity or FACT_CUES.search(core):
            kind = "factual"
        else:
            continue
        found.append({
            "claim": core,
            "type": kind,
            "time_sensitive": bool(TIME_CUES.search(core) or STATISTIC.search(core) or kind == "prediction"),
            "speaker": u.speaker_name,
            "timestamp": u.timestamp,
            "quote": quote,
            "_rank": (PRIORITY[kind], -_score(core, weights), u.idx),
        })
    # drop claims whose text is already contained in a richer claim
    found = [c for c in found if not any(c is not o and c["claim"].rstrip(".") in o["claim"] for o in found)]
    found.sort(key=lambda c: c["_rank"])
    chosen = sorted(found[:max_claims], key=lambda c: c["_rank"][2])
    for c in chosen:
        c.pop("_rank")
    return {"claims": chosen}


# --------------------------------------------------------------------------- verification


def _past_deadline(text: str, today: str) -> bool:
    year_now, month_now = int(today[:4]), int(today[5:7])
    for m in re.finditer(r"(?i)\b(early|mid|late|end of)?\s*(20\d{2})\b", text):
        qualifier, year = (m.group(1) or "").lower(), int(m.group(2))
        end_month = {"early": 6, "mid": 9}.get(qualifier, 12)
        if (year, end_month) < (year_now, month_now):
            return True
    return False


def focus_query(text: str) -> str:
    keep = [w for w in re.findall(r"[A-Za-z0-9$%][A-Za-z0-9$%'-]*", text)
            if w.lower() not in STOPWORDS and w.lower() not in FILLERISH and len(w) > 2]
    return " ".join(dict.fromkeys(keep))[:120]


def verify_step(context: dict[str, Any]) -> dict:
    claim, history = context["claim"], context["history"]
    tools, force, today = context["tools"], context["force_finish"], context["today"]
    done = {h["action"] for h in history}
    evidence = [r for h in history for r in h.get("results", [])]
    deadline_passed = _past_deadline(claim.claim, today)

    if claim.type == "anecdotal":
        return _finish("The claim is a personal anecdote; there is no public record to check it against.",
                       "unverifiable", 0.3, "Personal anecdote from the speaker; no independent source can confirm it.", [])
    if claim.type == "prediction" and not deadline_passed:
        return _finish("This is a forward-looking prediction whose deadline has not passed.",
                       "unverifiable", 0.35, "Forward-looking prediction; it cannot be verified yet.", [])
    if not force and "search_knowledge_base" in tools and "search_knowledge_base" not in done:
        hint = " The deadline has passed, so the prediction is checkable." if claim.type == "prediction" else ""
        return {"purpose": f"Start with the curated knowledge base.{hint}",
                "action": "search_knowledge_base", "input": {"query": focus_query(claim.claim)}}
    best = max(evidence, key=lambda e: e["relevance"], default=None)
    if not force and "web_search" in tools and "web_search" not in done and (
        best is None or best["relevance"] < 0.6 or claim.time_sensitive
    ):
        why = "weak" if best is None or best["relevance"] < 0.6 else "possibly stale for a time-sensitive claim"
        return {"purpose": f"Knowledge-base evidence is {why}; check the web for current information.",
                "action": "web_search", "input": {"query": focus_query(claim.claim)}}
    if best is None or best["relevance"] < 0.5:
        return _finish("No source covers this claim closely enough.", "unverifiable", 0.3,
                       "No knowledge-base entry or search result addresses this claim directly.", [])
    cited = [best, *[e for e in evidence if e is not best and e["relevance"] >= 0.5][:1]]
    joined = " ".join(e["snippet"] + " " + e["title"] for e in cited)
    claim_years = set(re.findall(r"\b20\d{2}\b", claim.claim))
    evidence_years = set(re.findall(r"\b20\d{2}\b", joined))
    contradicted = bool(CONTRADICTION_CUES.search(joined)) or (
        bool(claim_years) and bool(evidence_years) and not claim_years <= evidence_years
    )
    ids = [e["id"] for e in cited]
    refs = ", ".join(f"[{i}]" for i in ids)
    if contradicted:
        return _finish("The best evidence covers the claim but signals a change or contradiction.",
                       "possibly_outdated_or_inaccurate", round(0.4 + 0.45 * best["relevance"], 2),
                       f"Sources {refs} cover the claim but indicate it is outdated or overstated.", ids)
    return _finish("The best evidence covers the claim and shows no sign of being outdated.",
                   "verified", round(0.4 + 0.5 * best["relevance"], 2),
                   f"Sources {refs} support the claim as stated.", ids)


def _finish(purpose: str, verdict: str, confidence: float, rationale: str, ids: list[str]) -> dict:
    return {"purpose": purpose, "action": "finish",
            "input": {"verdict": verdict, "confidence": confidence, "rationale": rationale, "evidence_ids": ids}}


HANDLERS = {
    "plan": plan,
    "summarize": summarize,
    "summarize_map": summarize_map,
    "extract_notes": notes,
    "extract_claims": extract_claims,
    "verify_step": verify_step,
}
