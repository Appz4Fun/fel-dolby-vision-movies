from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import httpx
import pytest

from bluray import (
    BlurayBlockedError,
    BlurayDetails,
    BlurayResolver,
    StaticBlurayResolver,
    fetch_bluray_details,
    normalize_bluray_audio,
    parse_hdr,
    search_bluray,
)


_DISC_HTML = """
<span class="subheading">Video</span><br> Codec: HEVC<br>
 HDR: Dolby Vision, HDR10<br>Aspect ratio: 2.39:1<br>
 <span class="subheading">Audio</span><br>
 <div id="longaudio" style="display: none"> English: Dolby Atmos<br>
English: Dolby TrueHD 7.1 (48kHz, 24-bit)<br>French: Dolby Digital 5.1 (640 kbps)<br>
&nbsp;(<a href="#">less</a>) </div>
 <span class="subheading">Subtitles</span><br>
<a title="Movie 4K Blu-ray Release Date April 21, 2026" href="x">d</a>
"""

_SEARCH_HTML = """
<a class="hoverlink" href="https://www.blu-ray.com/movies/The-Northman-Blu-ray/300/" title="The Northman (2022)">x</a>
<a class="hoverlink" href="https://www.blu-ray.com/movies/The-Northman-4K-Blu-ray/301/" title="The Northman 4K (2022)">x</a>
<a class="hoverlink" href="https://www.blu-ray.com/movies/Unrelated-Film-4K-Blu-ray/999/" title="Unrelated Film 4K (2010)">x</a>
"""


def test_parse_hdr_keeps_known_formats_in_order():
    assert parse_hdr("Dolby Vision, HDR10") == ["Dolby Vision", "HDR10"]
    assert parse_hdr("HDR10+, HDR10") == ["HDR10+", "HDR10"]
    assert parse_hdr("") == []
    assert parse_hdr("SDR, junk") == []


def test_normalize_audio_strips_freqs_and_abbreviates():
    tracks = [
        ("English", "Dolby Digital 5.1 (640 kbps)"),
        ("French", "DTS-HD Master Audio 5.1 (48kHz, 24-bit)"),
        ("German", "Dolby Digital Plus 7.1"),
    ]
    assert normalize_bluray_audio(tracks) == [
        "DD 5.1",
        "DTS-HD MA 5.1",
        "DD+ 7.1",
    ]


def test_normalize_audio_combines_atmos_and_dtsx_with_core():
    atmos = [
        ("English", "Dolby Atmos"),
        ("English", "Dolby TrueHD 7.1 (48kHz, 24-bit)"),
    ]
    assert normalize_bluray_audio(atmos) == ["Dolby TrueHD/Atmos 7.1"]

    dtsx = [
        ("English", "DTS:X"),
        ("English", "DTS-HD Master Audio 7.1 (48kHz, 24-bit)"),
    ]
    assert normalize_bluray_audio(dtsx) == ["DTS:X 7.1"]


def test_normalize_audio_dedupes_across_languages():
    tracks = [
        ("English", "Dolby Digital 5.1"),
        ("Spanish", "Dolby Digital 5.1"),
    ]
    assert normalize_bluray_audio(tracks) == ["DD 5.1"]


def test_fetch_bluray_details_parses_disc_page():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_DISC_HTML)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    details = fetch_bluray_details(
        client, "https://www.blu-ray.com/movies/Movie-4K-Blu-ray/1/"
    )
    client.close()

    assert isinstance(details, BlurayDetails)
    assert details.hdr_formats == ["Dolby Vision", "HDR10"]
    assert details.audio_formats == ["Dolby TrueHD/Atmos 7.1", "DD 5.1"]
    assert details.audio_languages == ["English", "French"]
    assert details.bluray_release_date == "2026-04-21"
    assert details.url.endswith("/1/")


def test_search_bluray_returns_high_confidence_4k_match():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_SEARCH_HTML)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    url = search_bluray(client, "The Northman", "2022")
    miss = search_bluray(client, "Some Movie Not Listed", "2019")
    wrong_year = search_bluray(client, "The Northman", "1999")
    client.close()

    assert url == "https://www.blu-ray.com/movies/The-Northman-4K-Blu-ray/301/"
    assert miss is None
    assert wrong_year is None


def test_search_bluray_accepts_direct_4k_redirect():
    direct_url = "https://www.blu-ray.com/movies/Send-Help-4K-Blu-ray/405659/"

    def handler(request: httpx.Request) -> httpx.Response:
        if "/search/" in request.url.path:
            return httpx.Response(302, headers={"Location": direct_url})
        return httpx.Response(200, text=_DISC_HTML, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        assert search_bluray(client, "Send Help", "2025") == direct_url
    finally:
        client.close()


def test_search_bluray_retries_with_4k_when_bare_title_redirects_wrong():
    wrong_redirect = "https://www.blu-ray.com/movies/Galaxy-Quest-Blu-ray/247576/"
    search_html = """
    <a class="hoverlink"
       href="https://www.blu-ray.com/movies/Never-Give-Up-4K-Blu-ray/367655/"
       title="Never Give Up 4K (1978)">x</a>
    """
    requested_keywords = []

    def handler(request: httpx.Request) -> httpx.Response:
        keyword = str(request.url.params.get("quicksearch_keyword", ""))
        if "/search/" not in request.url.path:
            return httpx.Response(200, text="<title>Galaxy Quest Blu-ray</title>")
        requested_keywords.append(keyword)
        if keyword == "Never Give Up":
            return httpx.Response(302, headers={"Location": wrong_redirect})
        return httpx.Response(200, text=search_html)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        assert search_bluray(client, "Never Give Up", "1978") == (
            "https://www.blu-ray.com/movies/Never-Give-Up-4K-Blu-ray/367655/"
        )
    finally:
        client.close()
    assert requested_keywords == ["Never Give Up", "Never Give Up 4K"]


def test_search_bluray_tries_known_title_aliases():
    alias_url = (
        "https://www.blu-ray.com/movies/"
        "The-Fantastic-Four-First-Steps-4K-Blu-ray/397023/"
    )
    requested_keywords = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "/search/" not in request.url.path:
            return httpx.Response(200, text="")
        keyword = str(request.url.params.get("quicksearch_keyword", ""))
        requested_keywords.append(keyword)
        if keyword == "The Fantastic Four: First Steps":
            return httpx.Response(302, headers={"Location": alias_url})
        return httpx.Response(200, text="")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        assert search_bluray(client, "The Fantastic 4: First Steps", "2025") == (
            alias_url
        )
    finally:
        client.close()
    assert requested_keywords == [
        "The Fantastic 4: First Steps",
        "The Fantastic 4: First Steps 4K",
        "The Fantastic Four: First Steps",
    ]


def test_static_bluray_resolver_returns_details():
    details = BlurayDetails(url="u", hdr_formats=["Dolby Vision"])
    resolver = StaticBlurayResolver({("The Northman", "2022"): details})
    assert resolver.resolve("The Northman", "2022") is details
    assert resolver.resolve("Missing", "2000") is None


def test_bluray_resolver_caches_lookups(tmp_path: Path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if "/search/" in request.url.path:
            return httpx.Response(200, text=_SEARCH_HTML)
        return httpx.Response(200, text=_DISC_HTML)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cache = tmp_path / "bluray.json"
    with BlurayResolver(client=client, cache_path=cache) as resolver:
        first = resolver.resolve("The Northman", "2022")
        assert first is not None
        assert first.hdr_formats == ["Dolby Vision", "HDR10"]
    calls_after_first = len(calls)
    # A fresh resolver reading the same cache makes no new requests.
    with BlurayResolver(client=client, cache_path=cache) as resolver:
        cached = resolver.resolve("The Northman", "2022")
        assert cached is not None
        assert cached.audio_formats == first.audio_formats
    assert len(calls) == calls_after_first


def test_bluray_resolver_uses_browser_user_agent():
    resolver = BlurayResolver()
    try:
        assert resolver.client.headers["user-agent"].startswith("Mozilla/5.0")
    finally:
        resolver.close()


def test_search_bluray_raises_on_block_page():
    # Under rate limiting blu-ray.com answers HTTP 200 with a bare
    # "error42"-style body; parsing it as a results page would record a
    # permanent miss for a title the site does carry.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="error42")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(BlurayBlockedError):
        search_bluray(client, "The Drama", "2026")
    client.close()


def test_fetch_bluray_details_raises_on_block_page():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="error9")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(BlurayBlockedError):
        fetch_bluray_details(client, "https://www.blu-ray.com/movies/X-4K-Blu-ray/1/")
    client.close()


def test_bluray_resolver_does_not_cache_blocked_lookups(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="error42")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cache = tmp_path / "bluray.json"
    with BlurayResolver(client=client, cache_path=cache) as resolver:
        with pytest.raises(BlurayBlockedError):
            resolver.resolve("The Drama", "2026")
        assert "The Drama\x002026" not in resolver.cache
    assert not cache.exists()


def test_bluray_resolver_caches_miss_with_timestamp_and_serves_it(tmp_path: Path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text="<html><body>No results</body></html>")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cache = tmp_path / "bluray.json"
    with BlurayResolver(client=client, cache_path=cache) as resolver:
        assert resolver.resolve("Ghost Title", "2001") is None
        record = resolver.cache["Ghost Title\x002001"]
        assert "miss_cached_at" in record
    calls_after_first = len(calls)
    # A fresh miss is served from the cache without touching the network.
    with BlurayResolver(client=client, cache_path=cache) as resolver:
        assert resolver.resolve("Ghost Title", "2001") is None
    assert len(calls) == calls_after_first


def test_bluray_resolver_retries_legacy_and_expired_misses(tmp_path: Path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "/search/" in request.url.path:
            return httpx.Response(200, text=_SEARCH_HTML)
        return httpx.Response(200, text=_DISC_HTML)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cache = tmp_path / "bluray.json"
    stale = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    cache.write_text(
        json.dumps(
            {
                # Legacy caches stored misses as bare nulls (no timestamp).
                "The Northman\x002022": None,
                "Old Miss\x002020": {"miss_cached_at": stale},
                "Broken Miss\x002019": {"miss_cached_at": "not-a-date"},
            }
        ),
        encoding="utf-8",
    )
    with BlurayResolver(client=client, cache_path=cache) as resolver:
        legacy = resolver.resolve("The Northman", "2022")
        assert legacy is not None and legacy.hdr_formats == ["Dolby Vision", "HDR10"]
        # The stale and malformed misses are re-queried (the mock search page
        # only carries The Northman, so they re-miss) and rewritten with a
        # fresh timestamp instead of being served from the cache.
        for title, year in (("Old Miss", "2020"), ("Broken Miss", "2019")):
            calls_before = len(calls)
            assert resolver.resolve(title, year) is None
            assert len(calls) > calls_before
            assert resolver.cache[f"{title}\x00{year}"]["miss_cached_at"] != stale
