import json
from pathlib import Path

from fel_dolby_vision_movies.artifacts import write_artifacts
from fel_dolby_vision_movies.models import FelEvidence, FelRelease


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
    assert "| Newer | Yes | 2026-05-01 | Unknown | TrueHD Atmos | Yes | Unknown |" in readme
    assert "Newer is Profile 7 FEL" not in readme


def test_links_contains_only_unique_source_urls(tmp_path: Path):
    write_artifacts([release("A", "2020"), release("A", "2020")], output_dir=tmp_path)

    links = (tmp_path / "links.md").read_text(encoding="utf-8")
    assert links.count("https://example.test/A") == 1
