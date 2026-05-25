"""Sync the FEL releases dataset to a Trakt custom list."""

from __future__ import annotations

from dataclasses import dataclass

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
