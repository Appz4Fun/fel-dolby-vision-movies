from models import FelEvidence, FelRelease, release_from_dict
from normalize import normalize_audio, normalize_fel_title, normalize_title


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


def test_normalize_fel_title_strips_prefixes_and_aka():
    assert normalize_fel_title("L.E. The Northman") == "The Northman"
    assert normalize_fel_title("EDIT: Dune") == "Dune"
    assert normalize_fel_title("-- Sicario") == "Sicario"
    assert normalize_fel_title("Nightbreed AKA Cabal") == "Nightbreed"
    assert normalize_fel_title("  Drop  ") == "Drop"
    assert normalize_fel_title(",- ") == ""


def test_normalize_fel_title_preserves_known_numeric_titles():
    assert normalize_fel_title("101 Dalmatians") == "101 Dalmatians"
    assert normalize_fel_title("127 Hours") == "127 Hours"
    assert normalize_fel_title("300 Rise of an Empire") == "300 Rise of an Empire"
    assert normalize_fel_title("365 Days") == "365 Days"


def test_normalize_fel_title_strips_trailing_disambiguator_parentheticals():
    # Wiki-style medium disambiguators never appear on the actual disc title
    # and defeat both TMDB search and duplicate merging ("Hamilton (musical)"
    # shipped as a second, unresolved Hamilton row).
    assert normalize_fel_title("Hamilton (musical)") == "Hamilton"
    assert normalize_fel_title("The Old Guard (film)") == "The Old Guard"
    assert normalize_fel_title("Shogun (TV series)") == "Shogun"
    assert normalize_fel_title("Dune (2021 film)") == "Dune"
    assert normalize_fel_title("The Fugitive (Movie)") == "The Fugitive"


def test_normalize_fel_title_preserves_meaningful_parentheticals():
    assert normalize_fel_title("(500) Days of Summer") == "(500) Days of Summer"
    assert (
        normalize_fel_title("Only the Brave (No Way Out)")
        == "Only the Brave (No Way Out)"
    )
    assert (
        normalize_fel_title("Apocalypse Now (Redux Cut)")
        == "Apocalypse Now (Redux Cut)"
    )


def test_normalize_fel_title_decodes_html_entities():
    assert normalize_fel_title("Fast &amp; Furious") == "Fast & Furious"
    assert normalize_fel_title("Hansel &amp; Gretel") == "Hansel & Gretel"
    assert normalize_fel_title("It&#39;s Alive") == "It's Alive"


def test_felrelease_round_trips_bluray_fields():
    original = FelRelease(
        movie_title="Nosferatu",
        release_date="2024-12-25",
        fel_evidence=FelEvidence(
            source_url="https://example.test/n", quote="q", evidence_type="fel-list"
        ),
        bluray_url="https://www.blu-ray.com/movies/Nosferatu-4K-Blu-ray/400000/",
        bluray_release_date="2025-02-18",
        hdr_formats=["Dolby Vision", "HDR10"],
        audio_languages=["English", "French"],
    )
    data = original.to_dict()
    assert data["bluray_url"].endswith("/400000/")
    assert data["bluray_release_date"] == "2025-02-18"
    assert data["hdr_formats"] == ["Dolby Vision", "HDR10"]
    assert data["audio_languages"] == ["English", "French"]

    restored = release_from_dict(data)
    assert restored.hdr_formats == ["Dolby Vision", "HDR10"]
    assert restored.audio_languages == ["English", "French"]
    assert restored.bluray_url == original.bluray_url
    assert restored.bluray_release_date == "2025-02-18"


def test_felrelease_media_type_defaults_to_movie():
    evidence = FelEvidence(
        source_url="https://example.test/thread",
        quote="The Matrix is Profile 7 FEL",
        evidence_type="sentence",
    )
    release = FelRelease(movie_title="The Matrix", fel_evidence=evidence)
    assert release.media_type == "movie"


def test_felrelease_round_trips_media_type():
    original = FelRelease(
        movie_title="Game of Thrones: The Complete First Season",
        release_date="2011",
        fel_evidence=FelEvidence(
            source_url="https://example.test/got",
            quote="GoT S1 is FEL",
            evidence_type="fel-list",
        ),
        tmdb_id="1399",
        imdb_id="tt0944947",
        media_type="tv",
    )

    data = original.to_dict()
    assert data["media_type"] == "tv"

    restored = release_from_dict(data)
    assert restored.media_type == "tv"


def test_release_from_dict_defaults_absent_media_type_to_movie():
    # Rows serialized before media typing existed carry no media_type key
    # (and must never carry an empty one); both shapes load as movie rows,
    # matching the movie-era assumption those rows were written under.
    base = {
        "movie_title": "Heat",
        "fel_evidence": {
            "source_url": "https://example.test/heat",
            "quote": "Heat is FEL",
            "evidence_type": "fel-list",
        },
    }
    assert release_from_dict(base).media_type == "movie"
    assert release_from_dict({**base, "media_type": ""}).media_type == "movie"


def test_tmdb_identity_namespaces_ids_by_media_type():
    evidence = FelEvidence(
        source_url="https://example.test/x",
        quote="x",
        evidence_type="fel-list",
    )
    unresolved = FelRelease(movie_title="X", fel_evidence=evidence)
    movie = FelRelease(
        movie_title="X", fel_evidence=evidence, tmdb_id="1399", media_type="movie"
    )
    tv = FelRelease(
        movie_title="X", fel_evidence=evidence, tmdb_id="1399", media_type="tv"
    )

    assert unresolved.tmdb_identity == ""
    assert movie.tmdb_identity == "movie/1399"
    assert tv.tmdb_identity == "tv/1399"
    assert movie.tmdb_identity != tv.tmdb_identity
