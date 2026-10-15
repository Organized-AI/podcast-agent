"""Build the guide's Walkthrough tab and docs/WRITEUP.md from website/walkthrough.yaml.

    python tools/guide/embed_walkthrough.py           # write both
    python tools/guide/embed_walkthrough.py --check   # exit 1 if either is out of date (make drift runs this)

The YAML is the one place to edit the walkthrough. Present mode shows one stop at a time, with `say` and `ask` as
speaker notes; the write-up shows every stop as one page. Each stop's `show` links are checked: a guide link must name a
view and an anchor that exists on the page (or a proof card the Audit trail renders), and any other link must be https.
Numbers that change with every pull request come from the captured proofs instead of being typed in: `{tests}` is the
passed count in samples/proof/pytest.txt.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "website" / "walkthrough.yaml"
HTML = ROOT / "website" / "public" / "index.html"
WRITEUP = ROOT / "docs" / "WRITEUP.md"
GUIDE = "https://podcast-agent-guide.jordan-691.workers.dev"
VIEWS = {"path", "release", "audit", "cards", "gloss"}
TAG = re.compile(r'<script type="application/json" id="walk-data">.*?</script>', re.S)
AUDIT_DATA = re.compile(r'<script type="application/json" id="audit-data">(.*?)</script>', re.S)
STOP_KEYS = ("title", "claim", "facts", "learned", "say", "show")
PYTEST_PROOF = ROOT / "samples" / "proof" / "pytest.txt"
TOKEN = re.compile(r"\{([a-z_]+)\}")


def tokens() -> dict[str, str]:
    passed = re.search(r"=+ (\d+) passed", PYTEST_PROOF.read_text(encoding="utf-8")) if PYTEST_PROOF.exists() else None
    return {"tests": passed.group(1)} if passed else {}


def fill(value: Any, values: dict[str, str]) -> Any:
    """Replace {token}s in every string; an unknown token is left in place for problems() to report."""
    if isinstance(value, str):
        return TOKEN.sub(lambda m: values.get(m.group(1), m.group(0)), value)
    if isinstance(value, list):
        return [fill(v, values) for v in value]
    if isinstance(value, dict):
        return {k: fill(v, values) for k, v in value.items()}
    return value


def load(path: Path = SRC) -> dict[str, Any]:
    doc: dict[str, Any] = fill(yaml.safe_load(path.read_text(encoding="utf-8")), tokens())
    return doc


def page_anchors(html: str) -> set[str]:
    """Ids a guide link can land on: static ids, ids in the views' templates, and the Audit trail's proof cards."""
    ids = set(re.findall(r'\bid="([A-Za-z][\w.-]*)"', html))
    data = AUDIT_DATA.search(html)
    if data:
        ids |= {f"pf-{p['id']}" for p in json.loads(data.group(1)).get("proof", [])}
    return ids


def problems(doc: dict[str, Any], html: str) -> list[str]:
    found = []
    anchors = page_anchors(html)
    for i, stop in enumerate(doc.get("stops", []), 1):
        where = f"stop {i} ({stop.get('title', '?')})"
        found += [f"{where}: missing {key}" for key in STOP_KEYS if not stop.get(key)]
        learned = stop.get("learned") or {}
        if not (learned.get("lesson") and learned.get("why")):
            found.append(f"{where}: learned needs a lesson and a why")
        for fact in stop.get("facts", []):
            if not (str(fact.get("value", "")).strip() and fact.get("label")):
                found.append(f"{where}: every fact needs a value and a label")
        for link in stop.get("show", []):
            if "href" in link:
                if not str(link["href"]).startswith("https://"):
                    found.append(f"{where}: {link.get('label')}: links must be https")
            elif link.get("view") not in VIEWS:
                found.append(f"{where}: {link.get('label')}: view must be one of {sorted(VIEWS)}")
            elif link.get("anchor") not in anchors:
                found.append(f"{where}: {link.get('label')}: no element with id {link.get('anchor')!r} in the guide")
    if not doc.get("stops"):
        found.append("no stops")
    left = sorted(set(TOKEN.findall(json.dumps(doc, ensure_ascii=False))))
    if left:
        found.append(f"unknown tokens: {', '.join('{' + t + '}' for t in left)}")
    return found


def as_json(doc: dict[str, Any]) -> str:
    # "</" would end the script element early.
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def embed(html: str, doc: dict[str, Any]) -> str:
    if not TAG.search(html):
        raise SystemExit(f'{HTML}: no <script type="application/json" id="walk-data"> to fill')
    tag = f'<script type="application/json" id="walk-data">{as_json(doc)}</script>'
    return TAG.sub(lambda _: tag, html, count=1)


def guide_url(link: dict[str, Any]) -> str:
    return str(link["href"]) if "href" in link else f"{GUIDE}/#{link['anchor']}"


def slug(title: str) -> str:
    return re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")


def writeup(doc: dict[str, Any]) -> str:
    w, stops = doc["writeup"], doc["stops"]
    heads = [f"{i}. {s['title']}" for i, s in enumerate(stops, 1)]
    out = [
        "<!-- Generated from website/walkthrough.yaml by tools/guide/embed_walkthrough.py: edit the YAML, then run "
        "`make walkthrough`. -->",
        "",
        f"# {w['title']}",
        "",
        f"*{w['byline']}*",
        "",
        *[line for p in w["intro"] for line in (p, "")],
        f"Read it in the guide with every link live ([Walkthrough › Write-up]({GUIDE}/#writeup)), or present it stop by "
        f"stop ([Walkthrough › Present]({GUIDE}/#walk-1)).",
        "",
        *[f"{i}. [{s['title']}](#{slug(h)})" for i, (s, h) in enumerate(zip(stops, heads, strict=True), 1)],
        "",
    ]
    for stop, head in zip(stops, heads, strict=True):
        out += [f"## {head}", "", stop["claim"], ""]
        out += [f"- **{f['value']}** {f['label']}" for f in stop["facts"]] + [""]
        out += ["**What I did**", ""] + [f"- {line}" for line in stop["say"]] + [""]
        out += [f"**What I learned: {stop['learned']['lesson']}** {stop['learned']['why']}", ""]
        out += ["Evidence: " + " · ".join(f"[{link['label']}]({guide_url(link)})" for link in stop["show"]), ""]
    return "\n".join(out).rstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if the guide or docs/WRITEUP.md is out of date")
    args = ap.parse_args(argv)

    doc = load()
    html = HTML.read_text(encoding="utf-8")
    issues = problems(doc, html)
    if issues:
        print("website/walkthrough.yaml:\n  " + "\n  ".join(issues))
        return 1
    new_html, new_md = embed(html, doc), writeup(doc)
    if args.check:
        stale = [p.relative_to(ROOT).as_posix() for p, old, new in
                 ((HTML, html, new_html), (WRITEUP, WRITEUP.read_text(encoding="utf-8") if WRITEUP.exists() else "", new_md))
                 if old != new]
        if stale:
            print(f"out of date: {', '.join(stale)} (run: make walkthrough)")
            return 1
        print(f"walkthrough: {len(doc['stops'])} stops, guide and docs/WRITEUP.md up to date")
        return 0
    HTML.write_text(new_html, encoding="utf-8")
    WRITEUP.write_text(new_md, encoding="utf-8")
    print(f"walkthrough: {len(doc['stops'])} stops → {HTML.relative_to(ROOT)} and {WRITEUP.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
