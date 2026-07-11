from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re
from typing import Iterable

from merge import dedupe_releases, title_bluray_key
from models import UNKNOWN, FelRelease, release_from_dict
from normalize import normalize_fel_title
from reconcile import ReconciliationResult, reconcile_releases


STALE_SHEET_COLLECTION_RE = re.compile(
    r"\b(?:collection|trilogy|duology|quadrilogy|tetralogy|saga|box\s*set|boxset)$",
    re.IGNORECASE,
)
STALE_DOTTED_YEAR_TITLE_RE = re.compile(r"[._-](?:19|20)\d{2}[.\s_-]*$")


def publish_outputs(
    releases: list[FelRelease],
    output_dir: Path | str = ".",
    review_output_path: Path | str | None = None,
) -> list[FelRelease]:
    from dashboard import build_dashboard

    root = Path(output_dir)
    sorted_releases = write_artifacts(
        releases, output_dir=root, review_output_path=review_output_path
    )
    build_dashboard(
        sorted_releases,
        output_dir=root / "dist",
        poster_src=root / "data" / "posters",
    )
    return sorted_releases


def write_artifacts(
    releases: list[FelRelease],
    output_dir: Path | str = ".",
    review_output_path: Path | str | None = None,
) -> list[FelRelease]:
    root = Path(output_dir)
    data_dir = root / "data"
    releases_path = data_dir / "releases.json"

    existing: list[FelRelease] = []
    if releases_path.exists():
        existing = [
            release_from_dict(item)
            for item in json.loads(releases_path.read_text(encoding="utf-8"))
        ]

    existing = [
        release for release in existing if not _is_stale_google_sheet_release(release)
    ]
    existing = _normalize_release_titles(existing)
    releases = _normalize_release_titles(releases)
    releases = [
        release for release in releases if not _is_stale_google_sheet_release(release)
    ]

    reconciliation = reconcile_releases(existing, releases)
    # Reconciliation is edition-aware; only collapse exact title/URL duplicates
    # defensively. A title/year-only dedupe would erase distinct physical cuts.
    merged = dedupe_releases(reconciliation.releases, title_bluray_key)
    _enforce_ai_source_label(merged)
    sorted_releases = sorted(merged, key=_sort_key)

    data_dir.mkdir(parents=True, exist_ok=True)
    releases_path.write_text(
        json.dumps(
            [release.to_dict() for release in sorted_releases],
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    if review_output_path is not None:
        _write_review_output(Path(review_output_path), reconciliation)
    print(
        "reconciliation complete; "
        f"merged={reconciliation.merged_count} "
        f"additions={len(reconciliation.additions)} "
        f"review={len(reconciliation.review_items)}"
    )
    return sorted_releases


def _write_review_output(path: Path, result: ReconciliationResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "merged": result.merged_count,
        "additions": len(result.additions),
        "review": len(result.review_items),
        "items": [
            {
                "reason": item.reason,
                "candidate": item.release.to_dict(),
                "candidate_titles": list(item.candidate_titles),
            }
            for item in result.review_items
        ],
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def prune_unreferenced_posters(
    poster_dir: Path,
    releases: list[FelRelease],
    candidate_names: Iterable[str],
) -> list[Path]:
    if not poster_dir.exists():
        return []
    referenced = {
        Path(release.poster_path).name for release in releases if release.poster_path
    }
    removed: list[Path] = []
    for candidate_name in dict.fromkeys(Path(name).name for name in candidate_names):
        poster_path = poster_dir / candidate_name
        if poster_path.is_file() and poster_path.name not in referenced:
            poster_path.unlink()
            removed.append(poster_path)
    return removed


def _enforce_ai_source_label(releases: list[FelRelease]) -> None:
    # AGENTS.md contract: AI-discovered evidence must publish with source_label
    # "codex-ai". A prior merge that promoted ai-extracted evidence over weak
    # list evidence could leave a stale label (e.g. "FEL.txt"), so reassert the
    # invariant on every publish over the fully merged set.
    for release in releases:
        if release.fel_evidence.evidence_type == "ai-extracted":
            release.source_label = "codex-ai"


def _normalize_release_titles(releases: list[FelRelease]) -> list[FelRelease]:
    normalized: list[FelRelease] = []
    for release in releases:
        title = normalize_fel_title(release.movie_title) or release.movie_title
        normalized.append(replace(release, movie_title=title))
    return normalized


def _is_stale_google_sheet_release(release: FelRelease) -> bool:
    if release.fel_evidence.evidence_type != "google-sheet-row":
        return False
    if STALE_SHEET_COLLECTION_RE.search(release.movie_title):
        return True
    return bool(STALE_DOTTED_YEAR_TITLE_RE.search(release.movie_title))


_BARE_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
_BARE_YEAR_MONTH_RE = re.compile(r"^(?:19|20)\d{2}-\d{2}$")


def _normalize_sort_date(value: str) -> str:
    # Pad bare year / year-month so they sort AFTER full dates of the same year
    # under newest-first ordering ('0' inverts higher than any real digit, so a
    # padded "2023-00-00" reads as older than every "2023-MM-DD").
    if _BARE_YEAR_RE.match(value):
        return f"{value}-00-00"
    if _BARE_YEAR_MONTH_RE.match(value):
        return f"{value}-00"
    return value


def _sort_key(release: FelRelease) -> tuple[int, str]:
    if release.release_date == UNKNOWN:
        return (1, "")
    return (0, _invert_date_text(_normalize_sort_date(release.release_date)))


def _invert_date_text(value: str) -> str:
    return "".join(chr(255 - ord(character)) for character in value)
