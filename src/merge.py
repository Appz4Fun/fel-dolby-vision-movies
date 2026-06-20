from __future__ import annotations

from collections.abc import Callable, Iterable
import re
import unicodedata
import urllib.parse

from models import UNKNOWN, FelEvidence, FelRelease


_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_FULL_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# List-membership evidence types (synthesized quotes). Any *-list type --
# fel-list, fel-bitrate-list, reddit-list, github-list, discourse-list,
# letterboxd-list, google-sheet-list -- is considered weak so a real scraped
# quote always wins over list membership.
_WEAK_EVIDENCE_SUFFIX = "-list"


def _is_weak_evidence(evidence_type: str) -> bool:
    return evidence_type.endswith(_WEAK_EVIDENCE_SUFFIX)


def _is_ai_evidence(evidence_type: str) -> bool:
    return evidence_type == "ai-extracted"


# Title tokens that mark a genuinely distinct physical release sharing one TMDB
# id (editions, cuts, season/series discs). When a tmdb group's titles carry one
# of these we keep the rows separate; otherwise same-tmdb rows are pure AKA /
# translation / spelling variants of one film and should collapse to one record.
_EDITION_DESCRIPTOR_RE = re.compile(
    r"\b(?:extended|collector|collectors|director|directors|theatrical|"
    r"remaster|remastered|restored|uncut|unrated|special\s+edition|anniversary|"
    r"complete|season|series|volume|vol\.|part\s+\w+|chapter|disc|criterion|"
    r"limited|ultimate|deluxe|definitive|edition)\b",
    re.IGNORECASE,
)


def _has_edition_descriptor(title: str) -> bool:
    return bool(_EDITION_DESCRIPTOR_RE.search(title or ""))


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


def canonical_url_key(value: str) -> str:
    parsed = urllib.parse.urlparse(value.strip())
    if not parsed.netloc:
        return ""
    scheme = (parsed.scheme or "https").lower()
    hostname = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunparse((scheme, hostname, path, "", "", ""))


def title_bluray_key(release: FelRelease) -> tuple[str, str]:
    bluray_url = canonical_url_key(release.bluray_url)
    if bluray_url:
        return (
            "title-bluray",
            f"{canonical_title_key(release.movie_title)}\0{bluray_url}",
        )
    return canonical_key(release)


def tmdb_key(release: FelRelease) -> tuple[str, str]:
    if release.tmdb_id:
        bluray_url = canonical_url_key(release.bluray_url)
        if bluray_url:
            return ("tmdb-bluray", f"{release.tmdb_id}\0{bluray_url}")
        return ("tmdb", release.tmdb_id)
    return canonical_key(release)


def dedupe_tmdb_releases(releases: Iterable[FelRelease]) -> list[FelRelease]:
    grouped: dict[tuple[str, str], list[FelRelease]] = {}
    order: list[tuple[str, str]] = []
    no_tmdb_index = 0
    for release in releases:
        if release.tmdb_id:
            key = ("tmdb", release.tmdb_id)
        else:
            key = ("no-tmdb", str(no_tmdb_index))
            no_tmdb_index += 1
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(release)

    deduped: list[FelRelease] = []
    for key in order:
        group = grouped[key]
        if key[0] == "tmdb":
            deduped.extend(_dedupe_one_tmdb_group(group))
        else:
            deduped.extend(group)
    return deduped


def _dedupe_one_tmdb_group(releases: list[FelRelease]) -> list[FelRelease]:
    if len(releases) <= 1:
        return list(releases)

    bluray_groups: dict[str, list[tuple[int, FelRelease]]] = {}
    bluray_order: list[str] = []
    unresolved: list[tuple[int, FelRelease]] = []
    for index, release in enumerate(releases):
        bluray_url = canonical_url_key(release.bluray_url)
        if not bluray_url:
            unresolved.append((index, release))
            continue
        if bluray_url not in bluray_groups:
            bluray_groups[bluray_url] = []
            bluray_order.append(bluray_url)
        bluray_groups[bluray_url].append((index, release))

    if not bluray_groups:
        return [
            _merge_identity_group([(index, release) for index, release in unresolved])[
                1
            ]
        ]

    resolved = [
        _merge_identity_group(bluray_groups[bluray_url]) for bluray_url in bluray_order
    ]

    # Same tmdb_id across several blu-ray editions: if NO title carries an
    # edition/season descriptor these are AKA/translation duplicates of one film
    # (different blu-ray.com pages for the same release), so collapse them into a
    # single enriched record. Distinct editions/seasons keep their descriptor and
    # stay separate.
    if len(resolved) > 1 and not any(
        _has_edition_descriptor(release.movie_title) for _, release in resolved
    ):
        # resolved is in first-occurrence order, so resolved[0] holds the lowest
        # original index; fold the rest into it, preserving its base title.
        base_index, base = resolved[0]
        for _, release in resolved[1:]:
            base = _merge_preserving_base_title(base, release)
        resolved = [(base_index, base)]

    for index, release in unresolved:
        target_index = _find_tmdb_merge_target(release, resolved)
        if target_index is None:
            resolved.append((index, release))
            continue
        target_position, target = resolved[target_index]
        if index < target_position:
            resolved[target_index] = (
                index,
                _merge_preserving_base_title(release, target),
            )
        else:
            resolved[target_index] = (
                target_position,
                _merge_preserving_base_title(target, release),
            )

    return [release for _, release in sorted(resolved, key=lambda item: item[0])]


def _merge_identity_group(
    releases: list[tuple[int, FelRelease]],
) -> tuple[int, FelRelease]:
    first_index, merged = releases[0]
    for _, release in releases[1:]:
        merged = _merge_preserving_base_title(merged, release)
    return first_index, merged


def _merge_preserving_base_title(base: FelRelease, other: FelRelease) -> FelRelease:
    title = base.movie_title
    merged = merge_releases(base, other)
    merged.movie_title = title
    return merged


def _find_tmdb_merge_target(
    release: FelRelease, candidates: list[tuple[int, FelRelease]]
) -> int | None:
    canonical_matches = [
        index
        for index, (_, candidate) in enumerate(candidates)
        if canonical_key(candidate) == canonical_key(release)
    ]
    if len(canonical_matches) == 1:
        return canonical_matches[0]

    title_key = canonical_title_key(release.movie_title)
    title_matches = [
        index
        for index, (_, candidate) in enumerate(candidates)
        if canonical_title_key(candidate.movie_title) == title_key
    ]
    if len(title_matches) == 1:
        return title_matches[0]

    release_year = _year(release.release_date)
    if release_year:
        year_matches = [
            index
            for index, (_, candidate) in enumerate(candidates)
            if _year(candidate.release_date) == release_year
        ]
        if len(year_matches) == 1:
            return year_matches[0]

    if len(candidates) == 1:
        return 0
    return None


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
    # AGENTS.md: ai-scrape must merge into existing data and never replace
    # deterministic scraper results. When exactly one side is AI-extracted, keep
    # the deterministic side regardless of its strength -- AI evidence is
    # supplemental and must not overwrite the title/year-specific quote that ties
    # the FEL claim to one release.
    left_ai = _is_ai_evidence(left.evidence_type)
    right_ai = _is_ai_evidence(right.evidence_type)
    if left_ai != right_ai:
        return right if left_ai else left
    left_weak = _is_weak_evidence(left.evidence_type)
    right_weak = _is_weak_evidence(right.evidence_type)
    if left_weak and not right_weak:
        return right
    return left


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
        collected_at=_prefer_known(base.collected_at, other.collected_at),
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
