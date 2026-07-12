from __future__ import annotations

from datetime import datetime, timezone
import re

from bs4 import BeautifulSoup, NavigableString

from models import FelEvidence, FelRelease
from normalize import normalize_audio, normalize_title


PROFILE_7_PATTERN = r"(?:profile[\s-]*7|p7)"
FEL_TOKEN_PATTERN = r"(?<![A-Za-z0-9])fel(?![A-Za-z0-9])"
MEL_TOKEN_PATTERN = r"(?<![A-Za-z0-9])mel(?![A-Za-z0-9])"
MEL_TOKEN_RE = re.compile(MEL_TOKEN_PATTERN, re.IGNORECASE)
EXPLICIT_DENIAL_STATUS_PATTERN = (
    r"(?:no|none|false|not\s+present|absent|unsupported|not\s+supported)"
)
MEL_LEADING_DENIAL_RE = re.compile(
    rf"\b(?:not(?!\s+only\b)|no|without)\s+"
    rf"(?:(?:a|any)\s+)?"
    rf"(?:(?:Dolby\s+Vision|{PROFILE_7_PATTERN})\s+)*"
    rf"{MEL_TOKEN_PATTERN}$",
    re.IGNORECASE,
)
MEL_TRAILING_DENIAL_RE = re.compile(
    rf"\s*(?:(?:[?:=–—-]|\bis\b)\s*)?{EXPLICIT_DENIAL_STATUS_PATTERN}\b",
    re.I,
)
PROFILE_7_RE = re.compile(rf"\b{PROFILE_7_PATTERN}\b", re.IGNORECASE)
FEL_RE = re.compile(FEL_TOKEN_PATTERN, re.IGNORECASE)
FEL_TRAILING_DENIAL_RE = re.compile(
    rf"{FEL_TOKEN_PATTERN}\s*(?::|=|-|–|—|\bis\b|\?)?\s*"
    rf"\b{EXPLICIT_DENIAL_STATUS_PATTERN}\b",
    re.IGNORECASE,
)
FEL_LEADING_DENIAL_RE = re.compile(
    rf"\b(?:"
    rf"(?:(?:has|have|is|are|with|features?|includes?)\s+)?"
    rf"(?:no|without|not(?!\s+only\b))\s+"
    rf"|(?:does|do)\s+not\s+"
    rf"(?:have|include|feature|support|contain|provide|identify)\s+"
    rf")"
    rf"(?:(?:a|an|any|the)\s+)?"
    rf"(?:(?:Dolby\s+Vision|DV|{PROFILE_7_PATTERN}|video|"
    rf"full\s+enhancement\s+layer|enhancement\s+layer|layer)\s+)*"
    rf"{FEL_TOKEN_PATTERN}",
    re.IGNORECASE,
)
FEL_CONFIRMATION_DENIAL_RE = re.compile(
    rf"\b(?:(?:is|are|was|were)\s+)?(?:not|never)\s+"
    rf"(?:confirmed|verified|proven)(?:\s+(?:as|to\s+be))?\s+"
    rf"(?:(?:a|an|the)\s+)?"
    rf"(?:(?:Dolby\s+Vision|DV|{PROFILE_7_PATTERN}|video|layer)\s+)*"
    rf"{FEL_TOKEN_PATTERN}",
    re.IGNORECASE,
)
TITLE_HEADER_RE = re.compile(r"\b(?:title|movie|film)\b", re.IGNORECASE)
TABLE_TITLE_YEAR_RE = re.compile(r"^(?P<title>.+?)\s*\((?P<year>(?:19|20)\d{2})\)$")
NON_TITLE_HEADER_RE = re.compile(
    r"\b(?:group|release\s*group|team|encoder|uploader|audio|sound|"
    r"language|region|country|studio|label|year|date|notes?|evidence|"
    r"source|proof|dv|dolby|vision|profile|hdr|video|format|status|disc|"
    r"layer)\b",
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
    r"(?:is|are|has|features|includes|confirmed as|confirmed to be)\b",
    re.IGNORECASE,
)
TITLE_BINDING_SUFFIX_RE = re.compile(
    r"\s+(?:is|has|features|includes|confirmed as|confirmed to be)$",
    re.IGNORECASE,
)
SUFFIX_TITLE_BINDING_RE = re.compile(
    r"\b(?:confirmed\s+for|for|on|in|applies\s+to)\s+"
    r"[\"']?(?P<title>[A-Z0-9][A-Za-z0-9:'&.,!?\- ]{0,80})[\"']?"
    r"(?:\s+\(\d{4}\))?(?=[.!?,;:]|$)"
    r"|\bconfirmed\s*:\s*"
    r"[\"']?(?P<confirmed_title>[A-Z0-9][A-Za-z0-9:'&.,!?\- ]{0,80})"
    r"[\"']?(?:\s+\(\d{4}\))?(?=[.!?,;:]|$)",
    re.IGNORECASE,
)
SEPARATOR_TITLE_BINDING_RE = re.compile(
    r"^\s*(?:[-:]|\()\s*"
    r"[\"']?(?P<title>[A-Z][A-Za-z0-9:'&.,!?\- ]{0,80})[\"']?"
    r"(?:\s+\(\d{4}\))?(?=[).!?,;:]|$)"
)
PROOF_METADATA_PREFIX_RE = re.compile(
    r"^(?:mediainfo|bdinfo|disc\s+scan|source|scan|proof)\b", re.IGNORECASE
)
SUFFIX_METADATA_RE = re.compile(
    r"\s+(?:with|including|via|by|from)\s+.+$", re.IGNORECASE
)
FEL_SUBJECT_DELIMITER_RE = re.compile(r"(?:[:,;/|&+•]|\band\b)", re.IGNORECASE)
_PARENTHESIZED_YEAR_RE = re.compile(r"\(\s*(?:19|20)\d{2}\s*\)")
_BINDING_RESIDUE_HEAD = r"(?:is|are|has|have|with|also|features?|includes?|confirmed)"
_BINDING_RESIDUE_TAIL = (
    r"(?:is|are|has|have|with|also|features?|includes?|confirmed|as|to|be|"
    r"a|an|the|only|not\s+only)"
)
_AUDIO_TECHNICAL_RESIDUE_TOKEN = (
    r"(?:truehd(?:\s+atmos)?|dts(?:-hd\s+(?:ma|master\s+audio)|:x)|"
    r"e-?ac-?3(?:\s+atmos)?|dd\+(?:\s+atmos)?)"
)
_AUDIO_METADATA_RESIDUE_RE = re.compile(
    rf"(?:english\s+)?{_AUDIO_TECHNICAL_RESIDUE_TOKEN}(?:\s+tracks?)?",
    re.IGNORECASE,
)
_TECHNICAL_RESIDUE_TOKEN = (
    rf"(?:dolby\s+vision|dv|video|blu-?ray|uhd|4k|ultra\s+hd|disc|edition|"
    rf"full\s+enhancement\s+layer|enhancement\s+layer|layer|"
    rf"{_AUDIO_TECHNICAL_RESIDUE_TOKEN})"
)
_ALLOWED_TECHNICAL_BINDING_RE = re.compile(
    rf"(?:{_TECHNICAL_RESIDUE_TOKEN}(?:\s+{_TECHNICAL_RESIDUE_TOKEN})*"
    rf"(?:\s+tracks?)?"
    rf"|{_BINDING_RESIDUE_HEAD}"
    rf"(?:\s+(?:{_BINDING_RESIDUE_TAIL}|{_TECHNICAL_RESIDUE_TOKEN}|release))*)",
    re.IGNORECASE,
)
_LOWERCASE_RELEASE_METADATA_RE = re.compile(
    r"(?:released(?:\s+(?:theatrically|in\s+(?:19|20)\d{2}|"
    r"by\s+[\w][\w .'-]*))?"
    r"|release\s+(?:date|year)\s+(?:19|20)\d{2}"
    r"|release\s+(?:label|edition)\s+[\w][\w .'-]*)"
)
_NEGATED_MEL_RESIDUE_RE = re.compile(
    r"(?:(?:definitely\s+)?(?:not|no|without)\s+(?:dolby\s+vision\s+)?mel"
    r"|mel\s*(?:(?::|=|-|–|—|\bis\b|\?)\s*)?"
    rf"{EXPLICIT_DENIAL_STATUS_PATTERN})",
    re.IGNORECASE,
)
_NO_DOUBT_RESIDUE_RE = re.compile(
    r"(?:has\s+)?no\s+doubt(?:\s+(?:it\s+)?is)?", re.IGNORECASE
)
_PROOF_SOURCE_RESIDUE_TOKEN = r"(?:mediainfo|bdinfo|disc\s+scan|source|scan|proof)"
_PROOF_METADATA_RESIDUE_PATTERN = (
    rf"(?:(?:(?:confirmed|verified|proven)\s+)?(?:by|via|from)\s+"
    rf"{_PROOF_SOURCE_RESIDUE_TOKEN}"
    rf"(?:\s+with\s+(?:english\s+)?{_AUDIO_TECHNICAL_RESIDUE_TOKEN})?"
    rf"|{_PROOF_SOURCE_RESIDUE_TOKEN}(?:\s+confirms?)?)"
)
_PROOF_METADATA_RESIDUE_RE = re.compile(
    _PROOF_METADATA_RESIDUE_PATTERN,
    re.IGNORECASE,
)
_TECHNICAL_PROOF_METADATA_RESIDUE_RE = re.compile(
    rf"{_TECHNICAL_RESIDUE_TOKEN}"
    rf"(?:\s+{_TECHNICAL_RESIDUE_TOKEN})*\s+"
    rf"{_PROOF_METADATA_RESIDUE_PATTERN}",
    re.IGNORECASE,
)
_GENERIC_STATUS_RESIDUE_RE = re.compile(r"yes", re.IGNORECASE)
_MASKED_ROW_TITLE_BINDING_RESIDUE_RE = re.compile(
    rf"(?:confirmed\s+for|for|on|in|applies\s+to)"
    rf"(?:\s+(?:with\s+{_AUDIO_TECHNICAL_RESIDUE_TOKEN}"
    rf"|(?:via|by|from)\s+{_PROOF_SOURCE_RESIDUE_TOKEN}))?",
    re.IGNORECASE,
)
_LOWERCASE_PARENTHETICAL_ADVERB_RE = re.compile(r"parenthetically")
AMBIGUOUS_PROSE_TITLE_RE = re.compile(
    r"^(?:(?:this|that|a|an|the)\s+)?(?:[A-Za-z0-9.+'-]+\s+){0,4}"
    r"(?:spreadsheet|list|post|thread|forum|page|source|site|table|note|comment|"
    r"review)\s+(?:says|lists|shows|mentions|reports)[:,]?\s+"
    r"|^according\s+to\s+(?:the\s+)?(?:spreadsheet|list|post|thread|forum|"
    r"page|source|site|table|note|comment|review),\s+"
    r"|^(?:list|table|source|spreadsheet)\s+entry:\s+"
    r"|^in\s+(?:the\s+)?(?:spreadsheet|list|post|thread|forum|page|source|"
    r"site|table|note|comment|review),\s+"
    r"|^for\s+.+,\s+",
    re.IGNORECASE,
)
PROSE_TITLE_PREFIX_RE = re.compile(
    r"^(?:the\s+)?(?:disc|release|blu-?ray|uhd|4k|movie|film)\s+(?:for|of)\s+",
    re.IGNORECASE,
)
COLLECTION_COUNT_TITLE_RE = re.compile(
    r"^(?:here|there)\s+(?:are|is)\s+\d+\s+"
    r"(?:verified|confirmed|listed|known)?\s*"
    r"(?:p7|profile\s*7|dolby\s+vision|fel|uhd|blu-?ray|films?|movies?)\b",
    re.IGNORECASE,
)
FORUM_TIMESTAMP_RE = re.compile(
    r"\b(?:mon|tue|wed|thu|fri|sat|sun)\s+"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+"
    r"\d{1,2},\s+\d{4}\s+\d{1,2}:\d{2}\s+(?:am|pm)\b",
    re.IGNORECASE,
)
TITLE_SENTENCE_RE = re.compile(
    r"(?P<title>[A-Z][A-Za-z0-9:'&.,!?\- ]{1,80}?)(?:\s+\((?P<year>\d{4})\))?"
    r"\s+(?:is|are|has|features|includes|confirmed as|confirmed to be).{0,120}?"
    rf"(?:{PROFILE_7_PATTERN}.{{0,40}}?{FEL_TOKEN_PATTERN}|"
    rf"{FEL_TOKEN_PATTERN}.{{0,40}}?{PROFILE_7_PATTERN})",
    re.IGNORECASE,
)
DIRECT_TITLE_YEAR_SENTENCE_RE = re.compile(
    r"^(?P<title>[A-Z0-9][A-Za-z0-9:'&.,!?\- ]{0,80}?)"
    r"\s+\((?P<year>\d{4})\)(?:\s+|[,:]\s*)"
    rf"(?:{PROFILE_7_PATTERN}.{{0,40}}?{FEL_TOKEN_PATTERN}|"
    rf"{FEL_TOKEN_PATTERN}.{{0,40}}?{PROFILE_7_PATTERN})",
    re.IGNORECASE,
)
MAX_FEL_SENTENCE_LENGTH = 16 * 1024
NON_TABLE_RECORD_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "dd",
        "details",
        "div",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "nav",
        "p",
        "pre",
        "section",
        "summary",
    }
)
NON_CONTENT_TAGS = frozenset({"head", "noscript", "script", "style", "template"})
LIST_ITEM_TITLE_RE = re.compile(
    r"^\s*(?P<title>.+?)\s+\((?P<year>\d{4})\)(?P<details>.*)$",
    re.IGNORECASE,
)
LIST_ITEM_FEL_BITRATE_RE = re.compile(
    rf"{FEL_TOKEN_PATTERN}\s*-\s*(?P<bitrate>\d+(?:\.\d+)?)\s*Mb/s",
    re.IGNORECASE,
)
# Substrings that mark a candidate as AV-hardware jargon or prose subject
# rather than a film title (e.g. "...this device...", "the splitter").
_BANNED_TITLE_WORDS = (
    "hardware",
    "player",
    "splitter",
    "profile",
    "dolby vision",
    "device",
)
# Playback-hardware names (FEL-capable media players, set-top boxes, TVs,
# chipsets, AV gear) that forum threads describe as "Profile 7 FEL" the same
# way they describe discs; these are devices, never film titles.  Brand words
# that collide with real film titles (e.g. "Dune", "Shield") only count as
# hardware with model context.
DEVICE_TITLE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"ugoos|zidoo|homatics|minix|reavon|magnetar|panasonic|amlogic|realtek|"
    r"mediatek|nvidia|chromecast|coreelec|madvr|hdfury|"
    r"dune[\s-]*hd|apple[\s-]*tv|fire[\s-]*(?:tv|stick)|android[\s-]*tv|"
    r"google[\s-]*tv|shield[\s-]*(?:tv|pro)|"
    r"oppo[\s-]*(?:udp|bdp)?[\s-]*\d{2,3}|"
    r"s922x|rtd[\s-]*1619|z9x|am6b?(?:[\s-]+plus)?|uhd\d{4}"
    r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)
# Hardware category words shared with _BANNED_TITLE_WORDS; kept separate so
# is_device_title never bans format jargon such as "profile" (a real film).
_DEVICE_TITLE_WORDS = ("hardware", "player", "splitter", "device", "set-top")


def is_device_title(value: str) -> bool:
    """Return True when a candidate title names playback hardware, not a film."""
    lowered = value.lower()
    if any(word in lowered for word in _DEVICE_TITLE_WORDS):
        return True
    return bool(DEVICE_TITLE_RE.search(value))


def parse_fel_releases(html: str, source_url: str) -> list[FelRelease]:
    soup = BeautifulSoup(html, "html.parser")
    releases: list[FelRelease] = []
    releases.extend(_parse_list_items(soup, source_url))
    releases.extend(_parse_tables(soup, source_url))
    for table in soup.find_all("table"):
        table.decompose()
    for record in _non_table_text_records(soup):
        releases.extend(_parse_sentences(record, source_url))
    return _dedupe_releases(releases)


def _non_table_text_records(soup: BeautifulSoup) -> list[str]:
    records: list[str] = []

    def flush(buffer: list[str]) -> None:
        text = normalize_title("".join(buffer))
        buffer.clear()
        if text:
            records.append(text)

    root_buffer: list[str] = []
    stack: list[tuple[str, object, list[str]]] = [("enter", soup, root_buffer)]
    while stack:
        action, node, buffer = stack.pop()
        if action == "exit":
            if node is soup or getattr(node, "name", None) in NON_TABLE_RECORD_TAGS:
                flush(buffer)
            continue

        if isinstance(node, NavigableString):
            if type(node) is NavigableString:
                buffer.append(str(node))
            continue

        name = getattr(node, "name", None)
        if name in NON_CONTENT_TAGS:
            continue
        if name in {"br", "hr"}:
            flush(buffer)
            continue

        child_buffer = buffer
        if name in NON_TABLE_RECORD_TAGS:
            flush(buffer)
            child_buffer = []

        stack.append(("exit", node, child_buffer))
        for child in reversed(list(getattr(node, "children", ()))):
            stack.append(("enter", child, child_buffer))

    return records


def _parse_list_items(soup: BeautifulSoup, source_url: str) -> list[FelRelease]:
    releases: list[FelRelease] = []
    for item in soup.find_all("li"):
        text = normalize_title(item.get_text(" ", strip=True))
        title_match = LIST_ITEM_TITLE_RE.search(text)
        if not title_match:
            continue
        evidence_match = LIST_ITEM_FEL_BITRATE_RE.search(title_match.group("details"))
        if not evidence_match:
            continue
        title = normalize_title(title_match.group("title"))
        if not _looks_like_list_item_title(title):
            continue  # pragma: no cover - list-item title rejected
        release = _build_release(title, text, source_url, "list-item")
        release.release_date = title_match.group("year")
        release.additional_characteristics["enhancement_bitrate_mbps"] = (
            evidence_match.group("bitrate")
        )
        releases.append(release)
    return releases


def _parse_tables(soup: BeautifulSoup, source_url: str) -> list[FelRelease]:
    releases: list[FelRelease] = []
    for table in soup.find_all("table"):
        headers: list[str] = []
        for row in table.find_all("tr"):
            cells = [
                cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])
            ]
            if len(cells) < 2:
                continue  # pragma: no cover - single-cell row skip
            if row.find("th") and not row.find("td"):
                headers = cells
                continue
            title_index = _title_cell_index(headers, cells)
            title = normalize_title(cells[title_index])
            if not _looks_like_title(title):
                continue
            title_year_match = TABLE_TITLE_YEAR_RE.fullmatch(title)
            release_title = (
                normalize_title(title_year_match.group("title"))
                if title_year_match
                else title
            )
            if not _looks_like_title(release_title):
                continue
            has_correlated_evidence = _has_table_evidence_for_title(
                title, cells, headers, title_index
            )
            if title_year_match:
                expected_year = title_year_match.group("year")
                if _has_conflicting_bound_title_year(
                    release_title, expected_year, cells, title_index
                ):
                    continue
                if not has_correlated_evidence:
                    has_correlated_evidence = _has_table_evidence_for_title(
                        release_title, cells, headers, title_index
                    )
            if not has_correlated_evidence:
                continue
            release = _build_release(
                release_title, " ".join(cells), source_url, "table-row"
            )
            if title_year_match:
                release.release_date = title_year_match.group("year")
            releases.append(release)
    return releases


def _parse_sentences(text: str, source_url: str) -> list[FelRelease]:
    releases: list[FelRelease] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if len(sentence) > MAX_FEL_SENTENCE_LENGTH:
            continue
        if not FEL_RE.search(sentence) or not PROFILE_7_RE.search(sentence):
            continue
        match = TITLE_SENTENCE_RE.search(sentence)
        if not match:
            match = DIRECT_TITLE_YEAR_SENTENCE_RE.search(sentence)
        if not match:
            continue  # pragma: no cover - no sentence title regex match
        title = _clean_sentence_title(match.group("title"))
        if not _looks_like_title(title):
            continue
        if not _has_direct_fel(sentence, title, match.span("title")):
            continue
        release = _build_release(title, sentence, source_url, "sentence")
        if match.group("year"):
            release.release_date = match.group("year")
        releases.append(release)
    return releases


def _without_bound_title(
    text: str, title: str, title_span: tuple[int, int] | None = None
) -> str:
    if title_span is not None:
        start, end = title_span
        return f"{text[:start]}{' ' * (end - start)}{text[end:]}"
    if not title:
        return text
    return re.sub(rf"(?<!\w){re.escape(title)}(?!\w)", "", text, count=1, flags=re.I)


def fel_clause_residue(clause: str) -> str:
    residue = _PARENTHESIZED_YEAR_RE.sub(" ", clause)
    residue = PROFILE_7_RE.sub(" ", residue)
    residue = FEL_RE.sub(" ", residue)
    return re.sub(r"\s+", " ", residue).strip(" \t:;,.!?()[]{}'\"–—-")


def _matches_allowed_fel_residue(residue: str) -> bool:
    return any(
        pattern.fullmatch(residue)
        for pattern in (
            _ALLOWED_TECHNICAL_BINDING_RE,
            _AUDIO_METADATA_RESIDUE_RE,
            _LOWERCASE_RELEASE_METADATA_RE,
            _NEGATED_MEL_RESIDUE_RE,
            _NO_DOUBT_RESIDUE_RE,
            _PROOF_METADATA_RESIDUE_RE,
            _TECHNICAL_PROOF_METADATA_RESIDUE_RE,
            _GENERIC_STATUS_RESIDUE_RE,
            _LOWERCASE_PARENTHETICAL_ADVERB_RE,
        )
    )


def is_allowed_fel_clause_residue(text: str, candidate_title: str = "") -> bool:
    residue = text.strip()
    if _matches_allowed_fel_residue(residue):
        return True
    if candidate_title and _MASKED_ROW_TITLE_BINDING_RESIDUE_RE.fullmatch(residue):
        return True
    if candidate_title:
        repeated_title = re.match(
            rf"(?<!\w){re.escape(candidate_title)}(?!\w)", residue, re.IGNORECASE
        )
        if repeated_title:
            remainder = residue[repeated_title.end() :].strip()
            return bool(remainder and _matches_allowed_fel_residue(remainder))
    return False


def fel_subject_clauses(text: str, candidate_title: str = "") -> list[str]:
    clauses: list[str] = []
    delimiters: list[str] = []
    start = 0
    for delimiter in FEL_SUBJECT_DELIMITER_RE.finditer(text):
        clauses.append(text[start : delimiter.start()])
        delimiters.append(delimiter.group())
        start = delimiter.end()
    clauses.append(text[start:])

    logical_clauses: list[str] = []
    index = 0
    while index < len(clauses):
        clause = clauses[index]
        residue = fel_clause_residue(clause)
        if (
            residue
            and not is_allowed_fel_clause_residue(residue, candidate_title)
            and index < len(delimiters)
        ):
            combined = f"{clause}{delimiters[index]}{clauses[index + 1]}"
            combined_residue = fel_clause_residue(combined)
            if is_allowed_fel_clause_residue(combined_residue, candidate_title):
                logical_clauses.append(combined)
                index += 2
                continue
        logical_clauses.append(clause)
        index += 1
    return logical_clauses


def has_correlated_fel_clause(text: str, candidate_title: str = "") -> bool:
    has_complete_marker_clause = False
    for clause in fel_subject_clauses(text, candidate_title):
        has_profile_7 = bool(PROFILE_7_RE.search(clause))
        has_fel = bool(FEL_RE.search(clause))
        residue = fel_clause_residue(clause)
        if residue and not is_allowed_fel_clause_residue(residue, candidate_title):
            return False
        has_complete_marker_clause |= has_profile_7 and has_fel
    return has_complete_marker_clause


def _has_direct_fel(
    text: str, title: str = "", title_span: tuple[int, int] | None = None
) -> bool:
    evidence = _without_bound_title(text, title, title_span)
    if not has_correlated_fel_clause(evidence, title):
        return False
    if re.search(r"\bREMUX\b", evidence, re.IGNORECASE):
        return False
    if has_unnegated_mel(evidence):
        return False
    if has_leading_fel_denial(evidence):
        return False
    if FEL_TRAILING_DENIAL_RE.search(evidence):
        return False
    return bool(FEL_RE.search(evidence) and PROFILE_7_RE.search(evidence))


def has_leading_fel_denial(text: str) -> bool:
    return bool(
        FEL_LEADING_DENIAL_RE.search(text) or FEL_CONFIRMATION_DENIAL_RE.search(text)
    )


def has_unnegated_mel(text: str) -> bool:
    for match in MEL_TOKEN_RE.finditer(text):
        leading_context = text[max(0, match.start() - 80) : match.end()]
        if MEL_LEADING_DENIAL_RE.search(leading_context):
            continue
        if MEL_TRAILING_DENIAL_RE.match(text, match.end()):
            continue
        return True
    return False


def _bound_table_title_span(cell: str, title: str) -> tuple[int, int] | None:
    normalized_cell = normalize_title(cell)
    normalized_title = normalize_title(title)
    if not normalized_title:
        return None
    title_matches = list(
        re.finditer(
            rf"(?<![A-Za-z0-9]){re.escape(normalized_title)}(?![A-Za-z0-9])",
            normalized_cell,
            re.IGNORECASE,
        )
    )
    if not title_matches:
        return None

    profile_7_match = PROFILE_7_RE.search(normalized_cell)
    fel_match = FEL_RE.search(normalized_cell)
    if not profile_7_match or not fel_match:
        return None
    evidence_start = min(profile_7_match.start(), fel_match.start())
    evidence_end = max(profile_7_match.end(), fel_match.end())

    leading_title = _leading_title_before_evidence(normalized_cell)
    if leading_title and _normalized_title_prefix(leading_title) == _normalized_value(
        title
    ):
        return next(
            (match.span() for match in title_matches if match.end() <= evidence_start),
            None,
        )

    suffix_titles = (
        _suffix_title_after_evidence(normalized_cell),
        _separator_title_after_evidence(normalized_cell),
    )
    if any(
        suffix_title and _normalized_value(suffix_title) == _normalized_value(title)
        for suffix_title in suffix_titles
    ):
        return next(
            (match.span() for match in title_matches if match.start() >= evidence_end),
            None,
        )
    return None


def _has_table_evidence_for_title(
    title: str, cells: list[str], headers: list[str], title_index: int
) -> bool:
    for index, cell in enumerate(cells):
        if index == title_index:
            continue
        normalized_cell = normalize_title(cell)
        title_span = _bound_table_title_span(normalized_cell, title)
        if not _has_direct_fel(
            normalized_cell,
            title if title_span is not None else "",
            title_span,
        ):
            continue
        header = headers[index] if index < len(headers) else ""
        if RELEASE_STATUS_HEADER_RE.search(header):
            return _cell_supports_row_title(cell, title)
        if TITLE_SPECIFIC_HEADER_RE.search(header):
            return _title_specific_cell_supports_row_title(cell, title)
        if not headers:
            return _cell_supports_row_title(cell, title)
        if _title_specific_cell_supports_row_title(cell, title):
            return True
    return False


def _has_conflicting_bound_title_year(
    title: str,
    expected_year: str,
    cells: list[str],
    title_index: int,
) -> bool:
    title_year_re = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(normalize_title(title))}\s*"
        rf"\((?P<year>(?:19|20)\d{{2}})\)(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    for index, cell in enumerate(cells):
        if index == title_index:
            continue
        if any(
            match.group("year") != expected_year
            for match in title_year_re.finditer(normalize_title(cell))
        ):
            return True
    return False


def _title_cell_index(headers: list[str], cells: list[str]) -> int:
    for index, header in enumerate(headers[: len(cells)]):
        if TITLE_HEADER_RE.search(header):
            return index
    for index, header in enumerate(
        headers[: len(cells)]
    ):  # pragma: no cover - no Title header fallback
        if not NON_TITLE_HEADER_RE.search(header):
            return index
    return 0


def _title_specific_cell_supports_row_title(cell: str, title: str) -> bool:
    leading_title = _leading_title_before_evidence(cell)
    if leading_title:
        return _normalized_title_prefix(leading_title) == _normalized_value(title)
    suffix_title = _suffix_title_after_evidence(cell)
    if suffix_title:
        return _normalized_value(suffix_title) == _normalized_value(title)
    separator_title = _separator_title_after_evidence(cell)
    if separator_title:
        return _normalized_value(separator_title) == _normalized_value(title)
    if TITLE_BINDING_RE.search(normalize_title(cell)):
        return _cell_mentions_title(
            cell, title
        )  # pragma: no cover - title-binding branch
    if _cell_mentions_title(cell, title):
        return True  # pragma: no cover - explicit mention branch
    return True


def _cell_supports_row_title(cell: str, title: str) -> bool:
    leading_title = _leading_title_before_evidence(cell)
    if leading_title:
        return _normalized_title_prefix(leading_title) == _normalized_value(title)
    suffix_title = _suffix_title_after_evidence(cell)
    if suffix_title:
        return _normalized_value(suffix_title) == _normalized_value(title)
    separator_title = _separator_title_after_evidence(cell)
    if separator_title:
        return _normalized_value(separator_title) == _normalized_value(
            title
        )  # pragma: no cover - separator-title branch
    if TITLE_BINDING_RE.search(normalize_title(cell)):
        return _cell_mentions_title(
            cell, title
        )  # pragma: no cover - title-binding branch
    return True


def _cell_mentions_title(cell: str, title: str) -> bool:
    normalized_title = normalize_title(title)
    if not normalized_title:
        return False  # pragma: no cover - empty title guard
    pattern = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(normalized_title)}(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    return bool(pattern.search(normalize_title(cell)))


def _leading_title_before_evidence(cell: str) -> str:
    normalized = normalize_title(cell)
    profile_7_match = PROFILE_7_RE.search(normalized)
    fel_match = FEL_RE.search(normalized)
    evidence_starts = [match.start() for match in (profile_7_match, fel_match) if match]
    if not evidence_starts:
        return ""  # pragma: no cover - no evidence in cell prefix
    prefix = normalized[: min(evidence_starts)].strip(" :-")
    if GENERIC_STATUS_PREFIX_RE.fullmatch(prefix):
        return ""
    return prefix


def _suffix_title_after_evidence(cell: str) -> str:
    normalized = normalize_title(cell)
    profile_7_match = PROFILE_7_RE.search(normalized)
    fel_match = FEL_RE.search(normalized)
    if not profile_7_match or not fel_match:
        return ""  # pragma: no cover - cell lacks both markers
    suffix_start = max(profile_7_match.end(), fel_match.end())
    match = SUFFIX_TITLE_BINDING_RE.search(normalized, suffix_start)
    if not match:
        return ""
    title = match.group("title") or match.group("confirmed_title") or ""
    title = SUFFIX_METADATA_RE.sub("", title)
    return title.strip(" :-().,!?;\"'")


def _separator_title_after_evidence(cell: str) -> str:
    normalized = normalize_title(cell)
    profile_7_match = PROFILE_7_RE.search(normalized)
    fel_match = FEL_RE.search(normalized)
    if not profile_7_match or not fel_match:
        return ""  # pragma: no cover - cell lacks both markers
    suffix = normalized[max(profile_7_match.end(), fel_match.end()) :]
    match = SEPARATOR_TITLE_BINDING_RE.search(suffix)
    if not match:
        return ""
    title = SUFFIX_METADATA_RE.sub("", match.group("title"))
    if PROOF_METADATA_PREFIX_RE.search(title.strip()):
        return ""
    return title.strip(" :-().,!?;")


def _normalized_value(value: str) -> str:
    return normalize_title(value).casefold()


def _normalized_title_prefix(value: str) -> str:
    prefix = TITLE_BINDING_SUFFIX_RE.sub("", normalize_title(value))
    return _normalized_value(prefix)


def _clean_sentence_title(value: str) -> str:
    title = normalize_title(value)
    if AMBIGUOUS_PROSE_TITLE_RE.match(title):
        return ""
    if COLLECTION_COUNT_TITLE_RE.match(title):
        return ""  # pragma: no cover - collection-count prefix
    return PROSE_TITLE_PREFIX_RE.sub("", title).strip(" :,-")


def _looks_like_title(value: str) -> bool:
    lowered = value.lower()
    if not value or len(value) > 100:
        return False
    if lowered in {"here", "there", "this", "these", "those"}:
        return False
    if FORUM_TIMESTAMP_RE.search(value):
        return False
    if re.search(r"\b(?:and|or)\b", lowered):
        return False
    if any(word in lowered for word in _BANNED_TITLE_WORDS):
        return False  # pragma: no cover - banned-word title rejection
    if is_device_title(value):
        return False
    return any(character.isalnum() for character in value)


def _looks_like_list_item_title(
    value: str,
) -> bool:  # pragma: no cover - list-item title guards
    lowered = value.lower()
    if not value or len(value) > 100:
        return False
    if lowered in {"here", "there", "this", "these", "those"}:
        return False
    if FORUM_TIMESTAMP_RE.search(value):
        return False
    if any(word in lowered for word in _BANNED_TITLE_WORDS):
        return False
    if is_device_title(value):
        return False
    return any(character.isalnum() for character in value)


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
