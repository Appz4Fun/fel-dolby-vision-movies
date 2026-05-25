from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re

from merge import canonical_key, dedupe_releases, title_bluray_key, tmdb_key
from models import UNKNOWN, FelRelease, release_from_dict
from normalize import normalize_fel_title


STALE_SHEET_COLLECTION_RE = re.compile(
    r"\b(?:collection|trilogy|duology|quadrilogy|tetralogy|saga|box\s*set|boxset)$",
    re.IGNORECASE,
)
STALE_DOTTED_YEAR_TITLE_RE = re.compile(r"[._-](?:19|20)\d{2}[.\s_-]*$")


def publish_outputs(
    releases: list[FelRelease], output_dir: Path | str = "."
) -> list[FelRelease]:
    from dashboard import build_dashboard

    root = Path(output_dir)
    sorted_releases = write_artifacts(releases, output_dir=root)
    build_dashboard(
        sorted_releases,
        output_dir=root / "dist",
        poster_src=root / "data" / "posters",
    )
    return sorted_releases


def write_artifacts(
    releases: list[FelRelease], output_dir: Path | str = "."
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

    merged = dedupe_releases([*existing, *releases], canonical_key)
    merged = dedupe_releases(merged, title_bluray_key)
    merged = dedupe_releases(merged, tmdb_key)
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
    _prune_unreferenced_posters(data_dir / "posters", sorted_releases)
    return sorted_releases


def _prune_unreferenced_posters(poster_dir: Path, releases: list[FelRelease]) -> None:
    if not poster_dir.exists():
        return
    referenced = {
        Path(release.poster_path).name for release in releases if release.poster_path
    }
    for poster_path in poster_dir.iterdir():
        if poster_path.is_file() and poster_path.name not in referenced:
            poster_path.unlink()


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


def _sort_key(release: FelRelease) -> tuple[int, str]:
    if release.release_date == UNKNOWN:
        return (1, "")
    return (0, _invert_date_text(release.release_date))


def _invert_date_text(value: str) -> str:
    return "".join(chr(255 - ord(character)) for character in value)
