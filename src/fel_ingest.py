from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import re

from models import FelEvidence, FelRelease
from normalize import normalize_fel_title


FEL_TXT_PROVENANCE = "FEL.txt (curated Profile 7 FEL list)"
RAW_FEL_PROVENANCE = "https://forum.blu-ray.com/showthread.php?t=276448"

_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_RAW_FEL_LINE_RE = re.compile(
    r"^(?P<title>.+?)\s*\((?P<year>(?:19|20)\d{2})\)\s*FEL\s*-\s*"
    r"(?P<bitrate>\d+(?:\.\d+)?)\s*Mb/s",
    re.IGNORECASE,
)


def parse_fel_txt(text: str) -> list[FelRelease]:
    collected_at = datetime.now(timezone.utc).isoformat()
    releases: list[FelRelease] = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2:
            continue  # pragma: no cover - short row skip
        title = normalize_fel_title(row[0])
        year = row[1].strip()
        if not title or not _YEAR_RE.fullmatch(year):
            continue
        raw_urls = row[2] if len(row) >= 3 else ""
        urls = list(
            dict.fromkeys(part.strip() for part in raw_urls.split("|") if part.strip())
        )
        additional: dict[str, object] = {}
        if urls:
            additional["source_urls"] = urls
        releases.append(
            FelRelease(
                movie_title=title,
                release_date=year,
                fel_evidence=FelEvidence(
                    source_url=urls[0] if urls else FEL_TXT_PROVENANCE,
                    quote=f"{title} ({year}) listed as Profile 7 FEL",
                    evidence_type="fel-list",
                ),
                additional_characteristics=additional,
                source_label="FEL.txt",
                collected_at=collected_at,
            )
        )
    return releases


def parse_raw_fel_txt(text: str) -> list[FelRelease]:
    collected_at = datetime.now(timezone.utc).isoformat()
    releases: list[FelRelease] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = _RAW_FEL_LINE_RE.match(line)
        if not match:
            continue
        title = normalize_fel_title(match.group("title"))
        if not title:
            continue  # pragma: no cover - normalized title empty
        releases.append(
            FelRelease(
                movie_title=title,
                release_date=match.group("year"),
                fel_evidence=FelEvidence(
                    source_url=RAW_FEL_PROVENANCE,
                    quote=line,
                    evidence_type="fel-bitrate-list",
                ),
                additional_characteristics={
                    "enhancement_bitrate_mbps": float(match.group("bitrate"))
                },
                source_label="raw_fel.txt",
                collected_at=collected_at,
            )
        )
    return releases
