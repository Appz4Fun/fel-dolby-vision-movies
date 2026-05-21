from pathlib import Path
import json

import pytest

import main
from models import FelEvidence, FelRelease


def release(
    title: str, source_url: str = "https://forum.example.test/thread"
) -> FelRelease:
    return FelRelease(
        movie_title=title,
        fel_evidence=FelEvidence(
            source_url=source_url,
            quote=f"{title} is confirmed Profile 7 FEL",
            evidence_type="fixture",
        ),
    )


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


def test_scrape_for_titles_includes_default_google_sheets_file(
    tmp_path: Path, monkeypatch, capsys
):
    sources_path = tmp_path / "forums.txt"
    google_sheets_path = tmp_path / "google_sheets.txt"
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    forum_url = "https://forum.example.test/thread"
    sheet_url = (
        "https://docs.google.com/spreadsheets/d/test-sheet-id/"
        "edit?gid=828864432#gid=828864432"
    )
    sources_path.write_text(f"{forum_url}\n", encoding="utf-8")
    google_sheets_path.write_text(f"{sheet_url}\n", encoding="utf-8")
    fetches = []

    class FakeFetchResult:
        def __init__(self, url: str, text: str):
            self.url = url
            self.text = text
            self.from_cache = False

    class FakeFetcher:
        def __init__(self, cache_dir: Path, cookie_header: str | None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def fetch(self, url: str):
            fetches.append(url)
            if url == forum_url:
                return FakeFetchResult(
                    url,
                    "Forum Movie is confirmed to be Profile 7 FEL.",
                )
            assert url == (
                "https://docs.google.com/spreadsheets/d/test-sheet-id/"
                "gviz/tq?tqx=out:csv&gid=828864432"
            )
            return FakeFetchResult(
                url,
                "Movie Name,DV Source\nSheet.Movie.2020,BD FEL\n",
            )

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
    assert fetches == [
        forum_url,
        "https://docs.google.com/spreadsheets/d/test-sheet-id/"
        "gviz/tq?tqx=out:csv&gid=828864432",
    ]
    data = json.loads((output_dir / "data/releases.json").read_text(encoding="utf-8"))
    assert [release["movie_title"] for release in data] == [
        "Sheet Movie",
        "Forum Movie",
    ]
    output = capsys.readouterr().out
    assert "sources=2" in output
    assert "google_sheets=1" in output
    assert "releases=2" in output


def test_compare_found_uses_ai_flag_without_printing_secret(
    tmp_path: Path, monkeypatch, capsys
):
    sources_path = tmp_path / "forums.txt"
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    sources_path.write_text("https://forum.example.test/thread\n", encoding="utf-8")
    calls = []

    def fake_compare_found(
        source_path: Path,
        output_dir: Path,
        cache_dir: Path,
        workers: int,
        use_ai: bool,
        ai_limit: int | None,
    ):
        calls.append((source_path, output_dir, cache_dir, workers, use_ai, ai_limit))
        return {
            "AI_found": 1,
            "PY_found": 1,
            "overlap": 1,
            "AI_only": 0,
            "PY_only": 0,
        }

    monkeypatch.setenv("OPENAI_API_KEY", "secret-token")
    monkeypatch.setattr(main.compare, "compare_found", fake_compare_found)

    exit_code = main.main(
        [
            "compare-found",
            "--sources",
            str(sources_path),
            "--output-dir",
            str(output_dir),
            "--cache-dir",
            str(cache_dir),
            "--workers",
            "2",
            "--use-ai",
            "--ai-limit",
            "3",
        ]
    )

    assert exit_code == 0
    assert calls == [(sources_path, output_dir, cache_dir, 2, True, 3)]
    output = capsys.readouterr().out
    assert "compare complete" in output
    assert "secret-token" not in output


def test_clean_fel_command_uses_tmdb_cleaner_without_printing_secret(
    tmp_path: Path, monkeypatch, capsys
):
    fel_path = tmp_path / "FEL.txt"
    report_path = tmp_path / "report.csv"
    cache_path = tmp_path / "cache.json"
    env_path = tmp_path / ".env"
    fel_path.write_text("Wall E,2008,https://example.test/a\n", encoding="utf-8")
    env_path.write_text("TMDB_API_KEY=secret-tmdb-key\n", encoding="utf-8")
    calls = []

    class FakeResolver:
        def __init__(self, api_key, cache_path):
            calls.append(("init", api_key, cache_path))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

    def fake_clean_fel_file(input_path, output_path, report_path_arg, resolver):
        calls.append(("clean", input_path, output_path, report_path_arg, resolver))
        return main.fel_cleanup.CleanupSummary(
            input_rows=1,
            output_rows=1,
            dropped_rows=0,
            resolved_rows=1,
            unresolved_rows=0,
            merged_rows=0,
        )

    monkeypatch.setattr(main.fel_cleanup, "TmdbResolver", FakeResolver)
    monkeypatch.setattr(main.fel_cleanup, "clean_fel_file", fake_clean_fel_file)

    exit_code = main.main(
        [
            "clean-fel",
            "--input",
            str(fel_path),
            "--output",
            str(fel_path),
            "--report",
            str(report_path),
            "--cache",
            str(cache_path),
            "--env",
            str(env_path),
        ]
    )

    assert exit_code == 0
    assert calls[0] == ("init", "secret-tmdb-key", cache_path)
    assert calls[1][0:4] == ("clean", fel_path, fel_path, report_path)
    output = capsys.readouterr().out
    assert "FEL cleanup complete" in output
    assert "secret-tmdb-key" not in output


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


def test_scrape_for_titles_dedupes_parser_results_before_writing(
    tmp_path: Path, monkeypatch, capsys
):
    sources_path = tmp_path / "forums.txt"
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    sources_path.write_text(
        "\n".join(
            [
                "https://forum.example.test/thread-a",
                "https://forum.example.test/thread-b",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    duplicated_release = release("Duplicate")
    unique_release = release("Unique", "https://forum.example.test/thread-b")
    published_releases = []

    class FakeFetchResult:
        def __init__(self, url: str):
            self.url = url
            self.text = f"<html>{url}</html>"
            self.from_cache = False

    class FakeFetcher:
        def __init__(self, cache_dir: Path, cookie_header: str | None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def fetch(self, url: str):
            return FakeFetchResult(url)

    def fake_parse_fel_releases(html: str, source_url: str):
        if source_url.endswith("thread-a"):
            return [duplicated_release]
        return [duplicated_release, unique_release]

    def fake_publish_outputs(releases, output_dir: Path):
        published_releases.extend(releases)
        return releases

    monkeypatch.setattr(main.fetcher, "Fetcher", FakeFetcher)
    monkeypatch.setattr(main.fel_parser, "parse_fel_releases", fake_parse_fel_releases)
    monkeypatch.setattr(main.artifacts, "publish_outputs", fake_publish_outputs)

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
    assert published_releases == [duplicated_release, unique_release]
    output = capsys.readouterr().out
    assert "releases=2" in output


def test_scrape_for_titles_uses_configured_worker_count(
    tmp_path: Path, monkeypatch, capsys
):
    sources_path = tmp_path / "forums.txt"
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    urls = [f"https://forum{i}.example.test/thread" for i in range(5)]
    sources_path.write_text("\n".join(urls) + "\n", encoding="utf-8")
    executor_workers = []

    class ImmediateExecutor:
        def __init__(self, max_workers: int):
            executor_workers.append(max_workers)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def map(self, func, iterable):
            for item in iterable:
                yield func(item)

    class FakeFetchResult:
        def __init__(self, url: str):
            self.url = url
            self.text = f"{url} is confirmed to be Profile 7 FEL."
            self.from_cache = False

    class FakeFetcher:
        def __init__(self, cache_dir: Path, cookie_header: str | None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def fetch(self, url: str):
            return FakeFetchResult(url)

    monkeypatch.setattr(main, "ThreadPoolExecutor", ImmediateExecutor, raising=False)
    monkeypatch.setattr(main.fetcher, "Fetcher", FakeFetcher)
    monkeypatch.setattr(
        main.fel_parser,
        "parse_fel_releases",
        lambda html, source_url: [release(source_url.split("/")[2], source_url)],
    )

    exit_code = main.main(
        [
            "scrape-for-titles",
            "--sources",
            str(sources_path),
            "--output-dir",
            str(output_dir),
            "--cache-dir",
            str(cache_dir),
            "--workers",
            "3",
        ]
    )

    assert exit_code == 0
    assert executor_workers == [3]
    output = capsys.readouterr().out
    assert "sources=5" in output
    assert "releases=5" in output


def test_scrape_for_titles_caps_default_workers_to_source_count(
    tmp_path: Path, monkeypatch
):
    sources_path = tmp_path / "forums.txt"
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    sources_path.write_text("https://forum.example.test/thread\n", encoding="utf-8")
    executor_workers = []

    class ImmediateExecutor:
        def __init__(self, max_workers: int):
            executor_workers.append(max_workers)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def map(self, func, iterable):
            for item in iterable:
                yield func(item)

    class FakeFetchResult:
        url = "https://forum.example.test/thread"
        text = "Alpha is confirmed to be Profile 7 FEL."
        from_cache = False

    class FakeFetcher:
        def __init__(self, cache_dir: Path, cookie_header: str | None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def fetch(self, url: str):
            return FakeFetchResult()

    monkeypatch.setattr(main, "ThreadPoolExecutor", ImmediateExecutor, raising=False)
    monkeypatch.setattr(main.fetcher, "Fetcher", FakeFetcher)
    monkeypatch.setattr(
        main.fel_parser,
        "parse_fel_releases",
        lambda html, source_url: [release("Alpha", source_url)],
    )

    assert (
        main.main(
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
        == 0
    )
    assert executor_workers == [1]


def test_scrape_for_titles_rejects_invalid_worker_count(tmp_path: Path):
    sources_path = tmp_path / "forums.txt"
    sources_path.write_text("https://forum.example.test/thread\n", encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        main.main(
            [
                "scrape-for-titles",
                "--sources",
                str(sources_path),
                "--workers",
                "0",
            ]
        )

    assert error.value.code == 2


def test_scrape_for_titles_fails_when_sources_file_is_missing(
    tmp_path: Path, monkeypatch, capsys
):
    sources_path = tmp_path / "missing-forums.txt"
    output_dir = tmp_path / "out"

    def fail_publish_outputs(releases, output_dir: Path):
        raise AssertionError("should not write artifacts without sources")

    monkeypatch.setattr(main.artifacts, "publish_outputs", fail_publish_outputs)

    exit_code = main.main(
        [
            "scrape-for-titles",
            "--sources",
            str(sources_path),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "sources file not found" in output
    assert str(sources_path) in output


def test_scrape_for_titles_publishes_empty_outputs_when_no_releases_found(
    tmp_path: Path, monkeypatch, capsys
):
    sources_path = tmp_path / "forums.txt"
    output_dir = tmp_path / "out"
    sources_path.write_text("https://forum.example.test/thread\n", encoding="utf-8")
    published_releases = []

    class FakeFetcher:
        def __init__(self, cache_dir: Path, cookie_header: str | None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def fetch(self, url: str):
            raise RuntimeError("network unavailable")

    def fake_publish_outputs(releases, output_dir: Path):
        published_releases.extend(releases)
        return releases

    monkeypatch.setattr(main.fetcher, "Fetcher", FakeFetcher)
    monkeypatch.setattr(main.artifacts, "publish_outputs", fake_publish_outputs)

    exit_code = main.main(
        [
            "scrape-for-titles",
            "--sources",
            str(sources_path),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    assert published_releases == []
    output = capsys.readouterr().out
    assert "fetched=0" in output
    assert "errors=1" in output
    assert "releases=0" in output


def test_run_searches_for_sources_before_scraping(tmp_path: Path, monkeypatch):
    sources_path = tmp_path / "forums.txt"
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    calls = []

    def fake_search_for_sources(source_path: Path):
        calls.append(("search", source_path))
        return 0

    def fake_scrape_for_titles(
        source_path: Path, output_dir: Path, cache_dir: Path, workers: int
    ):
        calls.append(("scrape", source_path, output_dir, cache_dir, workers))
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
            "--workers",
            "6",
        ]
    )

    assert exit_code == 0
    assert calls == [
        ("search", sources_path),
        ("scrape", sources_path, output_dir, cache_dir, 6),
    ]


def test_run_scrapes_existing_sources_after_discovery_failure(
    tmp_path: Path, monkeypatch, capsys
):
    sources_path = tmp_path / "forums.txt"
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    sources_path.write_text("https://forum.example.test/thread\n", encoding="utf-8")
    calls = []

    def fake_search_for_sources(source_path: Path):
        calls.append(("search", source_path))
        return 1

    def fake_scrape_for_titles(
        source_path: Path, output_dir: Path, cache_dir: Path, workers: int
    ):
        calls.append(("scrape", source_path, output_dir, cache_dir, workers))
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
        ("scrape", sources_path, output_dir, cache_dir, main.DEFAULT_SCRAPE_WORKERS),
    ]
    output = capsys.readouterr().out
    assert "source discovery failed; scraping existing sources" in output


def test_run_returns_scrape_exit_code_after_discovery_failure(
    tmp_path: Path, monkeypatch
):
    sources_path = tmp_path / "forums.txt"
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    sources_path.write_text("https://forum.example.test/thread\n", encoding="utf-8")

    monkeypatch.setattr(main, "_search_for_sources", lambda source_path: 1)
    monkeypatch.setattr(
        main,
        "_scrape_for_titles",
        lambda source_path, output_dir, cache_dir, workers: 3,
    )

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

    assert exit_code == 3


def test_run_without_brave_key_uses_existing_sources_and_does_not_print_secret(
    tmp_path: Path, monkeypatch, capsys
):
    sources_path = tmp_path / "forums.txt"
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    sources_path.write_text("https://forum.example.test/thread\n", encoding="utf-8")
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    calls = []

    class FakeFetcher:
        def __init__(self, cache_dir: Path, cookie_header: str | None):
            calls.append(("init", cache_dir, cookie_header))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def fetch(self, url: str):
            calls.append(("fetch", url))
            return type(
                "FetchResult",
                (),
                {
                    "url": url,
                    "text": "Existing Source is confirmed Profile 7 FEL.",
                    "from_cache": False,
                },
            )()

    monkeypatch.setattr(main.fetcher, "Fetcher", FakeFetcher)
    monkeypatch.setattr(
        main.fel_parser,
        "parse_fel_releases",
        lambda html, source_url: [release("Existing Source", source_url)],
    )

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
        ("init", cache_dir, None),
        ("fetch", "https://forum.example.test/thread"),
    ]
    output = capsys.readouterr().out
    assert "Brave unavailable" in output
    assert "BRAVE_SEARCH_API_KEY" in output
