from pathlib import Path
import json

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


def test_search_for_sources_merges_only_accepted_urls(
    tmp_path: Path, monkeypatch, capsys
):
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


def test_scrape_for_titles_fetches_sources_and_writes_artifacts(
    tmp_path: Path, monkeypatch, capsys
):
    sources_path = tmp_path / "forums.txt"
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    urls = [
        "https://forum.example.test/thread-a",
        "https://forum.example.test/thread-b",
    ]
    sources_path.write_text("\n".join(urls) + "\n", encoding="utf-8")
    monkeypatch.setenv("FORUM_COOKIE_HEADER", "session=secret")

    html_by_url = {
        urls[0]: """
            <table>
              <tr><th>Movie</th><th>DV</th></tr>
              <tr><td>Alpha</td><td>Profile 7 FEL English TrueHD Atmos</td></tr>
            </table>
        """,
        urls[1]: "Beta (2024) is confirmed to be Profile 7 FEL with DTS-HD MA.",
    }
    calls = []

    class FakeFetchResult:
        def __init__(self, url: str, text: str):
            self.url = url
            self.text = text
            self.from_cache = False

    class FakeFetcher:
        def __init__(self, cache_dir: Path, cookie_header: str | None):
            calls.append(("init", cache_dir, cookie_header))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def fetch(self, url: str):
            calls.append(("fetch", url))
            return FakeFetchResult(url, html_by_url[url])

    monkeypatch.setattr(main.fetcher, "Fetcher", FakeFetcher)

    exit_code = main.main(
        [
            "scrape-for-titles",
            "--sources",
            str(sources_path),
            "--output-dir",
            str(output_dir),
            "--cache-dir",
            str(cache_dir),
        ]
    )

    assert exit_code == 0
    assert calls == [
        ("init", cache_dir, "session=secret"),
        ("fetch", urls[0]),
        ("fetch", urls[1]),
    ]
    data = json.loads((output_dir / "data/releases.json").read_text(encoding="utf-8"))
    assert [release["movie_title"] for release in data] == ["Beta", "Alpha"]
    assert (output_dir / "README.md").exists()
    assert (output_dir / "links.md").exists()
    assert (output_dir / "dist/index.html").exists()
    output = capsys.readouterr().out
    assert "sources=2" in output
    assert "fetched=2" in output
    assert "releases=2" in output
    assert "errors=0" in output
    assert "session=secret" not in output


def test_scrape_for_titles_continues_after_fetch_errors(
    tmp_path: Path, monkeypatch, capsys
):
    sources_path = tmp_path / "forums.txt"
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    sources_path.write_text(
        "\n".join(
            [
                "https://forum.example.test/failing-thread",
                "https://forum.example.test/working-thread",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class FakeFetchResult:
        url = "https://forum.example.test/working-thread"
        text = "Gamma is confirmed to be Profile 7 FEL with TrueHD."
        from_cache = False

    class FakeFetcher:
        def __init__(self, cache_dir: Path, cookie_header: str | None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def fetch(self, url: str):
            if "failing" in url:
                raise RuntimeError("network unavailable")
            return FakeFetchResult()

    monkeypatch.setattr(main.fetcher, "Fetcher", FakeFetcher)

    exit_code = main.main(
        [
            "scrape-for-titles",
            "--sources",
            str(sources_path),
            "--output-dir",
            str(output_dir),
            "--cache-dir",
            str(cache_dir),
        ]
    )

    assert exit_code == 0
    data = json.loads((output_dir / "data/releases.json").read_text(encoding="utf-8"))
    assert [release["movie_title"] for release in data] == ["Gamma"]
    output = capsys.readouterr().out
    assert "fetched=1" in output
    assert "releases=1" in output
    assert "errors=1" in output


def test_run_searches_for_sources_before_scraping(tmp_path: Path, monkeypatch):
    sources_path = tmp_path / "forums.txt"
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    calls = []

    def fake_search_for_sources(source_path: Path):
        calls.append(("search", source_path))
        return 0

    def fake_scrape_for_titles(source_path: Path, output_dir: Path, cache_dir: Path):
        calls.append(("scrape", source_path, output_dir, cache_dir))
        return 0

    monkeypatch.setattr(main, "_search_for_sources", fake_search_for_sources)
    monkeypatch.setattr(main, "_scrape_for_titles", fake_scrape_for_titles)

    exit_code = main.main(
        [
            "run",
            "--sources",
            str(sources_path),
            "--output-dir",
            str(output_dir),
            "--cache-dir",
            str(cache_dir),
        ]
    )

    assert exit_code == 0
    assert calls == [
        ("search", sources_path),
        ("scrape", sources_path, output_dir, cache_dir),
    ]
