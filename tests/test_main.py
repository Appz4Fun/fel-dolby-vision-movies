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
    assert (output_dir / "dist/index.html").exists()
    output = capsys.readouterr().out
    assert "sources=2" in output
    assert "fetched=2" in output
    assert "releases=2" in output
    assert "errors=0" in output
    assert "session=secret" not in output


def test_scrape_for_titles_routes_google_sheet_urls(
    tmp_path: Path, monkeypatch, capsys
):
    sources_path = tmp_path / "sources_needs_evidence.txt"
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    forum_url = "https://forum.example.test/thread"
    sheet_url = (
        "https://docs.google.com/spreadsheets/d/test-sheet-id/"
        "edit?gid=828864432#gid=828864432"
    )
    sources_path.write_text(f"{forum_url}\n{sheet_url}\n", encoding="utf-8")
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
    assert "needs_evidence=2" in output
    assert "always_fel=0" in output
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


def test_pr_summary_command_writes_body_and_github_outputs(tmp_path: Path, capsys):
    base_path = tmp_path / "base.json"
    previous_path = tmp_path / "previous.json"
    head_path = tmp_path / "head.json"
    body_path = tmp_path / "body.md"
    github_output_path = tmp_path / "github-output.txt"
    new_release = release("New Movie", "https://forum.example.test/new")

    base_path.write_text("[]\n", encoding="utf-8")
    previous_path.write_text("[]\n", encoding="utf-8")
    head_path.write_text(json.dumps([new_release.to_dict()]) + "\n", encoding="utf-8")

    exit_code = main.main(
        [
            "pr-summary",
            "--base-releases",
            str(base_path),
            "--previous-releases",
            str(previous_path),
            "--head-releases",
            str(head_path),
            "--body-output",
            str(body_path),
            "--github-output",
            str(github_output_path),
        ]
    )

    assert exit_code == 0
    assert "| New Movie | Unknown |" in body_path.read_text(encoding="utf-8")
    assert "new_release_count=1" in github_output_path.read_text(encoding="utf-8")
    output = capsys.readouterr().out
    assert "release delta complete" in output
    assert "pending=1" in output
    assert "new=1" in output


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
        source_path: Path,
        output_dir: Path,
        cache_dir: Path,
        workers: int,
        re_enrich: bool,
    ):
        calls.append(("scrape", source_path, output_dir, cache_dir, workers, re_enrich))
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
        ("scrape", sources_path, output_dir, cache_dir, 6, False),
    ]


def test_run_reenrich_flag_parsed(monkeypatch, tmp_path):
    captured = {}

    def fake_scrape(source_path, output_dir, cache_dir, workers, re_enrich):
        captured["re_enrich"] = re_enrich
        return 0

    monkeypatch.setattr(main, "_scrape_for_titles", fake_scrape)
    monkeypatch.setattr(main, "_search_for_sources", lambda *a, **k: 0)
    main.main(["run", "--sources", str(tmp_path / "forums.txt"), "--re-enrich"])
    assert captured["re_enrich"] is True


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
        source_path: Path,
        output_dir: Path,
        cache_dir: Path,
        workers: int,
        re_enrich: bool,
    ):
        calls.append(("scrape", source_path, output_dir, cache_dir, workers, re_enrich))
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
        (
            "scrape",
            sources_path,
            output_dir,
            cache_dir,
            main.DEFAULT_SCRAPE_WORKERS,
            False,
        ),
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
        lambda source_path, output_dir, cache_dir, workers, re_enrich: 3,
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


def test_scrape_for_titles_reenrich_includes_existing_releases(
    tmp_path: Path, monkeypatch
):
    sources_path = tmp_path / "forums.txt"
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    sources_path.write_text("https://forum.example.test/thread\n", encoding="utf-8")
    (output_dir / "data").mkdir(parents=True)
    existing = release("Existing Movie")
    (output_dir / "data/releases.json").write_text(
        json.dumps([existing.to_dict()]), encoding="utf-8"
    )
    published_releases = []

    class FakeFetchResult:
        url = "https://forum.example.test/thread"
        text = "New Movie is confirmed Profile 7 FEL."
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

    monkeypatch.setattr(main.fetcher, "Fetcher", FakeFetcher)
    monkeypatch.setattr(
        main.fel_parser,
        "parse_fel_releases",
        lambda html, source_url: [release("New Movie", source_url)],
    )

    def fake_publish_outputs(releases, output_dir: Path):
        published_releases.extend(releases)
        return releases

    monkeypatch.setattr(main.artifacts, "publish_outputs", fake_publish_outputs)

    exit_code = main._scrape_for_titles(
        sources_path, output_dir, cache_dir, workers=1, re_enrich=True
    )

    assert exit_code == 0
    assert [item.movie_title for item in published_releases] == [
        "Existing Movie",
        "New Movie",
    ]


def test_reddit_url_routes_to_reddit_parser(monkeypatch):
    captured = {}

    def fake_reddit(html, url):
        captured["html"] = html
        captured["url"] = url
        return []

    monkeypatch.setattr(main.reddit_source, "parse_reddit_releases", fake_reddit)

    class FakeFetcher:
        def fetch(self, url):
            from fetcher import FetchResult

            return FetchResult(url=url, text="<reddit html>", from_cache=False)

    job = main._SourceJob(
        url="https://old.reddit.com/r/CoreELEC/comments/x/list/",
        strictness="always-fel",
    )
    result = main._scrape_source(job, FakeFetcher())

    assert result.error == ""
    assert captured["html"] == "<reddit html>"


def test_needs_evidence_reddit_url_routes_to_reddit_parser(monkeypatch):
    list_called = {}
    fel_called = False

    def fake_reddit(html, url):
        list_called["html"] = html
        list_called["url"] = url
        return []

    def fake_fel(html, url):
        nonlocal fel_called
        fel_called = True
        return []

    monkeypatch.setattr(main.reddit_source, "parse_reddit_releases", fake_reddit)
    monkeypatch.setattr(main.fel_parser, "parse_fel_releases", fake_fel)

    class FakeFetcher:
        def fetch(self, url):
            from fetcher import FetchResult

            return FetchResult(url=url, text="<reddit discussion>", from_cache=False)

    job = main._SourceJob(
        url="https://old.reddit.com/r/AndroidTV/comments/x/some_discussion/",
        strictness="needs-evidence",
    )
    main._scrape_source(job, FakeFetcher())

    assert list_called["html"] == "<reddit discussion>"
    assert fel_called is False


def test_scrape_letterboxd_records_detail_page_source_urls():
    base_url = "https://letterboxd.com/mikimajk/list/list-of-dolby-vision-p7-fel-films/"
    first_page = (
        '<a href="/mikimajk/list/list-of-dolby-vision-p7-fel-films/page/2/">2</a>'
        '<li data-item-full-display-name="The Matrix (1999)"></li>'
    )
    second_page = '<li data-item-full-display-name="Never Give Up (1978)"></li>'
    calls = []

    class FakeFetcher:
        def fetch(self, url: str, *, raise_on_error: bool = True):
            calls.append((url, raise_on_error))
            text = second_page if url.endswith("/page/2/") else first_page
            return type(
                "FetchResult",
                (),
                {"url": url, "text": text, "from_cache": False},
            )()

    releases = main._scrape_letterboxd(base_url, FakeFetcher())

    assert calls == [
        (base_url, True),
        (
            "https://letterboxd.com/mikimajk/list/"
            "list-of-dolby-vision-p7-fel-films/page/2/",
            False,
        ),
    ]
    assert [(release.movie_title, release.source_url) for release in releases] == [
        (
            "The Matrix",
            "https://letterboxd.com/mikimajk/list/"
            "list-of-dolby-vision-p7-fel-films/detail/",
        ),
        (
            "Never Give Up",
            "https://letterboxd.com/mikimajk/list/"
            "list-of-dolby-vision-p7-fel-films/detail/page/2/",
        ),
    ]
    assert releases[1].fel_evidence.source_url.endswith("/detail/page/2/")


def test_letterboxd_page_urls_normalize_existing_page_variants():
    root = "https://letterboxd.com/user/list/example/"

    assert main._letterboxd_fetch_page_url(f"{root}page/6/", 2) == (
        "https://letterboxd.com/user/list/example/page/2/"
    )
    assert main._letterboxd_source_page_url(f"{root}detail/page/6/", 6) == (
        "https://letterboxd.com/user/list/example/detail/page/6/"
    )
    assert main._letterboxd_source_page_url(f"{root}detail/", 1) == (
        "https://letterboxd.com/user/list/example/detail/"
    )


def test_github_raw_url_resolves_blob_paths_and_repo_root():
    assert main._github_raw_url("https://github.com/iammarxg/FEL") == (
        "https://raw.githubusercontent.com/iammarxg/FEL/HEAD/README.md"
    )
    assert (
        main._github_raw_url("https://github.com/owner/repo/blob/main/lists/fel.txt")
        == "https://raw.githubusercontent.com/owner/repo/main/lists/fel.txt"
    )
    # nested paths preserved
    assert (
        main._github_raw_url("https://github.com/owner/repo/blob/v1.0/dir/sub/file.md")
        == "https://raw.githubusercontent.com/owner/repo/v1.0/dir/sub/file.md"
    )


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


def test_enrich_if_possible_skips_when_no_tmdb_key(
    real_enrich_if_possible, monkeypatch, capsys
):
    def _no_key(*args, **kwargs):
        raise RuntimeError("missing key")

    monkeypatch.setattr(main.enrich, "load_tmdb_api_key", _no_key)
    real_enrich_if_possible([])

    assert "TMDB enrichment skipped" in capsys.readouterr().out


def test_run_migration_merges_files_and_writes_report(tmp_path, monkeypatch):
    import csv as _csv
    import json as _json

    (tmp_path / "FEL.txt").write_text("Drop,2025,https://reddit.test/list\n", "utf-8")
    (tmp_path / "raw_fel.txt").write_text(
        "Apocalypse Now (1979) FEL - 7.58 Mb/s\n", "utf-8"
    )

    def fake_enrich(releases):
        for release in releases:
            release.tmdb_id = "100" if release.movie_title == "Drop" else "101"

    monkeypatch.setattr(main, "_enrich_if_possible", fake_enrich)

    exit_code = main.run_migration(
        fel_path=tmp_path / "FEL.txt",
        raw_fel_path=tmp_path / "raw_fel.txt",
        output_dir=tmp_path,
        report_path=tmp_path / "data" / "migration_report.csv",
    )

    assert exit_code == 0
    data = _json.loads((tmp_path / "data/releases.json").read_text("utf-8"))
    titles = sorted(item["movie_title"] for item in data)
    assert titles == ["Apocalypse Now", "Drop"]

    rows = list(_csv.DictReader((tmp_path / "data/migration_report.csv").open()))
    assert {row["input_title"] for row in rows} == {"Drop", "Apocalypse Now"}
    assert {row["tmdb_id"] for row in rows} == {"100", "101"}


def test_run_migration_report_marks_unresolved_titles(tmp_path, monkeypatch):
    import csv as _csv

    (tmp_path / "FEL.txt").write_text(
        "Resolved Movie,2020,\nUnresolved Movie,2021,\n", "utf-8"
    )
    (tmp_path / "raw_fel.txt").write_text("", "utf-8")

    def fake_enrich(releases):
        for release in releases:
            if release.movie_title == "Resolved Movie":
                release.tmdb_id = "123"

    monkeypatch.setattr(main, "_enrich_if_possible", fake_enrich)

    main.run_migration(
        fel_path=tmp_path / "FEL.txt",
        raw_fel_path=tmp_path / "raw_fel.txt",
        output_dir=tmp_path,
        report_path=tmp_path / "data" / "migration_report.csv",
    )

    rows = {
        row["input_title"]: row
        for row in _csv.DictReader((tmp_path / "data/migration_report.csv").open())
    }
    assert rows["Resolved Movie"]["tmdb_resolved"] == "yes"
    assert rows["Resolved Movie"]["tmdb_id"] == "123"
    assert rows["Unresolved Movie"]["tmdb_resolved"] == "no"
    assert rows["Unresolved Movie"]["tmdb_id"] == ""


def test_run_migration_reports_dropped_fel_rows(tmp_path, monkeypatch, capsys):
    (tmp_path / "FEL.txt").write_text(
        "Good Movie,2020,\nJunk row with no year,,\n", "utf-8"
    )
    (tmp_path / "raw_fel.txt").write_text("", "utf-8")
    monkeypatch.setattr(main, "_enrich_if_possible", lambda releases: None)

    main.run_migration(
        fel_path=tmp_path / "FEL.txt",
        raw_fel_path=tmp_path / "raw_fel.txt",
        output_dir=tmp_path,
        report_path=tmp_path / "data" / "migration_report.csv",
    )

    out = capsys.readouterr().out
    assert "fel_txt_rows=2" in out
    assert "fel_ingested=1" in out
    assert "fel_dropped=1" in out
