"""Regression tests for the PR #29 review-comment fixes.

Each test pins one root-cause fix raised by the automated reviewer so the daily
FEL refresh cannot regenerate the flagged data again.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from artifacts import _sort_key, write_artifacts
from bluray import StaticBlurayResolver, fetch_bluray_details
from enrich import StaticTmdbResolver, enrich_releases
from merge import dedupe_tmdb_releases
from models import FelEvidence, FelRelease
from reddit_source import parse_reddit_releases


def _release(title, year, **kwargs):
    quote = kwargs.pop("quote", f"{title}")
    return FelRelease(
        movie_title=title,
        release_date=year,
        fel_evidence=FelEvidence(
            source_url=kwargs.pop("source_url", f"https://src.test/{title}"),
            quote=quote,
            evidence_type=kwargs.pop("evidence_type", "reddit-list"),
        ),
        **kwargs,
    )


# --- Bucket A: reddit question/discussion prose is not a release -------------


def test_reddit_parser_drops_question_prose_but_keeps_real_titles():
    html = (
        '<div class="usertext-body"><div class="md">'
        "<p>Are you sure about Joker: Folie a Deux [2024]</p>"
        "<p>Does anyone know if Dune [2021]</p>"
        "<p>Are We There Yet [2005]</p>"
        "<p>Is Paris Burning? [1966]</p>"
        "</div></div>"
    )
    titles = [r.movie_title for r in parse_reddit_releases(html, "https://reddit.test")]
    assert "Are We There Yet" in titles
    assert "Is Paris Burning?" in titles
    assert not any("Are you sure" in t for t in titles)
    assert not any("anyone" in t.lower() for t in titles)


# --- Bucket C: same-tmdb AKA duplicates collapse; editions stay split --------


def _enriched(title, tmdb, bluray, imdb=""):
    return _release(
        title,
        "1977",
        tmdb_id=tmdb,
        imdb_id=imdb,
        bluray_url=bluray,
    )


def test_dedupe_tmdb_collapses_aka_titles_with_distinct_bluray_urls():
    rows = [
        _enriched(
            "Last Cannibal World",
            "30876",
            "https://www.blu-ray.com/movies/Last-Cannibal-World/385926/",
            "tt0078437",
        ),
        _enriched(
            "Ultimo mondo cannibale",
            "30876",
            "https://www.blu-ray.com/movies/Ultimo-mondo-cannibale/356115/",
            "tt0078437",
        ),
    ]
    deduped = dedupe_tmdb_releases(rows)
    assert len(deduped) == 1
    assert deduped[0].movie_title == "Last Cannibal World"
    assert deduped[0].imdb_id == "tt0078437"


def test_dedupe_tmdb_keeps_distinct_editions_and_seasons():
    editions = dedupe_tmdb_releases(
        [
            _enriched("Avatar", "19995", "https://www.blu-ray.com/movies/Avatar/1/"),
            _enriched(
                "Avatar: Extended Collector's Edition",
                "19995",
                "https://www.blu-ray.com/movies/Avatar-Extended/2/",
            ),
        ]
    )
    assert len(editions) == 2

    seasons = dedupe_tmdb_releases(
        [
            _enriched(
                "Game of Thrones: The Complete First Season",
                "1399",
                "https://www.blu-ray.com/movies/GoT-S1/1/",
            ),
            _enriched(
                "Game of Thrones: The Complete Second Season",
                "1399",
                "https://www.blu-ray.com/movies/GoT-S2/2/",
            ),
        ]
    )
    assert len(seasons) == 2


# --- Bucket F: ai-extracted evidence is always published as codex-ai ---------


def test_publish_relabels_ai_extracted_rows_as_codex_ai(tmp_path: Path):
    # A stale row whose evidence is ai-extracted but whose label drifted to
    # "FEL.txt" (via an earlier weak-evidence merge) must publish as codex-ai.
    stale = _release(
        "Past Lives",
        "2023-06-23",
        evidence_type="ai-extracted",
        source_label="FEL.txt",
    )
    deterministic = _release(
        "The Northman",
        "2022-04-22",
        evidence_type="fel-list",
        source_label="FEL.txt",
    )
    written = write_artifacts([stale, deterministic], output_dir=tmp_path)

    by_title = {r.movie_title: r for r in written}
    assert by_title["Past Lives"].source_label == "codex-ai"
    # Non-AI rows keep their own label.
    assert by_title["The Northman"].source_label == "FEL.txt"


# --- Bucket D: bare-year rows sort after full dates in the same year ---------


def test_bare_year_sorts_after_full_dates_same_year():
    rows = [
        _release("BareYear", "2023"),
        _release("FullLate", "2023-12-22"),
        _release("FullEarly", "2023-01-05"),
        _release("BareMonth", "2023-06"),
        _release("Next", "2024-01-01"),
    ]
    order = [r.movie_title for r in sorted(rows, key=_sort_key)]
    assert order == ["Next", "FullLate", "BareMonth", "FullEarly", "BareYear"]


# --- Bucket I: parser notes never leak into audio_languages -----------------


def test_fetch_bluray_details_drops_note_lines_from_audio_languages():
    disc_html = (
        '<span class="subheading">Video</span><br> Codec: HEVC<br>'
        " HDR: Dolby Vision, HDR10<br>"
        ' <div id="longaudio"> English: Dolby TrueHD 7.1 (48kHz, 24-bit)<br>'
        "French: Dolby Digital 5.1 (640 kbps)<br>"
        "Note: Dolby Atmos track is French only<br>"
        "Music: Dolby Digital 2.0<br></div>"
        '<a title="Movie 4K Blu-ray Release Date April 21, 2026" href="x">d</a>'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=disc_html)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    details = fetch_bluray_details(
        client, "https://www.blu-ray.com/movies/Movie-4K-Blu-ray/1/"
    )
    client.close()

    assert details.audio_languages == ["English", "French"]
    assert "Note" not in details.audio_languages
    assert "Music" not in details.audio_languages


# --- Enrichment buckets (C/G AKA fallback, E year guard, J yearless) ---------


def _tmdb_handler(dates):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/3/movie/"):
            tmdb_id = path.rsplit("/", 1)[1]
            return httpx.Response(
                200,
                json={
                    "poster_path": "",
                    "release_date": dates.get(tmdb_id, ""),
                    "production_companies": [],
                },
            )
        return httpx.Response(404)

    return handler


def test_enrich_resolves_foreign_row_via_english_aka(tmp_path: Path):
    release = _release(
        "Ying hung boon sik",
        "1986",
        quote="Ying hung boon sik AKA A Better Tomorrow [1986]",
    )
    resolver = StaticTmdbResolver(
        {
            ("A Better Tomorrow", "1986"): {
                "tmdb_id": "11469",
                "title": "A Better Tomorrow",
                "year": "1986",
                "imdb_id": "tt0092263",
            }
        }
    )
    client = httpx.Client(transport=httpx.MockTransport(_tmdb_handler({})))
    enrich_releases(
        [release], resolver, client=client, api_key="x", poster_dir=tmp_path
    )
    client.close()

    assert release.tmdb_id == "11469"
    assert release.imdb_id == "tt0092263"
    assert release.movie_title == "A Better Tomorrow"


def test_enrich_keeps_evidence_year_when_tmdb_year_differs(tmp_path: Path):
    release = _release(
        "Good Luck, Have Fun, Don't Die",
        "2025",
        quote="Good Luck, Have Fun, Don't Die [2025]",
    )
    resolver = StaticTmdbResolver(
        {
            ("Good Luck, Have Fun, Don't Die", "2025"): {
                "tmdb_id": "1119449",
                "title": "Good Luck, Have Fun, Don't Die",
                "year": "2025",
                "imdb_id": "tt1341338",
            }
        }
    )
    client = httpx.Client(
        transport=httpx.MockTransport(_tmdb_handler({"1119449": "2026-02-13"}))
    )
    enrich_releases(
        [release], resolver, client=client, api_key="x", poster_dir=tmp_path
    )
    client.close()

    assert release.tmdb_id == "1119449"
    assert release.release_date == "2025"


def test_enrich_adopts_tmdb_date_when_alias_changes_the_year(tmp_path: Path):
    release = _release("The VVitch", "2015", quote="The VVitch [2015]")
    resolver = StaticTmdbResolver(
        {
            ("The Witch", "2016"): {
                "tmdb_id": "694",
                "title": "The Witch",
                "year": "2016",
                "imdb_id": "tt4263482",
            }
        }
    )
    client = httpx.Client(
        transport=httpx.MockTransport(_tmdb_handler({"694": "2016-02-19"}))
    )
    enrich_releases(
        [release], resolver, client=client, api_key="x", poster_dir=tmp_path
    )
    client.close()

    assert release.release_date == "2016-02-19"


def test_enrich_skips_yearless_ambiguous_title(tmp_path: Path):
    release = _release("Halloween II", "Unknown", quote="Halloween II | Unknown")
    resolver = StaticTmdbResolver(
        {
            ("Halloween II", ""): {
                "tmdb_id": "24150",
                "title": "Halloween II",
                "year": "2009",
                "imdb_id": "tt1311067",
            }
        }
    )
    client = httpx.Client(transport=httpx.MockTransport(_tmdb_handler({})))
    # A blu-ray resolver is supplied so the yearless candidate is also skipped on
    # the blu-ray lookup path (no guessing there either).
    summary = enrich_releases(
        [release],
        resolver,
        client=client,
        api_key="x",
        poster_dir=tmp_path,
        bluray_resolver=StaticBlurayResolver({}),
    )
    client.close()

    assert release.tmdb_id == ""
    assert release.release_date == "Unknown"
    assert release.bluray_url == ""
    assert summary.unresolved == 1


def test_enrich_pins_misspelled_title_to_canonical_via_alias(tmp_path: Path):
    release = _release("Notting Hilll", "1999", quote="Notting Hilll [1999]")
    resolver = StaticTmdbResolver(
        {
            ("Notting Hill", "1999"): {
                "tmdb_id": "509",
                "title": "Notting Hill",
                "year": "1999",
                "imdb_id": "tt0125439",
            }
        }
    )
    client = httpx.Client(transport=httpx.MockTransport(_tmdb_handler({})))
    enrich_releases(
        [release], resolver, client=client, api_key="x", poster_dir=tmp_path
    )
    client.close()

    assert release.tmdb_id == "509"
    assert release.movie_title == "Notting Hill"
