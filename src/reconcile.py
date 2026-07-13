from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from itertools import chain

from merge import (
    canonical_key,
    canonical_title_key,
    canonical_url_key,
    dedupe_tmdb_release_groups,
    has_edition_descriptor,
    merge_releases,
)
from models import FelRelease


@dataclass(frozen=True)
class ReviewItem:
    release: FelRelease
    reason: str
    candidate_titles: tuple[str, ...] = ()


@dataclass
class ReconciliationResult:
    releases: list[FelRelease]
    additions: list[FelRelease]
    review_items: list[ReviewItem]
    merged_count: int


@dataclass(frozen=True)
class _MatchDecision:
    index: int | None = None
    reason: str = ""
    candidate_titles: tuple[str, ...] = ()


def reconcile_releases(
    existing: Iterable[FelRelease],
    incoming: Iterable[FelRelease],
) -> ReconciliationResult:
    existing_rows = list(existing)
    incoming_rows = list(incoming)
    catalog = [release for release in existing_rows if _year(release)]
    catalog_is_addition = [False] * len(catalog)
    existing_yearless = [release for release in existing_rows if not _year(release)]
    incoming_dated = [release for release in incoming_rows if _year(release)]
    incoming_yearless = [release for release in incoming_rows if not _year(release)]
    review_items: list[ReviewItem] = []
    merged_count = 0

    candidates = chain(
        ((release, False, True) for release in incoming_dated),
        ((release, True, False) for release in existing_yearless),
        ((release, False, False) for release in incoming_yearless),
    )
    for candidate, is_existing, track_addition in candidates:
        review_item, merged = _process_candidate(
            candidate, is_existing, track_addition, catalog, catalog_is_addition
        )
        if review_item is not None:
            review_items.append(review_item)
        if merged:
            merged_count += 1

    finalized = dedupe_tmdb_release_groups(catalog)
    releases = [release for release, _ in finalized]
    additions = [
        release
        for release, source_indices in finalized
        if all(catalog_is_addition[index] for index in source_indices)
    ]
    # Keep the established review contract: merged_count covers matches made in
    # the candidate loop. Proven AKA cleanup only finalizes catalog/addition
    # identity and does not retroactively change that diagnostic count.
    return ReconciliationResult(releases, additions, review_items, merged_count)


def _process_candidate(
    candidate: FelRelease,
    is_existing: bool,
    track_addition: bool,
    catalog: list[FelRelease],
    catalog_is_addition: list[bool],
) -> tuple[ReviewItem | None, bool]:
    """Match one candidate against the catalog, updating it in place.

    Returns (review_item, merged): review_item is set when the candidate
    needs human review; merged is True when it was folded into an existing
    catalog row (otherwise it was appended as a new row or sent to review).
    """
    decision = _match_candidate(candidate, catalog)
    if decision.reason:
        return (
            ReviewItem(candidate, decision.reason, decision.candidate_titles),
            False,
        )
    if decision.index is not None:
        if is_existing and catalog_is_addition[decision.index]:
            target = catalog[decision.index]
            catalog[decision.index] = replace(
                merge_releases(candidate, target), movie_title=target.movie_title
            )
            catalog_is_addition[decision.index] = False
        else:
            target = catalog[decision.index]
            catalog[decision.index] = replace(
                merge_releases(target, candidate), movie_title=target.movie_title
            )
        return None, True
    if not _year(candidate):
        return ReviewItem(candidate, "missing-year-no-match"), False
    catalog.append(candidate)
    catalog_is_addition.append(track_addition)
    return None, False


def _match_candidate(
    candidate: FelRelease, catalog: list[FelRelease]
) -> _MatchDecision:
    signal_decision = _match_by_strong_signals(candidate, catalog)
    if signal_decision is not None:
        return signal_decision
    if _year(candidate):
        return _match_by_title_year(candidate, catalog)
    return _match_by_yearless_title(candidate, catalog)


def _match_by_strong_signals(
    candidate: FelRelease, catalog: list[FelRelease]
) -> _MatchDecision | None:
    """Match by Blu-ray URL, TMDB id, or IMDb id; None if none of them hit."""
    candidate_url = canonical_url_key(candidate.bluray_url)
    url_matches = [
        index
        for index, release in enumerate(catalog)
        if candidate_url and canonical_url_key(release.bluray_url) == candidate_url
    ]
    tmdb_matches = _id_matches(candidate.tmdb_id, "tmdb_id", catalog)
    imdb_matches = _id_matches(candidate.imdb_id, "imdb_id", catalog)
    signal_matches = [
        matches for matches in (url_matches, tmdb_matches, imdb_matches) if matches
    ]
    if not signal_matches:
        return None

    implicated_matches = sorted(set().union(*signal_matches))
    if not _match_sets_are_connected(signal_matches):
        return _review_decision("identity-conflict", catalog, implicated_matches)
    if len(url_matches) == 1:
        return _target_decision(candidate, catalog, url_matches[0])

    common_matches = sorted(set(signal_matches[0]).intersection(*signal_matches[1:]))
    target_matches = common_matches or implicated_matches
    consistent_matches = [
        index
        for index in target_matches
        if _ids_are_consistent(candidate, catalog[index])
    ]
    if not consistent_matches:
        return _review_decision("identity-conflict", catalog, target_matches)
    if len(consistent_matches) == 1:
        return _target_decision(candidate, catalog, consistent_matches[0])

    candidate_year = _year(candidate)
    if candidate_year:
        candidate_key = canonical_key(candidate)
        narrowed = [
            index
            for index in consistent_matches
            if canonical_key(catalog[index]) == candidate_key
        ]
        if len(narrowed) == 1:
            return _target_decision(candidate, catalog, narrowed[0])
    return _review_decision("ambiguous-edition", catalog, consistent_matches)


def _match_by_title_year(
    candidate: FelRelease, catalog: list[FelRelease]
) -> _MatchDecision:
    """Match a dated candidate with no strong-signal hit by title and year."""
    candidate_key = canonical_key(candidate)
    title_year_matches = [
        index
        for index, release in enumerate(catalog)
        if canonical_key(release) == candidate_key
    ]
    if not title_year_matches:
        return _MatchDecision()
    if any(
        not _ids_are_consistent(candidate, catalog[index])
        for index in title_year_matches
    ):
        return _review_decision("identity-conflict", catalog, title_year_matches)
    if len(title_year_matches) == 1:
        return _target_decision(candidate, catalog, title_year_matches[0])
    return _review_decision("ambiguous-edition", catalog, title_year_matches)


def _match_by_yearless_title(
    candidate: FelRelease, catalog: list[FelRelease]
) -> _MatchDecision:
    """Match a yearless candidate with no strong-signal hit by title alone."""
    title_key = canonical_title_key(candidate.movie_title)
    yearless_title_matches = [
        index
        for index, release in enumerate(catalog)
        if _year(release) and canonical_title_key(release.movie_title) == title_key
    ]
    if len(yearless_title_matches) == 1:
        return _target_decision(candidate, catalog, yearless_title_matches[0])
    if len(yearless_title_matches) > 1:
        return _review_decision(
            "ambiguous-yearless-title", catalog, yearless_title_matches
        )
    return _MatchDecision()


def _target_decision(
    candidate: FelRelease,
    catalog: list[FelRelease],
    index: int,
) -> _MatchDecision:
    target = catalog[index]
    if not _ids_are_consistent(candidate, target):
        return _review_decision("identity-conflict", catalog, [index])
    if _series_id_edition_conflict(candidate, target):
        return _MatchDecision()
    if _has_distinct_url(candidate, target) and not _same_release_despite_distinct_urls(
        candidate, target
    ):
        if _year(candidate):
            return _MatchDecision()
        return _review_decision("ambiguous-edition", catalog, [index])
    return _MatchDecision(index=index)


def _series_id_edition_conflict(candidate: FelRelease, target: FelRelease) -> bool:
    """Report whether shared strong ids prove only a shared series, not a disc."""
    # Every season disc of a show resolves to the same series-level TMDB and
    # IMDb ids, so a strong-id hit between two rows that both carry edition
    # descriptors but name different editions ("The Complete First Season"
    # vs "The Complete Second Season") is not evidence of the same physical
    # release -- folding them would silently swallow a new season into an
    # older one. A shared blu-ray.com page overrides the stop signal: one
    # disc page is one release no matter how the source spelled the
    # descriptor.
    candidate_url = canonical_url_key(candidate.bluray_url)
    if candidate_url and candidate_url == canonical_url_key(target.bluray_url):
        return False
    if canonical_title_key(candidate.movie_title) == canonical_title_key(
        target.movie_title
    ):
        return False
    return has_edition_descriptor(candidate.movie_title) and has_edition_descriptor(
        target.movie_title
    )


def _same_release_despite_distinct_urls(
    candidate: FelRelease, target: FelRelease
) -> bool:
    """Same canonical identity re-resolved to a different disc page."""
    # Multiple pressings of one cut share the canonical title+year, so the
    # URL difference is disc-page drift rather than a new edition -- but only
    # when a shared strong id proves both rows name the same film, or when
    # neither row has any id at all (two id-less same-title rows are
    # indistinguishable to readers, so publishing both is meaningless).
    # One-sided enrichment keeps the append path: the unresolved side may be
    # a different same-titled film whose TMDB resolution failed.
    if canonical_key(candidate) != canonical_key(target):
        return False
    if _shares_strong_id(candidate, target):
        return True
    return not _has_strong_id(candidate) and not _has_strong_id(target)


def _shares_strong_id(left: FelRelease, right: FelRelease) -> bool:
    return (bool(left.tmdb_id) and left.tmdb_id == right.tmdb_id) or (
        bool(left.imdb_id) and left.imdb_id == right.imdb_id
    )


def _has_strong_id(release: FelRelease) -> bool:
    return bool(release.tmdb_id or release.imdb_id)


def _id_matches(
    value: str,
    field: str,
    catalog: list[FelRelease],
) -> list[int]:
    if not value:
        return []
    return [
        index
        for index, release in enumerate(catalog)
        if getattr(release, field) == value
    ]


def _match_sets_are_connected(match_sets: list[list[int]]) -> bool:
    component = set(match_sets[0])
    pending = [set(matches) for matches in match_sets[1:]]
    while pending:
        overlapping = [matches for matches in pending if component & matches]
        if not overlapping:
            return False
        for matches in overlapping:
            component.update(matches)
            pending.remove(matches)
    return True


def _ids_are_consistent(left: FelRelease, right: FelRelease) -> bool:
    return all(
        not left_value or not right_value or left_value == right_value
        for left_value, right_value in (
            (left.tmdb_id, right.tmdb_id),
            (left.imdb_id, right.imdb_id),
        )
    )


def _has_distinct_url(left: FelRelease, right: FelRelease) -> bool:
    left_url = canonical_url_key(left.bluray_url)
    right_url = canonical_url_key(right.bluray_url)
    return bool(left_url and right_url and left_url != right_url)


def _review_decision(
    reason: str,
    catalog: list[FelRelease],
    indices: list[int],
) -> _MatchDecision:
    return _MatchDecision(
        reason=reason,
        candidate_titles=tuple(catalog[index].movie_title for index in indices),
    )


def _year(release: FelRelease) -> str:
    return canonical_key(release)[1]
