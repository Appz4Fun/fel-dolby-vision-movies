from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
import re

from models import FelEvidence, FelRelease
from normalize import normalize_fel_title


_MOVIE_YEAR_RE = re.compile(r"\s*[\[(](?P<year>(?:19|20)\d{2})[\])]\s*$")
_MOVIE_TITLE_RE = re.compile(
    r"(?:^|\W)(?P<title>(?:[A-Z][a-z]*(?:\s+[A-Z][a-z]*)*|\b[A-Z]\w*(?:\s+[A-Z]\w*)*))$"
)


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
        if tag != "div":
            return
        self.div_depth += 1
        for name, value in attrs:
            if name == "class" and value and "usertext-body" in value:
                self.in_usertext = True
                self.usertext_depth = self.div_depth

    def handle_endtag(self, tag: str) -> None:
        if tag != "div":
            return
        if self.in_usertext and self.div_depth == self.usertext_depth:
            self.in_usertext = False
            self.text.append("\n")
        self.div_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.in_usertext:
            self.text.append(data)


def parse_reddit_releases(html: str, url: str) -> list[FelRelease]:
    parser = _RedditUserTextParser()
    parser.feed(html)
    collected_at = datetime.now(timezone.utc).isoformat()
    seen: set[tuple[str, str]] = set()
    releases: list[FelRelease] = []
    for raw_line in "".join(parser.text).splitlines():
        line = raw_line.strip()
        if not line or "MEL" in line:
            continue
        # Extract year from line
        year_match = _MOVIE_YEAR_RE.search(line)
        if not year_match:
            continue
        year = year_match.group("year")
        # Extract title (last capitalized phrase before year)
        before_year = _MOVIE_YEAR_RE.sub("", line)
        title_match = _MOVIE_TITLE_RE.search(before_year)
        if not title_match:
            continue
        title = normalize_fel_title(title_match.group("title"))
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
