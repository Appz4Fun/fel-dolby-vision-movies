"""Sync the FEL releases dataset to a Trakt custom list."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import httpx


TRAKT_BASE_URL = "https://api.trakt.tv"
TRAKT_REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"
TRAKT_API_VERSION = "2"

# Cap CI-blocking sleep when Trakt reports a long Retry-After. The actual
# rate-limit window for /oauth/token can be ~45 minutes, which is too long to
# block a workflow; we fail fast and let the next scheduled run retry.
MAX_RETRY_SLEEP_SECONDS = 10
DEFAULT_RETRY_SLEEP_SECONDS = 5
_RETRY_AFTER_BODY_RE = re.compile(r"wait\s+(\d+)\s+seconds", re.IGNORECASE)

# Trakt paginates list-item responses. We ask for the max allowed page size to
# minimize round-trips. The safety cap protects against a misbehaving server
# that never advances the page-count header.
LIST_FETCH_PAGE_LIMIT = 100
MAX_LIST_FETCH_PAGES = 100


class TraktError(RuntimeError):
    """Base class for Trakt sync errors."""


class TraktAuthError(TraktError):
    """OAuth refresh failed."""


class TraktRateLimitError(TraktError):
    """Trakt returned HTTP 429."""

    def __init__(self, message: str, retry_after_seconds: int | None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def _parse_retry_after(response: httpx.Response) -> int | None:
    header = response.headers.get("Retry-After")
    if header and header.strip().isdigit():
        return int(header.strip())
    match = _RETRY_AFTER_BODY_RE.search(response.text or "")
    if match:
        return int(match.group(1))
    return None


def _rate_limit_error(response: httpx.Response, context: str) -> TraktRateLimitError:
    retry_after = _parse_retry_after(response)
    suffix = f" retry-after={retry_after}s" if retry_after is not None else ""
    return TraktRateLimitError(
        f"trakt {context} rate limit hit: {response.status_code} {response.text}"
        f"{suffix}",
        retry_after_seconds=retry_after,
    )


def _sleep_for_retry(response: httpx.Response) -> None:
    retry_after = _parse_retry_after(response)
    delay = retry_after if retry_after is not None else DEFAULT_RETRY_SLEEP_SECONDS
    time.sleep(float(min(delay, MAX_RETRY_SLEEP_SECONDS)))


@dataclass(frozen=True)
class TraktTokens:
    access_token: str
    refresh_token: str


def refresh_access_token(
    *,
    http: httpx.Client,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> TraktTokens:
    response = http.post(
        "/oauth/token",
        json={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": TRAKT_REDIRECT_URI,
        },
    )
    if response.status_code == 429:
        # Wait window can be ~45 min — too long to block CI; fail fast and let
        # the next scheduled run retry.
        raise _rate_limit_error(response, "refresh")
    if response.status_code >= 400:
        raise TraktAuthError(
            f"trakt refresh failed: {response.status_code} {response.text}"
        )
    payload = response.json()
    return TraktTokens(
        access_token=payload["access_token"],
        refresh_token=payload["refresh_token"],
    )


def _auth_headers(access_token: str, client_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "trakt-api-version": TRAKT_API_VERSION,
        "trakt-api-key": client_id,
    }


def _parse_pagination_total_pages(response: httpx.Response) -> int | None:
    raw = response.headers.get("X-Pagination-Page-Count")
    if raw and raw.strip().isdigit():
        return int(raw.strip())
    return None


def _get_list_page(
    *,
    http: httpx.Client,
    path: str,
    headers: dict[str, str],
    page: int,
) -> httpx.Response:
    params = {"page": page, "limit": LIST_FETCH_PAGE_LIMIT}
    response = http.get(path, headers=headers, params=params)
    if response.status_code == 429:
        _sleep_for_retry(response)
        response = http.get(path, headers=headers, params=params)
        if response.status_code == 429:
            raise _rate_limit_error(response, f"GET {path} page {page}")
    if response.status_code >= 400:
        raise TraktError(
            f"trakt list fetch failed on page {page}: "
            f"{response.status_code} {response.text}"
        )
    return response


def fetch_list_imdb_ids(
    *,
    http: httpx.Client,
    user: str,
    slug: str,
    access_token: str,
    client_id: str,
) -> set[str]:
    headers = _auth_headers(access_token, client_id)
    path = f"/users/{user}/lists/{slug}/items/movies"
    imdb_ids: set[str] = set()
    for page in range(1, MAX_LIST_FETCH_PAGES + 1):
        response = _get_list_page(http=http, path=path, headers=headers, page=page)
        items = response.json()
        imdb_ids.update(
            item["movie"]["ids"]["imdb"]
            for item in items
            if item.get("movie", {}).get("ids", {}).get("imdb")
        )
        total_pages = _parse_pagination_total_pages(response)
        if total_pages is not None:
            if page >= total_pages:
                break
        else:
            # Trakt convention: a partial page means we're on the last page.
            # Defensive fallback when the page-count header is missing.
            if len(items) < LIST_FETCH_PAGE_LIMIT:
                break
    else:
        # Loop exhausted without natural termination — Trakt's pagination is
        # either misbehaving or the list is larger than we can safely fetch.
        # Fail closed rather than return an incomplete set (which would cause
        # the sync to re-POST "missing" entries forever).
        raise TraktError(
            f"trakt list fetch exceeded {MAX_LIST_FETCH_PAGES} pages "
            f"({len(imdb_ids)} items collected) without pagination terminating"
        )
    return imdb_ids


_IMDB_ID_RE = re.compile(r"^tt\d+$")

# TV season rows carry the *series* IMDb id (a show id). This sync mirrors
# the catalog through Trakt's movie-only endpoints, so posting a show id
# under "movies" can never match: Trakt reports it not-found and the diff
# would re-add it on every scheduled run forever. TV rows are identified by
# the /tv/ release URL enrichment writes for them.
_TV_RELEASE_URL_MARKER = "themoviedb.org/tv/"


def extract_imdb_ids(releases: Iterable[dict]) -> tuple[list[str], list[str]]:
    valid: list[str] = []
    skipped: list[str] = []
    for release in releases:
        imdb_id = release.get("imdb_id")
        is_tv = _TV_RELEASE_URL_MARKER in str(release.get("release_url") or "")
        if not is_tv and isinstance(imdb_id, str) and _IMDB_ID_RE.match(imdb_id):
            valid.append(imdb_id)
        else:
            skipped.append(str(release.get("movie_title", "<unknown>")))
    return valid, skipped


def compute_diff(
    *, current: set[str], desired: set[str]
) -> tuple[list[str], list[str]]:
    to_add = sorted(desired - current)
    to_remove = sorted(current - desired)
    return to_add, to_remove


BATCH_SIZE = 500


def _batched(seq: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(seq), size):
        yield seq[start : start + size]


def _post_movies(
    *,
    http: httpx.Client,
    path: str,
    access_token: str,
    client_id: str,
    imdb_ids: list[str],
) -> None:
    if not imdb_ids:
        return
    headers = _auth_headers(access_token, client_id)
    for i, batch in enumerate(_batched(imdb_ids, BATCH_SIZE), start=1):
        body = {"movies": [{"ids": {"imdb": imdb}} for imdb in batch]}
        response = http.post(path, headers=headers, json=body)
        if response.status_code == 429:
            _sleep_for_retry(response)
            response = http.post(path, headers=headers, json=body)
            if response.status_code == 429:
                raise _rate_limit_error(response, f"POST {path} batch {i}")
        if response.status_code >= 400:
            raise TraktError(
                f"trakt {path} failed on batch {i}: {response.status_code} {response.text}"
            )


def add_items(
    *,
    http: httpx.Client,
    user: str,
    slug: str,
    access_token: str,
    client_id: str,
    imdb_ids: list[str],
) -> None:
    _post_movies(
        http=http,
        path=f"/users/{user}/lists/{slug}/items",
        access_token=access_token,
        client_id=client_id,
        imdb_ids=imdb_ids,
    )


def remove_items(
    *,
    http: httpx.Client,
    user: str,
    slug: str,
    access_token: str,
    client_id: str,
    imdb_ids: list[str],
) -> None:
    _post_movies(
        http=http,
        path=f"/users/{user}/lists/{slug}/items/remove",
        access_token=access_token,
        client_id=client_id,
        imdb_ids=imdb_ids,
    )


@dataclass(frozen=True)
class SyncSummary:
    added: int
    removed: int
    unchanged: int
    skipped: list[str]
    rotated_refresh_token: str


def run_sync(
    *,
    http: httpx.Client,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    user: str,
    slug: str,
    releases_path: Path,
    refresh_token_out: Path | None,
    dry_run: bool,
    allow_empty: bool,
) -> SyncSummary:
    releases = json.loads(releases_path.read_text())
    desired_ids, skipped = extract_imdb_ids(releases)
    if not desired_ids and not allow_empty:
        raise TraktError(
            "refusing to sync an empty desired set; "
            "pass --allow-empty to wipe the trakt list"
        )

    tokens = refresh_access_token(
        http=http,
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
    )

    if refresh_token_out is not None:
        refresh_token_out.write_text(tokens.refresh_token)

    current_ids = fetch_list_imdb_ids(
        http=http,
        user=user,
        slug=slug,
        access_token=tokens.access_token,
        client_id=client_id,
    )
    to_add, to_remove = compute_diff(
        current=current_ids,
        desired=set(desired_ids),
    )
    unchanged = len(set(desired_ids) & current_ids)

    if not dry_run:
        add_items(
            http=http,
            user=user,
            slug=slug,
            access_token=tokens.access_token,
            client_id=client_id,
            imdb_ids=to_add,
        )
        remove_items(
            http=http,
            user=user,
            slug=slug,
            access_token=tokens.access_token,
            client_id=client_id,
            imdb_ids=to_remove,
        )

    return SyncSummary(
        added=len(to_add),
        removed=len(to_remove),
        unchanged=unchanged,
        skipped=skipped,
        rotated_refresh_token=tokens.refresh_token,
    )
