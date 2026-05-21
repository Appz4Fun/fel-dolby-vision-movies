"""Parse Reddit P7-FEL list pages into FelRelease objects.

Only clean, whole-line entries are extracted: a line must be a title
followed by a bracketed year ("Title [2024]" or "Title (2024)"),
optionally preceded by a comment lead-in such as "You forgot ". Titles
embedded mid-sentence in prose are intentionally not extracted, to avoid
false positives.
"""

from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
import re

from models import FelEvidence, FelRelease
from normalize import normalize_fel_title


# A clean list line: the whole line is "<title> [YYYY]" or "<title> (YYYY)".
_LIST_LINE_RE = re.compile(
    r"^(?P<title>.+?)\s*[\[(](?P<year>(?:19|20)\d{2})[\])]\s*$"
)
# Comment lead-ins that precede a title in discussion replies, e.g.
# "You forgot Sicario [2015]" -> the title is "Sicario".
_COMMENT_PREFIX_RE = re.compile(
    r"^(?:you forgot one:?|you forgot|you missed|don'?t forget|missing:?|also:?|add(?:ed)?:?)\s+",
    re.IGNORECASE,
)
# MEL (Minimal Enhancement Layer) lines are not FEL; skip them. Matched as a
# whole uppercase word so titles like "Melancholia" are not affected.
_MEL_RE = re.compile(r"\bMEL\b")


class _RedditUserTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []
        self.in_usertext = False
        self.div_depth = 0
        self.usertext_depth = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag == "br":
            if self.in_usertext:
                self.text.append("\n")
            return
        if tag != "div":
            return
        self.div_depth += 1
        for name, value in attrs:
            if name == "class" and value and "usertext-body" in value:
                self.in_usertext = True
                self.usertext_depth = self.div_depth

    def handle_endtag(self, tag: str) -> None:
        if tag == "p" and self.in_usertext:
            self.text.append("\n")
        elif tag == "div":
            if self.in_usertext and self.div_depth == self.usertext_depth:
                self.in_usertext = False
                self.text.append("\n")
            self.div_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.in_usertext:
            self.text.append(data)


def parse_reddit_releases(html: str, url: str) -> list[FelRelease]:
    """Extract FelRelease objects from a Reddit P7-FEL list page.

    Only clean whole-line "Title [Year]" entries (optionally with a
    leading comment phrase) are extracted; see the module docstring.
    """
    parser = _RedditUserTextParser()
    parser.feed(html)
    collected_at = datetime.now(timezone.utc).isoformat()
    seen: set[tuple[str, str]] = set()
    releases: list[FelRelease] = []
    for raw_line in "".join(parser.text).splitlines():
        line = raw_line.strip()
        if not line or _MEL_RE.search(line):
            continue
        candidate = _COMMENT_PREFIX_RE.sub("", line)
        match = _LIST_LINE_RE.match(candidate)
        if not match:
            continue
        title = normalize_fel_title(match.group("title"))
        year = match.group("year")
        if not title:
            continue
        key = (title.casefold(), year)
        if key in seen:
            continue
        seen.add(key)
        releases.append(
            FelRelease(
                movie_title=title,
                release_date=year,
                fel_evidence=FelEvidence(
                    source_url=url,
                    quote=line,
                    evidence_type="reddit-list",
                ),
                source_label="reddit",
                collected_at=collected_at,
            )
        )
    return releases
