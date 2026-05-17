from __future__ import annotations

from datetime import datetime, timezone
import re

from bs4 import BeautifulSoup

from .models import FelEvidence, FelRelease
from .normalize import normalize_audio, normalize_title


TITLE_SENTENCE_RE = re.compile(
    r"(?P<title>[A-Z][A-Za-z0-9:'&.,!?\- ]{1,80}?)(?:\s+\((?P<year>\d{4})\))?"
    r"\s+(?:is|has|features|includes|confirmed as|confirmed to be).{0,120}?"
    r"(?:profile\s*7.{0,40}?fel|fel.{0,40}?profile\s*7|dolby vision.{0,40}?fel)",
    re.IGNORECASE,
)


def parse_fel_releases(html: str, source_url: str) -> list[FelRelease]:
    soup = BeautifulSoup(html, "html.parser")
    releases: list[FelRelease] = []
    releases.extend(_parse_tables(soup, source_url))
    releases.extend(_parse_sentences(soup.get_text("\n", strip=True), source_url))
    return _dedupe_releases(releases)


def _parse_tables(soup: BeautifulSoup, source_url: str) -> list[FelRelease]:
    releases: list[FelRelease] = []
    for row in soup.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        row_text = " ".join(cells)
        if not _has_direct_fel(row_text):
            continue
        title = normalize_title(cells[0])
        if not _looks_like_title(title):
            continue
        releases.append(_build_release(title, row_text, source_url, "table-row"))
    return releases


def _parse_sentences(text: str, source_url: str) -> list[FelRelease]:
    releases: list[FelRelease] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if not _has_direct_fel(sentence):
            continue
        match = TITLE_SENTENCE_RE.search(sentence)
        if not match:
            continue
        title = normalize_title(match.group("title"))
        if not _looks_like_title(title):
            continue
        release = _build_release(title, sentence, source_url, "sentence")
        if match.group("year"):
            release.release_date = match.group("year")
        releases.append(release)
    return releases


def _has_direct_fel(text: str) -> bool:
    lowered = text.lower()
    if "mel" in lowered:
        return False
    if re.search(r"\b(?:not|no|without|does not|do not)\b.{0,40}\bfel\b", lowered):
        return False
    return "fel" in lowered and (
        "profile 7" in lowered or "p7" in lowered or "dolby vision" in lowered
    )


def _looks_like_title(value: str) -> bool:
    lowered = value.lower()
    if not value or len(value) > 100:
        return False
    if any(
        word in lowered
        for word in ("hardware", "player", "splitter", "profile", "dolby vision")
    ):
        return False
    return any(character.isalpha() for character in value)


def _build_release(
    title: str, evidence_text: str, source_url: str, evidence_type: str
) -> FelRelease:
    release = FelRelease(
        movie_title=title,
        fel_evidence=FelEvidence(
            source_url=source_url,
            quote=evidence_text[:500],
            evidence_type=evidence_type,
        ),
        audio_formats=normalize_audio(evidence_text),
        collected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    if "english" in evidence_text.lower():
        release.english_audio = "Yes"
    return release


def _dedupe_releases(releases: list[FelRelease]) -> list[FelRelease]:
    seen: set[tuple[str, str]] = set()
    unique: list[FelRelease] = []
    for release in releases:
        key = (release.movie_title.lower(), release.source_url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(release)
    return unique
