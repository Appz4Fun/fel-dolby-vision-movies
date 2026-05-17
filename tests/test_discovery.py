import httpx
import respx

from fel_dolby_vision_movies.discovery import brave_search, extract_candidate_links


def test_extract_candidate_links_keeps_physical_media_candidates():
    html = """
    <a href="https://forum.blu-ray.com/showthread.php?t=123">Dolby Vision FEL thread</a>
    <a href="https://example.test/hardware">HDMI splitter FEL support</a>
    """
    assert extract_candidate_links(html, "https://forum.blu-ray.com/") == [
        "https://forum.blu-ray.com/showthread.php?t=123"
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
