import csv
from pathlib import Path

from find_urls import append_cached_urls_to_fel, cache_path_for_url
from fix_lines import strip_leading_commas
from parse_all_fel import parse_all_fel
from parse_and_write import parse_reddit_dump
from parse_movies import find_comment_movies
from test_hash import count_public_cache_hits


def test_parse_all_fel_combines_sources_without_import_side_effects(tmp_path: Path):
    raw = tmp_path / "raw_fel.txt"
    ai = tmp_path / "AI_found.txt"
    output = tmp_path / "FEL.txt"
    raw.write_text("The Matrix (1999)\n", encoding="utf-8")
    ai.write_text("404 Top Gun (1986)\n", encoding="utf-8")

    count = parse_all_fel((raw, ai), output)

    assert count == 2
    assert output.read_text(encoding="utf-8").splitlines() == [
        "The Matrix,1999",
        "Top Gun,1986",
    ]


def test_parse_reddit_dump_writes_main_list_and_comment_additions(tmp_path: Path):
    dump = tmp_path / "reddit_dump.txt"
    output = tmp_path / "reddit.txt"
    dump.write_text(
        "\n".join(
            [
                "List of P7-FEL films:",
                "The Matrix [1999]",
                "---",
                "Top Gun (1986)",
            ]
        ),
        encoding="utf-8",
    )

    count = parse_reddit_dump(dump, output)

    assert count == 2
    assert output.read_text(encoding="utf-8").splitlines() == [
        "The Matrix [1999]",
        "Top Gun [1986]",
    ]


def test_find_comment_movies_reports_comment_candidates(tmp_path: Path):
    dump = tmp_path / "reddit_dump.txt"
    dump.write_text(
        "List of P7-FEL films:\nThe Matrix [1999]\n---\nTop Gun (1986)\n",
        encoding="utf-8",
    )

    assert find_comment_movies(dump) == ["Top Gun (1986)"]


def test_strip_leading_commas(tmp_path: Path):
    path = tmp_path / "reddit.txt"
    path.write_text(", The Matrix\n  Top Gun\n", encoding="utf-8")

    assert strip_leading_commas(path) == 2
    assert path.read_text(encoding="utf-8") == "The Matrix\nTop Gun\n"


def test_append_cached_urls_to_fel_and_count_hashes(tmp_path: Path):
    fel = tmp_path / "FEL.txt"
    expanded = tmp_path / "ai_expanded_urls.txt"
    cache_dir = tmp_path / "html"
    cache_dir.mkdir()
    url = "https://forum.example.test/thread"
    expanded.write_text(f"{url}\n", encoding="utf-8")
    fel.write_text("The Matrix,1999\n", encoding="utf-8")
    cache_path_for_url(cache_dir, url).write_text(
        "The Matrix (1999) Profile 7 FEL", encoding="utf-8"
    )

    assert append_cached_urls_to_fel(fel, expanded, cache_dir) == 1
    with fel.open(encoding="utf-8", newline="") as handle:
        assert list(csv.reader(handle)) == [["The Matrix", "1999", url]]
    assert count_public_cache_hits(expanded, cache_dir) == (1, 1, 1)
