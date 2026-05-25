from __future__ import annotations

import json

import httpx
import pytest

import trakt_sync


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler), base_url=trakt_sync.TRAKT_BASE_URL
    )


def test_refresh_access_token_returns_tokens_and_rotates_refresh():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "access_token": "new-access",
                "refresh_token": "rotated-refresh",
                "expires_in": 7776000,
                "token_type": "Bearer",
            },
        )

    with _mock_client(handler) as client:
        tokens = trakt_sync.refresh_access_token(
            http=client,
            client_id="cid",
            client_secret="csecret",
            refresh_token="old-refresh",
        )

    assert tokens.access_token == "new-access"
    assert tokens.refresh_token == "rotated-refresh"
    assert captured["url"].endswith("/oauth/token")
    assert captured["body"] == {
        "grant_type": "refresh_token",
        "refresh_token": "old-refresh",
        "client_id": "cid",
        "client_secret": "csecret",
        "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
    }


def test_refresh_access_token_raises_on_non_2xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_grant"})

    with _mock_client(handler) as client:
        with pytest.raises(trakt_sync.TraktAuthError):
            trakt_sync.refresh_access_token(
                http=client,
                client_id="cid",
                client_secret="csecret",
                refresh_token="old-refresh",
            )
