"""Parsers for curated, always-FEL list pages.

These sources are hand-vetted (see data/sources_always_fel.txt): every movie
listed is treated as Dolby Vision Profile 7 FEL. Only clean whole-line
"Title [Year]" / "Title (Year)" entries are extracted, so prose mentions are
not picked up by accident.
"""

from __future__ import annotations

from datetime import datetime, timezone
import html as html_lib
import re

from models import UNKNOWN, FelEvidence, FelRelease
from normalize import normalize_fel_title


# A whole-line list entry: "Title [YYYY]" or "Title (YYYY)".
_LIST_LINE_RE = re.compile(r"^(?P<title>.+?)\s*[\[(](?P<year>(?:19|20)\d{2})[\])]\s*$")
# Leading list numbering, e.g. "12. " or "12) ".
_LEADING_NUMBER_RE = re.compile(r"^\s*\d+[.)]\s*")
# MEL (Minimal Enhancement Layer) lines are not FEL; skip them.
_MEL_RE = re.compile(r"\bMEL\b")
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style).*?</\1>")
_ITEM_NAME_RE = re.compile(r'data-item-full-display-name="([^"]+)"')
_LETTERBOXD_PAGE_RE = re.compile(r"/list/[^\"\s]+/page/(\d+)/")


def _strip_html(markup: str) -> str:
    return _TAG_RE.sub(" ", _SCRIPT_STYLE_RE.sub(" ", markup))


def _release(title: str, year: str, url: str, quote: str, label: str) -> FelRelease:
    return FelRelease(
        movie_title=title,
        release_date=year,
        fel_evidence=FelEvidence(
            source_url=url,
            quote=quote,
            evidence_type=f"{label}-list",
        ),
        source_label=label,
        collected_at=datetime.now(timezone.utc).isoformat(),
    )


def parse_fel_list_text(text: str, url: str, source_label: str) -> list[FelRelease]:
    """Extract releases from a plain-text "Title (Year)" curated FEL list."""
    seen: set[tuple[str, str]] = set()
    releases: list[FelRelease] = []
    for raw_line in text.splitlines():
        line = _LEADING_NUMBER_RE.sub("", raw_line.strip())
        if not line or _MEL_RE.search(line):
            continue
        match = _LIST_LINE_RE.match(line)
        if not match:
            continue
        title = normalize_fel_title(match.group("title"))
        year = match.group("year")
        if not title:
            continue  # pragma: no cover - normalized title became empty
        key = (title.casefold(), year)
        if key in seen:
            continue  # pragma: no cover - duplicate within same list
        seen.add(key)
        releases.append(_release(title, year, url, line, source_label))
    return releases


def parse_github_md_list(markdown: str, url: str) -> list[FelRelease]:
    """Parse a GitHub markdown FEL list (numbered "N. Title [Year]" lines)."""
    return parse_fel_list_text(markdown, url, "github")


def parse_discourse_list(markup: str, url: str) -> list[FelRelease]:
    """Parse a Discourse forum FEL-list page."""
    return parse_fel_list_text(_strip_html(markup), url, "discourse")


def parse_letterboxd_list(markup: str, url: str) -> list[FelRelease]:
    """Parse one page of a Letterboxd film list."""
    seen: set[tuple[str, str]] = set()
    releases: list[FelRelease] = []
    for raw_name in _ITEM_NAME_RE.findall(markup):
        display = html_lib.unescape(raw_name).strip()
        match = _LIST_LINE_RE.match(display)
        if match:
            title = normalize_fel_title(match.group("title"))
            year = match.group("year")
        else:  # pragma: no cover - letterboxd title without year
            title = normalize_fel_title(display)
            year = UNKNOWN
        if not title:
            continue  # pragma: no cover - normalized title became empty
        key = (title.casefold(), year)
        if key in seen:
            continue  # pragma: no cover - duplicate within same page
        seen.add(key)
        releases.append(_release(title, year, url, display, "letterboxd"))
    return releases


def letterboxd_page_count(markup: str) -> int:
    """Highest page number referenced on a Letterboxd list page (>= 1)."""
    pages = [int(n) for n in _LETTERBOXD_PAGE_RE.findall(markup)]
    return max(pages) if pages else 1
