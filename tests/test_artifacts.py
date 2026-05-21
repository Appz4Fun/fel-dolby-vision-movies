import json
from pathlib import Path

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


def test_publish_outputs_writes_artifacts_and_dashboard_from_releases(tmp_path: Path):
    sorted_releases = publish_outputs(
        [
            release("Older", "2020"),
            release("Newer", "2026-05-01"),
        ],
        output_dir=tmp_path,
    )

    assert [item.movie_title for item in sorted_releases] == ["Newer", "Older"]
    assert (tmp_path / "README.md").exists()
    assert (tmp_path / "links.md").exists()
    assert (tmp_path / "data/releases.json").exists()
    assert (tmp_path / "dist/index.html").exists()
    assert (tmp_path / "dist/releases.json").exists()


def test_write_artifacts_sorts_known_dates_newest_first_unknown_last(
    tmp_path: Path,
):
    write_artifacts(
        [
            release("Unknown Date", "Unknown"),
            release("Newer", "2026-05-01"),
            release("Older", "2020"),
        ],
        output_dir=tmp_path,
    )

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    assert [item["movie_title"] for item in data] == [
        "Newer",
        "Older",
        "Unknown Date",
    ]
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "| Newer |" in readme
    assert "[source](https://example.test/Newer)" in readme
    assert "Newer is Profile 7 FEL" not in readme


def test_write_artifacts_merges_into_existing_releases_json(tmp_path: Path):
    write_artifacts([release("First", "2020")], output_dir=tmp_path)
    write_artifacts([release("Second", "2021")], output_dir=tmp_path)

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    titles = sorted(item["movie_title"] for item in data)
    assert titles == ["First", "Second"]


def test_write_artifacts_renders_poster_and_tmdb_columns(tmp_path: Path):
    item = release("Enriched", "2024")
    item.tmdb_id = "111"
    item.poster_path = "data/posters/111.jpg"
    item.release_url = "https://www.themoviedb.org/movie/111"

    write_artifacts([item], output_dir=tmp_path)

    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "![Enriched](data/posters/111.jpg)" in readme
    assert "[TMDB](https://www.themoviedb.org/movie/111)" in readme


def test_links_contains_only_unique_source_urls(tmp_path: Path):
    write_artifacts([release("A", "2020"), release("A", "2020")], output_dir=tmp_path)

    links = (tmp_path / "links.md").read_text(encoding="utf-8")
    assert links.count("https://example.test/A") == 1


def test_links_for_empty_release_set_has_no_extra_blank_line(tmp_path: Path):
    write_artifacts([], output_dir=tmp_path)

    assert (tmp_path / "links.md").read_text(encoding="utf-8") == "# Source Links\n"


def test_readme_omits_release_group_metadata(tmp_path: Path):
    item = release("A", "2020")
    item.additional_characteristics = {
        "release_group": "GROUP",
        "disc_count": 2,
    }

    write_artifacts([item], output_dir=tmp_path)

    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "disc_count: 2" in readme
    assert "release_group" not in readme
    assert "GROUP" not in readme
