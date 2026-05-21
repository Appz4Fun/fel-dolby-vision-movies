from __future__ import annotations

from collections.abc import Callable, Iterable
import re
import unicodedata

from models import UNKNOWN, FelEvidence, FelRelease


_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_SPECIFIC_EVIDENCE = ("google-sheet-row", "forum-post", "reddit-list")


def canonical_title_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    normalized = normalized.casefold().replace("&", " and ")
    normalized = re.sub(r"['`´']", "'", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _year(value: str) -> str:
    match = _YEAR_RE.search(value or "")
    return match.group(0) if match else ""


def canonical_key(release: FelRelease) -> tuple[str, str]:
    return (canonical_title_key(release.movie_title), _year(release.release_date))


def tmdb_key(release: FelRelease) -> tuple[str, str]:
    if release.tmdb_id:
        return ("tmdb", release.tmdb_id)
    return canonical_key(release)


def _prefer_known(left: str, right: str) -> str:
    if left and left != UNKNOWN:
        return left
    return right


def _prefer_date(left: str, right: str) -> str:
    if "-" in left:
        return left
    if "-" in right:
        return right
    return _prefer_known(left, right)


def _prefer_title(left: str, right: str) -> str:
    if left.count(".") != right.count("."):
        return left if left.count(".") < right.count(".") else right
    return left if len(left) >= len(right) else right


def _prefer_evidence(left: FelEvidence, right: FelEvidence) -> FelEvidence:
    if right.evidence_type in _SPECIFIC_EVIDENCE and left.evidence_type not in _SPECIFIC_EVIDENCE:
        return right
    return left


def merge_releases(base: FelRelease, other: FelRelease) -> FelRelease:
    additional = dict(base.additional_characteristics)
    for key, value in other.additional_characteristics.items():
        if key == "source_urls":
            existing = list(additional.get("source_urls", []))
            additional["source_urls"] = list(dict.fromkeys([*existing, *value]))
        elif key not in additional:
            additional[key] = value
    return FelRelease(
        movie_title=_prefer_title(base.movie_title, other.movie_title),
        fel_evidence=_prefer_evidence(base.fel_evidence, other.fel_evidence),
        release_date=_prefer_date(base.release_date, other.release_date),
        studio=_prefer_known(base.studio, other.studio),
        audio_formats=list(dict.fromkeys([*base.audio_formats, *other.audio_formats])),
        english_audio=_prefer_known(base.english_audio, other.english_audio),
        additional_characteristics=additional,
        source_label=_prefer_known(base.source_label, other.source_label),
        collected_at=max(base.collected_at, other.collected_at),
        fel_confirmed=base.fel_confirmed or other.fel_confirmed,
        tmdb_id=base.tmdb_id or other.tmdb_id,
        imdb_id=base.imdb_id or other.imdb_id,
        poster_path=base.poster_path or other.poster_path,
        release_url=base.release_url or other.release_url,
    )


def dedupe_releases(
    releases: Iterable[FelRelease],
    key_func: Callable[[FelRelease], tuple[str, str]],
) -> list[FelRelease]:
    grouped: dict[tuple[str, str], FelRelease] = {}
    order: list[tuple[str, str]] = []
    for release in releases:
        key = key_func(release)
        if key in grouped:
            grouped[key] = merge_releases(grouped[key], release)
        else:
            grouped[key] = release
            order.append(key)
    return [grouped[key] for key in order]
