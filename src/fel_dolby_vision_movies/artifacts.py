from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import UNKNOWN, FelRelease


RELEASE_GROUP_KEYS = frozenset({"group", "release_group", "release group"})


def publish_outputs(
    releases: list[FelRelease], output_dir: Path | str = "."
) -> list[FelRelease]:
    from .dashboard import build_dashboard

    root = Path(output_dir)
    sorted_releases = write_artifacts(releases, output_dir=root)
    build_dashboard(sorted_releases, output_dir=root / "dist")
    return sorted_releases


def write_artifacts(
    releases: list[FelRelease], output_dir: Path | str = "."
) -> list[FelRelease]:
    root = Path(output_dir)
    sorted_releases = sorted(releases, key=_sort_key)

    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "releases.json").write_text(
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
        "| Movie | FEL | Release Date | Studio | Audio | English Audio | Additional | Source |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for release in releases:
        lines.append(
            "| "
            + " | ".join(
                [
                    release.movie_title,
                    "Yes",
                    release.release_date,
                    release.studio,
                    ", ".join(release.audio_formats) or UNKNOWN,
                    release.english_audio,
                    _render_additional(release.additional_characteristics),
                    f"[source]({release.source_url})",
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
    ]
    if not visible_items:
        return UNKNOWN
    return ", ".join(f"{key}: {value}" for key, value in visible_items)


def _render_links(releases: list[FelRelease]) -> str:
    urls = list(dict.fromkeys(release.source_url for release in releases))
    lines = ["# Source Links", ""]
    lines.extend(f"- {url}" for url in urls)
    return "\n".join(lines).rstrip() + "\n"
