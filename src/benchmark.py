from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Sequence

from parser import parse_fel_releases


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    source_url: str
    html: str
    expected_titles: list[str]


@dataclass(frozen=True)
class BenchmarkMismatch:
    case_name: str
    expected_titles: list[str]
    actual_titles: list[str]


@dataclass(frozen=True)
class BenchmarkResult:
    total: int
    passed: int
    mismatches: list[BenchmarkMismatch]


def load_cases(path: str | Path) -> list[BenchmarkCase]:
    data = json.loads(Path(path).read_text())
    return [
        BenchmarkCase(
            name=str(case["name"]),
            source_url=str(case["source_url"]),
            html=str(case["html"]),
            expected_titles=list(case["expected_titles"]),
        )
        for case in data
    ]


def evaluate_cases(cases: Sequence[BenchmarkCase]) -> BenchmarkResult:
    mismatches: list[BenchmarkMismatch] = []
    for case in cases:
        releases = parse_fel_releases(case.html, case.source_url)
        actual_titles = [release.movie_title for release in releases]
        if actual_titles != case.expected_titles:
            mismatches.append(
                BenchmarkMismatch(
                    case_name=case.name,
                    expected_titles=case.expected_titles,
                    actual_titles=actual_titles,
                )
            )
    total = len(cases)
    return BenchmarkResult(
        total=total,
        passed=total - len(mismatches),
        mismatches=mismatches,
    )


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI entrypoint
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("Usage: python -m benchmark CASES_JSON")
        return 2

    result = evaluate_cases(load_cases(args[0]))
    print(f"Benchmark: {result.passed}/{result.total} cases passed")
    for mismatch in result.mismatches:
        print(
            f"- {mismatch.case_name}: expected {mismatch.expected_titles} "
            f"got {mismatch.actual_titles}"
        )
    return 1 if result.mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
