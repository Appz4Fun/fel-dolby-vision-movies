from pathlib import Path
import json

import release_delta
from models import FelEvidence, FelRelease


def release(
    title: str,
    date: str = "2026",
    source_url: str = "https://forum.example.test/thread",
    tmdb_id: str = "",
    imdb_id: str = "",
) -> FelRelease:
    return FelRelease(
        movie_title=title,
        release_date=date,
        tmdb_id=tmdb_id,
        imdb_id=imdb_id,
        fel_evidence=FelEvidence(
            source_url=source_url,
            quote=f"{title} is confirmed Profile 7 FEL",
            evidence_type="forum-post",
        ),
    )


def write_releases(path: Path, releases: list[FelRelease]) -> None:
    path.write_text(
        json.dumps([item.to_dict() for item in releases]) + "\n",
        encoding="utf-8",
    )


def test_added_releases_ignores_existing_titles_and_metadata_refreshes():
    base = [release("Dune", "2021", "https://forum.example.test/old", tmdb_id="438631")]
    head = [
        release(
            "Dune: Part One",
            "2021-09-03",
            "https://forum.example.test/newer",
            tmdb_id="438631",
        ),
        release("Alien", "1979", "https://forum.example.test/alien"),
    ]

    additions = release_delta.added_releases(base, head)

    assert [item.movie_title for item in additions] == ["Alien"]


def test_added_releases_matches_existing_by_canonical_title_when_ids_are_added_later():
    base = [release("The Matrix", "1999")]
    head = [
        release(
            "The Matrix",
            "1999-03-31",
            "https://forum.example.test/matrix-enriched",
            tmdb_id="603",
        )
    ]

    assert release_delta.added_releases(base, head) == []


def test_added_releases_matches_existing_by_imdb_id():
    base = [release("Heat", "1995", imdb_id="tt0113277")]
    head = [release("Heat Director's Definitive Edition", "1995", imdb_id="tt0113277")]

    assert release_delta.added_releases(base, head) == []


def test_added_releases_matches_same_title_same_bluray_url_across_years():
    base = [release("Sisu", "2022", tmdb_id="840326")]
    base[0].bluray_url = "https://www.blu-ray.com/movies/Sisu-4K-Blu-ray/333344/"
    head = [release("Sisu", "2023", tmdb_id="935906")]
    head[0].bluray_url = "https://www.blu-ray.com/movies/Sisu-4K-Blu-ray/333344/"

    assert release_delta.added_releases(base, head) == []


def test_build_pr_body_lists_added_releases_with_evidence_links():
    additions = [
        release("Alien", "1979", "https://forum.example.test/alien", tmdb_id="348"),
        release("Heat", "1995", "https://forum.example.test/heat", imdb_id="tt0113277"),
    ]

    body = release_delta.build_pr_body(additions)

    assert "Adds 2 FEL release entries" in body
    assert (
        "| Alien | 1979 | [source](https://forum.example.test/alien) | forum-post |"
    ) in body
    assert (
        "| Heat | 1995 | [source](https://forum.example.test/heat) | forum-post |"
    ) in body
    assert "Automated daily scrape" in body


def test_write_pr_summary_outputs_pending_and_new_counts(tmp_path: Path):
    base_path = tmp_path / "base.json"
    previous_path = tmp_path / "previous.json"
    head_path = tmp_path / "head.json"
    body_path = tmp_path / "body.md"
    github_output_path = tmp_path / "github-output.txt"

    previous = [release("Alien", "1979", "https://forum.example.test/alien")]
    head = [
        *previous,
        release("Heat", "1995", "https://forum.example.test/heat"),
    ]
    write_releases(base_path, [])
    write_releases(previous_path, previous)
    write_releases(head_path, head)

    summary = release_delta.write_pr_summary(
        base_releases_path=base_path,
        previous_releases_path=previous_path,
        head_releases_path=head_path,
        body_output_path=body_path,
        github_output_path=github_output_path,
    )

    assert summary.pending_release_count == 2
    assert summary.new_release_count == 1
    assert "| Alien | 1979 |" in body_path.read_text(encoding="utf-8")
    assert "| Heat | 1995 |" in body_path.read_text(encoding="utf-8")
    assert github_output_path.read_text(encoding="utf-8").splitlines() == [
        "pending_release_count=2",
        "new_release_count=1",
        "has_new_releases=true",
    ]
