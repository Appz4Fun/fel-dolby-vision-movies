import pytest

import main


# Captured at import time, before any test monkeypatches the module attribute,
# so a test that needs the genuine implementation can request the
# `real_enrich_if_possible` fixture.
_REAL_ENRICH_IF_POSSIBLE = main._enrich_if_possible


@pytest.fixture(autouse=True)
def _disable_live_enrichment(monkeypatch):
    """Stub TMDB enrichment so the test suite never makes live network calls.

    `_scrape_for_titles` invokes `_enrich_if_possible`, which would otherwise
    reach the live TMDB API whenever a real TMDB_API_KEY is configured in a
    developer's .env file. This autouse fixture neutralizes it for every test.
    """
    monkeypatch.setattr(main, "_enrich_if_possible", lambda releases: None)


@pytest.fixture
def real_enrich_if_possible():
    """The genuine `_enrich_if_possible`, for tests that exercise it directly."""
    return _REAL_ENRICH_IF_POSSIBLE
