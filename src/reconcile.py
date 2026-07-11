from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from itertools import chain

from merge import canonical_key, canonical_title_key, canonical_url_key, merge_releases
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
    existing_yearless = [release for release in existing_rows if not _year(release)]
    incoming_dated = [release for release in incoming_rows if _year(release)]
    incoming_yearless = [release for release in incoming_rows if not _year(release)]
    additions_by_index: dict[int, FelRelease] = {}
    review_items: list[ReviewItem] = []
    merged_count = 0

    candidates = chain(
        ((release, False, True) for release in incoming_dated),
        ((release, True, False) for release in existing_yearless),
        ((release, False, False) for release in incoming_yearless),
    )
    for candidate, is_existing, track_addition in candidates:
        decision = _match_candidate(candidate, catalog)
        if decision.reason:
            review_items.append(
                ReviewItem(candidate, decision.reason, decision.candidate_titles)
            )
        elif decision.index is not None:
            if is_existing and decision.index in additions_by_index:
                target = catalog[decision.index]
                catalog[decision.index] = replace(
                    merge_releases(candidate, target), movie_title=target.movie_title
                )
                del additions_by_index[decision.index]
            else:
                target = catalog[decision.index]
                catalog[decision.index] = replace(
                    merge_releases(target, candidate), movie_title=target.movie_title
                )
            merged_count += 1
        elif not _year(candidate):
            review_items.append(ReviewItem(candidate, "missing-year-no-match"))
        else:
            catalog.append(candidate)
            if track_addition:
                additions_by_index[len(catalog) - 1] = candidate

    return ReconciliationResult(
        catalog, list(additions_by_index.values()), review_items, merged_count
    )


def _match_candidate(
    candidate: FelRelease, catalog: list[FelRelease]
) -> _MatchDecision:
    candidate_url = canonical_url_key(candidate.bluray_url)
    if candidate_url:
        url_matches = [
            index
            for index, release in enumerate(catalog)
            if canonical_url_key(release.bluray_url) == candidate_url
        ]
        if len(url_matches) == 1:
            return _target_decision(candidate, catalog, url_matches[0])

    tmdb_matches = _id_matches(candidate.tmdb_id, "tmdb_id", catalog)
    imdb_matches = _id_matches(candidate.imdb_id, "imdb_id", catalog)
    if tmdb_matches and imdb_matches:
        imdb_match_set = set(imdb_matches)
        shared_matches = [index for index in tmdb_matches if index in imdb_match_set]
        if not shared_matches:
            return _review_decision(
                "identity-conflict",
                catalog,
                _ordered_union(tmdb_matches, imdb_matches),
            )
        id_matches = shared_matches
    else:
        id_matches = _ordered_union(tmdb_matches, imdb_matches)

    if id_matches:
        consistent_matches = [
            index
            for index in id_matches
            if _ids_are_consistent(candidate, catalog[index])
        ]
        if not consistent_matches:
            return _review_decision("identity-conflict", catalog, id_matches)
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

    candidate_year = _year(candidate)
    if candidate_year:
        candidate_key = canonical_key(candidate)
        title_year_matches = [
            index
            for index, release in enumerate(catalog)
            if canonical_key(release) == candidate_key
        ]
        if title_year_matches:
            if any(
                not _ids_are_consistent(candidate, catalog[index])
                for index in title_year_matches
            ):
                return _review_decision(
                    "identity-conflict", catalog, title_year_matches
                )
            if len(title_year_matches) == 1:
                return _target_decision(candidate, catalog, title_year_matches[0])
            return _review_decision("ambiguous-edition", catalog, title_year_matches)
        return _MatchDecision()

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
    if _has_distinct_url(candidate, target):
        if _year(candidate):
            return _MatchDecision()
        return _review_decision("ambiguous-edition", catalog, [index])
    return _MatchDecision(index=index)


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


def _ordered_union(left: list[int], right: list[int]) -> list[int]:
    return sorted({*left, *right})


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
