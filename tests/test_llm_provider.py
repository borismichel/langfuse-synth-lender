"""Provider-layer routing (LAN-378): default Anthropic, opt-in OpenAI, model resolution.

Also pins the back-compat invariant that matters most for this kit: with LLM_PROVIDER
unset and no LLM_MODEL, an explicit candidate model is honoured exactly — so the
multi-candidate certification comparison is byte-for-byte unchanged.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from synth import llm
from synth.workbench import judges


def test_default_provider_is_anthropic(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert llm.resolve_provider() == "anthropic"


def test_openai_opt_in_and_case_insensitive(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "OpenAI")
    assert llm.resolve_provider() == "openai"


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    with pytest.raises(ValueError):
        llm.resolve_provider()


def test_candidate_model_honoured_under_anthropic_default(monkeypatch):
    # The heart of back-compat: each certification candidate keeps its own model.
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    for candidate in ("claude-sonnet-4-5", "claude-sonnet-4-6", "claude-haiku-4-5"):
        client = llm.get_llm(candidate)
        assert (client.provider, client.model) == ("anthropic", candidate)


def test_openai_ignores_anthropic_candidate_and_pins_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o")
    client = llm.get_llm("claude-sonnet-4-5")
    assert (client.provider, client.model) == ("openai", "gpt-4o")


def test_complete_routes_to_anthropic_shape():
    client = llm.LLMClient("anthropic", "claude-sonnet-4-6")

    def create(**kwargs):
        assert kwargs["system"] == "S"  # Anthropic keeps system separate
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            usage=SimpleNamespace(input_tokens=11, output_tokens=3),
        )

    client._impl = SimpleNamespace(messages=SimpleNamespace(create=create))
    res = client.complete(system="S", messages=[{"role": "user", "content": "hi"}])
    assert (res.text, res.input_tokens, res.output_tokens) == ("ok", 11, 3)


def test_complete_routes_to_openai_shape():
    client = llm.LLMClient("openai", "gpt-4o")
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=3),
        )

    client._impl = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    res = client.complete(system="S", messages=[{"role": "user", "content": "hi"}])
    assert (res.text, res.input_tokens, res.output_tokens) == ("ok", 11, 3)
    assert captured["messages"][0] == {"role": "system", "content": "S"}


def test_judge_key_guard_is_provider_aware():
    assert judges._looks_like_real_key("anthropic", "sk-ant-" + "x" * 50)
    assert not judges._looks_like_real_key("anthropic", "sk-ant-...")  # .env placeholder
    assert judges._looks_like_real_key("openai", "sk-proj-" + "x" * 50)
    assert not judges._looks_like_real_key("openai", "sk-...")


def test_judge_provider_fallback_capitalises(monkeypatch):
    # No connections reachable -> capitalised provider id (matches the UI registration).
    monkeypatch.setattr(judges.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(judges.requests.RequestException()))
    assert judges._judge_provider("http://x", "anthropic") == "Anthropic"
    assert judges._judge_provider("http://x", "openai") == "Openai"
