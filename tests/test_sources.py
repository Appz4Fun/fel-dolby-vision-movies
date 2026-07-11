from pathlib import Path

from sources import (
    canonical_source_key,
    merge_confirmed_sources,
    read_source_urls,
    write_source_urls,
)


def test_google_sheet_source_keys_preserve_distinct_numeric_tabs(tmp_path: Path):
    gid_1 = "https://docs.google.com/spreadsheets/d/sheet-id/edit#gid=1"
    gid_1_duplicate = "https://docs.google.com/spreadsheets/d/sheet-id/edit#gid=01"
    gid_2 = "https://docs.google.com/spreadsheets/d/sheet-id/edit#gid=2"

    assert canonical_source_key(gid_1) == canonical_source_key(gid_1_duplicate)
    assert canonical_source_key(gid_1) != canonical_source_key(gid_2)

    path = tmp_path / "google_sheets.txt"
    write_source_urls(path, [gid_1, gid_1_duplicate, gid_2])
    assert read_source_urls(path) == [gid_1, gid_2]


def test_canonical_source_key_preserves_distinct_non_default_ports():
    default_port = "https://forum.example/list"
    custom_port = "https://forum.example:8443/list"

    assert canonical_source_key(default_port) != canonical_source_key(custom_port)


def test_canonical_source_key_tolerates_malformed_port():
    assert canonical_source_key("https://forum.example:notaport/list") == (
        "https://forum.example/list"
    )


def test_read_source_urls_keeps_distinct_non_default_port_sources(tmp_path: Path):
    path = tmp_path / "forums.txt"
    path.write_text(
        "\n".join(
            [
                "https://forum.example/list",
                "https://forum.example:8443/list",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert read_source_urls(path) == [
        "https://forum.example/list",
        "https://forum.example:8443/list",
    ]


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


def test_read_source_urls_missing_file_returns_empty(tmp_path: Path):
    assert read_source_urls(tmp_path / "missing.txt") == []


def test_read_source_urls_dedupes_reddit_thread_variants(tmp_path: Path):
    path = tmp_path / "forums.txt"
    path.write_text(
        "\n".join(
            [
                "https://old.reddit.com/r/AndroidTV/comments/18kmowh/dolby_vision_profile_7_full_enhancement_layer_fel",
                "https://www.reddit.com/r/AndroidTV/comments/18kmowh/dolby_vision_profile_7_full_enhancement_layer_fel",
                "https://www.reddit.com/r/AndroidTV/comments/18kmowh/dolby_vision_profile_7_full_enhancement_layer_fel?tl=hi-latn",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert read_source_urls(path) == [
        "https://old.reddit.com/r/AndroidTV/comments/18kmowh/dolby_vision_profile_7_full_enhancement_layer_fel"
    ]


def test_read_source_urls_dedupes_reddit_non_thread_variants(tmp_path: Path):
    path = tmp_path / "forums.txt"
    path.write_text(
        "\n".join(
            [
                "https://old.reddit.com/r/AndroidTV?sort=new",
                "https://www.reddit.com/r/AndroidTV?sort=top",
                "https://old.reddit.com/r/AndroidTV/comments",
                "https://www.reddit.com/r/AndroidTV/comments?sort=top",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert read_source_urls(path) == [
        "https://old.reddit.com/r/AndroidTV?sort=new",
        "https://old.reddit.com/r/AndroidTV/comments",
    ]


def test_read_source_urls_dedupes_avsforum_thread_slug_variants(tmp_path: Path):
    path = tmp_path / "forums.txt"
    path.write_text(
        "\n".join(
            [
                "https://www.avsforum.com/threads/dolby-vision-uhd-blu-ray-movies.2945964/",
                "https://www.avsforum.com/threads/dolby-vision-uhd-blu-ray-release-list.2945964/",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert read_source_urls(path) == [
        "https://www.avsforum.com/threads/dolby-vision-uhd-blu-ray-movies.2945964/"
    ]


def test_read_source_urls_normalizes_avsforum_non_thread_urls(tmp_path: Path):
    path = tmp_path / "forums.txt"
    path.write_text(
        "\n".join(
            [
                "https://www.avsforum.com/forums/home-theater.9/?b=2&a=1",
                "https://avsforum.com/forums/home-theater.9/?a=1&b=2",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert read_source_urls(path) == [
        "https://www.avsforum.com/forums/home-theater.9/?b=2&a=1"
    ]


def test_merge_confirmed_sources_rewrites_canonical_duplicates(tmp_path: Path):
    path = tmp_path / "forums.txt"
    path.write_text(
        "\n".join(
            [
                "https://forum.makemkv.com/forum/viewtopic.php?t=21937",
                "https://forum.makemkv.com/forum/viewtopic.php?f=12&t=21937",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    changed = merge_confirmed_sources(path, confirmed_urls=[])

    assert changed is True
    assert path.read_text(encoding="utf-8") == (
        "https://forum.makemkv.com/forum/viewtopic.php?t=21937\n"
    )


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
