import csv
from pathlib import Path

import pytest

from fel_cleanup import (
    FelListCleaner,
    StaticTmdbResolver,
    _best_tmdb_candidate,
    clean_query_title,
    clean_fel_file,
    load_tmdb_api_key,
)


def read_rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.reader(handle))


def test_clean_fel_file_merges_aliases_with_tmdb_title_and_unique_links(tmp_path: Path):
    source = tmp_path / "FEL.txt"
    report = tmp_path / "report.csv"
    source.write_text(
        "\n".join(
            [
                "Wall E,2008,https://example.test/a|https://example.test/b",
                "WALL·E,2008,https://example.test/b|https://example.test/c",
                "Deadpool and Wolverine,2024,https://example.test/d",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    resolver = StaticTmdbResolver(
        {
            ("Wall E", "2008"): {
                "tmdb_id": "10681",
                "title": "WALL·E",
                "year": "2008",
                "imdb_id": "tt0910970",
            },
            ("WALL·E", "2008"): {
                "tmdb_id": "10681",
                "title": "WALL·E",
                "year": "2008",
                "imdb_id": "tt0910970",
            },
            ("Deadpool and Wolverine", "2024"): {
                "tmdb_id": "533535",
                "title": "Deadpool & Wolverine",
                "year": "2024",
                "imdb_id": "tt6263850",
            },
        }
    )

    summary = clean_fel_file(source, source, report, resolver)

    assert summary.input_rows == 3
    assert summary.output_rows == 2
    assert summary.merged_rows == 1
    assert read_rows(source) == [
        [
            "Deadpool & Wolverine",
            "2024",
            "https://example.test/d",
        ],
        [
            "WALL·E",
            "2008",
            "https://example.test/a|https://example.test/b|https://example.test/c",
        ],
    ]
    report_rows = read_rows(report)
    assert report_rows[0] == [
        "line",
        "input_title",
        "input_year",
        "cleaned_query",
        "action",
        "output_title",
        "output_year",
        "tmdb_id",
        "imdb_id",
        "notes",
    ]
    assert any(row[4] == "merged" and row[7] == "10681" for row in report_rows[1:])


def test_cleaner_merges_same_tmdb_title_year_from_different_ids():
    resolver = StaticTmdbResolver(
        {
            ("Le grand bleu", "1988"): {
                "tmdb_id": "175",
                "title": "The Big Blue",
                "year": "1988",
                "imdb_id": "tt0095250",
            },
            ("The Big Blue", "1988"): {
                "tmdb_id": "657524",
                "title": "The Big Blue",
                "year": "1988",
                "imdb_id": "tt0094738",
            },
        }
    )
    cleaner = FelListCleaner(resolver)

    results = cleaner.clean_rows(
        [
            ["Le grand bleu", "1988", "https://example.test/a"],
            ["The Big Blue", "1988", "https://example.test/b"],
        ]
    )

    assert [[row.title, row.year, row.sources] for row in results.rows] == [
        ["The Big Blue", "1988", ["https://example.test/a", "https://example.test/b"]]
    ]
    assert [entry.action for entry in results.report_entries] == [
        "resolved",
        "merged",
    ]


def test_cleaner_strips_scraped_prefixes_and_drops_mel_rows():
    resolver = StaticTmdbResolver(
        {
            ("John Wick", "2014"): {
                "tmdb_id": "245891",
                "title": "John Wick",
                "year": "2014",
                "imdb_id": "tt2911666",
            },
        }
    )
    cleaner = FelListCleaner(resolver)

    results = cleaner.clean_rows(
        [
            [
                "Quote: Originally Posted by Angry Virginian John Wick",
                "2014",
                "https://example.test/john",
            ],
            ["MEL - 0.083 Mbps Transformers", "2007", "https://example.test/mel"],
        ]
    )

    assert [[row.title, row.year, row.sources] for row in results.rows] == [
        ["John Wick", "2014", ["https://example.test/john"]]
    ]
    assert [entry.action for entry in results.report_entries] == [
        "resolved",
        "dropped",
    ]
    assert "MEL row" in results.report_entries[1].notes


def test_clean_query_title_strips_stacked_fel_and_quote_prefixes():
    title, notes = clean_query_title(
        "4Kult Italy - FEL 12.58 Mb/s Quote: Originally Posted by "
        "Angry Virginian La La Land"
    )

    assert title == "La La Land"
    assert notes == "stripped scraped prefix"


def test_cleaner_uses_embedded_year_when_row_year_is_scrape_noise():
    resolver = StaticTmdbResolver(
        {
            ("Jurassic Park", "1993"): {
                "tmdb_id": "329",
                "title": "Jurassic Park",
                "year": "1993",
                "imdb_id": "tt0107290",
            },
        }
    )
    cleaner = FelListCleaner(resolver)

    results = cleaner.clean_rows([["Jurassic Park 1993", "2025", ""]])

    assert [[row.title, row.year] for row in results.rows] == [
        ["Jurassic Park", "1993"]
    ]
    assert results.report_entries[0].cleaned_query == "Jurassic Park"


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
