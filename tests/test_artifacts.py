import json
from pathlib import Path

import artifacts
from artifacts import publish_outputs, write_artifacts
from models import FelEvidence, FelRelease


def release(title: str, date: str) -> FelRelease:
    return FelRelease(
        movie_title=title,
        release_date=date,
        studio="Unknown",
        audio_formats=["TrueHD Atmos"],
        english_audio="Yes",
        fel_evidence=FelEvidence(
            source_url=f"https://example.test/{title}",
            quote=f"{title} is Profile 7 FEL",
            evidence_type="fixture",
        ),
    )


def test_publish_outputs_writes_data_and_dashboard_from_releases(tmp_path: Path):
    sorted_releases = publish_outputs(
        [
            release("Older", "2020"),
            release("Newer", "2026-05-01"),
        ],
        output_dir=tmp_path,
    )

    assert [item.movie_title for item in sorted_releases] == ["Newer", "Older"]
    assert (tmp_path / "data/releases.json").exists()
    assert (tmp_path / "dist/index.html").exists()
    assert (tmp_path / "dist/releases.json").exists()


def test_write_artifacts_quarantines_unmatched_unknown_and_writes_review(
    tmp_path: Path,
):
    review_path = tmp_path / "review.json"
    write_artifacts(
        [
            release("Unknown Date", "Unknown"),
            release("Newer", "2026-05-01"),
            release("Older", "2020"),
        ],
        output_dir=tmp_path,
        review_output_path=review_path,
    )

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    assert [item["movie_title"] for item in data] == [
        "Newer",
        "Older",
    ]
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert review["merged_count"] == 0
    assert review["addition_count"] == 2
    assert review["review_count"] == 1
    assert review["items"][0]["reason"] == "missing-year-no-match"


def test_write_artifacts_merges_into_existing_releases_json(tmp_path: Path):
    write_artifacts([release("First", "2020")], output_dir=tmp_path)
    write_artifacts([release("Second", "2021")], output_dir=tmp_path)

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    titles = sorted(item["movie_title"] for item in data)
    assert titles == ["First", "Second"]


def test_write_artifacts_normalizes_existing_release_titles(tmp_path: Path):
    write_artifacts([release("281 Nobody", "2021")], output_dir=tmp_path)
    write_artifacts([release("Nobody", "2021")], output_dir=tmp_path)

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    assert [item["movie_title"] for item in data] == ["Nobody"]


def test_write_artifacts_dedupes_same_title_same_bluray_url_across_years(
    tmp_path: Path,
):
    existing = release("Sisu", "2022")
    existing.tmdb_id = "840326"
    existing.bluray_url = "https://www.blu-ray.com/movies/Sisu-4K-Blu-ray/333344/"
    incoming = release("Sisu", "2023")
    incoming.tmdb_id = "935906"
    incoming.bluray_url = "https://www.blu-ray.com/movies/Sisu-4K-Blu-ray/333344/"

    write_artifacts([existing], output_dir=tmp_path)
    write_artifacts([incoming], output_dir=tmp_path)

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    assert [(item["movie_title"], item["tmdb_id"]) for item in data] == [
        ("Sisu", "840326")
    ]


def test_write_artifacts_replaces_stale_rows_from_refreshed_sources(tmp_path: Path):
    stale = release("Rango.2011.", "Unknown")
    stale.fel_evidence = FelEvidence(
        source_url="https://docs.example.test/sheet",
        quote="Rango.2011. BD FEL",
        evidence_type="google-sheet-row",
    )
    preserved = release("Preserved", "2020")
    fresh = release("Rango", "2011")
    fresh.fel_evidence = FelEvidence(
        source_url="https://docs.example.test/sheet",
        quote="Rango.2011. BD FEL",
        evidence_type="google-sheet-row",
    )

    write_artifacts([stale, preserved], output_dir=tmp_path)
    write_artifacts([fresh], output_dir=tmp_path)

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    titles = sorted(item["movie_title"] for item in data)
    assert titles == ["Preserved", "Rango"]


def test_write_artifacts_filters_stale_google_sheet_shapes_only(tmp_path: Path):
    stale_collection = release("Godfather Trilogy", "Unknown")
    stale_collection.fel_evidence = FelEvidence(
        source_url="https://docs.example.test/sheet",
        quote="Godfather Trilogy BD FEL",
        evidence_type="google-sheet-row",
    )
    stale_dotted = release("Rango.2011.", "2011")
    stale_dotted.fel_evidence = FelEvidence(
        source_url="https://docs.example.test/sheet",
        quote="Rango.2011. BD FEL",
        evidence_type="google-sheet-row",
    )
    forum_collection = release("Godfather Trilogy", "1972")
    forum_collection.fel_evidence = FelEvidence(
        source_url="https://forum.example.test/post",
        quote="Godfather Trilogy confirmed by post",
        evidence_type="forum-post",
    )

    write_artifacts(
        [stale_collection, stale_dotted, forum_collection],
        output_dir=tmp_path,
    )

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    assert [(item["movie_title"], item["release_date"]) for item in data] == [
        ("Godfather Trilogy", "1972")
    ]


def test_write_artifacts_dedupes_by_tmdb_id(tmp_path: Path):
    first = release("Spelling One", "2021")
    first.tmdb_id = "777"
    second = release("Spelling Two", "2021")
    second.tmdb_id = "777"

    write_artifacts([first, second], output_dir=tmp_path)

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    assert sum(1 for item in data if item["tmdb_id"] == "777") == 1


def test_write_artifacts_preserves_same_tmdb_distinct_bluray_releases(
    tmp_path: Path,
):
    first = release("Game of Thrones: The Complete First Season", "2011")
    first.tmdb_id = "1399"
    first.bluray_url = (
        "https://www.blu-ray.com/movies/"
        "Game-of-Thrones-The-Complete-First-Season-4K-Blu-ray/202472/"
    )
    seventh = release("Game of Thrones: The Complete Seventh Season", "2017-07-16")
    seventh.tmdb_id = "1399"
    seventh.bluray_url = (
        "https://www.blu-ray.com/movies/"
        "Game-of-Thrones-The-Complete-Seventh-Season-4K-Blu-ray/272494/"
    )

    write_artifacts([first, seventh], output_dir=tmp_path)

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    assert [(item["movie_title"], item["release_date"]) for item in data] == [
        ("Game of Thrones: The Complete Seventh Season", "2017-07-16"),
        ("Game of Thrones: The Complete First Season", "2011"),
    ]
    assert {
        (item["movie_title"], item["bluray_url"])
        for item in data
        if item["tmdb_id"] == "1399"
    } == {
        (first.movie_title, first.bluray_url),
        (seventh.movie_title, seventh.bluray_url),
    }


def test_write_artifacts_keeps_same_title_year_different_bluray_urls(
    tmp_path: Path,
):
    first = release("Avatar", "2009")
    first.bluray_url = "https://www.blu-ray.com/movies/Avatar/1/"
    second = release("Avatar", "2009")
    second.bluray_url = "https://www.blu-ray.com/movies/Avatar/2/"

    write_artifacts([first, second], output_dir=tmp_path)

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    assert {item["bluray_url"] for item in data} == {
        first.bluray_url,
        second.bluray_url,
    }


def test_write_artifacts_merges_same_tmdb_when_existing_lacks_bluray(
    tmp_path: Path,
):
    existing = release("The Three Musketeers: Milady", "2023-12-13")
    existing.tmdb_id = "845111"
    existing.imdb_id = "tt12672620"
    existing.release_url = "https://www.themoviedb.org/movie/845111"

    incoming = release("Les Trois Mousquetaires: Milady", "2023-12-13")
    incoming.tmdb_id = "845111"
    incoming.imdb_id = "tt12672620"
    incoming.release_url = "https://www.themoviedb.org/movie/845111"
    incoming.bluray_url = (
        "https://www.blu-ray.com/movies/"
        "Les-Trois-Mousquetaires--Milady-4K-Blu-ray/347971/"
    )

    write_artifacts([existing], output_dir=tmp_path)
    write_artifacts([incoming], output_dir=tmp_path)

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    assert [(item["movie_title"], item["tmdb_id"]) for item in data] == [
        ("The Three Musketeers: Milady", "845111")
    ]
    assert data[0]["bluray_url"] == incoming.bluray_url


def test_write_artifacts_preserves_enriched_fields(tmp_path: Path):
    item = release("Enriched", "2024")
    item.tmdb_id = "111"
    item.poster_path = "data/posters/111.jpg"
    item.release_url = "https://www.themoviedb.org/movie/111"
    item.hdr_formats = ["Dolby Vision", "HDR10"]
    item.bluray_url = "https://www.blu-ray.com/movies/Enriched-4K-Blu-ray/9/"

    write_artifacts([item], output_dir=tmp_path)

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    assert len(data) == 1
    entry = data[0]
    assert entry["tmdb_id"] == "111"
    assert entry["poster_path"] == "data/posters/111.jpg"
    assert entry["hdr_formats"] == ["Dolby Vision", "HDR10"]
    assert entry["bluray_url"].endswith("/9/")


def test_write_artifacts_preserves_unreferenced_existing_poster_files(tmp_path: Path):
    poster_dir = tmp_path / "data/posters"
    poster_dir.mkdir(parents=True)
    referenced = poster_dir / "111.jpg"
    existing_unreferenced = poster_dir / "222.jpg"
    referenced.write_bytes(b"referenced")
    existing_unreferenced.write_bytes(b"existing")

    item = release("Enriched", "2024")
    item.tmdb_id = "111"
    item.poster_path = "data/posters/111.jpg"

    write_artifacts([item], output_dir=tmp_path)

    assert referenced.exists()
    assert existing_unreferenced.exists()


def test_prune_unreferenced_posters_removes_only_candidate_files(tmp_path: Path):
    poster_dir = tmp_path / "data/posters"
    poster_dir.mkdir(parents=True)
    referenced = poster_dir / "111.jpg"
    stale_candidate = poster_dir / "222.jpg"
    protected_unreferenced = poster_dir / "333.jpg"
    referenced.write_bytes(b"referenced")
    stale_candidate.write_bytes(b"stale")
    protected_unreferenced.write_bytes(b"protected")

    item = release("Enriched", "2024")
    item.poster_path = "data/posters/111.jpg"

    removed = artifacts.prune_unreferenced_posters(
        poster_dir,
        [item],
        candidate_names=["111.jpg", "222.jpg"],
    )

    assert removed == [stale_candidate]
    assert referenced.exists()
    assert not stale_candidate.exists()
    assert protected_unreferenced.exists()


def test_prune_unreferenced_posters_noops_when_poster_dir_is_missing(tmp_path: Path):
    removed = artifacts.prune_unreferenced_posters(
        tmp_path / "missing",
        [release("Enriched", "2024")],
        candidate_names=["222.jpg"],
    )

    assert removed == []
