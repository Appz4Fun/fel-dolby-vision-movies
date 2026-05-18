from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import time
import unicodedata
from typing import Any, Protocol, Sequence

import httpx


TMDB_SEARCH_URL = "https://api.themoviedb.org/3/search/movie"
TMDB_EXTERNAL_IDS_URL = "https://api.themoviedb.org/3/movie/{tmdb_id}/external_ids"
DEFAULT_CACHE_PATH = Path(".cache/tmdb_fel_cleanup.json")
DEFAULT_REPORT_PATH = Path("data/fel_cleanup_report.csv")


@dataclass(frozen=True)
class TmdbMovie:
    tmdb_id: str
    title: str
    year: str
    imdb_id: str = ""


@dataclass(frozen=True)
class CleanFelRow:
    title: str
    year: str
    sources: list[str]
    tmdb_id: str = ""
    imdb_id: str = ""


@dataclass(frozen=True)
class CleanupReportEntry:
    line: str
    input_title: str
    input_year: str
    cleaned_query: str
    action: str
    output_title: str
    output_year: str
    tmdb_id: str
    imdb_id: str
    notes: str


@dataclass(frozen=True)
class CleanupResult:
    rows: list[CleanFelRow]
    report_entries: list[CleanupReportEntry]


@dataclass(frozen=True)
class CleanupSummary:
    input_rows: int
    output_rows: int
    dropped_rows: int
    resolved_rows: int
    unresolved_rows: int
    merged_rows: int


class MovieResolver(Protocol):
    def resolve(self, title: str, year: str) -> TmdbMovie | None: ...


class StaticTmdbResolver:
    def __init__(self, records: dict[tuple[str, str], dict[str, str]]) -> None:
        self.records = records

    def resolve(self, title: str, year: str) -> TmdbMovie | None:
        record = self.records.get((title, year))
        if record is None:
            return None
        return TmdbMovie(
            tmdb_id=record["tmdb_id"],
            title=record["title"],
            year=record["year"],
            imdb_id=record.get("imdb_id", ""),
        )


class TmdbResolver:
    def __init__(
        self,
        api_key: str,
        cache_path: Path = DEFAULT_CACHE_PATH,
        client: httpx.Client | None = None,
        delay_seconds: float = 0.025,
    ) -> None:
        self.api_key = api_key
        self.cache_path = cache_path
        self.client = client or httpx.Client(timeout=httpx.Timeout(20.0))
        self._owns_client = client is None
        self.delay_seconds = delay_seconds
        self.cache: dict[str, dict[str, str] | None] = self._read_cache()

    def resolve(self, title: str, year: str) -> TmdbMovie | None:
        cache_key = f"{title}\0{year}"
        if cache_key in self.cache:
            return _movie_from_cache_record(self.cache[cache_key])

        result = self._search(title, year)
        self.cache[cache_key] = _movie_to_cache_record(result)
        self._write_cache()
        time.sleep(self.delay_seconds)
        return result

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> TmdbResolver:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def _search(self, title: str, year: str) -> TmdbMovie | None:
        best = self._search_candidates(title, year)
        if best is None and year:
            best = self._search_candidates(title, "")
        if best is None:
            return None
        tmdb_id = str(best.get("id", ""))
        external = self._external_ids(tmdb_id)
        return TmdbMovie(
            tmdb_id=tmdb_id,
            title=str(best.get("title") or best.get("name") or title).strip(),
            year=_year_from_date(str(best.get("release_date") or "")) or year,
            imdb_id=str(external.get("imdb_id") or ""),
        )

    def _search_candidates(self, title: str, year: str) -> dict[str, Any] | None:
        params = {
            "api_key": self.api_key,
            "query": title,
            "include_adult": "false",
        }
        if re.fullmatch(r"(?:19|20)\d{2}", year):
            params["year"] = year
            params["primary_release_year"] = year
        response = self.client.get(TMDB_SEARCH_URL, params=params)
        response.raise_for_status()
        candidates = response.json().get("results", [])
        return _best_tmdb_candidate(title, year, candidates)

    def _external_ids(self, tmdb_id: str) -> dict[str, Any]:
        if not tmdb_id:
            return {}
        response = self.client.get(
            TMDB_EXTERNAL_IDS_URL.format(tmdb_id=tmdb_id),
            params={"api_key": self.api_key},
        )
        response.raise_for_status()
        return dict(response.json())

    def _read_cache(self) -> dict[str, dict[str, str] | None]:
        if not self.cache_path.exists():
            return {}
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(key): value if isinstance(value, dict) else None
            for key, value in data.items()
        }

    def _write_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self.cache, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


class FelListCleaner:
    def __init__(self, resolver: MovieResolver) -> None:
        self.resolver = resolver

    def clean_rows(self, rows: list[list[str]]) -> CleanupResult:
        report_entries: list[CleanupReportEntry] = []
        grouped: dict[tuple[str, str], CleanFelRow] = {}
        input_by_group: dict[tuple[str, str], list[str]] = {}

        for line_number, row in enumerate(rows, start=1):
            if len(row) < 2:
                continue
            input_title = row[0].strip()
            input_year = row[1].strip()
            sources = _split_sources(row[2] if len(row) >= 3 else "")
            cleaned_title, title_note = clean_query_title(input_title)
            cleaned_title, resolved_year, year_note = _extract_embedded_year(
                cleaned_title, input_year
            )
            if year_note:
                title_note = _join_notes(title_note, year_note)

            if not cleaned_title:
                report_entries.append(
                    _report(
                        line_number,
                        input_title,
                        input_year,
                        cleaned_title,
                        "dropped",
                        "",
                        "",
                        "",
                        "",
                        title_note or "empty title",
                    )
                )
                continue

            movie = self.resolver.resolve(cleaned_title, resolved_year)
            if movie is None:
                output_title = _local_display_title(cleaned_title)
                output_year = resolved_year
                group_key = ("local", _canonical_title_key(output_title), output_year)
                tmdb_id = ""
                imdb_id = ""
                action = "unresolved"
                notes = title_note or "TMDB match not found"
            else:
                output_title = movie.title
                output_year = movie.year or input_year
                group_key = (
                    "resolved",
                    _canonical_title_key(output_title),
                    output_year,
                )
                tmdb_id = movie.tmdb_id
                imdb_id = movie.imdb_id
                action = "resolved"
                notes = title_note

            existing = grouped.get(group_key)
            if existing is None:
                grouped[group_key] = CleanFelRow(
                    title=output_title,
                    year=output_year,
                    sources=sources,
                    tmdb_id=tmdb_id,
                    imdb_id=imdb_id,
                )
                input_by_group[group_key] = [input_title]
            else:
                grouped[group_key] = CleanFelRow(
                    title=existing.title,
                    year=existing.year,
                    sources=_merge_sources(existing.sources, sources),
                    tmdb_id=existing.tmdb_id,
                    imdb_id=existing.imdb_id,
                )
                input_by_group[group_key].append(input_title)
                action = "merged"

            report_entries.append(
                _report(
                    line_number,
                    input_title,
                    input_year,
                    cleaned_title,
                    action,
                    output_title,
                    output_year,
                    tmdb_id,
                    imdb_id,
                    notes,
                )
            )

        output_rows = sorted(
            grouped.values(),
            key=lambda item: (-_year_sort_value(item.year), item.title),
        )
        return CleanupResult(rows=output_rows, report_entries=report_entries)


def clean_fel_file(
    input_path: Path,
    output_path: Path,
    report_path: Path,
    resolver: MovieResolver,
) -> CleanupSummary:
    rows = _read_fel_rows(input_path)
    result = FelListCleaner(resolver).clean_rows(rows)
    _write_fel_rows(output_path, result.rows)
    _write_report(report_path, result.report_entries)
    dropped_rows = sum(
        1 for entry in result.report_entries if entry.action == "dropped"
    )
    resolved_rows = sum(
        1 for entry in result.report_entries if entry.action in {"resolved", "merged"}
    )
    unresolved_rows = sum(
        1 for entry in result.report_entries if entry.action == "unresolved"
    )
    return CleanupSummary(
        input_rows=len(rows),
        output_rows=len(result.rows),
        dropped_rows=dropped_rows,
        resolved_rows=resolved_rows,
        unresolved_rows=unresolved_rows,
        merged_rows=len(rows) - dropped_rows - len(result.rows),
    )


def clean_query_title(value: str) -> tuple[str, str]:
    title = _normalize_space(value)
    notes: list[str] = []
    if re.match(r"^\s*MEL(?:\s+-|\s+)", title, re.IGNORECASE):
        return "", "MEL row"

    original = title
    title = re.sub(
        r"^(?:Quote:\s*)?Originally Posted by\s+Angry Virginian\s+",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(
        r"^(?:Quote:\s*)?Originally Posted by\s+\S+\s+",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(
        r"^You forgot one:\s*",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(
        r"^\S+\s+\S+\s+-\s+FEL\s+[0-9.]+\s*(?:Mb/s|Mbps|kbps)\s+",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(
        r"^FEL\s+-?\s+[0-9.]+\s*(?:Mb/s|Mbps|kbps)\s+",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(
        r"^(?:Quote:\s*)?Originally Posted by\s+Angry Virginian\s+",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(
        r"^(?:Quote:\s*)?Originally Posted by\s+\S+\s+",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(r"\s+", " ", title).strip(" ,-")
    if title != original:
        notes.append("stripped scraped prefix")

    title = re.sub(r"(?<=\D)\.(?=\D)", " ", title)
    title = re.sub(r"\.(?=(?:19|20)\d{2}\b)", " ", title)
    title = _normalize_space(title)
    return title, "; ".join(notes)


def _extract_embedded_year(title: str, fallback_year: str) -> tuple[str, str, str]:
    match = re.search(r"\b((?:19|20)\d{2})\b", title)
    if match is None:
        return title, fallback_year, ""
    embedded_year = match.group(1)
    cleaned = _normalize_space(
        (title[: match.start()] + title[match.end() :]).strip(" .,-")
    )
    if not cleaned:
        return title, fallback_year, ""
    return cleaned, embedded_year, "used embedded title year"


def load_tmdb_api_key(env_path: Path = Path(".env")) -> str:
    file_value: str | None = None
    try:
        from dotenv import dotenv_values, load_dotenv

        if env_path.exists():
            values = dotenv_values(env_path)
            if "TMDB_API_KEY" in values:
                file_value = values["TMDB_API_KEY"] or ""
        load_dotenv(env_path)
    except Exception:
        pass
    api_key = (
        file_value if file_value is not None else os.environ.get("TMDB_API_KEY", "")
    ).strip()
    if not api_key:
        raise RuntimeError("TMDB_API_KEY is required to clean FEL.txt")
    return api_key


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    api_key = load_tmdb_api_key(args.env)
    with TmdbResolver(api_key=api_key, cache_path=args.cache) as resolver:
        summary = clean_fel_file(args.input, args.output, args.report, resolver)
    print(
        "FEL cleanup complete; "
        f"input_rows={summary.input_rows} "
        f"output_rows={summary.output_rows} "
        f"dropped={summary.dropped_rows} "
        f"resolved={summary.resolved_rows} "
        f"unresolved={summary.unresolved_rows} "
        f"merged={summary.merged_rows} "
        f"report={args.report}"
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fel-cleanup",
        description="Canonicalize and merge FEL.txt rows with TMDB movie metadata.",
    )
    parser.add_argument("--input", type=Path, default=Path("FEL.txt"))
    parser.add_argument("--output", type=Path, default=Path("FEL.txt"))
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument("--env", type=Path, default=Path(".env"))
    return parser


def _read_fel_rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [row for row in csv.reader(handle) if row]


def _write_fel_rows(path: Path, rows: list[CleanFelRow]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        for row in rows:
            writer.writerow([row.title, row.year, "|".join(row.sources)])


def _write_report(path: Path, rows: list[CleanupReportEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            [
                "line",
                "input_title",
                "input_year",
                "cleaned_query",
                "action",
                "output_title",
                "output_year",
                "tmdb_id",
                "imdb_id",
                "notes",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.line,
                    row.input_title,
                    row.input_year,
                    row.cleaned_query,
                    row.action,
                    row.output_title,
                    row.output_year,
                    row.tmdb_id,
                    row.imdb_id,
                    row.notes,
                ]
            )


def _report(
    line_number: int,
    input_title: str,
    input_year: str,
    cleaned_query: str,
    action: str,
    output_title: str,
    output_year: str,
    tmdb_id: str,
    imdb_id: str,
    notes: str,
) -> CleanupReportEntry:
    return CleanupReportEntry(
        line=str(line_number),
        input_title=input_title,
        input_year=input_year,
        cleaned_query=cleaned_query,
        action=action,
        output_title=output_title,
        output_year=output_year,
        tmdb_id=tmdb_id,
        imdb_id=imdb_id,
        notes=notes,
    )


def _best_tmdb_candidate(
    query_title: str, query_year: str, candidates: list[dict[str, Any]]
) -> dict[str, Any] | None:
    best: tuple[int, dict[str, Any]] | None = None
    query_key = _canonical_title_key(query_title)
    for candidate in candidates:
        title = str(candidate.get("title") or candidate.get("name") or "")
        original_title = str(candidate.get("original_title") or "")
        release_year = _year_from_date(str(candidate.get("release_date") or ""))
        title_key = _canonical_title_key(title)
        original_key = _canonical_title_key(original_title)
        score = max(
            _title_score(query_key, title_key), _title_score(query_key, original_key)
        )
        if query_year and release_year == query_year:
            score += 80
        elif query_year and release_year:
            score -= 90
        if title_key == query_key:
            score += 20
        if original_key == query_key:
            score += 20
        if best is None or score > best[0]:
            best = (score, candidate)
    if best is None or best[0] < 65:
        return None
    return best[1]


def _title_score(left: str, right: str) -> int:
    if not left or not right:
        return 0
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    overlap = len(left_tokens & right_tokens) / max(len(left_tokens), len(right_tokens))
    ordered = 1.0 if left == right else 0.0
    return round((overlap * 70) + (ordered * 30))


def _canonical_title_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    normalized = normalized.casefold().replace("&", " and ")
    normalized = re.sub(r"[’`´]", "'", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return _normalize_space(normalized)


def _split_sources(value: str) -> list[str]:
    return [
        part
        for part in dict.fromkeys(part.strip() for part in value.split("|"))
        if part
    ]


def _merge_sources(left: list[str], right: list[str]) -> list[str]:
    return list(dict.fromkeys([*left, *right]))


def _local_display_title(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _join_notes(*notes: str) -> str:
    return "; ".join(note for note in notes if note)


def _year_from_date(value: str) -> str:
    match = re.match(r"((?:19|20)\d{2})", value)
    return match.group(1) if match else ""


def _year_sort_value(value: str) -> int:
    return int(value) if value.isdigit() else 0


def _movie_from_cache_record(record: dict[str, str] | None) -> TmdbMovie | None:
    if record is None:
        return None
    return TmdbMovie(
        tmdb_id=record["tmdb_id"],
        title=record["title"],
        year=record["year"],
        imdb_id=record.get("imdb_id", ""),
    )


def _movie_to_cache_record(movie: TmdbMovie | None) -> dict[str, str] | None:
    if movie is None:
        return None
    return {
        "tmdb_id": movie.tmdb_id,
        "title": movie.title,
        "year": movie.year,
        "imdb_id": movie.imdb_id,
    }


if __name__ == "__main__":
    raise SystemExit(main())
