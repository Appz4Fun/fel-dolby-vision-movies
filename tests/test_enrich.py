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
    # The media type must be persisted on the row itself: merge identity
    # keys, reconciliation, and the Trakt sync all read the field, never
    # the release URL.
    assert row.media_type == "tv"
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


def _static_details_client(tmdb_id, release_date):
    def handler(request):
        if request.url.path == f"/3/movie/{tmdb_id}":
            return httpx.Response(
                200,
                json={"poster_path": "", "release_date": release_date},
            )
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_enrich_retitles_row_whose_spelling_matches_neither_tmdb_title(tmp_path):
    # TMDB search can score a romanized query directly (no alternative-title
    # rescue runs), leaving the row titled by a third spelling that matches
    # neither the canonical nor the original TMDB title -- here the non-latin
    # original strips to the garbage key "diary", so the recorded
    # canonical/original pair cannot supply a reconciliation edge either, and
    # a later English-titled candidate would publish a duplicate row (#55's
    # "Kaseki no kouya" beside "Fossilized Wilderness"). Adopt the canonical
    # title so the spellings connect directly.
    resolver = StaticTmdbResolver(
        {
            ("Umimachi Diary", "2015"): {
                "tmdb_id": "315846",
                "title": "Our Little Sister",
                "year": "2015",
                "imdb_id": "tt3756788",
                "original_title": "海街diary",
            }
        }
    )
    client = _static_details_client("315846", "2015-06-13")
    releases = [make("Umimachi Diary", "2015")]
    enrich_releases(releases, resolver, client=client, api_key="x", poster_dir=tmp_path)
    client.close()

    assert releases[0].movie_title == "Our Little Sister"
    assert releases[0].tmdb_id == "315846"


def test_enrich_keeps_row_titled_by_the_original_spelling(tmp_path):
    # A row titled by the film's (latin) original title connects through the
    # recorded canonical/original pair, which is reconciliation's edge
    # between the two spellings, so native-titled rows keep their source
    # spelling (the catalog convention).
    resolver = StaticTmdbResolver(
        {
            ("O Agente Secreto", "2025"): {
                "tmdb_id": "1220564",
                "title": "The Secret Agent",
                "year": "2025",
                "imdb_id": "tt31710303",
                "original_title": "O Agente Secreto",
            }
        }
    )
    client = _static_details_client("1220564", "2025-07-23")
    releases = [make("O Agente Secreto", "2025")]
    enrich_releases(releases, resolver, client=client, api_key="x", poster_dir=tmp_path)
    client.close()

    assert releases[0].movie_title == "O Agente Secreto"
    assert releases[0].additional_characteristics["tmdb_title"] == "The Secret Agent"


def test_enrich_keeps_edition_title_when_original_title_is_non_latin(tmp_path):
    # Edition-descriptor rows keep their source spelling for the same reason
    # the alternative-title rescue skips them: renaming would collapse a
    # distinct physical edition into the base film.
    resolver = StaticTmdbResolver(
        {
            ("Umimachi Diary Steelbook", "2015"): {
                "tmdb_id": "315846",
                "title": "Our Little Sister",
                "year": "2015",
                "imdb_id": "tt3756788",
                "original_title": "海街diary",
            }
        }
    )
    client = _static_details_client("315846", "2015-06-13")
    releases = [make("Umimachi Diary Steelbook", "2015")]
    enrich_releases(releases, resolver, client=client, api_key="x", poster_dir=tmp_path)
    client.close()

    assert releases[0].movie_title == "Umimachi Diary Steelbook"


def test_enrich_keeps_source_title_when_canonical_title_is_also_non_latin(tmp_path):
    # When TMDB's canonical title strips to an empty key too, retitling
    # cannot create a connectable spelling; the source spelling stays.
    resolver = StaticTmdbResolver(
        {
            ("Mo tong jiang shi", "2019"): {
                "tmdb_id": "615453",
                "title": "哪吒之魔童降世",
                "year": "2019",
                "imdb_id": "tt10627720",
                "original_title": "哪吒之魔童降世",
            }
        }
    )
    client = _static_details_client("615453", "2019-07-26")
    releases = [make("Mo tong jiang shi", "2019")]
    enrich_releases(releases, resolver, client=client, api_key="x", poster_dir=tmp_path)
    client.close()

    assert releases[0].movie_title == "Mo tong jiang shi"


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
        # Festival-year mislabels: reddit's [2022] Talk to Me is the A24 film
        # TMDB dates 2023, and reddit's [2005] Brick is the Rian Johnson film
        # TMDB dates 2006; unpinned searches match low-vote same-titled films
        # that happen to carry the labeled year.
        ("Talk to Me", "2022"): ("Talk to Me", "2023"),
        ("Brick", "2005"): ("Brick", "2006"),
        # FEL.txt spells the same Brick disc "Brick Vision (2005)", which an
        # unpinned search resolves to a zero-vote short of that exact name.
        ("Brick Vision", "2005"): ("Brick", "2006"),
        # The blu-ray forum list spells Eggers' film "The Witch (2015)"
        # (festival year); only the "The VVitch" spelling was pinned, so the
        # 2015 search matched an unrelated Russian film instead.
        ("The Witch", "2015"): ("The Witch", "2016"),
        # Reddit's leading title is the "Monster Problems" working title; an
        # unpinned search matches the unrelated 2015 short of that name
        # instead of the film's canonical title.
        ("Monster Problems", "2020"): ("Love and Monsters", "2020"),
        # The blu-ray forum labels Lustig's Vigilante by its 1982 production
        # year while TMDB dates it 1983; the 1982 search once matched the
        # Italian comedy "Vigili e vigilesse" (TMDB 306529) instead.
        ("Vigilante", "1982"): ("Vigilante", "1983"),
        # Letterboxd's "The Grey (2011)" labels the film by its festival year
        # while TMDB dates it 2012; the 2011 search matched the five-vote
        # "Documenting the Grey Man" (TMDB 120881) instead.
        ("The Grey", "2011"): ("The Grey", "2012"),
        # The google sheet titles Divergent in Spanish; an unpinned search
        # matches an unrelated same-titled documentary (TMDB 1190479).
        ("Divergente", "2014"): ("Divergent", "2014"),
        # FEL lists label Schwentke's film by its 2017 TIFF festival year
        # while TMDB dates it 2018; unpinned, both the native and English
        # spellings resolved to the 1971 "Der Hauptmann" (TMDB 508015)
        # instead of the real film (TMDB 475094).
        ("Der Hauptmann", "2017"): ("Der Hauptmann", "2018"),
        ("The Captain", "2017"): ("Der Hauptmann", "2018"),
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
