from merge import canonical_key, dedupe_releases, merge_releases
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


def test_dedupe_releases_merges_same_canonical_key():
    releases = [
        make("Sicario", "2015"),
        make("sicario", "2015"),
        make("Arrival", "2016"),
    ]
    deduped = dedupe_releases(releases, canonical_key)
    assert [r.movie_title for r in deduped] == ["Sicario", "Arrival"]
