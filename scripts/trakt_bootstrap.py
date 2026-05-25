#!/usr/bin/env python3
"""One-time Trakt OAuth device-code flow bootstrap.

Run once locally to mint the initial (access_token, refresh_token) pair, then
copy the refresh_token into the TRAKT_REFRESH_TOKEN GitHub secret.

Usage:
    TRAKT_APP_CLIENT_ID=... TRAKT_APP_CLIENT_SECRET=... \
        python scripts/trakt_bootstrap.py

Tokens are printed to stdout. Treat them as secrets.
"""

from __future__ import annotations

import os
import sys
import time

import httpx


TRAKT_BASE_URL = "https://api.trakt.tv"


def main() -> int:
    client_id = os.environ.get("TRAKT_APP_CLIENT_ID")
    client_secret = os.environ.get("TRAKT_APP_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("set TRAKT_APP_CLIENT_ID and TRAKT_APP_CLIENT_SECRET before running.")
        return 2

    with httpx.Client(base_url=TRAKT_BASE_URL, timeout=30.0) as http:
        device_response = http.post("/oauth/device/code", json={"client_id": client_id})
        if device_response.status_code >= 400:
            print(
                f"device code request failed: {device_response.status_code} {device_response.text}"
            )
            return 1
        device = device_response.json()

        print()
        print("=" * 60)
        print(f"  Open this URL: {device['verification_url']}")
        print(f"  Enter code:    {device['user_code']}")
        print("=" * 60)
        print()
        print(
            f"Polling every {device['interval']}s; expires in {device['expires_in']}s."
        )

        deadline = time.time() + device["expires_in"]
        while time.time() < deadline:
            time.sleep(device["interval"])
            token_response = http.post(
                "/oauth/device/token",
                json={
                    "code": device["device_code"],
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            if token_response.status_code == 200:
                payload = token_response.json()
                print()
                print("SUCCESS. Copy these into GitHub secrets:")
                print()
                print(f"  TRAKT_REFRESH_TOKEN = {payload['refresh_token']}")
                print()
                print(
                    "(access_token is not stored; the workflow refreshes on every run.)"
                )
                return 0
            if token_response.status_code == 400:
                continue  # pending — keep polling
            if token_response.status_code in (404, 410):
                print("device code expired; rerun the script.")
                return 1
            if token_response.status_code == 409:
                print("already used; rerun the script for a fresh code.")
                return 1
            if token_response.status_code == 429:
                time.sleep(device["interval"])  # slow down
                continue
            print(
                f"unexpected status: {token_response.status_code} {token_response.text}"
            )
            return 1

        print("timed out waiting for approval.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
