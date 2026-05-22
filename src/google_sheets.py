from __future__ import annotations

import csv
from datetime import datetime, timezone
import re
from urllib.parse import parse_qs, urlparse

from models import FelEvidence, FelRelease
from normalize import normalize_title


TITLE_HEADERS = {"movie name", "title", "movie", "film"}
FEL_HEADERS = {"dv source", "dv", "dolby vision", "layer", "source"}
FEL_SOURCE_RE = re.compile(r"(?<![A-Za-z0-9])fel(?![A-Za-z0-9])", re.IGNORECASE)
YEAR_RE = re.compile(
    r"^(?P<title>.+?)[\s.(_-]+(?P<year>(?:19|20)\d{2})\)?[.\s_-]*"
    r"(?=$|(?:NEW|US|UK|FRA|ITA|EUR|BD)\b)",
    re.IGNORECASE,
)
COLLECTION_TITLE_RE = re.compile(
    r"\b(?:collection|trilogy|duology|quadrilogy|tetralogy|saga|box\s*set|boxset)$",
    re.IGNORECASE,
)


def google_sheet_csv_url(url: str) -> str:
    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    try:
        spreadsheet_id = path_parts[path_parts.index("d") + 1]
    except (ValueError, IndexError) as exc:
        raise ValueError(f"not a Google Sheets URL: {url}") from exc
    gid = _gid_from_url(parsed)
    return (
        f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/"
        f"gviz/tq?tqx=out:csv&gid={gid}"
    )


def parse_google_sheet_releases(csv_text: str, source_url: str) -> list[FelRelease]:
    rows = list(csv.reader(csv_text.splitlines()))
    releases: list[FelRelease] = []
    title_index: int | None = None
    fel_index: int | None = None

    for row in rows:
        header_indexes = _header_indexes(row)
        if header_indexes is not None:
            title_index, fel_index = header_indexes
            continue
        if title_index is None or fel_index is None:
            continue
        if max(title_index, fel_index) >= len(row):
            continue
        raw_title = normalize_title(row[title_index])
        raw_fel_source = normalize_title(row[fel_index])
        if not raw_title or not FEL_SOURCE_RE.search(raw_fel_source):
            continue
        title, year = _split_title_year(raw_title)
        if not title:
            continue
        if COLLECTION_TITLE_RE.search(title):
            continue
        releases.append(_build_sheet_release(title, year, row, source_url))

    return releases


def parse_always_fel_sheet(csv_text: str, source_url: str) -> list[FelRelease]:
    """Parse a curated FEL sheet where every listed film is Profile 7 FEL.

    Unlike parse_google_sheet_releases this needs no DV/FEL column -- the
    sheet itself is the evidence -- so every titled row becomes a release.
    """
    rows = list(csv.reader(csv_text.splitlines()))
    releases: list[FelRelease] = []
    title_index: int | None = None
    for row in rows:
        if title_index is None:
            normalized = [_normalize_header(cell) for cell in row]
            title_index = _first_header_index(normalized, TITLE_HEADERS)
            continue
        if title_index >= len(row):
            continue
        raw_title = normalize_title(row[title_index])
        if not raw_title:
            continue
        title, year = _split_title_year(raw_title)
        if not title or COLLECTION_TITLE_RE.search(title):
            continue
        quote = " | ".join(
            normalize_title(cell) for cell in row if normalize_title(cell)
        )
        releases.append(
            FelRelease(
                movie_title=title,
                release_date=year,
                fel_evidence=FelEvidence(
                    source_url=source_url,
                    quote=quote[:500],
                    evidence_type="google-sheet-list",
                ),
                source_label="google-sheet",
                collected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
        )
    return releases


def _gid_from_url(parsed_url) -> str:
    query_gid = parse_qs(parsed_url.query).get("gid")
    if query_gid and query_gid[0]:
        return query_gid[0]
    fragment_gid = parse_qs(parsed_url.fragment).get("gid")
    if fragment_gid and fragment_gid[0]:
        return fragment_gid[0]
    return "0"


def _header_indexes(row: list[str]) -> tuple[int, int] | None:
    normalized = [_normalize_header(cell) for cell in row]
    title_index = _first_header_index(normalized, TITLE_HEADERS)
    fel_index = _first_header_index(normalized, FEL_HEADERS)
    if title_index is None or fel_index is None or title_index == fel_index:
        return None
    return title_index, fel_index


def _first_header_index(values: list[str], accepted: set[str]) -> int | None:
    for index, value in enumerate(values):
        if _header_matches(value, accepted):
            return index
    return None


def _header_matches(value: str, accepted: set[str]) -> bool:
    if value in accepted:
        return True
    return any(re.search(rf"\b{re.escape(header)}\b", value) for header in accepted)


def _normalize_header(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())


def _split_title_year(value: str) -> tuple[str, str]:
    match = YEAR_RE.match(value)
    if not match:
        return _clean_sheet_title(value), "Unknown"
    return _clean_sheet_title(match.group("title")), match.group("year")


def _clean_sheet_title(value: str) -> str:
    title = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", value)
    return normalize_title(title).strip(" -_()")


def _build_sheet_release(
    title: str, year: str, row: list[str], source_url: str
) -> FelRelease:
    quote = " | ".join(normalize_title(cell) for cell in row if normalize_title(cell))
    return FelRelease(
        movie_title=title,
        release_date=year,
        fel_evidence=FelEvidence(
            source_url=source_url,
            quote=quote[:500],
            evidence_type="google-sheet-row",
        ),
        collected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
