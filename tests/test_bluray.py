import httpx

from bluray import (
    BlurayDetails,
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
