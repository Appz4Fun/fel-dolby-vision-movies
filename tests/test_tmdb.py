from pathlib import Path

import pytest

from tmdb import (
    _best_tmdb_candidate,
    load_tmdb_api_key,
)


def test_best_tmdb_candidate_matches_original_title_when_display_title_differs():
    candidate = _best_tmdb_candidate(
        "Ajeossi",
        "2010",
        [
            {
                "id": 101,
                "title": "The Man from Nowhere",
                "original_title": "아저씨",
                "release_date": "2010-08-04",
            },
            {
                "id": 102,
                "title": "Ajeossi",
                "original_title": "Ajeossi",
                "release_date": "2005-01-01",
            },
        ],
    )

    assert candidate is not None
    assert candidate["id"] == 101


def test_load_tmdb_api_key_reads_dotenv_without_printing_secret(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("TMDB_API_KEY=secret-tmdb-key\n", encoding="utf-8")

    assert load_tmdb_api_key(env_path) == "secret-tmdb-key"


def test_load_tmdb_api_key_requires_value_without_echoing_secret(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("TMDB_API_KEY=\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="TMDB_API_KEY"):
        load_tmdb_api_key(env_path)
