import json

import httpx
import pytest

from ai_scrape import (
    _candidate_to_release,
    _fetch_url_for_ai_source,
    _is_google_doc_url,
    _load_existing_releases,
    _parse_url_list,
    ai_discover_sources,
    ai_extract_releases,
    ai_scrape_releases,
)
from compare import FoundCandidate
from models import FelEvidence, FelRelease


class FakeAIClient:
    def __init__(self, complete_text: str = "", candidates=None) -> None:
        self._complete_text = complete_text
        self._candidates = candidates or []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        return self._complete_text

    def extract_candidates(self, source_url: str, text: str):
        return list(self._candidates)


def test_parse_url_list_handles_plain_and_fenced_json():
    assert _parse_url_list('["https://a.test", "https://b.test"]') == [
        "https://a.test",
        "https://b.test",
    ]
    assert _parse_url_list('```json\n["https://c.test"]\n```') == ["https://c.test"]
    assert _parse_url_list("not json at all") == []


def test_parse_url_list_accepts_dict_payload_and_rejects_non_lists():
    assert _parse_url_list('{"urls": ["https://a.test"]}') == ["https://a.test"]
    assert _parse_url_list('{"items": ["https://b.test"]}') == ["https://b.test"]
    assert _parse_url_list('{"urls": "https://not-a-list.test"}') == []


def test_candidate_to_release_marks_ai_extracted():
    candidate = FoundCandidate(
        title="Nosferatu",
        year="2024",
        source_url="https://src.test/list",
        evidence="Nosferatu (2024) FEL",
        extraction_method="ai",
    )
    release = _candidate_to_release(candidate, "2026-05-22T00:00:00+00:00")
    assert release.movie_title == "Nosferatu"
    assert release.release_date == "2024"
    assert release.fel_evidence.evidence_type == "ai-extracted"
    assert release.source_url == "https://src.test/list"
    assert release.source_label == "codex-ai"


def test_ai_discover_sources_keeps_new_well_formed_urls():
    client = FakeAIClient(
        complete_text=json.dumps(
            [
                "https://forum.blu-ray.com/showthread.php?t=999",
                "https://known.test/list",
                "not-a-url",
            ]
        )
    )
    result = ai_discover_sources(client, ["https://known.test/list"])
    assert result == ["https://forum.blu-ray.com/showthread.php?t=999"]


def test_ai_discover_sources_returns_empty_on_ai_http_error():
    class FailingAIClient(FakeAIClient):
        def complete(self, system_prompt: str, user_prompt: str) -> str:
            raise httpx.HTTPError("boom")

    assert ai_discover_sources(FailingAIClient(), []) == []


def test_ai_extract_releases_converts_nonblank_candidates():
    candidates = [
        FoundCandidate("Drop", "2025", "https://src.test", "Drop (2025) Profile 7 FEL", "ai"),
        FoundCandidate("", "2020", "https://src.test", "blank", "ai"),
    ]
    client = FakeAIClient(candidates=candidates)
    releases = ai_extract_releases(client, [("https://src.test", "Drop (2025) Profile 7 FEL")])
    assert [r.movie_title for r in releases] == ["Drop"]
    assert releases[0].fel_evidence.evidence_type == "ai-extracted"


def test_ai_extract_releases_skips_sources_that_raise_http_errors():
    class FailingAIClient(FakeAIClient):
        def extract_candidates(self, source_url: str, text: str):
            raise httpx.HTTPError("boom")

    assert (
        ai_extract_releases(FailingAIClient(), [("https://src.test", "<html>")]) == []
    )


def test_ai_extract_releases_retries_transient_http_errors():
    sheet_url = "https://docs.google.com/spreadsheets/d/sheet-id/edit#gid=99"

    class FlakyAIClient(FakeAIClient):
        def __init__(self) -> None:
            self.calls = 0

        def extract_candidates(self, source_url: str, text: str):
            self.calls += 1
            if self.calls == 1:
                request = httpx.Request("POST", "https://api.example.test/extract")
                response = httpx.Response(503, request=request)
                raise httpx.HTTPStatusError(
                    "service unavailable",
                    request=request,
                    response=response,
                )
            return [
                FoundCandidate(
                    "Alien",
                    "1979",
                    source_url,
                        "Alien (1979) is confirmed Profile 7 FEL",
                    "ai",
                )
            ]

    client = FlakyAIClient()

    releases = ai_extract_releases(client, [(sheet_url, "Alien (1979) is confirmed Profile 7 FEL")])

    assert client.calls == 2
    assert [release.movie_title for release in releases] == ["Alien"]


def test_ai_extract_releases_does_not_retry_permanent_status_errors(
    monkeypatch,
    capsys,
):
    import ai_scrape as ai_scrape_mod

    source_url = "https://forum.example.test/thread"
    sleeps: list[float] = []
    request = httpx.Request("POST", "https://api.example.test/extract")
    response = httpx.Response(401, request=request)

    class PermanentFailureAIClient(FakeAIClient):
        def __init__(self) -> None:
            self.calls = 0

        def extract_candidates(self, source_url: str, text: str):
            self.calls += 1
            raise httpx.HTTPStatusError(
                "unauthorized",
                request=request,
                response=response,
            )

    monkeypatch.setattr(ai_scrape_mod.time, "sleep", sleeps.append)
    client = PermanentFailureAIClient()

    assert ai_extract_releases(client, [(source_url, "<html>Alien FEL</html>")]) == []

    assert client.calls == 1
    assert sleeps == []
    assert f"ai-scrape: extraction failed for {source_url}" in capsys.readouterr().out


def test_ai_extract_releases_backs_off_between_retry_attempts(monkeypatch):
    import ai_scrape as ai_scrape_mod

    source_url = "https://forum.example.test/thread"
    sleeps: list[float] = []

    class FlakyAIClient(FakeAIClient):
        def __init__(self) -> None:
            self.calls = 0

        def extract_candidates(self, source_url: str, text: str):
            self.calls += 1
            if self.calls < 3:
                raise httpx.HTTPError("temporary read failure")
            return [
                FoundCandidate(
                    "Alien",
                    "1979",
                    source_url,
                        "Alien (1979) is confirmed Profile 7 FEL",
                    "ai",
                )
            ]

    monkeypatch.setattr(ai_scrape_mod.time, "sleep", sleeps.append)
    client = FlakyAIClient()

    releases = ai_extract_releases(client, [(source_url, "Alien (1979) is confirmed Profile 7 FEL")])

    assert client.calls == 3
    assert sleeps == [1.0, 2.0]
    assert [release.movie_title for release in releases] == ["Alien"]


def test_ai_extract_releases_moves_on_after_retries_are_exhausted(capsys):
    sheet_url = "https://docs.google.com/spreadsheets/d/sheet-id/edit#gid=99"
    forum_url = "https://forum.example.test/thread"
    calls: list[str] = []

    class FailingSheetAIClient(FakeAIClient):
        def extract_candidates(self, source_url: str, text: str):
            calls.append(source_url)
            if source_url == sheet_url:
                raise httpx.HTTPError("peer closed connection")
            return [
                FoundCandidate(
                    "Heat",
                    "1995",
                    source_url,
                    "Heat (1995) is confirmed Profile 7 FEL.",
                    "ai",
                )
            ]

    releases = ai_extract_releases(
        FailingSheetAIClient(),
        [
            (sheet_url, "Movie Name,DV Source\n"),
            (forum_url, "Heat (1995) is confirmed Profile 7 FEL."),
        ],
    )

    assert calls == [sheet_url, sheet_url, sheet_url, forum_url]
    assert [release.movie_title for release in releases] == ["Heat"]
    assert f"ai-scrape: extraction failed for {sheet_url}" in capsys.readouterr().out


def test_is_google_doc_url_uses_hostname_not_substring():
    assert _is_google_doc_url("https://docs.google.com/spreadsheets/d/sheet-id/edit")
    assert _is_google_doc_url("https://foo.docs.google.com/document/d/doc-id/edit")
    assert not _is_google_doc_url(
        "https://example.test/thread?next=https://docs.google.com/spreadsheets/d/id"
    )


def test_fetch_url_for_ai_source_only_converts_real_google_sheets_urls():
    sheet_url = "https://docs.google.com/spreadsheets/d/sheet-id/edit#gid=99"
    scoped_sheet_url = "https://docs.google.com/spreadsheets/u/0/d/sheet-id/edit#gid=99"
    malformed_sheet_url = "https://docs.google.com/spreadsheets/edit#gid=99"
    non_google_url = (
        "https://example.test/thread?"
        "next=https://docs.google.com/spreadsheets/d/sheet-id/edit"
    )

    assert _fetch_url_for_ai_source(sheet_url) == (
        "https://docs.google.com/spreadsheets/d/sheet-id/gviz/tq?tqx=out:csv&gid=99"
    )
    assert _fetch_url_for_ai_source(scoped_sheet_url) == (
        "https://docs.google.com/spreadsheets/d/sheet-id/gviz/tq?tqx=out:csv&gid=99"
    )
    assert _fetch_url_for_ai_source(malformed_sheet_url) == malformed_sheet_url
    assert _fetch_url_for_ai_source(non_google_url) == non_google_url


def test_ai_scrape_releases_uses_google_sheet_csv_urls_and_fetches_forum_sources(
    monkeypatch,
    tmp_path,
):
    import ai_scrape as ai_scrape_mod

    fetched_urls: list[str] = []

    class FakeFetchResult:
        def __init__(self, text: str = "", error: str = "") -> None:
            self.text = text
            self.error = error

    class FakeFetcher:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def fetch(self, url: str, raise_on_error: bool = False):
            fetched_urls.append(url)
            return FakeFetchResult(text="<html>Alien FEL</html>")

    monkeypatch.setattr(ai_scrape_mod.fetcher, "Fetcher", FakeFetcher)
    monkeypatch.setattr(
        ai_scrape_mod,
        "ai_extract_releases",
        lambda client, pages: [
            FelRelease(
                movie_title=source_url,
                fel_evidence=FelEvidence(
                    source_url=source_url,
                    quote=text,
                    evidence_type="ai-extracted",
                ),
            )
            for source_url, text in pages
        ],
    )

    sheet_url = "https://docs.google.com/spreadsheets/d/sheet-id/edit#gid=99"
    forum_url = "https://forum.example.test/thread"
    releases = ai_scrape_releases(
        [sheet_url, forum_url],
        tmp_path / ".cache",
        FakeAIClient(),
    )

    assert fetched_urls == [
        "https://docs.google.com/spreadsheets/d/sheet-id/gviz/tq?tqx=out:csv&gid=99",
        forum_url,
    ]
    assert [release.movie_title for release in releases] == [sheet_url, forum_url]


def test_ai_scrape_releases_skips_non_google_fetch_result_errors(
    monkeypatch,
    tmp_path,
    capsys,
):
    import ai_scrape as ai_scrape_mod

    fetched_urls: list[str] = []
    bad_url = "https://bad.test/thread"

    class FakeFetchResult:
        text = ""
        error = "failed to fetch bad URL"

    class FakeFetcher:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def fetch(self, url: str, raise_on_error: bool = False):
            fetched_urls.append(url)
            assert raise_on_error is False
            return FakeFetchResult()

    monkeypatch.setattr(ai_scrape_mod.fetcher, "Fetcher", FakeFetcher)

    assert ai_scrape_releases([bad_url], tmp_path / ".cache", FakeAIClient()) == []

    assert fetched_urls == [bad_url]
    assert f"ai-scrape: fetch failed for {bad_url}" in capsys.readouterr().out


def test_ai_scrape_releases_skips_google_sheet_fetch_errors(
    monkeypatch,
    tmp_path,
    capsys,
):
    import ai_scrape as ai_scrape_mod

    fetched_urls: list[str] = []
    sheet_url = "https://docs.google.com/spreadsheets/d/sheet-id/edit#gid=99"
    forum_url = "https://forum.example.test/thread"

    class FakeFetchResult:
        def __init__(self, text: str = "", error: str = "") -> None:
            self.text = text
            self.error = error

    class FakeFetcher:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def fetch(self, url: str, raise_on_error: bool = False):
            fetched_urls.append(url)
            if "gviz/tq" in url:
                return FakeFetchResult(error="read failed")
            return FakeFetchResult(text="<html>Heat FEL</html>")

    monkeypatch.setattr(ai_scrape_mod.fetcher, "Fetcher", FakeFetcher)
    monkeypatch.setattr(
        ai_scrape_mod,
        "ai_extract_releases",
        lambda client, pages: [
            FelRelease(
                movie_title=source_url,
                fel_evidence=FelEvidence(
                    source_url=source_url,
                    quote=text,
                    evidence_type="ai-extracted",
                ),
            )
            for source_url, text in pages
        ],
    )

    releases = ai_scrape_releases(
        [sheet_url, forum_url],
        tmp_path / ".cache",
        FakeAIClient(),
    )

    assert fetched_urls == [
        "https://docs.google.com/spreadsheets/d/sheet-id/gviz/tq?tqx=out:csv&gid=99",
        forum_url,
    ]
    assert [release.movie_title for release in releases] == [forum_url]
    assert f"ai-scrape: fetch failed for {sheet_url}" in capsys.readouterr().out


def test_ai_scrape_releases_propagates_unexpected_google_fetch_exceptions(
    monkeypatch,
    tmp_path,
):
    import ai_scrape as ai_scrape_mod

    sheet_url = "https://docs.google.com/spreadsheets/d/sheet-id/edit#gid=99"

    class FakeFetcher:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def fetch(self, url: str, raise_on_error: bool = False):
            raise AssertionError("internal fetcher bug")

    monkeypatch.setattr(ai_scrape_mod.fetcher, "Fetcher", FakeFetcher)

    with pytest.raises(AssertionError, match="internal fetcher bug"):
        ai_scrape_releases([sheet_url], tmp_path / ".cache", FakeAIClient())


def test_load_existing_releases_round_trips(tmp_path):
    release = FelRelease(
        movie_title="Dune",
        release_date="2021",
        fel_evidence=FelEvidence(
            source_url="https://src.test",
            quote="Dune FEL",
            evidence_type="forum-post",
        ),
        source_label="forums",
        collected_at="2026-05-21T00:00:00+00:00",
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "releases.json").write_text(
        json.dumps([release.to_dict()]), encoding="utf-8"
    )

    loaded = _load_existing_releases(tmp_path)

    assert [r.movie_title for r in loaded] == ["Dune"]


def test_load_existing_releases_missing_file_returns_empty(tmp_path):
    assert _load_existing_releases(tmp_path) == []


def test_run_ai_scrape_merges_into_existing_database(tmp_path, monkeypatch):
    """ai-scrape must add to releases.json, never replace it."""
    import ai_scrape as ai_scrape_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "posters").mkdir()
    existing = FelRelease(
        movie_title="Existing Movie",
        release_date="2020",
        fel_evidence=FelEvidence(
            source_url="https://forum.test/1",
            quote="Existing FEL",
            evidence_type="forum-post",
        ),
        source_label="forums",
        collected_at="2026-05-01T00:00:00+00:00",
    )
    (data_dir / "releases.json").write_text(
        json.dumps([existing.to_dict()]), encoding="utf-8"
    )
    (tmp_path / "forums.txt").write_text("https://forum.test/1\n", encoding="utf-8")
    (tmp_path / "google_sheets.txt").write_text("", encoding="utf-8")

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    class _NoopClient:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    ai_candidate = FoundCandidate(
        "New AI Movie", "2025", "https://ai.test", "New AI Movie FEL", "ai"
    )
    monkeypatch.setattr(ai_scrape_mod, "AIClient", lambda settings: _NoopClient())
    monkeypatch.setattr(ai_scrape_mod, "ai_discover_sources", lambda *a, **k: [])
    monkeypatch.setattr(
        ai_scrape_mod,
        "ai_scrape_releases",
        lambda *a, **k: [
            _candidate_to_release(ai_candidate, "2026-05-21T00:00:00+00:00")
        ],
    )

    rc = ai_scrape_mod.run_ai_scrape(
        tmp_path / "forums.txt", tmp_path, tmp_path / ".cache"
    )

    assert rc == 0
    result = json.loads((data_dir / "releases.json").read_text(encoding="utf-8"))
    titles = {row["movie_title"] for row in result}
    assert "Existing Movie" in titles  # pre-existing data preserved
    assert "New AI Movie" in titles  # AI-discovered release added
