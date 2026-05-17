from __future__ import annotations

import json
import subprocess
import sys

from fel_dolby_vision_movies.benchmark import (
    BenchmarkCase,
    BenchmarkMismatch,
    evaluate_cases,
    load_cases,
)


def test_load_cases_reads_curated_fixture(tmp_path):
    fixture = tmp_path / "cases.json"
    fixture.write_text(
        json.dumps(
            [
                {
                    "name": "table fel",
                    "source_url": "https://example.test/thread",
                    "html": "<table><tr><th>Title</th><th>DV</th></tr>"
                    "<tr><td>Alien</td><td>Profile 7 FEL</td></tr></table>",
                    "expected_titles": ["Alien"],
                }
            ]
        )
    )

    assert load_cases(fixture) == [
        BenchmarkCase(
            name="table fel",
            source_url="https://example.test/thread",
            html="<table><tr><th>Title</th><th>DV</th></tr>"
            "<tr><td>Alien</td><td>Profile 7 FEL</td></tr></table>",
            expected_titles=["Alien"],
        )
    ]


def test_evaluate_cases_reports_title_mismatches():
    cases = [
        BenchmarkCase(
            name="false positive",
            source_url="https://example.test/thread",
            html="<p>Alien is Profile 7 FEL.</p>",
            expected_titles=[],
        )
    ]

    result = evaluate_cases(cases)

    assert result.total == 1
    assert result.passed == 0
    assert result.mismatches == [
        BenchmarkMismatch(
            case_name="false positive",
            expected_titles=[],
            actual_titles=["Alien"],
        )
    ]


def test_evaluate_cases_passes_when_parser_titles_match():
    cases = [
        BenchmarkCase(
            name="positive table row",
            source_url="https://example.test/thread",
            html="<table><tr><th>Title</th><th>DV</th></tr>"
            "<tr><td>The Matrix</td><td>Profile 7 FEL</td></tr></table>",
            expected_titles=["The Matrix"],
        )
    ]

    result = evaluate_cases(cases)

    assert result.total == 1
    assert result.passed == 1
    assert result.mismatches == []


def test_cli_exits_nonzero_and_summarizes_mismatches(tmp_path):
    fixture = tmp_path / "cases.json"
    fixture.write_text(
        json.dumps(
            [
                {
                    "name": "unexpected parser hit",
                    "source_url": "https://example.test/thread",
                    "html": "<p>Alien is Profile 7 FEL.</p>",
                    "expected_titles": [],
                }
            ]
        )
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fel_dolby_vision_movies.benchmark",
            str(fixture),
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 1
    assert "Benchmark: 0/1 cases passed" in completed.stdout
    assert "unexpected parser hit" in completed.stdout
    assert "expected [] got ['Alien']" in completed.stdout
