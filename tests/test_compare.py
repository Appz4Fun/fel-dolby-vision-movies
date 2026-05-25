from pathlib import Path
import csv
import hashlib
import json

import compare
import main
from models import FelEvidence, FelRelease


def release(title: str, year: str, source_url: str, quote: str) -> FelRelease:
    return FelRelease(
        movie_title=title,
        release_date=year,
        fel_evidence=FelEvidence(
            source_url=source_url,
            quote=quote,
            evidence_type="fixture",
        ),
    )


def test_write_comparison_outputs_source_and_match_percent(tmp_path: Path):
    ai_candidates = [
        compare.FoundCandidate(
            title="The Matrix",
            year="1999",
            source_url="https://forum.example.test/thread",
            evidence="The Matrix (1999) FEL",
            extraction_method="ai",
        ),
        compare.FoundCandidate(
            title="Only AI",
            year="2024",
            source_url="https://forum.example.test/thread",
            evidence="Only AI (2024) FEL",
            extraction_method="ai",
        ),
    ]
    py_releases = [
        release(
            "The Matrix",
            "1999",
            "https://forum.example.test/thread",
            "The Matrix is confirmed Profile 7 FEL",
        ),
        release(
            "Only Python",
            "2020",
            "https://forum.example.test/other",
            "Only Python is confirmed Profile 7 FEL",
        ),
    ]

    summary = compare.write_comparison_outputs(
        ai_candidates=ai_candidates,
        py_releases=py_releases,
        output_dir=tmp_path,
    )

    assert summary == {
        "AI_found": 2,
        "PY_found": 2,
        "overlap": 1,
        "AI_only": 1,
        "PY_only": 1,
    }
    ai_rows = list(csv.DictReader((tmp_path / "AI_found.csv").open()))
    py_rows = list(csv.DictReader((tmp_path / "PY_found.csv").open()))
    assert ai_rows[0]["source_url"] == "https://forum.example.test/thread"
    assert ai_rows[0]["match_percent"] == "100"
    assert ai_rows[1]["match_percent"] != "100"
    assert py_rows[0]["source_url"] == "https://forum.example.test/thread"
    assert py_rows[0]["match_percent"] == "100"
    assert (tmp_path / "AI_found.txt").read_text(encoding="utf-8").splitlines() == [
        "The Matrix (1999) | match=100% | source=https://forum.example.test/thread",
        "Only AI (2024) | match=50% | source=https://forum.example.test/thread",
    ]
    assert (tmp_path / "AI_PY_overlap.txt").read_text(
        encoding="utf-8"
    ).splitlines() == [
        "The Matrix (1999) | match=100% | source=https://forum.example.test/thread"
    ]
    assert (tmp_path / "AI_only.txt").read_text(encoding="utf-8").splitlines() == [
        "Only AI (2024) | match=50% | source=https://forum.example.test/thread"
    ]
    assert (tmp_path / "PY_only.txt").read_text(encoding="utf-8").splitlines() == [
        "Only Python (2020) | match=50% | source=https://forum.example.test/other"
    ]


def test_ai_client_loads_env_without_printing_secret(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-token")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.test/codex")

    settings = compare.AISettings.from_env()

    assert settings.api_key == "secret-token"
    assert settings.base_url == "https://api.example.test/codex"
    assert settings.model == "gpt-5.5"
    assert settings.reasoning_effort == "xhigh"


def test_ai_extraction_prompt_rejects_list_ordinals():
    assert "Do not include list numbering" in compare.AI_EXTRACTION_SYSTEM_PROMPT
    assert "281 Nobody" in compare.AI_EXTRACTION_SYSTEM_PROMPT


def test_candidates_from_ai_response_accepts_responses_sse():
    body = "\n".join(
        [
            "event: response.created",
            'data: {"type":"response.created","response":{"status":"in_progress"}}',
            "",
            "event: response.output_text.done",
            (
                'data: {"type":"response.output_text.done","text":'
                '"{\\"items\\":[{\\"title\\":\\"The Matrix\\",'
                '\\"year\\":\\"1999\\",\\"evidence\\":\\"Profile 7 FEL\\"}]}" }'
            ),
            "",
            "event: response.content_part.done",
            (
                'data: {"type":"response.content_part.done","part":{"text":'
                '"{\\"items\\":[{\\"title\\":\\"The Matrix\\",'
                '\\"year\\":\\"1999\\",\\"evidence\\":\\"Profile 7 FEL\\"}]}" }}'
            ),
            "",
        ]
    )

    candidates = compare._candidates_from_ai_response_text(
        body,
        "https://forum.example.test/thread",
    )

    assert candidates == [
        compare.FoundCandidate(
            title="The Matrix",
            year="1999",
            source_url="https://forum.example.test/thread",
            evidence="Profile 7 FEL",
            extraction_method="ai",
        )
    ]


def test_candidates_from_ai_response_strips_list_ordinals():
    body = json.dumps(
        {
            "output_text": json.dumps(
                {
                    "items": [
                        {
                            "title": "281 Nobody",
                            "year": "2021",
                            "evidence": "281 Nobody (2021)",
                        }
                    ]
                }
            )
        }
    )

    candidates = compare._candidates_from_ai_response_text(
        body,
        "https://forum.example.test/thread",
    )

    assert [candidate.title for candidate in candidates] == ["Nobody"]


def test_compare_found_with_ai_fetches_sources_and_marks_origin(
    tmp_path: Path, monkeypatch
):
    sources_path = tmp_path / "forums.txt"
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    source_url = "https://forum.example.test/thread"
    sources_path.write_text(f"{source_url}\n", encoding="utf-8")

    class FakeFetchResult:
        url = source_url
        text = "The Matrix (1999) is Profile 7 FEL."
        error = None

    class FakeFetcher:
        def __init__(self, cache_dir: Path, cookie_header: str | None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def fetch(self, url: str, *, raise_on_error: bool = True):
            assert url == source_url
            return FakeFetchResult()

    class FakeAIClient:
        def __init__(self, settings: compare.AISettings):
            self.settings = settings

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def extract_candidates(self, source_url: str, text: str):
            assert text == FakeFetchResult.text
            return [
                compare.FoundCandidate(
                    title="The Matrix",
                    year="1999",
                    source_url=source_url,
                    evidence="The Matrix (1999) is Profile 7 FEL.",
                    extraction_method="ai",
                )
            ]

    def fake_scrape_for_titles(
        source_path: Path, scrape_output: Path, cache_dir: Path, workers: int
    ):
        data_dir = scrape_output / "data"
        data_dir.mkdir(parents=True)
        data_dir.joinpath("releases.json").write_text(
            json.dumps(
                [
                    release(
                        "The Matrix",
                        "1999",
                        source_url,
                        "The Matrix (1999) is Profile 7 FEL.",
                    ).to_dict()
                ]
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setenv("OPENAI_API_KEY", "secret-token")
    monkeypatch.setattr(compare.fetcher, "Fetcher", FakeFetcher)
    monkeypatch.setattr(compare, "AIClient", FakeAIClient)
    monkeypatch.setattr(main, "_scrape_for_titles", fake_scrape_for_titles)

    summary = compare.compare_found(
        sources_path,
        output_dir,
        cache_dir,
        workers=1,
        use_ai=True,
    )

    assert summary == {
        "AI_found": 1,
        "PY_found": 1,
        "overlap": 1,
        "AI_only": 0,
        "PY_only": 0,
    }
    assert (output_dir / "AI_found.txt").read_text(encoding="utf-8") == (
        "The Matrix (1999) | match=100% | source=https://forum.example.test/thread\n"
    )


def test_compare_found_with_ai_prefers_expanded_source_list(
    tmp_path: Path, monkeypatch
):
    sources_path = tmp_path / "forums.txt"
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / ".cache/html"
    sources_path.write_text("https://forum.example.test/thread\n", encoding="utf-8")
    expanded_url = "https://forum.example.test/thread?page=2"
    cache_dir.parent.mkdir(parents=True)
    cache_dir.parent.joinpath("ai_expanded_urls.txt").write_text(
        f"{expanded_url}\n", encoding="utf-8"
    )
    fetched_urls = []

    class FakeFetchResult:
        def __init__(self, url: str):
            self.url = url
            self.text = "The Matrix (1999) is Profile 7 FEL."
            self.error = None

    class FakeFetcher:
        def __init__(self, cache_dir: Path, cookie_header: str | None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def fetch(self, url: str, *, raise_on_error: bool = True):
            fetched_urls.append(url)
            return FakeFetchResult(url)

    class FakeAIClient:
        def __init__(self, settings: compare.AISettings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def extract_candidates(self, source_url: str, text: str):
            return [
                compare.FoundCandidate(
                    title="The Matrix",
                    year="1999",
                    source_url=source_url,
                    evidence=text,
                    extraction_method="ai",
                )
            ]

    def fake_scrape_for_titles(
        source_path: Path, scrape_output: Path, cache_dir: Path, workers: int
    ):
        data_dir = scrape_output / "data"
        data_dir.mkdir(parents=True)
        data_dir.joinpath("releases.json").write_text("[]\n", encoding="utf-8")
        return 0

    monkeypatch.setenv("OPENAI_API_KEY", "secret-token")
    monkeypatch.setattr(compare.fetcher, "Fetcher", FakeFetcher)
    monkeypatch.setattr(compare, "AIClient", FakeAIClient)
    monkeypatch.setattr(main, "_scrape_for_titles", fake_scrape_for_titles)

    compare.compare_found(
        sources_path,
        output_dir,
        cache_dir,
        workers=1,
        use_ai=True,
    )

    assert fetched_urls == [expanded_url]
    assert (output_dir / "AI_found.txt").read_text(encoding="utf-8") == (
        "The Matrix (1999) | match=0% | source=https://forum.example.test/thread?page=2\n"
    )


def test_compare_found_reads_legacy_ai_text_and_recovers_source_from_cache(
    tmp_path: Path, monkeypatch
):
    sources_path = tmp_path / "forums.txt"
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / ".cache/html"
    source_url = "https://forum.example.test/thread?page=2"
    sources_path.write_text("https://forum.example.test/thread\n", encoding="utf-8")
    output_dir.mkdir()
    output_dir.joinpath("AI_found.txt").write_text(
        "The Matrix (1999)\n", encoding="utf-8"
    )
    cache_dir.mkdir(parents=True)
    cache_dir.parent.joinpath("ai_expanded_urls.txt").write_text(
        f"{source_url}\n", encoding="utf-8"
    )
    cache_key = f"public\0{source_url}"
    digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
    cache_dir.joinpath(f"{digest}.html").write_text(
        "<html>The Matrix (1999) is Profile 7 FEL.</html>",
        encoding="utf-8",
    )

    def fake_scrape_for_titles(
        source_path: Path, scrape_output: Path, cache_dir: Path, workers: int
    ):
        data_dir = scrape_output / "data"
        data_dir.mkdir(parents=True)
        data_dir.joinpath("releases.json").write_text(
            json.dumps(
                [
                    release(
                        "The Matrix",
                        "1999",
                        source_url,
                        "The Matrix (1999) is Profile 7 FEL.",
                    ).to_dict()
                ]
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(main, "_scrape_for_titles", fake_scrape_for_titles)

    summary = compare.compare_found(
        sources_path,
        output_dir,
        cache_dir,
        workers=1,
        use_ai=False,
    )

    assert summary == {
        "AI_found": 1,
        "PY_found": 1,
        "overlap": 1,
        "AI_only": 0,
        "PY_only": 0,
    }
    assert (output_dir / "AI_found.txt").read_text(encoding="utf-8") == (
        "The Matrix (1999) | match=100% | source=https://forum.example.test/thread?page=2\n"
    )


def test_compare_found_falls_back_to_legacy_ai_text_when_csv_is_empty(
    tmp_path: Path, monkeypatch
):
    sources_path = tmp_path / "forums.txt"
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / ".cache/html"
    source_url = "https://forum.example.test/thread"
    sources_path.write_text(f"{source_url}\n", encoding="utf-8")
    output_dir.mkdir()
    output_dir.joinpath("AI_found.csv").write_text(
        "title,year,label,match_percent,source_url,extraction_method,evidence\n",
        encoding="utf-8",
    )
    output_dir.joinpath("AI_found.txt").write_text(
        "The Matrix (1999)\n", encoding="utf-8"
    )

    def fake_scrape_for_titles(
        source_path: Path, scrape_output: Path, cache_dir: Path, workers: int
    ):
        data_dir = scrape_output / "data"
        data_dir.mkdir(parents=True)
        data_dir.joinpath("releases.json").write_text(
            json.dumps(
                [
                    release(
                        "The Matrix",
                        "1999",
                        source_url,
                        "The Matrix (1999) is Profile 7 FEL.",
                    ).to_dict()
                ]
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(main, "_scrape_for_titles", fake_scrape_for_titles)

    summary = compare.compare_found(
        sources_path,
        output_dir,
        cache_dir,
        workers=1,
        use_ai=False,
    )

    assert summary["AI_found"] == 1
    assert summary["overlap"] == 1
