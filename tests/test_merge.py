from merge import canonical_key, dedupe_releases, merge_releases, tmdb_key
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
    weak = make("Dune", "2021", evidence_type="fel-list")
    strong = make("Dune", "2021", evidence_type="reddit-list")
    assert merge_releases(weak, strong).fel_evidence.evidence_type == "reddit-list"
    assert merge_releases(strong, weak).fel_evidence.evidence_type == "reddit-list"


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


def test_dedupe_is_order_independent_for_strong_evidence():
    weak = make("Sicario", "2015", evidence_type="fel-list")
    strong = make("Sicario", "2015", evidence_type="google-sheet-row")
    bitrate = make("Sicario", "2015", evidence_type="fel-bitrate-list")
    forward = dedupe_releases([weak, strong, bitrate], canonical_key)
    backward = dedupe_releases([bitrate, strong, weak], canonical_key)
    assert len(forward) == len(backward) == 1
    assert forward[0].fel_evidence.evidence_type == "google-sheet-row"
    assert backward[0].fel_evidence.evidence_type == "google-sheet-row"
