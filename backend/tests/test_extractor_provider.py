"""The extraction provider switch (services.extractor._PROVIDER).

_PROVIDER is resolved once at import, so a regression here is silent: extraction
would quietly route to a provider whose key has no credit and every post would come
back empty. These tests pin the default and the override in both directions.
"""
import importlib

import pytest

from services import extractor


@pytest.fixture(autouse=True)
def _restore_module():
    """Reload the module back to its ambient state so a reload here can't leak into
    other tests, which import services.extractor at collection time."""
    yield
    importlib.reload(extractor)


def _reload_with(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("EXTRACTOR_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("EXTRACTOR_PROVIDER", value)
    return importlib.reload(extractor)


def test_default_provider_is_groq(monkeypatch):
    """Unset means Groq — the whole pipeline runs on a free tier by default."""
    assert _reload_with(monkeypatch, None)._PROVIDER == "groq"


def test_anthropic_is_opt_in(monkeypatch):
    assert _reload_with(monkeypatch, "anthropic")._PROVIDER == "anthropic"


def test_provider_is_case_insensitive(monkeypatch):
    assert _reload_with(monkeypatch, "GROQ")._PROVIDER == "groq"


def test_groq_extraction_falls_back_to_mock_without_a_key(monkeypatch):
    """No GROQ_API_KEY must not raise — it degrades to the mock, matching the
    Anthropic branch, so a keyless dev environment still boots."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    mod = _reload_with(monkeypatch, "groq")
    assert mod._extract_groq("some post text") == mod._mock_extract()
