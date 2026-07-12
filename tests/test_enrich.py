from pathlib import Path

import httpx

import enrich
from bluray import BlurayDetails, StaticBlurayResolver
from enrich import StaticTmdbResolver, enrich_releases, release_url_for
from models import FelEvidence, FelRelease


def make(title, year):
    return FelRelease(
        movie_title=title,
        release_date=year,
        fel_evidence=FelEvidence(
            source_url=f"https://src.test/{title}",
            quote=f"{title} FEL",
            evidence_type="fel-list",
        ),
    )


def test_release_url_for_prefers_tmdb_then_imdb():
    assert release_url_for("550", "tt0137523") == "https://www.themoviedb.org/movie/550"
    assert release_url_for("", "tt0137523") == "https://www.imdb.com/title/tt0137523/"
    assert release_url_for("", "") == ""


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/3/movie/550":
        return httpx.Response(
            200,
            json={
                "poster_path": "/poster550.jpg",
                "release_date": "1999-10-15",
                "production_companies": [{"name": "Fox 2000 Pictures"}],
            },
        )
    if request.url.path.startswith("/t/p/w185"):
        return httpx.Response(200, content=b"\xff\xd8\xff-jpeg-bytes")
    return httpx.Response(404)


def test_enrich_releases_sets_ids_poster_and_release_url(tmp_path: Path):
    resolver = StaticTmdbResolver(
        {
            ("Fight Club", "1999"): {
                "tmdb_id": "550",
                "title": "Fight Club",
                "year": "1999",
                "imdb_id": "tt0137523",
            }
        }
    )
    releases = [make("Fight Club", "1999"), make("Unknown Movie", "2099")]
    client = httpx.Client(transport=httpx.MockTransport(_handler))

    summary = enrich_releases(
        releases, resolver, client=client, api_key="x", poster_dir=tmp_path
    )
    client.close()

    fight_club = releases[0]
    assert fight_club.tmdb_id == "550"
    assert fight_club.imdb_id == "tt0137523"
    assert fight_club.release_url == "https://www.themoviedb.org/movie/550"
    assert fight_club.studio == "Fox 2000 Pictures"
    assert fight_club.release_date == "1999-10-15"
    assert fight_club.poster_path == str(tmp_path / "550.jpg")
    assert (tmp_path / "550.jpg").read_bytes() == b"\xff\xd8\xff-jpeg-bytes"

    assert releases[1].tmdb_id == ""
    assert summary.resolved == 1
    assert summary.unresolved == 1
    assert summary.posters_downloaded == 1


def test_enrich_releases_tolerates_poster_download_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(enrich.time, "sleep", lambda *_: None)

    resolver = StaticTmdbResolver(
        {
            ("Fight Club", "1999"): {
                "tmdb_id": "550",
                "title": "Fight Club",
                "year": "1999",
                "imdb_id": "tt0137523",
            }
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3/movie/550":
            return httpx.Response(
                200,
                json={
                    "poster_path": "/p.jpg",
                    "release_date": "1999-10-15",
                    "production_companies": [{"name": "Fox"}],
                },
            )
        return httpx.Response(502)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    releases = [make("Fight Club", "1999")]
    summary = enrich_releases(
        releases, resolver, client=client, api_key="x", poster_dir=tmp_path
    )
    client.close()

    assert summary.resolved == 1
    assert summary.failed == 1
    assert summary.posters_downloaded == 0
    assert releases[0].tmdb_id == "550"
    assert releases[0].studio == "Fox"
    assert releases[0].release_date == "1999-10-15"
    assert releases[0].poster_path == ""


def test_enrich_releases_treats_resolver_http_errors_as_unresolved(tmp_path, capsys):
    class FailingResolver:
        def resolve(self, title: str, year: str):
            raise httpx.HTTPStatusError(
                "server error",
                request=httpx.Request("GET", "https://api.example.test/search"),
                response=httpx.Response(500),
            )

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    releases = [make("Rain Man", "1988")]

    summary = enrich_releases(
        releases, FailingResolver(), client=client, api_key="x", poster_dir=tmp_path
    )
    client.close()

    assert summary.resolved == 0
    assert summary.unresolved == 1
    assert releases[0].tmdb_id == ""
    assert "enrich: resolve failed for 'Rain Man'" in capsys.readouterr().out


def test_enrich_releases_applies_bluray_details(tmp_path):
    resolver = StaticTmdbResolver(
        {
            ("Fight Club", "1999"): {
                "tmdb_id": "550",
                "title": "Fight Club",
                "year": "1999",
                "imdb_id": "tt0137523",
            }
        }
    )
    bluray = StaticBlurayResolver(
        {
            ("Fight Club", "1999"): BlurayDetails(
                url="https://www.blu-ray.com/movies/Fight-Club-4K-Blu-ray/1/",
                bluray_release_date="2025-09-16",
                audio_formats=["Dolby TrueHD/Atmos 7.1", "DD 5.1"],
                audio_languages=["English", "French"],
                hdr_formats=["Dolby Vision", "HDR10"],
            )
        }
    )

    def handler(request):
        if request.url.path == "/3/movie/550":
            return httpx.Response(
                200,
                json={
                    "poster_path": "/p.jpg",
                    "release_date": "1999-10-15",
                    "production_companies": [{"name": "Fox"}],
                },
            )
        return httpx.Response(200, content=b"jpeg")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    releases = [make("Fight Club", "1999")]
    summary = enrich_releases(
        releases,
        resolver,
        client=client,
        api_key="x",
        poster_dir=tmp_path,
        bluray_resolver=bluray,
    )
    client.close()

    release = releases[0]
    assert release.bluray_url.endswith("/1/")
    assert release.bluray_release_date == "2025-09-16"
    assert release.hdr_formats == ["Dolby Vision", "HDR10"]
    assert release.audio_formats == ["Dolby TrueHD/Atmos 7.1", "DD 5.1"]
    assert release.audio_languages == ["English", "French"]
    assert release.english_audio == "Yes"
    assert summary.bluray_matched == 1
    assert summary.bluray_failed == 0


def test_enrich_releases_uses_known_lookup_alias_for_tmdb_and_bluray(tmp_path):
    resolver = StaticTmdbResolver(
        {
            ("Ip Man", "2008"): {
                "tmdb_id": "14756",
                "title": "Ip Man",
                "year": "2008",
                "imdb_id": "tt1220719",
            }
        }
    )
    bluray = StaticBlurayResolver(
        {
            ("Ip Man", "2008"): BlurayDetails(
                url="https://www.blu-ray.com/movies/Ip-Man-4K-Blu-ray/280168/",
                bluray_release_date="2022-11-22",
                audio_formats=["Dolby TrueHD/Atmos 7.1"],
                audio_languages=["Cantonese", "English"],
                hdr_formats=["Dolby Vision", "HDR10"],
            )
        }
    )

    def handler(request):
        if request.url.path == "/3/movie/14756":
            return httpx.Response(
                200,
                json={
                    "poster_path": "/ip-man.jpg",
                    "release_date": "2008-12-12",
                    "production_companies": [{"name": "Mandarin Films"}],
                },
            )
        return httpx.Response(200, content=b"jpeg")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    releases = [make("Yip Man", "2008")]
    summary = enrich_releases(
        releases,
        resolver,
        client=client,
        api_key="x",
        poster_dir=tmp_path,
        bluray_resolver=bluray,
    )
    client.close()

    release = releases[0]
    assert release.movie_title == "Ip Man"
    assert release.tmdb_id == "14756"
    assert release.imdb_id == "tt1220719"
    assert release.release_date == "2008-12-12"
    assert release.bluray_url.endswith("/280168/")
    assert release.audio_languages == ["Cantonese", "English"]
    assert summary.resolved == 1
    assert summary.bluray_matched == 1


def test_enrich_releases_uses_alias_year_when_source_year_is_not_tmdb_year(
    tmp_path,
):
    resolver = StaticTmdbResolver(
        {
            ("The Witch", "2016"): {
                "tmdb_id": "310131",
                "title": "The Witch",
                "year": "2016",
                "imdb_id": "tt4263482",
            }
        }
    )

    def handler(request):
        if request.url.path == "/3/movie/310131":
            return httpx.Response(
                200,
                json={
                    "poster_path": "",
                    "release_date": "2016-02-19",
                    "production_companies": [{"name": "A24"}],
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    releases = [make("The VVitch", "2015")]
    summary = enrich_releases(
        releases,
        resolver,
        client=client,
        api_key="x",
        poster_dir=tmp_path,
    )
    client.close()

    assert releases[0].movie_title == "The Witch"
    assert releases[0].tmdb_id == "310131"
    assert releases[0].release_date == "2016-02-19"
    assert summary.resolved == 1


def test_enrich_releases_uses_alias_year_for_1917_home_video_year_mislabel(
    tmp_path,
):
    # FEL list sources sometimes label "1917" with its home-video/rerelease
    # year (2020, matching the 4K Blu-ray release the FEL disc is drawn from)
    # rather than its TMDB theatrical year (2019). A plain title+year search
    # for ("1917", "2020") misses the real film on TMDB (whose primary release
    # year is 2019) and can latch onto an unrelated same-titled work instead
    # (e.g. TMDB id 766967, "2020: A 1917 Parody", an 18-minute fan short with
    # a matching 2020 release year and near-zero title overlap otherwise). The
    # alias pins the search to the correct TMDB year so the real film resolves.
    resolver = StaticTmdbResolver(
        {
            ("1917", "2019"): {
                "tmdb_id": "530915",
                "title": "1917",
                "year": "2019",
                "imdb_id": "tt8579674",
            }
        }
    )

    def handler(request):
        if request.url.path == "/3/movie/530915":
            return httpx.Response(
                200,
                json={
                    "poster_path": "",
                    "release_date": "2019-12-25",
                    "production_companies": [{"name": "DreamWorks Pictures"}],
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    releases = [make("1917", "2020")]
    summary = enrich_releases(
        releases,
        resolver,
        client=client,
        api_key="x",
        poster_dir=tmp_path,
    )
    client.close()

    assert releases[0].movie_title == "1917"
    assert releases[0].tmdb_id == "530915"
    assert releases[0].imdb_id == "tt8579674"
    assert releases[0].release_date == "2019-12-25"
    assert summary.resolved == 1


def test_enrich_releases_counts_bluray_failures(tmp_path):
    class FailingBlurayResolver:
        def resolve(self, title: str, year: str):
            raise httpx.ConnectError("blu-ray unavailable")

    resolver = StaticTmdbResolver(
        {
            ("Fight Club", "1999"): {
                "tmdb_id": "550",
                "title": "Fight Club",
                "year": "1999",
                "imdb_id": "tt0137523",
            }
        }
    )
    client = httpx.Client(transport=httpx.MockTransport(_handler))
    releases = [make("Fight Club", "1999")]

    summary = enrich_releases(
        releases,
        resolver,
        client=client,
        api_key="x",
        poster_dir=tmp_path,
        bluray_resolver=FailingBlurayResolver(),
    )
    client.close()

    assert summary.resolved == 1
    assert summary.bluray_matched == 0
    assert summary.bluray_failed == 1
    assert releases[0].bluray_url == ""
