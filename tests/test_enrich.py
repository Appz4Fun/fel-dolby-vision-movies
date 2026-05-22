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
