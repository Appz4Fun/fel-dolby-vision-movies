from pathlib import Path

from fel_dolby_vision_movies import main


def test_search_for_sources_without_api_key_exits_zero_and_leaves_sources_unchanged(
    tmp_path: Path, monkeypatch, capsys
):
    sources_path = tmp_path / "forums.txt"
    original_text = "https://forum.example.test/seed\n"
    sources_path.write_text(original_text, encoding="utf-8")
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)

    exit_code = main.main(["search-for-sources", "--sources", str(sources_path)])

    assert exit_code == 0
    assert sources_path.read_text(encoding="utf-8") == original_text
    output = capsys.readouterr().out
    assert "Brave unavailable" in output
    assert "added=0" in output


def test_search_for_sources_merges_only_accepted_urls(tmp_path: Path, monkeypatch, capsys):
    sources_path = tmp_path / "forums.txt"
    sources_path.write_text("https://forum.example.test/seed\n", encoding="utf-8")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "secret-token")

    class FakeDiscoveryResult:
        brave_available = True
        queries = ["query one", "query two"]
        raw_url_count = 3
        rejected_url_count = 1
        candidate_urls = [
            "https://forum.example.test/seed",
            "https://forum.example.test/dolby-vision-profile-7-fel-uhd-blu-ray",
        ]

    def fake_discover_source_candidates(api_key: str):
        assert api_key == "secret-token"
        return FakeDiscoveryResult()

    monkeypatch.setattr(
        main.discovery,
        "discover_source_candidates",
        fake_discover_source_candidates,
    )

    exit_code = main.main(["search-for-sources", "--sources", str(sources_path)])

    assert exit_code == 0
    assert sources_path.read_text(encoding="utf-8").splitlines() == [
        "https://forum.example.test/seed",
        "https://forum.example.test/dolby-vision-profile-7-fel-uhd-blu-ray",
    ]
    output = capsys.readouterr().out
    assert "existing=1" in output
    assert "candidates=2" in output
    assert "added=1" in output
    assert "secret-token" not in output


def test_placeholder_commands_keep_clear_not_implemented_path(capsys):
    exit_code = main.main(["scrape-for-titles", "--sources", "forums.txt"])

    assert exit_code == 1
    assert capsys.readouterr().out == "scrape-for-titles is not implemented yet\n"
