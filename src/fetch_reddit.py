from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import urllib.request


DEFAULT_URL = (
    "https://old.reddit.com/r/CoreElecOS/comments/1j3lgw2/"
    "list_of_dolby_vision_p7fel_films/"
)


class RedditUserTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []
        self.in_usertext = False
        self.div_depth = 0
        self.usertext_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
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
            self.text.append("\n---\n")
        self.div_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.in_usertext:
            self.text.append(data)


def fetch_reddit_dump(url: str = DEFAULT_URL) -> str:
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )
    with urllib.request.urlopen(request) as response:
        html = response.read().decode("utf-8")
    parser = RedditUserTextParser()
    parser.feed(html)
    return "".join(parser.text)


def main() -> int:
    Path("reddit_dump.txt").write_text(fetch_reddit_dump(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
