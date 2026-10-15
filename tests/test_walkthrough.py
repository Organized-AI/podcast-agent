"""The Walkthrough tab and docs/WRITEUP.md come from website/walkthrough.yaml, and every link in it lands somewhere."""

import importlib.util
import re

import pytest
from conftest import ROOT


def _embed():
    spec = importlib.util.spec_from_file_location("embed_walkthrough", ROOT / "tools" / "guide" / "embed_walkthrough.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ew():
    return _embed()


@pytest.fixture(scope="module")
def doc(ew):
    return ew.load()


def test_every_stop_is_complete_and_every_link_lands(ew, doc):
    assert ew.problems(doc, ew.HTML.read_text(encoding="utf-8")) == []


def test_guide_and_writeup_match_the_yaml(ew):
    assert ew.main(["--check"]) == 0, "run: make walkthrough"


def test_each_stop_says_what_was_learned_and_live_stops_have_something_to_run(doc):
    titles = [s["title"] for s in doc["stops"]]
    assert len(titles) == len(set(titles))
    for stop in doc["stops"]:
        assert len(stop["learned"]["why"]) > 80, stop["title"]
        if stop.get("live"):
            assert stop.get("run", {}).get("cmd"), f"{stop['title']} is marked live but has nothing to run"


def test_a_link_to_a_missing_place_is_caught(ew, doc):
    broken = {**doc, "stops": [{**doc["stops"][0], "show": [{"label": "Nowhere", "view": "path", "anchor": "no-such-id"}]}]}
    assert ew.problems(broken, ew.HTML.read_text(encoding="utf-8")) == [
        "stop 1 (The brief, and what I delivered): Nowhere: no element with id 'no-such-id' in the guide"]
    plain = {**doc, "stops": [{**doc["stops"][0], "show": [{"label": "Plain", "href": "http://example.com"}]}]}
    assert "links must be https" in ew.problems(plain, ew.HTML.read_text(encoding="utf-8"))[0]


def test_writeup_has_a_section_per_stop_and_its_contents_links_resolve(ew, doc):
    md = ew.writeup(doc)
    heads = re.findall(r"^## (.+)$", md, re.M)
    assert heads == [f"{i}. {s['title']}" for i, s in enumerate(doc["stops"], 1)]
    assert re.findall(r"^\d+\. \[[^\]]+\]\(#([^)]+)\)$", md, re.M) == [ew.slug(h) for h in heads]
    assert md.count("**What I learned: ") == len(doc["stops"])


def test_the_guide_routes_walkthrough_links(ew):
    html = ew.HTML.read_text(encoding="utf-8")
    assert 'data-view="walk"' in html and "showView('walk',{mode:'read'})" in html
    assert re.search(r"/\^walk\(-\\d\+\)\?\$/", html), "#walk-N deep links"


def test_test_count_comes_from_the_captured_proof_and_unknown_tokens_are_caught(ew, doc):
    passed = re.search(r"=+ (\d+) passed", (ROOT / "samples" / "proof" / "pytest.txt").read_text()).group(1)
    assert any(f["value"] == passed and "tests" in f["label"] for s in doc["stops"] for f in s["facts"])
    odd = ew.fill({"stops": [{**doc["stops"][0], "claim": "{nonsense} claims"}]}, ew.tokens())
    assert "unknown tokens: {nonsense}" in ew.problems(odd, ew.HTML.read_text(encoding="utf-8"))
