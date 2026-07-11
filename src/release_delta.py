from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from models import FelRelease, release_from_dict
from reconcile import reconcile_releases


@dataclass(frozen=True)
class ReleaseDeltaSummary:
    pending_release_count: int
    new_release_count: int


def load_releases(path: Path) -> list[FelRelease]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [release_from_dict(item) for item in raw]


def added_releases(
    base_releases: list[FelRelease], head_releases: list[FelRelease]
) -> list[FelRelease]:
    return reconcile_releases(base_releases, head_releases).additions


def build_pr_body(additions: list[FelRelease]) -> str:
    lines = [
        "## FEL Release Refresh",
        "",
        f"Adds {len(additions)} FEL release entries to `data/releases.json`.",
        "",
        "| Title | Release date | Evidence source | Evidence type |",
        "| --- | --- | --- | --- |",
    ]
    for release in additions:
        lines.append(
            "| "
            f"{_table_cell(release.movie_title)} | "
            f"{_table_cell(release.release_date)} | "
            f"{_source_link(release.source_url)} | "
            f"{_table_cell(release.fel_evidence.evidence_type)} |"
        )
    lines.extend(
        [
            "",
            "Automated daily scrape. The PR body is regenerated from the full "
            "branch diff against `main`, so this list grows until the PR is merged.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_pr_summary(
    base_releases_path: Path,
    previous_releases_path: Path,
    head_releases_path: Path,
    body_output_path: Path,
    github_output_path: Path | None = None,
) -> ReleaseDeltaSummary:
    base_releases = load_releases(base_releases_path)
    previous_releases = load_releases(previous_releases_path)
    head_releases = load_releases(head_releases_path)

    pending_additions = added_releases(base_releases, head_releases)
    new_additions = added_releases(previous_releases, head_releases)

    body_output_path.write_text(build_pr_body(pending_additions), encoding="utf-8")
    summary = ReleaseDeltaSummary(
        pending_release_count=len(pending_additions),
        new_release_count=len(new_additions),
    )
    if github_output_path is not None:
        github_output_path.write_text(
            "\n".join(
                [
                    f"pending_release_count={summary.pending_release_count}",
                    f"new_release_count={summary.new_release_count}",
                    f"has_new_releases={str(summary.new_release_count > 0).lower()}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
    return summary


def _source_link(url: str) -> str:
    escaped_url = url.replace(")", "%29")
    return f"[source]({escaped_url})"


def _table_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
