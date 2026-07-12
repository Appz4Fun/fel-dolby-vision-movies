import json

import httpx
import pytest

import ai_scrape as ai_scrape_mod
import compare
from ai_scrape import (
    _candidate_to_release,
    _extract_candidates_with_retries,
    _fetch_url_for_ai_source,
    _is_google_doc_url,
    _is_retryable_extraction_error,
    _load_existing_releases,
    _parse_url_list,
    ai_discover_sources,
    ai_extract_releases,
    ai_scrape_releases,
)
from compare import AIResponseFormatError, AIServiceUnavailableError, FoundCandidate
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
    assert _parse_url_list('{"unexpected": []}') == []
    assert _parse_url_list('{"urls": "https://not-a-list.test"}') == []


@pytest.mark.parametrize(
    "hostile_json",
    ["9" * 5000, "[" * 1500 + "]" * 1500],
    ids=["huge-integer", "deep-nesting"],
)
def test_parse_url_list_tolerates_hostile_json(hostile_json):
    assert _parse_url_list(hostile_json) == []


def test_discovery_input_limit_accepts_limit_and_rejects_limit_plus_one():
    limit = ai_scrape_mod.MAX_AI_DISCOVERY_ITEMS
    at_limit = [f"https://source-{index}.example/list" for index in range(limit)]
    over_limit = [*at_limit, "https://one-too-many.example/list"]

    assert _parse_url_list(json.dumps(at_limit)) == at_limit
    assert _parse_url_list(json.dumps(over_limit)) == []


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
    result = ai_discover_sources(
        client,
        ["https://known.test/list"],
        resolver=lambda _hostname: ["93.184.216.34"],
    )
    assert result == ["https://forum.blu-ray.com/showthread.php?t=999"]


def test_ai_discover_sources_rejects_unsafe_urls_and_caps_unique_sources():
    safe_urls = [f"https://source{i}.example/list" for i in range(30)]
    client = FakeAIClient(
        complete_text=json.dumps(
            [
                "http://127.0.0.1/admin",
                "https://user:secret@attacker.example/list",
                "https://attacker.example/line\nhttps://second.example/list",
                "https://old.reddit.com/r/test/comments/abc/title",
                "https://www.reddit.com/r/test/comments/abc/another-title",
                *safe_urls,
            ]
        )
    )

    result = ai_discover_sources(
        client,
        [],
        resolver=lambda _hostname: ["93.184.216.34"],
    )

    assert len(result) == 25
    assert result[0] == "https://old.reddit.com/r/test/comments/abc/title"
    assert result[1:] == safe_urls[:24]


def test_ai_discover_sources_rejects_hosts_with_private_dns_answers():
    client = FakeAIClient(
        complete_text=json.dumps(
            [
                "https://private.example/list",
                "https://public.example/list#fragment",
            ]
        )
    )

    result = ai_discover_sources(
        client,
        ["not-a-url", "https://public.example/other"],
        resolver=lambda hostname: (
            ["127.0.0.1"] if hostname == "private.example" else ["93.184.216.34"]
        ),
    )

    assert result == ["https://public.example/list"]


def test_ai_discover_sources_preserves_google_sheet_tab_identifier():
    sheet_url = "https://docs.google.com/spreadsheets/d/sheet-id/edit#gid=99"
    client = FakeAIClient(complete_text=json.dumps([sheet_url]))

    result = ai_discover_sources(
        client,
        [],
        resolver=lambda _hostname: ["93.184.216.34"],
    )

    assert result == [sheet_url]


def test_ai_discover_sources_keeps_distinct_google_sheet_tabs():
    gid_1 = "https://docs.google.com/spreadsheets/d/sheet-id/edit#gid=1"
    gid_2 = "https://docs.google.com/spreadsheets/d/sheet-id/edit#gid=2"
    client = FakeAIClient(complete_text=json.dumps([gid_1, gid_2]))

    result = ai_discover_sources(
        client,
        [gid_1],
        resolver=lambda _hostname: ["93.184.216.34"],
    )

    assert result == [gid_2]


def test_ai_discover_sources_returns_empty_with_safe_http_diagnostic(capsys):
    request = httpx.Request(
        "POST", "https://user:credential@api.example.test/responses?key=secret"
    )
    response = httpx.Response(503, text="private response body", request=request)

    class FailingAIClient(FakeAIClient):
        def complete(self, system_prompt: str, user_prompt: str) -> str:
            raise httpx.HTTPStatusError(
                "private response body",
                request=request,
                response=response,
            )

    assert ai_discover_sources(FailingAIClient(), []) == []
    diagnostic = capsys.readouterr().out.strip()
    assert (
        diagnostic == "ai-scrape: source discovery failed: HTTPStatusError status=503"
    )
    assert "credential" not in diagnostic
    assert "secret" not in diagnostic
    assert "private response body" not in diagnostic


@pytest.mark.parametrize("status_code", [300, 301, 302, 307, 308, 501, 505])
def test_ai_discover_sources_fails_globally_on_permanent_endpoint_status(
    status_code,
):
    request = httpx.Request("POST", "https://api.example.test/responses")
    response = httpx.Response(status_code, text="private body", request=request)

    class PermanentFailureClient(FakeAIClient):
        def complete(self, system_prompt: str, user_prompt: str) -> str:
            raise httpx.HTTPStatusError(
                "private body",
                request=request,
                response=response,
            )

    with pytest.raises(compare.AIGlobalHTTPError) as exc_info:
        ai_discover_sources(PermanentFailureClient(), [])

    assert exc_info.value.status_code == status_code
    assert "private body" not in str(exc_info.value)


def test_ai_discover_sources_propagates_ai_response_format_error():
    class MalformedAIClient(FakeAIClient):
        def complete(self, system_prompt: str, user_prompt: str) -> str:
            raise AIResponseFormatError()

    with pytest.raises(AIResponseFormatError):
        ai_discover_sources(MalformedAIClient(), [])


def test_ai_discover_sources_propagates_redacted_global_http_error():
    error = compare.AIGlobalHTTPError(401)

    class AuthFailureClient(FakeAIClient):
        def complete(self, system_prompt: str, user_prompt: str) -> str:
            raise error

    with pytest.raises(compare.AIGlobalHTTPError) as exc_info:
        ai_discover_sources(AuthFailureClient(), [])

    assert exc_info.value is error


def test_ai_discover_sources_raises_format_error_for_invalid_model_json():
    with pytest.raises(AIResponseFormatError):
        ai_discover_sources(FakeAIClient(complete_text="not-json"), [])


def test_ai_discover_sources_rejects_noncompleted_nested_json_status():
    payload = json.dumps({"status": "failed", "urls": ["https://public.example/list"]})

    with pytest.raises(AIResponseFormatError):
        ai_discover_sources(FakeAIClient(complete_text=payload), [])


def test_ai_discover_sources_accepts_completed_nested_json_status():
    payload = json.dumps(
        {"status": "completed", "urls": ["https://public.example/list"]}
    )

    assert ai_discover_sources(
        FakeAIClient(complete_text=payload),
        [],
        resolver=lambda _hostname: ["93.184.216.34"],
    ) == ["https://public.example/list"]


@pytest.mark.parametrize(
    "items",
    [
        [None, 7, False],
        ["", "   ", "\n"],
        [{"url": "https://source.example/list"}],
    ],
)
def test_ai_discover_sources_rejects_invalid_only_nonempty_lists(items):
    with pytest.raises(AIResponseFormatError):
        ai_discover_sources(FakeAIClient(complete_text=json.dumps(items)), [])


def test_ai_discover_sources_accepts_empty_and_mixed_lists():
    assert ai_discover_sources(FakeAIClient(complete_text="[]"), []) == []

    result = ai_discover_sources(
        FakeAIClient(
            complete_text=json.dumps([None, " ", "https://public.example/list", 7])
        ),
        [],
        resolver=lambda _hostname: ["93.184.216.34"],
    )

    assert result == ["https://public.example/list"]


def test_ai_extract_releases_converts_nonblank_candidates():
    candidates = [
        FoundCandidate(
            "Drop", "2025", "https://src.test", "Drop (2025) Profile 7 FEL", "ai"
        ),
        FoundCandidate("", "2020", "https://src.test", "blank", "ai"),
    ]
    client = FakeAIClient(candidates=candidates)
    releases = ai_extract_releases(
        client, [("https://src.test", "Drop (2025) Profile 7 FEL")]
    )
    assert [r.movie_title for r in releases] == ["Drop"]
    assert releases[0].fel_evidence.evidence_type == "ai-extracted"


def test_ai_extract_releases_skips_sources_that_raise_http_errors():
    class FailingAIClient(FakeAIClient):
        def extract_candidates(self, source_url: str, text: str):
            raise httpx.HTTPError("boom")

    assert (
        ai_extract_releases(FailingAIClient(), [("https://src.test", "<html>")]) == []
    )


def test_ai_extraction_format_error_is_not_retried_or_swallowed(monkeypatch):
    import ai_scrape as ai_scrape_mod

    sleeps: list[float] = []

    class MalformedAIClient(FakeAIClient):
        def __init__(self) -> None:
            self.calls = 0

        def extract_candidates(self, source_url: str, text: str):
            self.calls += 1
            raise AIResponseFormatError()

    monkeypatch.setattr(ai_scrape_mod.time, "sleep", sleeps.append)
    client = MalformedAIClient()

    assert not _is_retryable_extraction_error(AIResponseFormatError())
    with pytest.raises(AIResponseFormatError):
        _extract_candidates_with_retries(client, "https://src.test", "source")
    with pytest.raises(AIResponseFormatError):
        ai_extract_releases(client, [("https://src.test", "source")])

    assert client.calls == 2
    assert sleeps == []


def test_ai_extract_releases_propagates_redacted_global_http_error():
    error = compare.AIGlobalHTTPError(403)

    class AuthFailureClient(FakeAIClient):
        def extract_candidates(self, source_url: str, text: str):
            raise error

    with pytest.raises(compare.AIGlobalHTTPError) as exc_info:
        ai_extract_releases(AuthFailureClient(), [("https://src.test", "source")])

    assert exc_info.value is error


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

    releases = ai_extract_releases(
        client, [(sheet_url, "Alien (1979) is confirmed Profile 7 FEL")]
    )

    assert client.calls == 2
    assert [release.movie_title for release in releases] == ["Alien"]


@pytest.mark.parametrize(
    "status_code",
    [
        300,
        301,
        302,
        307,
        308,
        400,
        401,
        403,
        404,
        405,
        406,
        407,
        410,
        415,
        418,
        422,
        499,
        501,
        505,
        511,
    ],
)
def test_ai_extract_releases_fails_globally_on_configuration_status(
    status_code, monkeypatch
):
    source_url = "https://forum.example.test/thread"
    sleeps: list[float] = []
    request = httpx.Request(
        "POST", "https://user:credential@api.example.test/extract?key=secret"
    )
    response = httpx.Response(status_code, text="private body", request=request)

    class PermanentFailureAIClient(FakeAIClient):
        def __init__(self) -> None:
            self.calls = 0

        def extract_candidates(self, source_url: str, text: str):
            self.calls += 1
            raise httpx.HTTPStatusError(
                "private body",
                request=request,
                response=response,
            )

    monkeypatch.setattr(ai_scrape_mod.time, "sleep", sleeps.append)
    client = PermanentFailureAIClient()

    with pytest.raises(compare.AIGlobalHTTPError) as exc_info:
        ai_extract_releases(
            client,
            [
                (source_url, "<html>Alien FEL</html>"),
                ("https://second.example/list", "<html>Heat FEL</html>"),
            ],
        )

    assert exc_info.value.status_code == status_code
    assert client.calls == 1
    assert sleeps == []


@pytest.mark.parametrize("status_code", [408, 409, 425, 429, 500, 502, 503, 504])
def test_ai_extraction_keeps_transient_statuses_retryable(status_code):
    request = httpx.Request("POST", "https://api.example.test/extract")
    response = httpx.Response(status_code, request=request)
    error = httpx.HTTPStatusError(
        "transient",
        request=request,
        response=response,
    )

    assert _is_retryable_extraction_error(error)


def test_ai_extraction_keeps_runtime_proxy_errors_retryable():
    request = httpx.Request("POST", "https://api.example.test/extract")

    assert _is_retryable_extraction_error(
        httpx.ProxyError("transient proxy outage", request=request)
    )


def test_ai_extraction_keeps_service_unavailable_retryable():
    assert _is_retryable_extraction_error(AIServiceUnavailableError())


def test_ai_extract_releases_retries_transient_service_unavailable():
    source_url = "https://forum.example.test/thread"

    class FlakyAIClient(FakeAIClient):
        def __init__(self) -> None:
            self.calls = 0

        def extract_candidates(self, source_url: str, text: str):
            self.calls += 1
            if self.calls == 1:
                raise AIServiceUnavailableError()
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

    releases = ai_extract_releases(
        client, [(source_url, "Alien (1979) is confirmed Profile 7 FEL")]
    )

    assert client.calls == 2
    assert [release.movie_title for release in releases] == ["Alien"]


def test_ai_discover_sources_retries_transient_service_unavailable():
    calls = 0

    class FlakyDiscoveryClient(FakeAIClient):
        def complete(self, system_prompt: str, user_prompt: str) -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise AIServiceUnavailableError()
            return json.dumps(["https://forum.example.test/new-thread"])

    discovered = ai_discover_sources(
        FlakyDiscoveryClient(),
        [],
        resolver=lambda _hostname: ["93.184.216.34"],
    )

    assert calls == 2
    assert discovered == ["https://forum.example.test/new-thread"]


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

    releases = ai_extract_releases(
        client, [(source_url, "Alien (1979) is confirmed Profile 7 FEL")]
    )

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
    assert capsys.readouterr().out.strip() == (
        "ai-scrape: extraction failed: HTTPError"
    )


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


def test_ai_scrape_releases_fetches_and_extracts_pages_incrementally(
    monkeypatch,
    tmp_path,
):
    import ai_scrape as ai_scrape_mod

    events = []

    class FakeFetchResult:
        error = ""

        def __init__(self, text):
            self.text = text

    class FakeFetcher:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def fetch(self, url, raise_on_error=False):
            events.append(f"fetch:{url}")
            return FakeFetchResult(url)

    def extract(_client, pages):
        releases = []
        for source_url, text in pages:
            events.append(f"extract:{source_url}")
            assert text == source_url
            releases.append(
                FelRelease(
                    source_url,
                    fel_evidence=FelEvidence(source_url, text, "ai-extracted"),
                )
            )
        return releases

    monkeypatch.setattr(ai_scrape_mod.fetcher, "Fetcher", FakeFetcher)
    monkeypatch.setattr(ai_scrape_mod, "ai_extract_releases", extract)
    urls = ["https://one.test/list", "https://two.test/list"]

    releases = ai_scrape_releases(urls, tmp_path / ".cache", FakeAIClient())

    assert [release.movie_title for release in releases] == urls
    assert events == [
        "fetch:https://one.test/list",
        "extract:https://one.test/list",
        "fetch:https://two.test/list",
        "extract:https://two.test/list",
    ]


def test_ai_scrape_releases_skips_non_google_fetch_result_errors(
    monkeypatch,
    tmp_path,
    capsys,
):
    import ai_scrape as ai_scrape_mod

    fetched_urls: list[str] = []
    secret = "credential-in-url"
    bad_url = f"https://user:{secret}@bad.test/thread?key=private"

    class FakeFetchResult:
        text = ""
        error = f"failed with private body and {secret}"

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
    diagnostic = capsys.readouterr().out.strip()
    assert diagnostic == "ai-scrape: fetch failed"
    assert secret not in diagnostic
    assert "private body" not in diagnostic


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
    assert capsys.readouterr().out.strip() == "ai-scrape: fetch failed"


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


def test_publish_ai_releases_forwards_review_path(monkeypatch, tmp_path):
    import ai_scrape as mod

    seen = {}
    monkeypatch.setattr(
        "main._enrich_if_possible",
        lambda releases: seen.setdefault("enriched", releases),
    )
    monkeypatch.setattr(
        "artifacts.publish_outputs",
        lambda releases, **kwargs: seen.update(kwargs) or releases,
    )
    review = tmp_path / "review.json"
    releases = [
        FelRelease(
            "Movie",
            "2025",
            FelEvidence("https://src", "Movie (2025) Profile 7 FEL", "ai-extracted"),
        )
    ]
    assert mod._publish_ai_releases(releases, tmp_path, review) == releases
    assert seen["review_output_path"] == review


def test_run_ai_scrape_missing_credentials_writes_canonical_review(
    tmp_path, monkeypatch, capsys
):
    import ai_scrape as mod
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda: False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.delenv("THECLAWBAY_API_KEY", raising=False)
    review = tmp_path / "review.json"
    assert (
        mod.run_ai_scrape(tmp_path / "forums.txt", tmp_path, tmp_path / "cache", review)
        == 0
    )
    assert json.loads(review.read_text()) == {
        "merged_count": 0,
        "addition_count": 0,
        "review_count": 0,
        "items": [],
    }
    output = capsys.readouterr().out
    assert "OPENAI_API_KEY" in output
    assert "CODEX_API_KEY" in output
    assert "THECLAWBAY_API_KEY" in output


@pytest.mark.parametrize("failure_phase", ["discovery", "extraction"])
def test_run_ai_scrape_fails_fast_on_redacted_ai_format_error(
    failure_phase, tmp_path, monkeypatch, capsys
):
    import ai_scrape as mod

    secret = "super-secret-api-key"
    response_body = "private-malformed-response-body"
    monkeypatch.setenv("OPENAI_API_KEY", secret)

    class NoopClient:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def format_error(*args, **kwargs):
        error = AIResponseFormatError()
        error.__cause__ = ValueError(response_body)
        raise error

    monkeypatch.setattr(mod, "AIClient", lambda settings: NoopClient())
    if failure_phase == "discovery":
        monkeypatch.setattr(mod, "ai_discover_sources", format_error)
        monkeypatch.setattr(
            mod,
            "ai_scrape_releases",
            lambda *args, **kwargs: pytest.fail("extraction must not run"),
        )
    else:
        monkeypatch.setattr(mod, "ai_discover_sources", lambda *args, **kwargs: [])
        monkeypatch.setattr(mod, "ai_scrape_releases", format_error)

    result = mod.run_ai_scrape(
        tmp_path / "forums.txt",
        tmp_path,
        tmp_path / "cache",
    )

    assert result == 1
    captured = capsys.readouterr()
    diagnostic = captured.out + captured.err
    assert "AI response format" in diagnostic
    assert secret not in diagnostic
    assert response_body not in diagnostic
    assert "ValueError" not in diagnostic
    assert "ai-scrape complete" not in diagnostic


@pytest.mark.parametrize("status_code", [302, 401, 501, 505])
def test_run_ai_scrape_stops_after_permanent_discovery_failure_with_safe_diagnostic(
    status_code, tmp_path, monkeypatch, capsys
):
    secret = "super-secret-api-key"
    private_body = "private-auth-response-body"
    request = httpx.Request(
        "POST", f"https://user:{secret}@api.example.test/responses?key={secret}"
    )
    response = httpx.Response(status_code, text=private_body, request=request)

    class AuthFailureClient:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def complete(self, system_prompt, user_prompt):
            raise httpx.HTTPStatusError(
                private_body,
                request=request,
                response=response,
            )

    monkeypatch.setenv("OPENAI_API_KEY", secret)
    monkeypatch.setattr(ai_scrape_mod, "AIClient", lambda settings: AuthFailureClient())
    monkeypatch.setattr(
        ai_scrape_mod,
        "ai_scrape_releases",
        lambda *args, **kwargs: pytest.fail(
            "extraction must not run after auth failure"
        ),
    )

    result = ai_scrape_mod.run_ai_scrape(
        tmp_path / "forums.txt",
        tmp_path,
        tmp_path / "cache",
    )

    assert result == 1
    diagnostic = capsys.readouterr().out.strip()
    assert diagnostic == f"ai-scrape failed; AIGlobalHTTPError status={status_code}"
    assert secret not in diagnostic
    assert private_body not in diagnostic


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda secret: httpx.UnsupportedProtocol(f"invalid URL {secret}"),
        lambda secret: httpx.InvalidURL(f"invalid URL {secret}"),
    ],
    ids=["unsupported-protocol", "invalid-url"],
)
def test_run_ai_scrape_stops_after_one_invalid_url_error_without_secrets(
    error_factory, tmp_path, monkeypatch, capsys
):
    secret = "private-url-credential"
    calls = 0

    class InvalidURLClient:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def complete(self, system_prompt, user_prompt):
            nonlocal calls
            calls += 1
            raise error_factory(secret)

    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    monkeypatch.setattr(ai_scrape_mod, "AIClient", lambda settings: InvalidURLClient())
    monkeypatch.setattr(
        ai_scrape_mod,
        "ai_scrape_releases",
        lambda *args, **kwargs: pytest.fail(
            "extraction must not run after invalid URL failure"
        ),
    )

    result = ai_scrape_mod.run_ai_scrape(
        tmp_path / "forums.txt", tmp_path, tmp_path / "cache"
    )

    assert result == 1
    assert calls == 1
    diagnostic = capsys.readouterr().out.strip()
    assert diagnostic == "ai-scrape failed; AIGlobalHTTPError"
    assert secret not in diagnostic


def test_run_ai_scrape_returns_one_for_blank_configured_base_url(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "")

    result = ai_scrape_mod.run_ai_scrape(
        tmp_path / "forums.txt", tmp_path, tmp_path / "cache"
    )

    assert result == 1
    diagnostic = capsys.readouterr().out.strip()
    assert diagnostic == "ai-scrape failed; AIGlobalHTTPError"
    assert "secret-key" not in diagnostic


def test_run_ai_scrape_fails_fast_on_global_extraction_configuration_error(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")

    class NoopClient:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def extraction_failure(*args, **kwargs):
        raise compare.AIGlobalHTTPError(422)

    monkeypatch.setattr(ai_scrape_mod, "AIClient", lambda settings: NoopClient())
    monkeypatch.setattr(ai_scrape_mod, "ai_discover_sources", lambda *args: [])
    monkeypatch.setattr(ai_scrape_mod, "ai_scrape_releases", extraction_failure)

    result = ai_scrape_mod.run_ai_scrape(
        tmp_path / "forums.txt", tmp_path, tmp_path / "cache"
    )

    assert result == 1
    assert capsys.readouterr().out.strip() == (
        "ai-scrape failed; AIGlobalHTTPError status=422"
    )


def test_run_ai_scrape_rejects_review_collision_before_credentials_or_writes(
    tmp_path,
    monkeypatch,
    capsys,
):
    import ai_scrape as mod

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    releases_path = data_dir / "releases.json"
    original_bytes = b'[{"sentinel": "existing release body"}]\n'
    releases_path.write_bytes(original_bytes)
    review_path = tmp_path / "review.json"
    review_path.hardlink_to(releases_path)

    def fail_credentials():
        raise AssertionError("credentials must not be inspected")

    monkeypatch.setattr(mod.AISettings, "from_env", staticmethod(fail_credentials))

    exit_code = mod.run_ai_scrape(
        tmp_path / "forums.txt",
        tmp_path,
        tmp_path / "cache",
        review_path,
    )

    assert exit_code == 2
    assert releases_path.read_bytes() == original_bytes
    output = capsys.readouterr().out
    assert output.strip() == "review output must not refer to data/releases.json"
    assert "existing release body" not in output


def test_run_ai_scrape_rejects_invalid_review_target_before_credentials(
    tmp_path,
    monkeypatch,
    capsys,
):
    import ai_scrape as mod

    review_path = tmp_path / "review.json"
    review_path.mkdir()

    def fail_credentials():
        raise AssertionError("credentials must not be inspected")

    monkeypatch.setattr(mod.AISettings, "from_env", staticmethod(fail_credentials))

    exit_code = mod.run_ai_scrape(
        tmp_path / "forums.txt",
        tmp_path,
        tmp_path / "cache",
        review_path,
    )

    assert exit_code == 2
    assert capsys.readouterr().out.strip() == (
        "review output must be a regular file path"
    )


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
