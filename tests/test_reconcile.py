import pytest

from models import FelEvidence, FelRelease
from reconcile import reconcile_releases


def release(title: str, date: str, **kwargs: str) -> FelRelease:
    evidence_type = kwargs.pop("evidence_type", "fel-list")
    return FelRelease(
        movie_title=title,
        release_date=date,
        fel_evidence=FelEvidence(
            source_url=f"https://source.test/{title}",
            quote=f"{title} is FEL",
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


def test_same_title_year_with_distinct_bluray_url_remains_an_addition():
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

    assert result.releases == [base, second_disc]
    assert result.additions == [second_disc]
    assert result.review_items == []
    assert result.merged_count == 0


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
