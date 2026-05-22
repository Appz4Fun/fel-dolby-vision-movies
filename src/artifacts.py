from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from merge import canonical_key, dedupe_releases, tmdb_key
from models import UNKNOWN, FelRelease, release_from_dict


RELEASE_GROUP_KEYS = frozenset({"group", "release_group", "release group"})
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

    merged = dedupe_releases([*existing, *releases], canonical_key)
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
    (root / "README.md").write_text(_render_readme(sorted_releases), encoding="utf-8")
    (root / "links.md").write_text(_render_links(sorted_releases), encoding="utf-8")
    return sorted_releases


def _is_stale_google_sheet_release(release: FelRelease) -> bool:
    if release.fel_evidence.evidence_type != "google-sheet-row":
        return False
    if STALE_SHEET_COLLECTION_RE.search(release.movie_title):
        return True
    return release.release_date == UNKNOWN and bool(
        STALE_DOTTED_YEAR_TITLE_RE.search(release.movie_title)
    )


def _sort_key(release: FelRelease) -> tuple[int, str]:
    if release.release_date == UNKNOWN:
        return (1, "")
    return (0, _invert_date_text(release.release_date))


def _invert_date_text(value: str) -> str:
    return "".join(chr(255 - ord(character)) for character in value)


def _render_readme(releases: list[FelRelease]) -> str:
    lines = [
        "# FEL List",
        "",
        "Confirmed Dolby Vision Profile 7 FEL physical media releases.",
        "",
        "| Release Date | Movie | Poster | Studio | Audio | English Audio | HDR | "
        "Additional | BR Link | Src Link | TMDB |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for release in releases:
        poster = (
            f"![{release.movie_title}]({release.poster_path})"
            if release.poster_path
            else ""
        )
        tmdb = f"[TMDB]({release.release_url})" if release.release_url else ""
        bluray = f"[BR]({release.bluray_url})" if release.bluray_url else ""
        lines.append(
            "| "
            + " | ".join(
                [
                    release.release_date,
                    release.movie_title,
                    poster,
                    release.studio,
                    ", ".join(release.audio_formats) or UNKNOWN,
                    release.english_audio,
                    ", ".join(release.hdr_formats) or UNKNOWN,
                    _render_additional(release.additional_characteristics),
                    bluray,
                    f"[src]({release.source_url})",
                    tmdb,
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _render_additional(additional: dict[str, Any]) -> str:
    visible_items = [
        (key, value)
        for key, value in additional.items()
        if key.lower().replace("-", "_") not in RELEASE_GROUP_KEYS
        and key != "source_urls"
    ]
    if not visible_items:
        return UNKNOWN
    return ", ".join(f"{key}: {value}" for key, value in visible_items)


def _render_links(releases: list[FelRelease]) -> str:
    seen: list[str] = []
    for release in releases:
        candidates = [
            release.source_url,
            *release.additional_characteristics.get("source_urls", []),
        ]
        for url in candidates:
            if url and url not in seen:
                seen.append(url)
    lines = ["# Source Links", ""]
    lines.extend(f"- {url}" for url in seen)
    return "\n".join(lines).rstrip() + "\n"
