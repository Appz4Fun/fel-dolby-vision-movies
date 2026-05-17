from pathlib import Path

from fel_dolby_vision_movies.sources import (
    merge_confirmed_sources,
    read_source_urls,
    write_source_urls,
)


def test_read_source_urls_ignores_blanks_and_comments(tmp_path: Path):
    path = tmp_path / "forums.txt"
    path.write_text(
        "\n# seed\nhttps://example.test/a\n\nhttps://example.test/a\nhttps://example.test/b\n",
        encoding="utf-8",
    )
    assert read_source_urls(path) == [
        "https://example.test/a",
        "https://example.test/b",
    ]


def test_merge_confirmed_sources_adds_only_confirmed(tmp_path: Path):
    path = tmp_path / "forums.txt"
    write_source_urls(path, ["https://example.test/a"])
    changed = merge_confirmed_sources(
        path,
        confirmed_urls=["https://example.test/b", "https://example.test/a"],
    )
    assert changed is True
    assert read_source_urls(path) == [
        "https://example.test/a",
        "https://example.test/b",
    ]


def test_merge_confirmed_sources_noops_without_confirmed_urls(tmp_path: Path):
    path = tmp_path / "forums.txt"
    write_source_urls(path, ["https://example.test/a"])
    changed = merge_confirmed_sources(path, confirmed_urls=[])
    assert changed is False
    assert read_source_urls(path) == ["https://example.test/a"]
