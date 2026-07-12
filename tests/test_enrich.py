from pathlib import Path

import httpx
import pytest

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


def test_release_url_for_emits_tv_page_for_tv_matches():
    assert (
        release_url_for("1399", "tt0944947", "tv")
        == "https://www.themoviedb.org/tv/1399"
    )
    assert (
        release_url_for("550", "tt0137523", "movie")
        == "https://www.themoviedb.org/movie/550"
    )
    assert (
        release_url_for("", "tt0944947", "tv")
        == "https://www.imdb.com/title/tt0944947/"
    )


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


def test_enrich_releases_uses_tv_endpoints_for_tv_matches(tmp_path: Path):
    """A TV-season row must be enriched entirely through TV endpoints: a
    /tv/ release URL, details from /3/tv/{id} (never /3/movie/{id} -- TMDB
    movie and TV ids are separate namespaces, so the movie endpoint could
    return a real but unrelated film), a network as the studio, and a
    poster filename that cannot collide with a same-id movie poster."""
    resolver = StaticTmdbResolver(
        {
            ("Ahsoka: The Complete First Season", "2023"): {
                "tmdb_id": "114461",
                "title": "Ahsoka",
                "year": "2023",
                "imdb_id": "tt13622776",
                "media_type": "tv",
            }
        }
    )
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/3/tv/114461":
            return httpx.Response(
                200,
                json={
                    "poster_path": "/ahsoka.jpg",
                    "first_air_date": "2023-08-22",
                    "networks": [{"name": "Disney+"}],
                    "production_companies": [{"name": "Lucasfilm Ltd."}],
                },
            )
        if request.url.path.startswith("/t/p/w185"):
            return httpx.Response(200, content=b"\xff\xd8\xff-tv-poster")
        return httpx.Response(404)

    releases = [make("Ahsoka: The Complete First Season", "2023")]
    client = httpx.Client(transport=httpx.MockTransport(handler))

    summary = enrich_releases(
        releases, resolver, client=client, api_key="x", poster_dir=tmp_path
    )
    client.close()

    row = releases[0]
    # The row keeps its per-season title; retitling it to the series name
    # would let reconciliation collapse distinct season discs into one row.
    assert row.movie_title == "Ahsoka: The Complete First Season"
    assert row.tmdb_id == "114461"
    assert row.imdb_id == "tt13622776"
    assert row.release_url == "https://www.themoviedb.org/tv/114461"
    assert row.studio == "Disney+"
    assert row.release_date == "2023-08-22"
    assert row.poster_path == str(tmp_path / "tv-114461.jpg")
    assert (tmp_path / "tv-114461.jpg").read_bytes() == b"\xff\xd8\xff-tv-poster"
    assert summary.resolved == 1
    assert not any(path.startswith("/3/movie/") for path in paths)


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


def test_enrich_retitles_row_resolved_via_alternative_title(tmp_path):
    # A row titled by a TMDB alternative title (romanized native name) that
    # the resolver rescued must adopt the canonical TMDB title, otherwise
    # reconciliation has no edge from the source spelling to the canonical
    # catalog row and can append a same-TMDB duplicate.
    resolver = StaticTmdbResolver(
        {
            ("Katayoku no Fake Title", "2021"): {
                "tmdb_id": "776503",
                "title": "Belle",
                "year": "2021",
                "imdb_id": "tt13651628",
                "matched_alternative_title": "Katayoku no Fake Title",
            }
        }
    )

    def handler(request):
        if request.url.path == "/3/movie/776503":
            return httpx.Response(
                200,
                json={"poster_path": "", "release_date": "2021-07-16"},
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    releases = [make("Katayoku no Fake Title", "2021")]
    enrich_releases(releases, resolver, client=client, api_key="x", poster_dir=tmp_path)
    client.close()

    assert releases[0].movie_title == "Belle"
    assert releases[0].tmdb_id == "776503"


def test_enrich_keeps_edition_title_despite_alternative_title_match(tmp_path):
    # TMDB sometimes lists edition names among alternative titles; adopting
    # the base film's canonical title would collapse a distinct physical
    # edition row into the base film, so edition-descriptor titles keep
    # their source spelling.
    resolver = StaticTmdbResolver(
        {
            ("Avatar Special Edition", "2009"): {
                "tmdb_id": "19995",
                "title": "Avatar",
                "year": "2009",
                "imdb_id": "tt0499549",
                "matched_alternative_title": "Avatar Special Edition",
            }
        }
    )

    def handler(request):
        if request.url.path == "/3/movie/19995":
            return httpx.Response(
                200,
                json={"poster_path": "", "release_date": "2009-12-16"},
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    releases = [make("Avatar Special Edition", "2009")]
    enrich_releases(releases, resolver, client=client, api_key="x", poster_dir=tmp_path)
    client.close()

    assert releases[0].movie_title == "Avatar Special Edition"
    assert releases[0].tmdb_id == "19995"


def test_lookup_aliases_cover_known_fel_list_romanizations():
    # Titles the reddit FEL list spells by romanized/native/sequel-alias
    # names that TMDB's title+original_title scoring cannot resolve; each is
    # pinned to the canonical English title so the row enriches to the same
    # id as (and merges with) the canonical catalog entry.
    cases = {
        ("Train to Busan 2", "2020"): ("Peninsula", "2020"),
        ("Ryu to sobakasu no hime", "2021"): ("Belle", "2021"),
        ("Long ma jing shen", "2023"): ("Ride On", "2023"),
        ("Rio 70", "1969"): ("The Girl from Rio", "1969"),
        # Reddit's "Obsession [2025]" labels the film by its festival year;
        # TMDB dates it 2026 (wide release), and an unpinned 2025 search
        # matches an unrelated same-titled French film instead.
        ("Obsession", "2025"): ("Obsession", "2026"),
    }
    for (source_title, source_year), (title, year) in cases.items():
        candidate = enrich._lookup_candidates(source_title, source_year)[0]
        assert (candidate.title, candidate.year) == (title, year)


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


def test_enrich_releases_records_tmdb_title_pair_for_foreign_language_film(tmp_path):
    resolver = StaticTmdbResolver(
        {
            ("Les rivières pourpres", "2000"): {
                "tmdb_id": "60670",
                "title": "The Crimson Rivers",
                "original_title": "Les rivières pourpres",
                "year": "2000",
                "imdb_id": "tt0228786",
            }
        }
    )

    def handler(request):
        if request.url.path == "/3/movie/60670":
            return httpx.Response(
                200,
                json={
                    "poster_path": "",
                    "release_date": "2000-09-27",
                    "production_companies": [{"name": "Gaumont"}],
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    releases = [make("Les rivières pourpres", "2000")]

    summary = enrich_releases(
        releases, resolver, client=client, api_key="x", poster_dir=tmp_path
    )
    client.close()

    assert summary.resolved == 1
    assert releases[0].movie_title == "Les rivières pourpres"
    assert releases[0].additional_characteristics["tmdb_title"] == "The Crimson Rivers"
    assert (
        releases[0].additional_characteristics["tmdb_original_title"]
        == "Les rivières pourpres"
    )


@pytest.mark.parametrize(
    "record",
    [
        {
            "tmdb_id": "550",
            "title": "Fight Club",
            "original_title": "Fight Club",
            "year": "1999",
            "imdb_id": "tt0137523",
        },
        {
            "tmdb_id": "550",
            "title": "Fight Club",
            "year": "1999",
            "imdb_id": "tt0137523",
        },
    ],
    ids=["same-original-title", "no-original-title"],
)
def test_enrich_releases_skips_tmdb_title_pair_without_distinct_original(
    tmp_path, record
):
    resolver = StaticTmdbResolver({("Fight Club", "1999"): record})
    client = httpx.Client(transport=httpx.MockTransport(_handler))
    releases = [make("Fight Club", "1999")]

    summary = enrich_releases(
        releases, resolver, client=client, api_key="x", poster_dir=tmp_path
    )
    client.close()

    assert summary.resolved == 1
    assert "tmdb_title" not in releases[0].additional_characteristics
    assert "tmdb_original_title" not in releases[0].additional_characteristics
