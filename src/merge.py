from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
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
# id (editions, cuts, season/series discs). These are a conservative stop signal,
# not proof that descriptor-free rows are aliases; reconciliation separately
# requires alias proof (explicit cross-title AKA evidence, a recorded TMDB
# canonical/original title pair, or titles that are mere spelling variants of
# each other) before it can collapse them.
_EDITION_DESCRIPTOR_RE = re.compile(
    r"\b(?:extended|collector|collectors|director|directors|theatrical|"
    r"remaster|remastered|restored|uncut|unrated|special\s+edition|anniversary|"
    r"complete|season|s0*[1-9]\d*|series|volume|vol\.|part\s+\w+|chapter|disc|"
    r"criterion|steelbook|final\s+cut|limited|ultimate|deluxe|definitive|edition)\b",
    re.IGNORECASE,
)
_AKA_RE = re.compile(r"\baka\b", re.IGNORECASE)
_AKA_LOCAL_BOUNDARY_RE = re.compile(r"[.!?;\n]+")

# additional_characteristics keys recorded by enrichment for foreign-language
# films: TMDB's canonical (usually English) title and the film's original title.
TMDB_TITLE_KEY = "tmdb_title"
TMDB_ORIGINAL_TITLE_KEY = "tmdb_original_title"


def has_edition_descriptor(title: str) -> bool:
    return bool(_EDITION_DESCRIPTOR_RE.search(title or ""))


# Season/series labels specifically -- the subset of edition descriptors
# whose rows share *series-level* TMDB/IMDb ids across physically distinct
# discs. Requires a season number/ordinal (or "complete series") so movie
# titles that merely contain the word "season" ("Season of the Witch")
# don't count.
_SEASON_LABEL_RE = re.compile(
    r"\b(?:the\s+complete\s+\w+\s+seasons?|seasons?\s+\d+|s0*[1-9]\d*|"
    r"complete\s+series)\b",
    re.IGNORECASE,
)


def has_season_descriptor(title: str) -> bool:
    """True for titles naming a TV season/series disc."""
    return bool(_SEASON_LABEL_RE.search(title or ""))


def canonical_title_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    normalized = normalized.casefold().replace("&", " and ")
    normalized = re.sub(r"['`´’]", "", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _year(value: str) -> str:
    match = _YEAR_RE.search(value or "")
    return match.group(0) if match else ""


def canonical_key(release: FelRelease) -> tuple[str, str]:
    return (canonical_title_key(release.movie_title), _year(release.release_date))


def canonical_url_key(value: str) -> str:
    parsed = urllib.parse.urlparse(value.strip())
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        return ""
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunparse(("https", parsed.hostname.lower(), path, "", "", ""))


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
    return [
        release
        for release, _ in _dedupe_tmdb_release_groups(
            releases, reconciliation_safe=False
        )
    ]


@dataclass(frozen=True)
class _ReleaseGroup:
    first_index: int
    release: FelRelease
    source_indices: tuple[int, ...]


def dedupe_tmdb_release_groups(
    releases: Iterable[FelRelease],
) -> list[tuple[FelRelease, tuple[int, ...]]]:
    """Return alias-safe TMDB groups with their input-row provenance."""
    return _dedupe_tmdb_release_groups(releases, reconciliation_safe=True)


def _dedupe_tmdb_release_groups(
    releases: Iterable[FelRelease],
    *,
    reconciliation_safe: bool,
) -> list[tuple[FelRelease, tuple[int, ...]]]:
    grouped: dict[tuple[str, str], list[tuple[int, FelRelease]]] = {}
    order: list[tuple[str, str]] = []
    no_tmdb_index = 0
    for index, release in enumerate(releases):
        if release.tmdb_id:
            key = ("tmdb", release.tmdb_id)
        else:
            key = ("no-tmdb", str(no_tmdb_index))
            no_tmdb_index += 1
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append((index, release))

    deduped: list[_ReleaseGroup] = []
    for key in order:
        group = grouped[key]
        if key[0] == "tmdb":
            deduped.extend(
                _dedupe_one_tmdb_group(group, reconciliation_safe=reconciliation_safe)
            )
        else:
            index, release = group[0]
            deduped.append(_ReleaseGroup(index, release, (index,)))
    return [
        (item.release, item.source_indices)
        for item in sorted(deduped, key=lambda item: item.first_index)
    ]


def _dedupe_one_tmdb_group(
    releases: list[tuple[int, FelRelease]],
    *,
    reconciliation_safe: bool,
) -> list[_ReleaseGroup]:
    if len(releases) <= 1:
        index, release = releases[0]
        return [_ReleaseGroup(index, release, (index,))]
    if reconciliation_safe and not _is_reconciliation_alias_group(releases):
        return [_ReleaseGroup(index, release, (index,)) for index, release in releases]

    bluray_groups: dict[str, list[tuple[int, FelRelease]]] = {}
    bluray_order: list[str] = []
    unresolved: list[tuple[int, FelRelease]] = []
    for index, release in releases:
        bluray_url = canonical_url_key(release.bluray_url)
        if not bluray_url:
            unresolved.append((index, release))
            continue
        if bluray_url not in bluray_groups:
            bluray_groups[bluray_url] = []
            bluray_order.append(bluray_url)
        bluray_groups[bluray_url].append((index, release))

    if not bluray_groups:
        return [_merge_identity_group(unresolved)]

    resolved = [
        _merge_identity_group(bluray_groups[bluray_url]) for bluray_url in bluray_order
    ]
    resolved = _merge_groups_sharing_canonical_key(resolved)

    # The legacy public deduper reaches this check directly. Reconciliation only
    # reaches it after the whole group passes the explicit AKA proof graph above.
    # In both paths, an edition descriptor keeps the physical rows separate.
    if (
        len(resolved) > 1
        and len({canonical_key(item.release) for item in resolved}) == len(resolved)
        and not any(
            has_edition_descriptor(item.release.movie_title) for item in resolved
        )
    ):
        # resolved is in first-occurrence order, so resolved[0] holds the lowest
        # original index; fold the rest into it, preserving its base title.
        base = resolved[0]
        for item in resolved[1:]:
            base = _merge_release_groups(base, item)
        resolved = [base]

    for index, release in unresolved:
        target_index = _find_tmdb_merge_target(
            release,
            [(item.first_index, item.release) for item in resolved],
        )
        if target_index is None:
            resolved.append(_ReleaseGroup(index, release, (index,)))
            continue
        target = resolved[target_index]
        incoming = _ReleaseGroup(index, release, (index,))
        if index < target.first_index:
            resolved[target_index] = _merge_release_groups(incoming, target)
        else:
            resolved[target_index] = _merge_release_groups(target, incoming)

    return sorted(resolved, key=lambda item: item.first_index)


def _merge_groups_sharing_canonical_key(
    resolved: list[_ReleaseGroup],
) -> list[_ReleaseGroup]:
    """Collapse per-disc-URL groups whose rows share one canonical identity."""
    # A film re-resolved to a different blu-ray.com page (multiple pressings
    # of the same cut) still names the same release; only differently-titled
    # rows can represent distinct physical editions. `resolved` arrives in
    # first-occurrence order, so the earliest group always absorbs later ones.
    merged: list[_ReleaseGroup] = []
    index_by_key: dict[tuple[str, str], int] = {}
    for item in resolved:
        key = canonical_key(item.release)
        target_index = index_by_key.get(key)
        if target_index is None:
            index_by_key[key] = len(merged)
            merged.append(item)
        else:
            merged[target_index] = _merge_release_groups(merged[target_index], item)
    return merged


def _is_reconciliation_alias_group(
    releases: list[tuple[int, FelRelease]],
) -> bool:
    rows = [release for _, release in releases]
    years = {_year(release.release_date) for release in rows}
    imdb_ids = {release.imdb_id for release in rows}
    identity_is_compatible = (
        "" not in years
        and len(years) == 1
        and "" not in imdb_ids
        and len(imdb_ids) == 1
        and not any(has_edition_descriptor(release.movie_title) for release in rows)
    )
    if not identity_is_compatible:
        return False

    edges = _explicit_aka_edges(rows) | _tmdb_title_alias_edges(rows)
    for left_index in range(len(rows)):
        for right_index in range(left_index + 1, len(rows)):
            if _titles_are_spelling_variants(
                rows[left_index].movie_title, rows[right_index].movie_title
            ):
                edges.add((left_index, right_index))
    return _graph_connects_all_rows(len(rows), edges)


def _recorded_tmdb_title_pairs(rows: list[FelRelease]) -> set[frozenset[str]]:
    """Distinct canonical/original title-key pairs recorded by enrichment."""
    pairs: set[frozenset[str]] = set()
    for row in rows:
        characteristics = row.additional_characteristics
        title_key = canonical_title_key(str(characteristics.get(TMDB_TITLE_KEY, "")))
        original_key = canonical_title_key(
            str(characteristics.get(TMDB_ORIGINAL_TITLE_KEY, ""))
        )
        if title_key and original_key and title_key != original_key:
            pairs.add(frozenset((title_key, original_key)))
    return pairs


def _tmdb_title_alias_edges(rows: list[FelRelease]) -> set[tuple[int, int]]:
    """Edges proven by enrichment's recorded TMDB canonical/original title pair.

    When any row in the group carries both titles TMDB reports for the film,
    the pair proves that one row titled by the original (native) title and one
    titled by the canonical (English) title name the same film, even when no
    scraped quote spells out an explicit "Native AKA English" alias.
    """
    alias_pairs = _recorded_tmdb_title_pairs(rows)
    if not alias_pairs:
        return set()
    row_keys = [canonical_title_key(row.movie_title) for row in rows]
    return {
        (left_index, right_index)
        for left_index in range(len(rows))
        for right_index in range(left_index + 1, len(rows))
        if frozenset((row_keys[left_index], row_keys[right_index])) in alias_pairs
    }


def _explicit_aka_edges(rows: list[FelRelease]) -> set[tuple[int, int]]:
    edges: set[tuple[int, int]] = set()
    for left_index in range(len(rows)):
        for right_index in range(left_index + 1, len(rows)):
            if any(
                _quote_names_alias_pair(
                    row.fel_evidence.quote,
                    rows[left_index].movie_title,
                    rows[right_index].movie_title,
                )
                for row in rows
            ):
                edges.add((left_index, right_index))
    return edges


def _quote_names_alias_pair(quote: str, left_title: str, right_title: str) -> bool:
    left_key = canonical_title_key(left_title)
    right_key = canonical_title_key(right_title)
    segments = _AKA_RE.split(quote)
    for before, after in zip(segments, segments[1:], strict=False):
        local_before = _AKA_LOCAL_BOUNDARY_RE.split(before)[-1]
        local_after = _AKA_LOCAL_BOUNDARY_RE.split(after, maxsplit=1)[0]
        if (
            _left_aka_side_names_title(local_before, left_key)
            and _right_aka_side_names_title(local_after, right_key)
        ) or (
            _left_aka_side_names_title(local_before, right_key)
            and _right_aka_side_names_title(local_after, left_key)
        ):
            return True
    return False


def _left_aka_side_names_title(text: str, title: str) -> bool:
    local_key = canonical_title_key(text)
    return bool(title) and (
        local_key == title
        or bool(re.fullmatch(rf"\d+\s+{re.escape(title)}", local_key))
    )


def _right_aka_side_names_title(text: str, title: str) -> bool:
    local_key = canonical_title_key(text)
    return bool(title) and (
        local_key == title
        or bool(re.fullmatch(rf"{re.escape(title)}\s+(?:19|20)\d{{2}}", local_key))
    )


# Orthography-only token spellings: regional variants plus spelled-out
# numbers ("The Fantastic Four" vs "The Fantastic 4"). Number words map to
# digits BEFORE the digit-run guard in _titles_are_spelling_variants, so
# "Iron Man Two" vs "Iron Man 3" still compares as 2 vs 3 and never merges.
_ORTHOGRAPHIC_TOKEN_REPLACEMENTS = {
    "colour": "color",
    "colours": "colors",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
}


def _orthographic_title_key(title: str) -> str:
    return " ".join(
        _ORTHOGRAPHIC_TOKEN_REPLACEMENTS.get(token, token)
        for token in canonical_title_key(title).split()
    )


_DIGIT_RUN_RE = re.compile(r"\d+")
# Below this many letters a one-character edit rewrites too much of the title
# ("Up" vs "Us") to be trusted as a typo, so short titles must match outright.
_MIN_TYPO_TITLE_LENGTH = 5


def _titles_are_spelling_variants(left_title: str, right_title: str) -> bool:
    """True when two titles differ only by orthography or a single typo.

    Callers must already hold identity corroboration (same TMDB id, same
    non-empty IMDb id, same year, no edition descriptors); this predicate only
    rejects edits that could still mark a distinct release: digit runs must
    match exactly so sequels and numbered parts ("Iron Man 2" vs "Iron Man 3")
    never count as typos, word counts must match so a whole extra word
    ("Alien" vs "Alien X") never counts as a typo either, and very short
    titles must match outright.
    """
    left_key = _orthographic_title_key(left_title)
    right_key = _orthographic_title_key(right_title)
    if left_key == right_key:
        return True
    if _DIGIT_RUN_RE.findall(left_key) != _DIGIT_RUN_RE.findall(right_key):
        return False
    left_compact = left_key.replace(" ", "")
    right_compact = right_key.replace(" ", "")
    if left_compact == right_compact:
        # Same letters, different spacing only (e.g. a missing space) -- no
        # word was added or removed, so word counts are allowed to differ.
        return True
    if len(left_key.split()) != len(right_key.split()):
        return False
    if min(len(left_compact), len(right_compact)) < _MIN_TYPO_TITLE_LENGTH:
        return False
    return _is_within_one_edit(left_compact, right_compact)


def _is_within_one_edit(left: str, right: str) -> bool:
    """True when exactly one substitution, indel, or adjacent swap apart.

    Callers must already have excluded the equal-strings case (the only
    caller, _titles_are_spelling_variants, returns early on that case).
    """
    if len(left) == len(right):
        return _is_one_substitution_or_swap_apart(left, right)
    return _is_one_indel_apart(left, right)


def _is_one_substitution_or_swap_apart(left: str, right: str) -> bool:
    """True when same-length strings differ by one substitution or swap."""
    mismatches = [
        index
        for index, (left_char, right_char) in enumerate(zip(left, right))
        if left_char != right_char
    ]
    if len(mismatches) == 1:
        return True
    return (
        len(mismatches) == 2
        and mismatches[1] == mismatches[0] + 1
        and left[mismatches[0]] == right[mismatches[1]]
        and left[mismatches[1]] == right[mismatches[0]]
    )


def _is_one_indel_apart(left: str, right: str) -> bool:
    """True when strings one character apart in length differ by one insert."""
    shorter, longer = sorted((left, right), key=len)
    if len(longer) - len(shorter) != 1:
        return False
    for index in range(len(shorter)):
        if shorter[index] != longer[index]:
            return shorter[index:] == longer[index + 1 :]
    return True


def _graph_connects_all_rows(
    row_count: int,
    edges: set[tuple[int, int]],
) -> bool:
    connected = {0}
    while True:
        expanded = connected | {
            right if left in connected else left
            for left, right in edges
            if left in connected or right in connected
        }
        if expanded == connected:
            return len(connected) == row_count
        connected = expanded


def _merge_identity_group(
    releases: list[tuple[int, FelRelease]],
) -> _ReleaseGroup:
    first_index, merged = releases[0]
    source_indices = [first_index]
    for index, release in releases[1:]:
        merged = _merge_preserving_base_title(merged, release)
        source_indices.append(index)
    return _ReleaseGroup(first_index, merged, tuple(source_indices))


def _merge_release_groups(base: _ReleaseGroup, other: _ReleaseGroup) -> _ReleaseGroup:
    return _ReleaseGroup(
        first_index=base.first_index,
        release=_merge_preserving_base_title(base.release, other.release),
        source_indices=tuple(sorted((*base.source_indices, *other.source_indices))),
    )


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


def _prefers_left_evidence(left: FelEvidence, right: FelEvidence) -> bool:
    # Weak list-membership evidence (a bare "Title [Year]" entry, no technical
    # detail) carries less information than even a generic AI-extracted quote,
    # so strength is decided first: non-weak beats weak regardless of source.
    # AGENTS.md: ai-scrape must merge into existing data and never replace
    # deterministic scraper results. Among evidence of equal strength, AI must
    # still never override a real deterministic quote -- AI evidence is
    # supplemental and must not overwrite the title/year-specific quote that
    # ties the FEL claim to one release.
    left_weak = _is_weak_evidence(left.evidence_type)
    right_weak = _is_weak_evidence(right.evidence_type)
    if left_weak != right_weak:
        return not left_weak
    left_ai = _is_ai_evidence(left.evidence_type)
    right_ai = _is_ai_evidence(right.evidence_type)
    if left_ai != right_ai:
        return not left_ai
    return True


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
    # source_url is a property of fel_evidence, so source_label -- which
    # describes what kind of page that URL is -- must be selected together
    # with whichever side's evidence wins, not independently via its own
    # "most known wins" rule. Otherwise the merged release can keep one
    # side's URL/evidence with the *other* side's label, describing a
    # provider the URL doesn't actually point to. Only fall back to the
    # losing side's label when the winning side never set its own (e.g. the
    # legacy forum HTML parser leaves source_label unset).
    prefer_left = _prefers_left_evidence(base.fel_evidence, other.fel_evidence)
    winner, loser = (base, other) if prefer_left else (other, base)
    return FelRelease(
        movie_title=_prefer_title(base.movie_title, other.movie_title),
        fel_evidence=winner.fel_evidence,
        release_date=_prefer_date(base.release_date, other.release_date),
        studio=_prefer_known(base.studio, other.studio),
        audio_formats=list(dict.fromkeys([*base.audio_formats, *other.audio_formats])),
        english_audio=_prefer_known(base.english_audio, other.english_audio),
        additional_characteristics=additional,
        source_label=_prefer_known(winner.source_label, loser.source_label),
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
