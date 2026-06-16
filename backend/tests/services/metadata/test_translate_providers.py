"""Tests for the multi-provider translation factory + fallback chain.

Covers ``build_translator`` chain parsing, ``OpenRouterTranslator`` request
shape / response parsing (with a stubbed httpx client), and
``FallbackTranslator`` failover semantics.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.metadata.translate import (
    AbstractTranslator,
    AnthropicTranslator,
    FallbackTranslator,
    OpenRouterTranslator,
    TranslatorUnavailableError,
    build_translator,
)
from tests._async_harness import run_async


def _settings(
    *,
    chain: str = "openrouter,anthropic",
    openrouter_key: str | None = None,
    anthropic_key: str | None = None,
) -> SimpleNamespace:
    """A Settings-shaped stub exposing only what build_translator reads."""
    return SimpleNamespace(
        TRANSLATION_PROVIDER_CHAIN=chain,
        OPENROUTER_API_KEY=openrouter_key,
        OPENROUTER_BASE_URL="https://openrouter.ai/api/v1",
        OPENROUTER_TRANSLATION_MODEL="anthropic/claude-3.5-haiku",
        ANTHROPIC_API_KEY=anthropic_key,
    )


# --- build_translator -------------------------------------------------------


def test_build_translator_returns_none_when_no_keys() -> None:
    assert build_translator(_settings()) is None


# NOTE: build_translator now ALWAYS wraps the provider chain in a
# FallbackTranslator — even a single provider — so a raw provider exception
# (e.g. httpx errors from OpenRouterTranslator) is normalized to
# TranslatorUnavailableError instead of bubbling up as an unhandled 500 (I5).
# These tests previously asserted a bare provider instance for the single-
# provider case; they now assert the wrapping + the wrapped provider type.


def test_build_translator_single_provider_when_only_openrouter() -> None:
    translator = build_translator(_settings(openrouter_key="sk-or-x"))
    assert isinstance(translator, FallbackTranslator)
    assert isinstance(translator._translators[0], OpenRouterTranslator)  # noqa: SLF001
    assert translator.model_name == "openrouter:anthropic/claude-3.5-haiku"


def test_build_translator_single_provider_when_only_anthropic() -> None:
    translator = build_translator(_settings(anthropic_key="sk-ant-x"))
    assert isinstance(translator, FallbackTranslator)
    assert isinstance(translator._translators[0], AnthropicTranslator)  # noqa: SLF001
    assert translator.model_name.startswith("anthropic:")


def test_build_translator_fallback_chain_order_is_respected() -> None:
    translator = build_translator(
        _settings(
            chain="anthropic,openrouter",
            openrouter_key="sk-or-x",
            anthropic_key="sk-ant-x",
        ),
    )
    assert isinstance(translator, FallbackTranslator)
    # First in chain is primary.
    assert translator.model_name.startswith("anthropic:")
    assert "openrouter:" in translator.model_name


def test_build_translator_skips_providers_without_keys() -> None:
    # Chain wants both, but only OpenRouter has a key -> single-provider chain.
    translator = build_translator(_settings(openrouter_key="sk-or-x"))
    assert isinstance(translator, FallbackTranslator)
    assert [type(t) for t in translator._translators] == [OpenRouterTranslator]  # noqa: SLF001


def test_build_translator_ignores_unknown_and_duplicate_providers() -> None:
    translator = build_translator(
        _settings(chain="bogus,openrouter,openrouter", openrouter_key="sk-or-x"),
    )
    assert isinstance(translator, FallbackTranslator)
    assert [type(t) for t in translator._translators] == [OpenRouterTranslator]  # noqa: SLF001


# --- OpenRouterTranslator (stubbed httpx) -----------------------------------


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[dict] = []

    async def post(self, url, headers=None, json=None):  # noqa: A002
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self._response


def _openrouter_payload(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def test_openrouter_translate_parses_choice_and_sends_expected_request() -> None:
    client = _FakeClient(_FakeResponse(_openrouter_payload("  Atmen & Fokus  ")))
    translator = OpenRouterTranslator(
        api_key="sk-or-x", model="anthropic/claude-3.5-haiku", http_client=client,
    )

    result = run_async(
        translator.translate(
            "Breathing & Focus", "en-US", "de-DE", "subtitle",
        ),
    )

    assert result == "Atmen & Fokus"  # stripped, within 30-char subtitle limit
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-or-x"
    assert call["json"]["model"] == "anthropic/claude-3.5-haiku"
    roles = [m["role"] for m in call["json"]["messages"]]
    assert roles == ["system", "user"]
    assert "Source (en-US):" in call["json"]["messages"][1]["content"]


def test_openrouter_translate_post_processes_keywords() -> None:
    client = _FakeClient(
        _FakeResponse(_openrouter_payload("Meditation, Ruhe, meditation, ATEM")),
    )
    translator = OpenRouterTranslator(
        api_key="sk-or-x", model="x/y", http_client=client,
    )

    result = run_async(
        translator.translate("meditation, calm", "en-US", "de-DE", "keywords"),
    )

    # lowercased, deduped, comma-joined, no spaces.
    assert result == "meditation,ruhe,atem"


def test_openrouter_translate_truncates_to_field_limit() -> None:
    client = _FakeClient(_FakeResponse(_openrouter_payload("x" * 100)))
    translator = OpenRouterTranslator(
        api_key="sk-or-x", model="x/y", http_client=client,
    )
    # name limit is 30
    result = run_async(translator.translate("src", "en-US", "de-DE", "name"))
    assert len(result) == 30


# --- FallbackTranslator -----------------------------------------------------


class _StubTranslator(AbstractTranslator):
    def __init__(self, name: str, *, result: str | None = None, exc: Exception | None = None) -> None:
        self._name = name
        self._result = result
        self._exc = exc
        self.called = False

    @property
    def model_name(self) -> str:
        return self._name

    async def translate(self, *args, **kwargs) -> str:
        self.called = True
        if self._exc is not None:
            raise self._exc
        assert self._result is not None
        return self._result


def test_fallback_uses_first_success() -> None:
    primary = _StubTranslator("p", result="primary-result")
    secondary = _StubTranslator("s", result="secondary-result")
    fb = FallbackTranslator([primary, secondary])

    result = run_async(fb.translate("t", "en-US", "de-DE", "name"))

    assert result == "primary-result"
    assert primary.called is True
    assert secondary.called is False


def test_fallback_fails_over_on_error() -> None:
    primary = _StubTranslator("p", exc=RuntimeError("boom"))
    secondary = _StubTranslator("s", result="secondary-result")
    fb = FallbackTranslator([primary, secondary])

    result = run_async(fb.translate("t", "en-US", "de-DE", "name"))

    assert result == "secondary-result"
    assert primary.called is True
    assert secondary.called is True


def test_fallback_raises_when_all_fail() -> None:
    primary = _StubTranslator("p", exc=RuntimeError("boom1"))
    secondary = _StubTranslator("s", exc=RuntimeError("boom2"))
    fb = FallbackTranslator([primary, secondary])

    with pytest.raises(TranslatorUnavailableError):
        run_async(fb.translate("t", "en-US", "de-DE", "name"))


def test_fallback_requires_at_least_one_translator() -> None:
    with pytest.raises(ValueError):
        FallbackTranslator([])
