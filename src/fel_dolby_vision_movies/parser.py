from __future__ import annotations

from datetime import datetime, timezone
import re

from bs4 import BeautifulSoup

from .models import FelEvidence, FelRelease
from .normalize import normalize_audio, normalize_title


PROFILE_7_PATTERN = r"(?:profile[\s-]*7|p7)"
FEL_TOKEN_PATTERN = r"(?<![A-Za-z0-9])fel(?![A-Za-z0-9])"
MEL_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])mel(?![A-Za-z0-9])", re.IGNORECASE)
PROFILE_7_RE = re.compile(rf"\b{PROFILE_7_PATTERN}\b", re.IGNORECASE)
FEL_RE = re.compile(FEL_TOKEN_PATTERN, re.IGNORECASE)
FEL_TRAILING_DENIAL_RE = re.compile(
    rf"{FEL_TOKEN_PATTERN}\s*(?::|=|-|\bis\b)?\s*\b(?:no|none|false)\b",
    re.IGNORECASE,
)
GENERIC_STATUS_PREFIX_RE = re.compile(
    r"^(?:confirmed|yes|mediainfo\s+confirms|dolby vision|dv|hdr|hdr10|uhd)$",
    re.IGNORECASE,
)
RELEASE_STATUS_HEADER_RE = re.compile(
    r"\b(?:dv|dolby|vision|profile|hdr|fel|video|format|status|disc|layer)\b",
    re.IGNORECASE,
)
TITLE_SPECIFIC_HEADER_RE = re.compile(
    r"\b(?:note|notes|evidence|comment|comments|source|proof)\b",
    re.IGNORECASE,
)
TITLE_BINDING_RE = re.compile(
    r"^[A-Z][A-Za-z0-9:'&.,!?\- ]{1,80}?\s+"
    r"(?:is|has|features|includes|confirmed as|confirmed to be)\b",
    re.IGNORECASE,
)
TITLE_BINDING_SUFFIX_RE = re.compile(
    r"\s+(?:is|has|features|includes|confirmed as|confirmed to be)$",
    re.IGNORECASE,
)
AMBIGUOUS_PROSE_TITLE_RE = re.compile(
    r"^(?:(?:this|that|a|an|the)\s+)?(?:spreadsheet|list|post|thread|forum|"
    r"page|source|site|table|note|comment)\s+"
    r"(?:says|lists|shows|mentions|reports)\s+",
    re.IGNORECASE,
)
PROSE_TITLE_PREFIX_RE = re.compile(
    r"^(?:the\s+)?(?:disc|release|blu-?ray|uhd|4k|movie|film)\s+(?:for|of)\s+",
    re.IGNORECASE,
)
TITLE_SENTENCE_RE = re.compile(
    r"(?P<title>[A-Z][A-Za-z0-9:'&.,!?\- ]{1,80}?)(?:\s+\((?P<year>\d{4})\))?"
    r"\s+(?:is|has|features|includes|confirmed as|confirmed to be).{0,120}?"
    rf"(?:{PROFILE_7_PATTERN}.{{0,40}}?{FEL_TOKEN_PATTERN}|"
    rf"{FEL_TOKEN_PATTERN}.{{0,40}}?{PROFILE_7_PATTERN})",
    re.IGNORECASE,
)


def parse_fel_releases(html: str, source_url: str) -> list[FelRelease]:
    soup = BeautifulSoup(html, "html.parser")
    releases: list[FelRelease] = []
    releases.extend(_parse_tables(soup, source_url))
    for table in soup.find_all("table"):
        table.decompose()
    releases.extend(_parse_sentences(soup.get_text("\n", strip=True), source_url))
    return _dedupe_releases(releases)


def _parse_tables(soup: BeautifulSoup, source_url: str) -> list[FelRelease]:
    releases: list[FelRelease] = []
    for table in soup.find_all("table"):
        headers: list[str] = []
        for row in table.find_all("tr"):
            cells = [
                cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])
            ]
            if len(cells) < 2:
                continue
            if row.find("th") and not row.find("td"):
                headers = cells
                continue
            title = normalize_title(cells[0])
            if not _looks_like_title(title):
                continue
            if not _has_table_evidence_for_title(title, cells, headers):
                continue
            releases.append(
                _build_release(title, " ".join(cells), source_url, "table-row")
            )
    return releases


def _parse_sentences(text: str, source_url: str) -> list[FelRelease]:
    releases: list[FelRelease] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if not _has_direct_fel(sentence):
            continue
        match = TITLE_SENTENCE_RE.search(sentence)
        if not match:
            continue
        title = _clean_sentence_title(match.group("title"))
        if not _looks_like_title(title):
            continue
        release = _build_release(title, sentence, source_url, "sentence")
        if match.group("year"):
            release.release_date = match.group("year")
        releases.append(release)
    return releases


def _has_direct_fel(text: str) -> bool:
    lowered = text.lower()
    if _has_unnegated_mel(text):
        return False
    if re.search(r"\b(?:not|no|without|does not|do not)\b.{0,40}\bfel\b", lowered):
        return False
    if FEL_TRAILING_DENIAL_RE.search(text):
        return False
    return bool(FEL_RE.search(text) and PROFILE_7_RE.search(text))


def _has_unnegated_mel(text: str) -> bool:
    for match in MEL_TOKEN_RE.finditer(text):
        before_mel = text[max(0, match.start() - 8) : match.start()].lower()
        if re.search(r"\bnot\s+$", before_mel):
            continue
        return True
    return False


def _has_table_evidence_for_title(
    title: str, cells: list[str], headers: list[str]
) -> bool:
    for index, cell in enumerate(cells[1:], start=1):
        if not _has_direct_fel(cell):
            continue
        header = headers[index] if index < len(headers) else ""
        if RELEASE_STATUS_HEADER_RE.search(header):
            return _cell_supports_row_title(cell, title)
        if TITLE_SPECIFIC_HEADER_RE.search(header):
            return _cell_mentions_title(cell, title)
        if not headers:
            return _cell_supports_row_title(cell, title)
        if _cell_mentions_title(cell, title):
            return True
    return False


def _cell_supports_row_title(cell: str, title: str) -> bool:
    leading_title = _leading_title_before_evidence(cell)
    if leading_title:
        return _normalized_title_prefix(leading_title) == _normalized_value(title)
    if TITLE_BINDING_RE.search(normalize_title(cell)):
        return _cell_mentions_title(cell, title)
    return True


def _cell_mentions_title(cell: str, title: str) -> bool:
    normalized_title = normalize_title(title)
    if not normalized_title:
        return False
    pattern = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(normalized_title)}(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    return bool(pattern.search(normalize_title(cell)))


def _leading_title_before_evidence(cell: str) -> str:
    normalized = normalize_title(cell)
    profile_7_match = PROFILE_7_RE.search(normalized)
    fel_match = FEL_RE.search(normalized)
    evidence_starts = [
        match.start() for match in (profile_7_match, fel_match) if match
    ]
    if not evidence_starts:
        return ""
    prefix = normalized[: min(evidence_starts)].strip(" :-")
    if GENERIC_STATUS_PREFIX_RE.fullmatch(prefix):
        return ""
    return prefix


def _normalized_value(value: str) -> str:
    return normalize_title(value).casefold()


def _normalized_title_prefix(value: str) -> str:
    prefix = TITLE_BINDING_SUFFIX_RE.sub("", normalize_title(value))
    return _normalized_value(prefix)


def _clean_sentence_title(value: str) -> str:
    title = normalize_title(value)
    if AMBIGUOUS_PROSE_TITLE_RE.match(title):
        return ""
    return PROSE_TITLE_PREFIX_RE.sub("", title).strip(" :,-")


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
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[FelRelease] = []
    for release in releases:
        key = (
            release.movie_title.lower(),
            release.source_url,
            release.fel_evidence.evidence_type,
            release.fel_evidence.quote,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(release)
    return unique
