import json
from types import SimpleNamespace

import pytest
from conftest import make_ctx

from podcast_agent.agent.pipeline import process_episode
from podcast_agent.config import Settings
from podcast_agent.llm import build_provider
from podcast_agent.llm.anthropic_provider import BedrockProvider
from podcast_agent.llm.base import LLMError
from podcast_agent.models import Claim, Evidence, FactCheck, Verdict
from podcast_agent.runtime import run_batch
from podcast_agent.transcript import load_episode


class FakeMessages:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        usage = SimpleNamespace(input_tokens=11, output_tokens=7, cache_creation_input_tokens=0, cache_read_input_tokens=5)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text='{"ok": true}')], usage=usage,
                               stop_reason="end_turn")


def test_bedrock_provider_uses_the_messages_api_with_prompt_caching():
    fake = SimpleNamespace(messages=FakeMessages())
    p = BedrockProvider("bedrock-main-id", "bedrock-fast-id", "us-east-1", client=fake)
    resp = p.complete(system="sys", messages=[{"role": "user", "content": "hi"}], tier="fast", cache_prefix="CTX")
    assert p.name == "bedrock" and resp.text == '{"ok": true}' and resp.cache_read_tokens == 5
    sent = fake.messages.kwargs
    assert sent["model"] == "bedrock-fast-id"
    assert sent["system"][0] == {"type": "text", "text": "CTX", "cache_control": {"type": "ephemeral"}}


def test_prompt_cache_can_be_disabled():
    fake = SimpleNamespace(messages=FakeMessages())
    p = BedrockProvider("m", None, "us-east-1", client=fake, prompt_cache=False)
    p.complete(system="sys", messages=[{"role": "user", "content": "hi"}], cache_prefix="CTX")
    assert "cache_control" not in fake.messages.kwargs["system"][0]


def test_bedrock_requires_a_model_id_and_is_never_auto_selected():
    with pytest.raises(LLMError, match="BEDROCK_MODEL"):
        build_provider(Settings(provider="bedrock", bedrock_model=None, cache_enabled=False))
    assert Settings(provider="auto", anthropic_api_key=None, openai_api_key=None,
                    bedrock_model="x").resolved_provider() == "offline"


def _fc(verdict, evidence, ctype="factual"):
    claim = Claim(id="C1", claim="Perseverance landed in February 2021.", speaker="N", timestamp="00:01",
                  quote="q", type=ctype)
    return FactCheck(claim=claim, verdict=verdict, confidence=0.9, raw_confidence=0.9, rationale="r", evidence=evidence)


def test_verification_text_matches_the_assignment_style():
    kb = Evidence(id="E1", source="knowledge_base", title="KB-031: Perseverance landed", snippet="s")
    web = Evidence(id="E2", source="web_search", title="Rover lands", snippet="s")
    model = Evidence(id="M1", source="model_knowledge", title="m", snippet="s")
    assert _fc(Verdict.VERIFIED, [kb]).verification == "✅ Verified true (confirmed by knowledge base KB-031)"
    assert _fc(Verdict.VERIFIED, [kb, web]).verification.endswith("(confirmed by knowledge base KB-031 and web search)")
    assert _fc(Verdict.OUTDATED, [web, kb]).verification == (
        "⚠️ Possibly outdated/inaccurate (contradicted by knowledge base KB-031)")
    assert _fc(Verdict.VERIFIED, [model]).verification == "✅ Verified true (reasoning step, no external source)"
    assert _fc(Verdict.VERIFIED, [model]).method == "reasoning step"
    assert _fc(Verdict.UNVERIFIABLE, [], "prediction").verification == "❓ Unverifiable (future prediction)"


def test_reports_carry_the_three_column_table_and_a_combined_file(tmp_path, data_dir):
    ctx = make_ctx()
    files = [data_dir / "transcripts" / "ep004_pdf_example.txt", data_dir / "transcripts" / "ep002_ai_healthcare.json"]
    reports, errors = run_batch(ctx, files, tmp_path, "test-run")
    assert not errors
    md = (tmp_path / "ep004.md").read_text()
    assert "| Claim | Verification | Confidence |" in md
    combined_md = (tmp_path / "report.md").read_text()
    assert combined_md.count("### Summary") == 2 and combined_md.count("### Fact-check") == 2
    combined = json.loads((tmp_path / "report.json").read_text())
    assert [e["episode"]["episode_id"] for e in combined["episodes"]] == ["ep004", "ep002"]
    row = combined["episodes"][0]["fact_check_table"][0]
    assert set(row) == {"claim", "verification", "confidence"}


def test_episode_json_round_trips_with_computed_fields(data_dir):
    report = process_episode(make_ctx(), load_episode(data_dir / "transcripts" / "ep003_bootstrapping.json"), "t")
    data = json.loads(report.model_dump_json())
    assert "fact_check_table" in data and "verification" in data["fact_checks"][0]
    type(report).model_validate(data)  # extra computed keys are ignored on load


def test_anthropic_request_matches_the_installed_sdk_and_claude_5_5_rules():
    """Every kwarg we send must be accepted by the real SDK; no sampling params; effort and thinking headroom."""
    import inspect

    import anthropic.resources.messages as sdk

    from podcast_agent.llm.anthropic_provider import THINKING_HEADROOM, AnthropicProvider

    fake = SimpleNamespace(messages=FakeMessages())
    p = AnthropicProvider("claude-sonnet-5-5", "claude-haiku-5-5", client=fake)
    p.complete(system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=900, temperature=0.2,
               cache_prefix="CTX")
    sent = fake.messages.kwargs
    inspect.signature(sdk.Messages.create).bind(None, **sent)  # raises TypeError on an unknown kwarg
    assert not {"temperature", "top_p", "top_k"} & set(sent)
    assert sent["output_config"] == {"effort": "low"} and sent["max_tokens"] == 900 + THINKING_HEADROOM

    fake2 = SimpleNamespace(messages=FakeMessages())
    AnthropicProvider("m", None, client=fake2, effort=None).complete(system="s", messages=[{"role": "user", "content": "x"}])
    assert "output_config" not in fake2.messages.kwargs


def test_workspace_id_is_sent_for_keys_not_scoped_to_a_workspace():
    from podcast_agent.llm.anthropic_provider import AnthropicProvider

    p = AnthropicProvider("m", None, api_key="sk-ant-test", workspace_id="wrkspc_test")
    assert p._client.default_headers["anthropic-workspace-id"] == "wrkspc_test"
    assert "anthropic-workspace-id" not in AnthropicProvider("m", None, api_key="sk-ant-test")._client.default_headers


class RefusingMessages:
    def create(self, **kwargs):
        details = SimpleNamespace(type="refusal", category="reasoning_extraction", explanation="blocked")
        usage = SimpleNamespace(input_tokens=5, output_tokens=0, cache_creation_input_tokens=0, cache_read_input_tokens=0)
        return SimpleNamespace(content=[], usage=usage, stop_reason="refusal", stop_details=details)


class ThinkingMessages(FakeMessages):
    def create(self, **kwargs):
        self.kwargs = kwargs
        usage = SimpleNamespace(input_tokens=9, output_tokens=30, cache_creation_input_tokens=0, cache_read_input_tokens=0)
        content = [SimpleNamespace(type="thinking", thinking="Checked the dates first.", signature="sig"),
                   SimpleNamespace(type="text", text='{"ok": true}')]
        return SimpleNamespace(content=content, usage=usage, stop_reason="end_turn")


def test_refusal_raises_with_its_category():
    from podcast_agent.llm.anthropic_provider import AnthropicProvider
    from podcast_agent.llm.base import LLMRefusal

    p = AnthropicProvider("m", None, client=SimpleNamespace(messages=RefusingMessages()))
    with pytest.raises(LLMRefusal, match="reasoning_extraction") as err:
        p.complete(system="s", messages=[{"role": "user", "content": "x"}])
    assert err.value.category == "reasoning_extraction"


def test_thinking_summaries_are_requested_parsed_and_logged():
    from rich.console import Console

    from podcast_agent.llm.anthropic_provider import AnthropicProvider
    from podcast_agent.trace import Tracer

    fake = SimpleNamespace(messages=ThinkingMessages())
    resp = AnthropicProvider("m", None, client=fake).complete(system="s", messages=[{"role": "user", "content": "x"}])
    assert fake.messages.kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert resp.text == '{"ok": true}' and resp.thinking == "Checked the dates first."
    events = []
    tracer = Tracer("t", console=Console(quiet=True))
    tracer.event = lambda kind, step, message, **kw: events.append((kind, message))
    tracer.llm_call("verify C1", resp)
    assert ("thinking", "Checked the dates first.") in events
