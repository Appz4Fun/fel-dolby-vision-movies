from __future__ import annotations

from collections.abc import Callable, Iterable
import re
import unicodedata

from models import UNKNOWN, FelEvidence, FelRelease


_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_FULL_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Evidence types that are mere list memberships with synthesized quotes.
# Any other evidence type (a real scraped quote) is preferred over these.
_WEAK_EVIDENCE = ("fel-list", "fel-bitrate-list")


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
    if _FULL_DATE_RE.fullmatch(left):
        return left
    if _FULL_DATE_RE.fullmatch(right):
        return right
    return _prefer_known(left, right)


def _prefer_title(left: str, right: str) -> str:
    if left.count(".") != right.count("."):
        return left if left.count(".") < right.count(".") else right
    return left if len(left) >= len(right) else right


def _prefer_evidence(left: FelEvidence, right: FelEvidence) -> FelEvidence:
    left_weak = left.evidence_type in _WEAK_EVIDENCE
    right_weak = right.evidence_type in _WEAK_EVIDENCE
    if left_weak and not right_weak:
        return right
    return left


def _prefer_recent(left: str, right: str) -> str:
    candidates = [value for value in (left, right) if value and value != UNKNOWN]
    return max(candidates) if candidates else UNKNOWN


def merge_releases(base: FelRelease, other: FelRelease) -> FelRelease:
    # dedupe_releases folds left: merge(merge(a, b), c). The _prefer_* rules are
    # deterministic but order-sensitive for ties (equal-length titles, two full
    # dates, two strong evidence types) — first-seen wins. Callers must feed
    # releases in a deterministic order.
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
        collected_at=_prefer_recent(base.collected_at, other.collected_at),
        fel_confirmed=base.fel_confirmed or other.fel_confirmed,
        tmdb_id=base.tmdb_id or other.tmdb_id,
        imdb_id=base.imdb_id or other.imdb_id,
        poster_path=base.poster_path or other.poster_path,
        release_url=base.release_url or other.release_url,
        bluray_url=base.bluray_url or other.bluray_url,
        bluray_release_date=_prefer_date(
            base.bluray_release_date, other.bluray_release_date
        ),
        hdr_formats=list(dict.fromkeys([*base.hdr_formats, *other.hdr_formats])),
        audio_languages=list(
            dict.fromkeys([*base.audio_languages, *other.audio_languages])
        ),
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
