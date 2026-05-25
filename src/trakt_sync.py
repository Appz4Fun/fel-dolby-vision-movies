"""Sync the FEL releases dataset to a Trakt custom list."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

import httpx


TRAKT_BASE_URL = "https://api.trakt.tv"
TRAKT_REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"
TRAKT_API_VERSION = "2"


class TraktError(RuntimeError):
    """Base class for Trakt sync errors."""


class TraktAuthError(TraktError):
    """OAuth refresh failed."""


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


def fetch_list_imdb_ids(
    *,
    http: httpx.Client,
    user: str,
    slug: str,
    access_token: str,
    client_id: str,
) -> set[str]:
    response = http.get(
        f"/users/{user}/lists/{slug}/items/movies",
        headers=_auth_headers(access_token, client_id),
    )
    if response.status_code >= 400:
        raise TraktError(
            f"trakt list fetch failed: {response.status_code} {response.text}"
        )
    items = response.json()
    return {
        item["movie"]["ids"]["imdb"]
        for item in items
        if item.get("movie", {}).get("ids", {}).get("imdb")
    }


_IMDB_ID_RE = re.compile(r"^tt\d+$")


def extract_imdb_ids(releases: Iterable[dict]) -> tuple[list[str], list[str]]:
    valid: list[str] = []
    skipped: list[str] = []
    for release in releases:
        imdb_id = release.get("imdb_id")
        if isinstance(imdb_id, str) and _IMDB_ID_RE.match(imdb_id):
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
    for batch in _batched(imdb_ids, BATCH_SIZE):
        body = {"movies": [{"ids": {"imdb": imdb}} for imdb in batch]}
        response = http.post(path, headers=headers, json=body)
        if response.status_code >= 400:
            raise TraktError(
                f"trakt {path} failed: {response.status_code} {response.text}"
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
