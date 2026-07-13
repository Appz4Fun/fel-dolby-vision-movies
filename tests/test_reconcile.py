import json
from pathlib import Path

import pytest

from models import FelEvidence, FelRelease, release_from_dict
from reconcile import reconcile_releases


@pytest.mark.parametrize(
    "case",
    json.loads(
        (Path(__file__).parent / "fixtures/yearless_duplicate_cases.json").read_text()
    ),
    ids=lambda case: case["name"],
)
def test_yearless_duplicate_refresh_cases_have_one_decision(case):
    existing = [release_from_dict(row) for row in case["existing"]]
    incoming = [release_from_dict(row) for row in case["incoming"]]
    result = reconcile_releases(existing, incoming)
    expected = case["expected"]

    assert not any(row.release_date == "Unknown" for row in result.releases)
    if expected["kind"] == "merge":
        assert result.merged_count == 1
        assert result.additions == []
        assert result.review_items == []
        assert len(result.releases) == 1
        assert result.releases[0].release_date == expected["release_date"]
    else:
        assert result.merged_count == 0
        assert result.additions == []
        assert len(result.review_items) == 1
        assert result.review_items[0].reason == expected["reason"]
        if "release_dates" in expected:
            assert [row.release_date for row in result.releases] == expected[
                "release_dates"
            ]


def release(title: str, date: str, **kwargs: str) -> FelRelease:
    evidence_type = kwargs.pop("evidence_type", "fel-list")
    quote = kwargs.pop("quote", f"{title} is FEL")
    return FelRelease(
        movie_title=title,
        release_date=date,
        fel_evidence=FelEvidence(
            source_url=f"https://source.test/{title}",
            quote=quote,
            evidence_type=evidence_type,
        ),
        **kwargs,
    )


def test_unique_yearless_title_merges_into_dated_catalog_row():
    base = release("Atomic Blonde", "2017-07-26", tmdb_id="341013")
    candidate = release("Atomic Blonde", "Unknown", evidence_type="google-sheet-list")

    result = reconcile_releases([base], [candidate])

    assert len(result.releases) == 1
    assert result.releases[0].release_date == "2017-07-26"
    assert result.additions == []
    assert result.review_items == []
    assert result.merged_count == 1


def test_existing_yearless_title_merges_into_dated_catalog_row():
    dated = release("Atomic Blonde", "2017-07-26", tmdb_id="341013")
    yearless = release("Atomic Blonde", "Unknown", evidence_type="google-sheet-list")

    result = reconcile_releases([yearless, dated], [])

    assert len(result.releases) == 1
    assert result.releases[0].release_date == "2017-07-26"
    assert result.additions == []
    assert result.review_items == []
    assert result.merged_count == 1


def test_existing_yearless_title_merges_with_incoming_dated_row():
    existing = release("Atomic Blonde", "Unknown", evidence_type="google-sheet-row")
    incoming = release("Atomic Blonde", "2017-07-26", evidence_type="ai-extracted")

    result = reconcile_releases([existing], [incoming])

    assert len(result.releases) == 1
    assert result.releases[0].release_date == "2017-07-26"
    assert result.releases[0].fel_evidence == existing.fel_evidence
    assert result.additions == []
    assert result.review_items == []
    assert result.merged_count == 1


def test_incoming_yearless_title_waits_for_later_dated_row():
    yearless = release("Atomic Blonde", "Unknown", evidence_type="ai-extracted")
    dated = release("Atomic Blonde", "2017-07-26", evidence_type="google-sheet-row")

    result = reconcile_releases([], [yearless, dated])

    assert len(result.releases) == 1
    assert result.releases[0].release_date == "2017-07-26"
    assert result.releases[0].fel_evidence == dated.fel_evidence
    assert result.additions == [dated]
    assert result.review_items == []
    assert result.merged_count == 1


def test_existing_unmatched_yearless_title_is_review_only():
    result = reconcile_releases([release("F9", "Unknown")], [])

    assert result.releases == []
    assert result.additions == []
    assert result.review_items[0].reason == "missing-year-no-match"


def test_existing_ambiguous_yearless_title_is_review_only():
    result = reconcile_releases(
        [
            release("Scream", "Unknown"),
            release("Scream", "1996"),
            release("Scream", "2022"),
        ],
        [],
    )

    assert [item.release_date for item in result.releases] == ["1996", "2022"]
    assert result.additions == []
    assert result.review_items[0].reason == "ambiguous-yearless-title"


def test_yearless_remake_title_is_review_only():
    result = reconcile_releases(
        [release("Scream", "1996"), release("Scream", "2022")],
        [release("Scream", "Unknown")],
    )

    assert [item.release_date for item in result.releases] == ["1996", "2022"]
    assert result.additions == []
    assert result.review_items[0].reason == "ambiguous-yearless-title"
    assert result.review_items[0].candidate_titles == ("Scream", "Scream")


def test_unmatched_yearless_title_is_review_only():
    result = reconcile_releases([], [release("F9", "Unknown")])

    assert result.releases == []
    assert result.additions == []
    assert result.review_items[0].reason == "missing-year-no-match"


def test_known_year_without_match_is_a_real_addition():
    candidate = release("New Film", "2026")

    result = reconcile_releases([], [candidate])

    assert result.releases == [candidate]
    assert result.additions == [candidate]


def test_addition_references_final_release_after_later_metadata_merge():
    first_seen = release("New Film", "2026")
    enriched = release(
        "New Film",
        "2026-07-11",
        tmdb_id="123",
        imdb_id="tt0000123",
        bluray_url="https://www.blu-ray.com/movies/New-Film/123/",
    )

    result = reconcile_releases([], [first_seen, enriched])

    assert len(result.releases) == 1
    assert result.releases[0].release_date == "2026-07-11"
    assert result.releases[0].tmdb_id == "123"
    assert result.additions == result.releases
    assert result.additions[0] is result.releases[0]


def test_new_translated_aliases_collapse_to_one_final_addition():
    english = release(
        "The Movie",
        "2000",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/The-Movie/1/",
    )
    localized = release(
        "Le Film",
        "2000",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/Le-Film/2/",
        quote="The Movie AKA Le Film [2000]",
    )

    result = reconcile_releases([], [english, localized])

    assert len(result.releases) == 1
    assert result.releases[0].movie_title == "The Movie"
    assert result.additions == result.releases
    assert result.additions[0] is result.releases[0]


def test_translated_alias_of_existing_release_is_not_an_addition():
    existing = release(
        "The Movie",
        "2000",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/The-Movie/1/",
    )
    localized = release(
        "Le Film",
        "2000",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/Le-Film/2/",
        quote="The Movie AKA Le Film [2000]",
    )

    result = reconcile_releases([existing], [localized])

    assert len(result.releases) == 1
    assert result.releases[0].movie_title == "The Movie"
    assert result.additions == []


def test_alias_finalization_keeps_mixed_physical_release_group():
    first = release(
        "The Movie",
        "2000",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/The-Movie/1/",
    )
    second = release(
        "the movie!",
        "2000-01-01",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/The-Movie/2/",
    )
    localized = release(
        "Le Film",
        "2000",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/Le-Film/3/",
    )

    result = reconcile_releases([first, second, localized], [])

    assert result.releases == [first, second, localized]
    assert result.additions == []


def test_alias_finalization_keeps_rows_with_conflicting_imdb_ids():
    first = release(
        "The Movie",
        "2000",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/The-Movie/1/",
    )
    conflicting = release(
        "Le Film",
        "2000",
        tmdb_id="1",
        imdb_id="tt0000002",
        bluray_url="https://www.blu-ray.com/movies/Le-Film/2/",
    )

    result = reconcile_releases([first, conflicting], [])

    assert result.releases == [first, conflicting]
    assert result.additions == []


@pytest.mark.parametrize(
    ("first_title", "second_title"),
    [
        ("Game of Thrones S01", "Game of Thrones S02"),
        ("Game of Thrones S1", "Game of Thrones S2"),
        ("Dune", "Dune Steelbook"),
        ("Blade Runner", "Blade Runner: The Final Cut"),
    ],
)
def test_alias_finalization_keeps_compact_season_and_edition_labels(
    first_title: str,
    second_title: str,
):
    first = release(
        first_title,
        "2024",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/Release/1/",
    )
    second = release(
        second_title,
        "2024",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/Release/2/",
    )

    result = reconcile_releases([first, second], [])

    assert result.releases == [first, second]
    assert result.additions == []


@pytest.mark.parametrize(
    "variant_title",
    ["Movie 3D", "Movie Workprint", "Movie Open Matte"],
)
def test_alias_finalization_keeps_unproven_physical_variants(variant_title: str):
    movie = release(
        "Movie",
        "2000",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/Movie/1/",
    )
    variant = release(
        variant_title,
        "2000",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/Movie-Variant/2/",
    )

    result = reconcile_releases([movie, variant], [])

    assert result.releases == [movie, variant]
    assert result.additions == []


def test_spelling_variant_titles_collapse_without_aka_evidence():
    american = release(
        "Three Colors: Blue",
        "1993",
        tmdb_id="108",
        imdb_id="tt0108394",
        bluray_url="https://www.blu-ray.com/movies/Three-Colors-Blue/1/",
    )
    british = release(
        "Three Colours: Blue",
        "1993",
        tmdb_id="108",
        imdb_id="tt0108394",
        bluray_url="https://www.blu-ray.com/movies/Three-Colours-Blue/2/",
    )

    result = reconcile_releases([american, british], [])

    assert len(result.releases) == 1
    assert result.releases[0].movie_title == "Three Colors: Blue"


@pytest.mark.parametrize(
    "typo_title",
    [
        "Gaurdians of the Galaxy",
        "Guardiens of the Galaxy",
        "Guardian of the Galaxy",
        "Guardians of the Galaxyy",
        "Guardians ofthe Galaxy",
    ],
)
def test_single_typo_title_collapses_when_strong_ids_agree(typo_title: str):
    correct = release(
        "Guardians of the Galaxy",
        "2014-08-01",
        tmdb_id="118340",
        imdb_id="tt2015381",
        bluray_url="https://www.blu-ray.com/movies/Guardians-of-the-Galaxy/1/",
    )
    typo = release(
        typo_title,
        "2014-08-01",
        tmdb_id="118340",
        imdb_id="tt2015381",
        bluray_url="https://www.blu-ray.com/movies/Gaurdians-of-the-Galaxy/2/",
    )

    result = reconcile_releases([correct], [typo])

    assert len(result.releases) == 1
    assert result.releases[0].movie_title == "Guardians of the Galaxy"
    assert result.additions == []


def test_same_title_year_distinct_urls_stays_separate_without_shared_id():
    # When only one side carries strong ids, the unenriched incoming row may
    # be a different same-titled film whose TMDB resolution failed (two
    # "Belle" 2021 films exist); the differing disc URL stays an ambiguity
    # signal and the row is appended rather than folded into the catalog row.
    base = release(
        "Belle",
        "2021",
        tmdb_id="776503",
        imdb_id="tt13651628",
        bluray_url="https://www.blu-ray.com/movies/Belle/1/",
    )
    unenriched = release(
        "Belle",
        "2021-12-03",
        bluray_url="https://www.blu-ray.com/movies/Belle/2/",
    )

    result = reconcile_releases([base], [unenriched])

    assert result.releases == [base, unenriched]
    assert result.additions == [unenriched]
    assert result.merged_count == 0


def test_same_title_year_distinct_urls_merges_when_neither_side_has_ids():
    # With no ids on either side the two rows are indistinguishable to
    # readers, so the differing disc URL alone must not publish both.
    base = release(
        "Avatar",
        "2009",
        bluray_url="https://www.blu-ray.com/movies/Avatar/1/",
    )
    rescraped = release(
        "Avatar",
        "2009",
        bluray_url="https://www.blu-ray.com/movies/Avatar/2/",
    )

    result = reconcile_releases([base], [rescraped])

    assert len(result.releases) == 1
    assert result.merged_count == 1


def test_same_title_year_distinct_urls_merges_on_shared_imdb_id_alone():
    base = release(
        "Movie",
        "2000",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/Movie/1/",
    )
    second_disc = release(
        "Movie",
        "2000-01-01",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/Movie/2/",
    )

    result = reconcile_releases([base], [second_disc])

    assert len(result.releases) == 1
    assert result.merged_count == 1


def test_number_word_variant_titles_collapse_without_aka_evidence():
    # Marketing spells the same film both ways ("The Fantastic 4" letterboxd
    # row vs "The Fantastic Four" reddit row, one TMDB id); a spelled-out
    # number is orthography, not a different release.
    digits = release(
        "The Fantastic 4: First Steps",
        "2025-07-23",
        tmdb_id="617126",
        imdb_id="tt10676052",
        bluray_url=(
            "https://www.blu-ray.com/movies/"
            "The-Fantastic-Four-First-Steps-4K-Blu-ray/397023/"
        ),
    )
    words = release(
        "The Fantastic Four: First Steps",
        "2025-07-23",
        tmdb_id="617126",
        imdb_id="tt10676052",
        bluray_url=(
            "https://www.blu-ray.com/movies/"
            "The-Fantastic-Four-First-Steps-4K-Blu-ray/397501/"
        ),
    )

    result = reconcile_releases([digits], [words])

    assert len(result.releases) == 1
    assert result.releases[0].movie_title == "The Fantastic 4: First Steps"
    assert result.additions == []


@pytest.mark.parametrize(
    ("first_title", "second_title"),
    [
        ("Iron Man 2", "Iron Man 3"),
        # A spelled-out number must normalize to its digit before the
        # digit-run guard, so word-vs-digit sequels stay distinct too.
        ("Iron Man Two", "Iron Man 3"),
        ("28 Days Later", "28 Weeks Later"),
        ("Up", "Us"),
        # A whole appended word must never read as a typo: space-stripped
        # "alien" -> "alienx" is a single insertion, indistinguishable from a
        # real one-letter typo unless word counts are compared too.
        ("Alien", "Alien X"),
    ],
)
def test_similar_but_distinct_titles_never_collapse_without_proof(
    first_title: str,
    second_title: str,
):
    first = release(
        first_title,
        "2010",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/First/1/",
    )
    second = release(
        second_title,
        "2010",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/Second/2/",
    )

    result = reconcile_releases([first, second], [])

    assert result.releases == [first, second]


def test_explicit_aka_evidence_connects_real_three_colors_alias_group():
    american = release(
        "Three Colors: Blue",
        "1993-09-08",
        tmdb_id="108",
        imdb_id="tt0108394",
        bluray_url="https://www.blu-ray.com/movies/Three-Colors-Blue/1/",
    )
    french = release(
        "Trois couleurs: Bleu",
        "1993-09-08",
        tmdb_id="108",
        imdb_id="tt0108394",
        quote="Trois couleurs: Bleu AKA Three Colors: Blue [1993]",
    )
    british = release(
        "Three Colours: Blue",
        "1993-09-08",
        tmdb_id="108",
        imdb_id="tt0108394",
        bluray_url="https://www.blu-ray.com/movies/Three-Colours-Blue/2/",
    )

    result = reconcile_releases([american, french, british], [])

    assert len(result.releases) == 1
    assert result.releases[0].movie_title == "Three Colors: Blue"


@pytest.mark.parametrize(
    "quote",
    [
        "Movie is FEL. Unrelated AKA Le Film",
        "Movie is not AKA Le Film",
    ],
)
def test_nonlocal_or_negated_aka_text_does_not_prove_an_alias(quote: str):
    movie = release(
        "Movie",
        "2000",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/Movie/1/",
    )
    localized = release(
        "Le Film",
        "2000",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/Le-Film/2/",
        quote=quote,
    )

    result = reconcile_releases([movie, localized], [])

    assert result.releases == [movie, localized]


def test_ordinal_punctuation_and_accent_aka_evidence_proves_an_alias():
    english = release(
        "The Crimson Rivers",
        "2000",
        tmdb_id="60670",
        imdb_id="tt0228786",
        bluray_url="https://www.blu-ray.com/movies/The-Crimson-Rivers/1/",
    )
    french = release(
        "Les rivières pourpres",
        "2000",
        tmdb_id="60670",
        imdb_id="tt0228786",
        bluray_url="https://www.blu-ray.com/movies/Les-Rivieres-Pourpres/2/",
        quote="314. Les rivières pourpres AKA The Crimson Rivers [2000]",
    )

    result = reconcile_releases([english, french], [])

    assert len(result.releases) == 1
    assert result.releases[0].movie_title == "The Crimson Rivers"


def test_tmdb_original_title_metadata_proves_foreign_alias_pair():
    english = release(
        "The Crimson Rivers",
        "2000-09-27",
        tmdb_id="60670",
        imdb_id="tt0228786",
        bluray_url="https://www.blu-ray.com/movies/The-Crimson-Rivers/1/",
    )
    french = release(
        "Les rivières pourpres",
        "2000-09-27",
        tmdb_id="60670",
        imdb_id="tt0228786",
        bluray_url="https://www.blu-ray.com/movies/Les-Rivieres-Pourpres/2/",
        quote="Les rivières pourpres (2000)",
        additional_characteristics={
            "tmdb_title": "The Crimson Rivers",
            "tmdb_original_title": "Les rivières pourpres",
        },
    )

    result = reconcile_releases([english], [french])

    assert len(result.releases) == 1
    assert result.releases[0].movie_title == "The Crimson Rivers"
    assert result.additions == []


@pytest.mark.parametrize(
    "characteristics",
    [
        {"tmdb_title": "Movie", "tmdb_original_title": "Movie"},
        {"tmdb_title": "Movie", "tmdb_original_title": "Another Film"},
        {"tmdb_original_title": "Le Film"},
    ],
    ids=["same-title-pair", "pair-names-other-film", "missing-canonical-title"],
)
def test_tmdb_title_metadata_must_name_both_rows_to_prove_an_alias(
    characteristics: dict[str, str],
):
    movie = release(
        "Movie",
        "2000",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/Movie/1/",
    )
    localized = release(
        "Le Film",
        "2000",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/Le-Film/2/",
        additional_characteristics=characteristics,
    )

    result = reconcile_releases([movie, localized], [])

    assert result.releases == [movie, localized]


def test_multi_aka_chain_connects_only_adjacent_named_titles():
    first = release(
        "Film A",
        "2000",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/Film-A/1/",
        quote="Film A AKA Film B AKA Film C [2000]",
    )
    second = release(
        "Film B",
        "2000",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/Film-B/2/",
    )
    third = release(
        "Film C",
        "2000",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/Film-C/3/",
    )

    result = reconcile_releases([first, second, third], [])

    assert len(result.releases) == 1
    assert result.releases[0].movie_title == "Film A"


def test_alias_finalization_restores_interleaved_catalog_order():
    first = release("Series S01", "2024", tmdb_id="1", imdb_id="tt0000001")
    between = release("Between", "2024", tmdb_id="2", imdb_id="tt0000002")
    second = release("Series S02", "2024", tmdb_id="1", imdb_id="tt0000001")

    result = reconcile_releases([first, between, second], [])

    assert result.releases == [first, between, second]


def test_additions_follow_final_catalog_first_index_order():
    first = release(
        "Series S01",
        "2024",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/Series-S01/1/",
    )
    between = release("Between", "2024", tmdb_id="2", imdb_id="tt0000002")
    second = release(
        "Series S02",
        "2024",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/Series-S02/2/",
    )

    result = reconcile_releases([], [first, between, second])

    assert result.releases == [first, between, second]
    assert result.additions == result.releases


def test_exact_bluray_url_selects_one_of_two_editions():
    theatrical = release(
        "Movie: Theatrical Edition",
        "2000",
        tmdb_id="1",
        bluray_url="https://www.blu-ray.com/movies/Movie/1/",
    )
    extended = release(
        "Movie: Extended Edition",
        "2000",
        tmdb_id="1",
        bluray_url="https://www.blu-ray.com/movies/Movie-Extended/2/",
    )
    candidate = release(
        "Movie",
        "Unknown",
        tmdb_id="1",
        bluray_url="http://www.blu-ray.com/movies/Movie-Extended/2/?src=list",
    )

    result = reconcile_releases([theatrical, extended], [candidate])

    assert result.review_items == []
    assert result.merged_count == 1
    assert len(result.releases) == 2


def test_movie_id_without_edition_identity_is_review_only():
    result = reconcile_releases(
        [
            release(
                "Movie: Theatrical Edition",
                "2000",
                tmdb_id="1",
                bluray_url="https://disc.test/1",
            ),
            release(
                "Movie: Extended Edition",
                "2000",
                tmdb_id="1",
                bluray_url="https://disc.test/2",
            ),
        ],
        [release("Movie", "Unknown", tmdb_id="1")],
    )

    assert result.review_items[0].reason == "ambiguous-edition"
    assert result.review_items[0].candidate_titles == (
        "Movie: Theatrical Edition",
        "Movie: Extended Edition",
    )


def test_conflicting_strong_ids_are_review_only():
    result = reconcile_releases(
        [
            release("One", "2001", tmdb_id="1", imdb_id="tt0000001"),
            release("Two", "2002", tmdb_id="2", imdb_id="tt0000002"),
        ],
        [release("One", "2001", tmdb_id="1", imdb_id="tt0000002")],
    )

    assert result.releases[0].fel_evidence.quote == "One is FEL"
    assert result.additions == []
    assert result.review_items[0].reason == "identity-conflict"
    assert result.review_items[0].candidate_titles == ("One", "Two")


def test_identity_conflict_candidate_titles_follow_catalog_order():
    imdb_match = release("IMDb Match", "2002", tmdb_id="2", imdb_id="tt0000002")
    tmdb_match = release("TMDB Match", "2001", tmdb_id="1", imdb_id="tt0000001")
    candidate = release("Candidate", "2001", tmdb_id="1", imdb_id="tt0000002")

    result = reconcile_releases([imdb_match, tmdb_match], [candidate])

    assert result.review_items[0].reason == "identity-conflict"
    assert result.review_items[0].candidate_titles == ("IMDb Match", "TMDB Match")


def test_exact_bluray_url_with_conflicting_strong_id_is_review_only():
    base = release(
        "Movie",
        "2001",
        tmdb_id="1",
        bluray_url="https://www.blu-ray.com/movies/Movie/1/",
    )
    candidate = release(
        "Movie",
        "2001",
        tmdb_id="2",
        bluray_url="http://www.blu-ray.com/movies/Movie/1/?src=list",
    )

    result = reconcile_releases([base], [candidate])

    assert result.releases == [base]
    assert result.additions == []
    assert result.merged_count == 0
    assert result.review_items[0].reason == "identity-conflict"


def test_conflicting_bluray_url_and_tmdb_matches_are_review_only():
    tmdb_match = release(
        "TMDB Match",
        "2001",
        tmdb_id="1",
        bluray_url="https://www.blu-ray.com/movies/TMDB-Match/1/",
    )
    url_match = release(
        "URL Match",
        "2002",
        bluray_url="https://www.blu-ray.com/movies/URL-Match/2/",
    )
    candidate = release(
        "Candidate",
        "2003",
        tmdb_id="1",
        bluray_url="http://www.blu-ray.com/movies/URL-Match/2/?src=list",
    )

    result = reconcile_releases([tmdb_match, url_match], [candidate])

    assert result.releases == [tmdb_match, url_match]
    assert result.additions == []
    assert result.merged_count == 0
    assert result.review_items[0].reason == "identity-conflict"
    assert result.review_items[0].candidate_titles == ("TMDB Match", "URL Match")


def test_unique_bluray_url_merges_sparse_ids_across_sibling_editions():
    theatrical = release(
        "Movie: Theatrical Edition",
        "2001",
        tmdb_id="1",
        bluray_url="https://www.blu-ray.com/movies/Movie/1/",
    )
    extended = release(
        "Movie: Extended Edition",
        "2001",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/Movie-Extended/2/",
    )
    candidate = release(
        "Movie: Theatrical Edition",
        "Unknown",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="http://www.blu-ray.com/movies/Movie/1/?src=list",
    )

    result = reconcile_releases([theatrical, extended], [candidate])

    assert len(result.releases) == 2
    assert result.releases[0].imdb_id == "tt0000001"
    assert result.releases[1] == extended
    assert result.additions == []
    assert result.review_items == []
    assert result.merged_count == 1


def test_connected_signals_without_unique_url_are_narrowed_by_title_year():
    url_only = release(
        "URL Only",
        "1999",
        bluray_url="https://www.blu-ray.com/movies/Shared/1/",
    )
    title_year_match = release(
        "Movie",
        "2000",
        tmdb_id="1",
        bluray_url="https://www.blu-ray.com/movies/Shared/1/",
    )
    id_match = release(
        "ID Match",
        "2001",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="https://www.blu-ray.com/movies/ID-Match/2/",
    )
    candidate = release(
        "movie!",
        "2000-01-01",
        tmdb_id="1",
        imdb_id="tt0000001",
        bluray_url="http://www.blu-ray.com/movies/Shared/1/?src=list",
    )

    result = reconcile_releases(
        [url_only, title_year_match, id_match],
        [candidate],
    )

    assert len(result.releases) == 3
    assert result.releases[1].imdb_id == "tt0000001"
    assert result.additions == []
    assert result.review_items == []
    assert result.merged_count == 1


def test_canonical_title_and_year_with_conflicting_strong_id_is_review_only():
    base = release("Movie", "2001-01-01", imdb_id="tt0000001")
    candidate = release("movie!", "2001", imdb_id="tt0000002")

    result = reconcile_releases([base], [candidate])

    assert result.releases == [base]
    assert result.additions == []
    assert result.merged_count == 0
    assert result.review_items[0].reason == "identity-conflict"


def test_single_movie_id_match_with_conflicting_other_id_is_review_only():
    base = release("Localized Title", "2001", tmdb_id="1", imdb_id="tt0000001")
    candidate = release("Original Title", "Unknown", tmdb_id="1", imdb_id="tt0000002")

    result = reconcile_releases([base], [candidate])

    assert result.releases == [base]
    assert result.additions == []
    assert result.review_items[0].reason == "identity-conflict"


def test_consistent_tmdb_and_imdb_ids_match_one_catalog_row():
    base = release("Localized Title", "2001", tmdb_id="1", imdb_id="tt0000001")
    candidate = release("Original Title", "Unknown", tmdb_id="1", imdb_id="tt0000001")

    result = reconcile_releases([base], [candidate])

    assert len(result.releases) == 1
    assert result.review_items == []
    assert result.merged_count == 1


def test_distinct_known_year_edition_remains_an_addition():
    base = release(
        "Avatar",
        "2009",
        tmdb_id="19995",
        bluray_url="https://www.blu-ray.com/movies/Avatar/1/",
    )
    extended = release(
        "Avatar: Extended Collector's Edition",
        "2009",
        tmdb_id="19995",
        bluray_url="https://www.blu-ray.com/movies/Avatar-Extended/2/",
    )

    result = reconcile_releases([base], [extended])

    assert result.additions == [extended]
    assert len(result.releases) == 2


def test_same_title_year_with_distinct_bluray_url_merges():
    # A re-scrape of a film already in the catalog can resolve a different
    # blu-ray.com page (multiple 4K pressings of the same cut), so a distinct
    # disc URL alone must not spawn a second identically-titled row.
    base = release(
        "Movie",
        "2000",
        tmdb_id="1",
        bluray_url="https://www.blu-ray.com/movies/Movie/1/",
    )
    second_disc = release(
        "movie!",
        "2000-01-01",
        tmdb_id="1",
        bluray_url="https://www.blu-ray.com/movies/Movie/2/",
    )

    result = reconcile_releases([base], [second_disc])

    assert len(result.releases) == 1
    assert result.releases[0].movie_title == "Movie"
    assert result.releases[0].bluray_url == "https://www.blu-ray.com/movies/Movie/1/"
    assert result.additions == []
    assert result.review_items == []
    assert result.merged_count == 1


def test_distinct_yearless_edition_is_review_only():
    base = release(
        "Avatar",
        "2009",
        tmdb_id="19995",
        bluray_url="https://www.blu-ray.com/movies/Avatar/1/",
    )
    extended = release(
        "Avatar: Extended Collector's Edition",
        "Unknown",
        tmdb_id="19995",
        bluray_url="https://www.blu-ray.com/movies/Avatar-Extended/2/",
    )

    result = reconcile_releases([base], [extended])

    assert result.additions == []
    assert result.review_items[0].reason == "ambiguous-edition"
    assert result.review_items[0].candidate_titles == ("Avatar",)


def test_same_title_yearless_row_with_distinct_bluray_url_is_review_only():
    base = release(
        "Movie",
        "2000",
        tmdb_id="1",
        bluray_url="https://www.blu-ray.com/movies/Movie/1/",
    )
    second_disc = release(
        "movie!",
        "Unknown",
        tmdb_id="1",
        bluray_url="https://www.blu-ray.com/movies/Movie/2/",
    )

    result = reconcile_releases([base], [second_disc])

    assert result.releases == [base]
    assert result.additions == []
    assert result.review_items[0].reason == "ambiguous-edition"


def test_canonical_title_and_year_match_existing_release():
    base = release("Schindler's List", "1993-12-15")
    candidate = release("Schindlers List!", "1993")

    result = reconcile_releases([base], [candidate])

    assert len(result.releases) == 1
    assert result.merged_count == 1
    assert result.additions == []


def test_duplicate_title_and_year_targets_are_review_only():
    first = release("Movie", "2000")
    second = release("movie!", "2000-01-01")

    result = reconcile_releases([first, second], [release("Movie", "2000")])

    assert result.releases == [first, second]
    assert result.additions == []
    assert result.merged_count == 0
    assert result.review_items[0].reason == "ambiguous-edition"
    assert result.review_items[0].candidate_titles == ("Movie", "movie!")


@pytest.mark.parametrize(
    ("id_field", "id_value"),
    [("tmdb_id", "123"), ("imdb_id", "tt0000123")],
)
def test_unique_movie_id_matches_existing_release(id_field: str, id_value: str):
    base = release("Localized Title", "2001", **{id_field: id_value})
    candidate = release("Original Title", "Unknown", **{id_field: id_value})

    result = reconcile_releases([base], [candidate])

    assert len(result.releases) == 1
    assert result.review_items == []
    assert result.merged_count == 1


def test_movie_id_matches_are_narrowed_by_canonical_title_and_year():
    first = release("Movie", "2000", tmdb_id="1")
    remake = release("Movie", "2020", tmdb_id="1")
    candidate = release("movie!", "2020-01-01", tmdb_id="1")

    result = reconcile_releases([first, remake], [candidate])

    assert len(result.releases) == 2
    assert result.releases[1].release_date == "2020-01-01"
    assert result.review_items == []
    assert result.merged_count == 1


def test_deterministic_evidence_wins_over_ai_evidence_in_either_order():
    deterministic = release("Dune", "2021", evidence_type="google-sheet-row")
    ai = release("Dune", "2021", evidence_type="ai-extracted")

    existing_deterministic = reconcile_releases([deterministic], [ai])
    incoming_deterministic = reconcile_releases([ai], [deterministic])

    assert (
        existing_deterministic.releases[0].fel_evidence.evidence_type
        == "google-sheet-row"
    )
    assert (
        incoming_deterministic.releases[0].fel_evidence.evidence_type
        == "google-sheet-row"
    )


def test_reconcile_releases_does_not_mutate_caller_lists():
    base = release("Existing", "2000")
    candidate = release("New", "2026")
    existing = [base]
    incoming = [candidate]

    reconcile_releases(existing, incoming)

    assert existing == [base]
    assert incoming == [candidate]


def test_new_season_with_shared_series_ids_stays_a_separate_row():
    """Every season disc of a show resolves to the same series-level TMDB and
    IMDb ids, so a strong-id hit between two differently-titled season rows
    proves the *series*, not the disc. A newly scraped later season (no
    blu-ray URL yet, as is typical for a fresh release) must be appended as
    its own row, not folded into the only existing season of the show."""
    first_season = release(
        "Ahsoka: The Complete First Season",
        "2023",
        tmdb_id="114461",
        imdb_id="tt13622776",
        bluray_url="https://www.blu-ray.com/movies/Ahsoka-S1-4K-Blu-ray/1/",
    )
    second_season = release(
        "Ahsoka: The Complete Second Season",
        "2026",
        tmdb_id="114461",
        imdb_id="tt13622776",
    )

    result = reconcile_releases([first_season], [second_season])

    assert result.releases == [first_season, second_season]
    assert result.additions == [second_season]
    assert result.merged_count == 0


def test_retitled_season_row_with_same_disc_url_still_merges():
    """A shared blu-ray.com page proves one physical disc regardless of how
    the source spelled the season descriptor, so the series-id stop signal
    must not keep URL-proven retitles apart."""
    base = release(
        "Ahsoka: The Complete First Season",
        "2023",
        tmdb_id="114461",
        imdb_id="tt13622776",
        bluray_url="https://www.blu-ray.com/movies/Ahsoka-S1-4K-Blu-ray/1/",
    )
    retitled = release(
        "Ahsoka: Season 1",
        "2023",
        tmdb_id="114461",
        imdb_id="tt13622776",
        bluray_url="https://www.blu-ray.com/movies/Ahsoka-S1-4K-Blu-ray/1/",
    )

    result = reconcile_releases([base], [retitled])

    assert result.merged_count == 1
    assert len(result.releases) == 1
    assert result.releases[0].movie_title == "Ahsoka: The Complete First Season"


def test_part_number_spelling_variant_with_shared_ids_still_merges():
    """Movie-edition descriptor words ("Part One" vs "Part 1") must not trip
    the series-id stop signal: only season/series descriptors mark rows whose
    shared TMDB/IMDb ids prove a series rather than one release."""
    base = release(
        "Mission: Impossible - Dead Reckoning Part One",
        "2023",
        tmdb_id="575264",
        imdb_id="tt9603212",
        bluray_url="https://www.blu-ray.com/movies/MI-DR-4K-Blu-ray/1/",
    )
    variant = release(
        "Mission: Impossible - Dead Reckoning Part 1",
        "2023",
        tmdb_id="575264",
        imdb_id="tt9603212",
    )

    result = reconcile_releases([base], [variant])

    assert result.merged_count == 1
    assert len(result.releases) == 1
    assert result.releases[0].movie_title == (
        "Mission: Impossible - Dead Reckoning Part One"
    )


def test_tv_series_id_does_not_match_same_numbered_movie_id():
    """TMDB movie and TV ids are separate namespaces, so a bare numeric
    tmdb_id match between a /movie/ row and a /tv/ row is a coincidence,
    not an identity signal: the TV season row must be appended, never
    folded into the same-numbered movie."""
    movie = release(
        "Some Unrelated Film",
        "1974",
        tmdb_id="1399",
        release_url="https://www.themoviedb.org/movie/1399",
    )
    tv_season = release(
        "Game of Thrones: The Complete First Season",
        "2011",
        tmdb_id="1399",
        imdb_id="tt0944947",
        release_url="https://www.themoviedb.org/tv/1399",
    )

    result = reconcile_releases([movie], [tv_season])

    assert result.releases == [movie, tv_season]
    assert result.additions == [tv_season]
    assert result.merged_count == 0


def test_same_season_title_variants_merge_without_disc_urls():
    """Two spellings of the *same* season ("The Complete First Season" vs
    "Season 1") with shared series ids name one physical release, so the
    series-id stop signal must let them fold even before Blu-ray enrichment
    provides a shared disc URL."""
    base = release(
        "Ahsoka: The Complete First Season",
        "2023",
        tmdb_id="114461",
        imdb_id="tt13622776",
    )
    variant = release(
        "Ahsoka: Season 1",
        "2023",
        tmdb_id="114461",
        imdb_id="tt13622776",
    )

    result = reconcile_releases([base], [variant])

    assert result.merged_count == 1
    assert len(result.releases) == 1
    assert result.releases[0].movie_title == "Ahsoka: The Complete First Season"


def test_new_season_appends_when_catalog_has_multiple_seasons():
    """A GoT-style catalog already holds several seasons sharing one series
    id; a freshly scraped later season (no Blu-ray URL yet) hits every one
    of them as a strong-id match. That multi-match must not be routed to
    ambiguous-edition review -- the season stop signal applies first and the
    new season is appended as its own row."""
    first = release(
        "Game of Thrones: The Complete First Season",
        "2011",
        tmdb_id="1399",
        imdb_id="tt0944947",
        bluray_url="https://www.blu-ray.com/movies/GoT-S1-4K-Blu-ray/1/",
    )
    second = release(
        "Game of Thrones: The Complete Second Season",
        "2012",
        tmdb_id="1399",
        imdb_id="tt0944947",
        bluray_url="https://www.blu-ray.com/movies/GoT-S2-4K-Blu-ray/2/",
    )
    third = release(
        "Game of Thrones: The Complete Third Season",
        "2013",
        tmdb_id="1399",
        imdb_id="tt0944947",
    )

    result = reconcile_releases([first, second], [third])

    assert result.releases == [first, second, third]
    assert result.additions == [third]
    assert result.merged_count == 0
    assert result.review_items == []
