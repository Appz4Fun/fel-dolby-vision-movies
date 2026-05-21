from models import FelEvidence, FelRelease, release_from_dict
from normalize import normalize_audio, normalize_title


def test_normalize_audio_known_aliases():
    assert normalize_audio("Dolby TrueHD Atmos") == ["TrueHD Atmos"]
    assert normalize_audio("Atmos (TrueHD)") == ["TrueHD Atmos"]
    assert normalize_audio("Dolby Digital Plus Atmos / E-AC3 Atmos") == ["DD+ Atmos"]
    assert normalize_audio("DTS-HD Master Audio 7.1") == ["DTS-HD MA"]
    assert normalize_audio("DTS-X") == ["DTS:X"]


def test_normalize_audio_preserves_multiple_distinct_formats():
    assert normalize_audio("English TrueHD Atmos; Japanese DTS-HD MA") == [
        "TrueHD Atmos",
        "DTS-HD MA",
    ]


def test_unknown_audio_returns_cleaned_value():
    assert normalize_audio("PCM 2.0 Mono") == ["PCM 2.0 Mono"]


def test_generic_forum_prose_does_not_become_audio_format():
    assert (
        normalize_audio(
            "Here are 456 verified P7 FEL dolby vision films in my collection."
        )
        == []
    )


def test_title_normalization_collapses_spacing():
    assert normalize_title("  The   Matrix\tReloaded  ") == "The Matrix Reloaded"


def test_fel_release_publish_gate_and_unknowns():
    evidence = FelEvidence(
        source_url="https://example.test/thread",
        quote="The Matrix is Profile 7 FEL",
        evidence_type="sentence",
    )
    release = FelRelease(movie_title="The Matrix", fel_evidence=evidence)
    assert release.fel_confirmed is True
    assert release.release_date == "Unknown"
    assert release.studio == "Unknown"
    assert release.english_audio == "Unknown"


def test_felrelease_round_trips_new_enrichment_fields():
    original = FelRelease(
        movie_title="Nosferatu",
        release_date="2024",
        fel_evidence=FelEvidence(
            source_url="https://example.test/nosferatu",
            quote="Nosferatu (2024) FEL",
            evidence_type="fel-list",
        ),
        tmdb_id="426063",
        imdb_id="tt5040012",
        poster_path="data/posters/426063.jpg",
        release_url="https://www.themoviedb.org/movie/426063",
    )

    data = original.to_dict()
    assert data["tmdb_id"] == "426063"
    assert data["imdb_id"] == "tt5040012"
    assert data["poster_path"] == "data/posters/426063.jpg"
    assert data["release_url"] == "https://www.themoviedb.org/movie/426063"

    restored = release_from_dict(data)
    assert restored.tmdb_id == "426063"
    assert restored.release_url == original.release_url
    assert restored.source_url == "https://example.test/nosferatu"
    assert restored.fel_evidence.evidence_type == "fel-list"
