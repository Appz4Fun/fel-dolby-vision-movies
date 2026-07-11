from merge import (
    canonical_key,
    canonical_title_key,
    canonical_url_key,
    dedupe_releases,
    dedupe_tmdb_releases,
    has_edition_descriptor,
    merge_releases,
    tmdb_key,
)
from models import FelEvidence, FelRelease


def make(title, date, **kwargs):
    return FelRelease(
        movie_title=title,
        release_date=date,
        fel_evidence=FelEvidence(
            source_url=kwargs.pop("source_url", f"https://src.test/{title}"),
            quote=f"{title} FEL",
            evidence_type=kwargs.pop("evidence_type", "fel-list"),
        ),
        **kwargs,
    )


def test_canonical_key_ignores_case_punctuation_and_uses_year():
    left = make("The Northman", "2022")
    right = make("the northman!", "2022-04-22")
    assert canonical_key(left) == canonical_key(right)


def test_canonical_title_key_collapses_possessive_apostrophes():
    assert canonical_title_key("Schindler's List") == canonical_title_key(
        "Schindlers List"
    )


def test_canonical_url_key_ignores_transport_and_tracking_parts():
    left = "http://WWW.BLU-RAY.COM/movies/Alien/123/?ref=list#details"
    right = "https://www.blu-ray.com/movies/Alien/123"
    assert canonical_url_key(left) == canonical_url_key(right)


def test_has_edition_descriptor_is_public():
    assert has_edition_descriptor("Avatar: Extended Collector's Edition")
    assert not has_edition_descriptor("Avatar")


def test_merge_releases_unions_fields_and_prefers_known_values():
    base = make("Dune", "2021", studio="Unknown", audio_formats=["DTS-HD MA"])
    base.additional_characteristics = {"source_urls": ["https://a.test"]}
    other = make("Dune", "2021", studio="Warner Bros.", audio_formats=["TrueHD Atmos"])
    other.additional_characteristics = {
        "source_urls": ["https://b.test"],
        "enhancement_bitrate_mbps": 7.1,
    }
    other.tmdb_id = "438631"

    merged = merge_releases(base, other)
    assert merged.studio == "Warner Bros."
    assert merged.audio_formats == ["DTS-HD MA", "TrueHD Atmos"]
    assert merged.additional_characteristics["source_urls"] == [
        "https://a.test",
        "https://b.test",
    ]
    assert merged.additional_characteristics["enhancement_bitrate_mbps"] == 7.1
    assert merged.tmdb_id == "438631"


def test_merge_releases_preserves_bluray_enrichment_fields():
    base = make("Dune", "2021")
    enriched = make("Dune", "2021")
    enriched.bluray_url = "https://www.blu-ray.com/movies/Dune-4K-Blu-ray/1/"
    enriched.bluray_release_date = "2022-01-11"
    enriched.hdr_formats = ["Dolby Vision", "HDR10"]
    enriched.audio_languages = ["English", "French"]

    merged = merge_releases(base, enriched)

    assert merged.bluray_url.endswith("/1/")
    assert merged.bluray_release_date == "2022-01-11"
    assert merged.hdr_formats == ["Dolby Vision", "HDR10"]
    assert merged.audio_languages == ["English", "French"]


def test_dedupe_releases_merges_same_canonical_key():
    releases = [
        make("Sicario", "2015"),
        make("sicario", "2015"),
        make("Arrival", "2016"),
    ]
    deduped = dedupe_releases(releases, canonical_key)
    assert [r.movie_title for r in deduped] == ["Sicario", "Arrival"]


def test_merge_prefers_real_evidence_over_weak_list_membership():
    # Any *-list evidence type is weak (list membership with a synthesized
    # quote). A real deterministic evidence type wins regardless of order.
    list_only = make("Dune", "2021", evidence_type="reddit-list")
    real = make("Dune", "2021", evidence_type="google-sheet-row")
    assert (
        merge_releases(list_only, real).fel_evidence.evidence_type == "google-sheet-row"
    )
    assert (
        merge_releases(real, list_only).fel_evidence.evidence_type == "google-sheet-row"
    )
    # Two weak list memberships tie -- first arg wins.
    fel = make("Dune", "2021", evidence_type="fel-list")
    github = make("Dune", "2021", evidence_type="github-list")
    assert merge_releases(fel, github).fel_evidence.evidence_type == "fel-list"


def test_merge_prefers_full_date_over_bare_year():
    year = make("Dune", "2021")
    full = make("Dune", "2021-10-22")
    assert merge_releases(year, full).release_date == "2021-10-22"
    assert merge_releases(full, year).release_date == "2021-10-22"


def test_merge_prefers_real_timestamp_over_unknown():
    has_ts = make("Dune", "2021", collected_at="2026-05-21T00:00:00+00:00")
    no_ts = make("Dune", "2021")
    assert merge_releases(has_ts, no_ts).collected_at == "2026-05-21T00:00:00+00:00"
    assert merge_releases(no_ts, has_ts).collected_at == "2026-05-21T00:00:00+00:00"


def test_merge_preserves_existing_timestamp_over_new_scrape():
    existing = make("Sisu", "2022", collected_at="2026-05-21T00:00:00+00:00")
    scraped = make("Sisu", "2022", collected_at="2026-05-25T00:00:00+00:00")

    assert merge_releases(existing, scraped).collected_at == "2026-05-21T00:00:00+00:00"


def test_merge_prefers_title_with_fewer_dots():
    dotted = make("The.Northman", "2022")
    spaced = make("The Northman", "2022")
    assert merge_releases(dotted, spaced).movie_title == "The Northman"
    assert merge_releases(spaced, dotted).movie_title == "The Northman"


def test_tmdb_key_uses_tmdb_id_when_present_else_canonical():
    resolved = make("Dune", "2021")
    resolved.tmdb_id = "438631"
    assert tmdb_key(resolved) == ("tmdb", "438631")

    unresolved = make("Dune", "2021")
    assert tmdb_key(unresolved) == canonical_key(unresolved)


def test_tmdb_key_includes_bluray_url_when_present():
    resolved = make("Dune", "2021")
    resolved.tmdb_id = "438631"
    resolved.bluray_url = "https://www.blu-ray.com/movies/Dune-4K-Blu-ray/1/?x=1"

    assert tmdb_key(resolved) == (
        "tmdb-bluray",
        "438631\0https://www.blu-ray.com/movies/Dune-4K-Blu-ray/1",
    )


def test_dedupe_tmdb_releases_merges_unresolved_canonical_match():
    resolved = make("Dune", "2021")
    resolved.tmdb_id = "438631"
    resolved.bluray_url = "https://www.blu-ray.com/movies/Dune-4K-Blu-ray/1/"
    unresolved = make("dune!", "2021")
    unresolved.tmdb_id = "438631"

    deduped = dedupe_tmdb_releases([resolved, unresolved])

    assert len(deduped) == 1
    assert deduped[0].movie_title == "Dune"


def test_dedupe_tmdb_releases_merges_unresolved_title_match():
    resolved = make("Dune", "2021")
    resolved.tmdb_id = "438631"
    resolved.bluray_url = "https://www.blu-ray.com/movies/Dune-4K-Blu-ray/1/"
    unresolved = make("Dune", "Unknown")
    unresolved.tmdb_id = "438631"

    deduped = dedupe_tmdb_releases([resolved, unresolved])

    assert len(deduped) == 1
    assert deduped[0].release_date == "2021"


def test_dedupe_tmdb_releases_merges_single_candidate_without_year():
    resolved = make("The Three Musketeers: Milady", "2023-12-13")
    resolved.tmdb_id = "845111"
    resolved.bluray_url = (
        "https://www.blu-ray.com/movies/"
        "Les-Trois-Mousquetaires--Milady-4K-Blu-ray/347971/"
    )
    unresolved = make("Les Trois Mousquetaires: Milady", "Unknown")
    unresolved.tmdb_id = "845111"

    deduped = dedupe_tmdb_releases([resolved, unresolved])

    assert len(deduped) == 1
    assert deduped[0].movie_title == "The Three Musketeers: Milady"


def test_dedupe_tmdb_releases_keeps_ambiguous_unresolved_row():
    theatrical = make("Avatar", "2009-12-16")
    theatrical.tmdb_id = "19995"
    theatrical.bluray_url = "https://www.blu-ray.com/movies/Avatar-4K-Blu-ray/349437/"
    extended = make("Avatar: Extended Collector's Edition", "2009-12-16")
    extended.tmdb_id = "19995"
    extended.bluray_url = "https://www.blu-ray.com/movies/Avatar-4K-Blu-ray/191856/"
    ambiguous = make("Avatar Collection", "Unknown")
    ambiguous.tmdb_id = "19995"

    deduped = dedupe_tmdb_releases([theatrical, extended, ambiguous])

    assert [release.movie_title for release in deduped] == [
        "Avatar",
        "Avatar: Extended Collector's Edition",
        "Avatar Collection",
    ]


def test_dedupe_tmdb_releases_keeps_same_identity_with_distinct_bluray_urls():
    first = make("Movie", "2000")
    first.tmdb_id = "1"
    first.bluray_url = "https://www.blu-ray.com/movies/Movie/1/"
    second = make("movie!", "2000-01-01")
    second.tmdb_id = "1"
    second.bluray_url = "https://www.blu-ray.com/movies/Movie/2/"

    deduped = dedupe_tmdb_releases([first, second])

    assert deduped == [first, second]


def test_dedupe_tmdb_releases_still_collapses_different_title_aliases():
    english = make("The Movie", "2000")
    english.tmdb_id = "1"
    english.bluray_url = "https://www.blu-ray.com/movies/The-Movie/1/"
    localized = make("Le Film", "2000")
    localized.tmdb_id = "1"
    localized.bluray_url = "https://www.blu-ray.com/movies/Le-Film/2/"

    deduped = dedupe_tmdb_releases([english, localized])

    assert len(deduped) == 1
    assert deduped[0].movie_title == "The Movie"


def test_dedupe_tmdb_releases_keeps_physical_rows_in_mixed_alias_group():
    first = make("The Movie", "2000")
    first.tmdb_id = "1"
    first.bluray_url = "https://www.blu-ray.com/movies/The-Movie/1/"
    second = make("the movie!", "2000-01-01")
    second.tmdb_id = "1"
    second.bluray_url = "https://www.blu-ray.com/movies/The-Movie/2/"
    localized = make("Le Film", "2000")
    localized.tmdb_id = "1"
    localized.bluray_url = "https://www.blu-ray.com/movies/Le-Film/3/"

    deduped = dedupe_tmdb_releases([first, second, localized])

    assert deduped == [first, second, localized]


def test_dedupe_is_order_independent_for_strong_evidence():
    weak = make("Sicario", "2015", evidence_type="fel-list")
    strong = make("Sicario", "2015", evidence_type="google-sheet-row")
    bitrate = make("Sicario", "2015", evidence_type="fel-bitrate-list")
    forward = dedupe_releases([weak, strong, bitrate], canonical_key)
    backward = dedupe_releases([bitrate, strong, weak], canonical_key)
    assert len(forward) == len(backward) == 1
    assert forward[0].fel_evidence.evidence_type == "google-sheet-row"
    assert backward[0].fel_evidence.evidence_type == "google-sheet-row"
