import httpx
import respx

from fel_dolby_vision_movies.discovery import (
    brave_search,
    build_source_search_queries,
    discover_source_candidates,
    extract_candidate_links,
    filter_candidate_source_urls,
)


def test_build_source_search_queries_includes_profile_7_fel_and_sites():
    queries = build_source_search_queries()

    assert any("Profile 7" in query and "FEL" in query for query in queries)
    assert any(query.startswith("site:") for query in queries)


def test_extract_candidate_links_keeps_physical_media_candidates():
    html = """
    <a href="https://forum.blu-ray.com/showthread.php?t=123">Dolby Vision FEL thread</a>
    <a href="https://example.test/hardware">HDMI splitter FEL support</a>
    """
    assert extract_candidate_links(html, "https://forum.blu-ray.com/") == [
        "https://forum.blu-ray.com/showthread.php?t=123"
    ]


def test_extract_candidate_links_rejects_unrelated_forum_links():
    html = """
    <a href="https://forum.blu-ray.com/showthread.php?t=123">General discussion</a>
    <a href="https://forum.blu-ray.com/releases/uhd-blu-ray">UHD Blu-ray releases</a>
    """
    assert extract_candidate_links(html, "https://forum.blu-ray.com/") == [
        "https://forum.blu-ray.com/releases/uhd-blu-ray"
    ]


def test_brave_search_without_key_returns_empty_list():
    assert brave_search("Dolby Vision FEL Blu-ray forum", api_key=None) == []


@respx.mock
def test_brave_search_returns_result_urls():
    respx.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {"url": "https://forum.blu-ray.com/showthread.php?t=276448"},
                        {"url": "https://github.com/iammarxg/FEL"},
                    ]
                }
            },
        )
    )
    assert brave_search("Dolby Vision FEL Blu-ray forum", api_key="test") == [
        "https://forum.blu-ray.com/showthread.php?t=276448",
        "https://github.com/iammarxg/FEL",
    ]


@respx.mock
def test_brave_search_dedupes_result_urls_preserving_order():
    respx.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {"url": "https://forum.blu-ray.com/showthread.php?t=276448"},
                        {"url": "https://example.test/fel"},
                        {"url": "https://forum.blu-ray.com/showthread.php?t=276448"},
                    ]
                }
            },
        )
    )
    assert brave_search("Dolby Vision FEL Blu-ray forum", api_key="test") == [
        "https://forum.blu-ray.com/showthread.php?t=276448",
        "https://example.test/fel",
    ]


@respx.mock
def test_brave_search_uses_title_and_description_for_candidate_filtering():
    respx.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "url": "https://forum.blu-ray.com/showthread.php?t=360730",
                            "title": "Hunt 4K Blu-ray BDInfo",
                            "description": "Dolby Vision Profile 7 FEL from BDInfo",
                        },
                        {
                            "url": "https://example.test/downloads/123",
                            "title": "Dolby Vision Profile 7 FEL remux download",
                            "description": "torrent link",
                        },
                        {
                            "url": "https://trakt.tv/users/example/lists/dolby-vision-profile-7-fel",
                            "title": "Dolby Vision Profile 7 FEL list",
                            "description": "Cloudflare-gated title list",
                        },
                        {
                            "url": "https://www.reddit.com/r/PleX/comments/example",
                            "title": "Dolby Vision Profile 7 FEL playback in Plex",
                            "description": "player support thread",
                        },
                    ]
                }
            },
        )
    )

    result = discover_source_candidates(api_key="test", queries=["query"])

    assert result.candidate_urls == [
        "https://forum.blu-ray.com/showthread.php?t=360730"
    ]
    assert result.raw_url_count == 4
    assert result.rejected_url_count == 3


def test_filter_candidate_source_urls_dedupes_normalizes_and_rejects_blocked_urls():
    assert filter_candidate_source_urls(
        [
            "HTTPS://Forum.Example.test/threads/profile-7-fel-uhd-blu-ray#reply",
            "https://forum.example.test/threads/profile-7-fel-uhd-blu-ray",
            "https://example.test/hardware/dolby-vision-profile-7-fel-player",
            "https://example.test/download/dolby-vision-profile-7-fel-remux",
            "https://github.com/example/fel",
            "https://lists.example.test/dolby-vision-profile-7-fel-uhd-blu-ray",
        ]
    ) == [
        "https://forum.example.test/threads/profile-7-fel-uhd-blu-ray",
        "https://lists.example.test/dolby-vision-profile-7-fel-uhd-blu-ray",
    ]


def test_discover_source_candidates_without_api_key_reports_unavailable():
    result = discover_source_candidates(api_key=None)

    assert result.brave_available is False
    assert result.candidate_urls == []
    assert result.queries


def test_discover_source_candidates_runs_queries_and_filters_results():
    calls = []

    def fake_search(query: str, api_key: str) -> list[str]:
        calls.append((query, api_key))
        return [
            "https://forum.example.test/dolby-vision-profile-7-fel-uhd-blu-ray",
            "https://example.test/hdmi/dolby-vision-profile-7-fel",
        ]

    result = discover_source_candidates(api_key="secret", search=fake_search)

    assert len(calls) == len(build_source_search_queries())
    assert {api_key for _, api_key in calls} == {"secret"}
    assert result.brave_available is True
    assert result.candidate_urls == [
        "https://forum.example.test/dolby-vision-profile-7-fel-uhd-blu-ray"
    ]
