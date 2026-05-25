from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import html
import json
from pathlib import Path
import re
import time
from typing import Protocol
import urllib.parse

import httpx

from enrich import _get_with_retry
from merge import canonical_title_key

_FREQ_RE = re.compile(r"\s*\([^)]*\)")
_ABBREVIATIONS = (
    ("Dolby Digital Plus", "DD+"),
    ("Dolby Digital", "DD"),
    ("DTS-HD Master Audio", "DTS-HD MA"),
    ("DTS-HD High-Resolution Audio", "DTS-HD HRA"),
)
# An audio line is a real track only when its format starts with one of these.
KNOWN_CODECS = (
    "Dolby Atmos",
    "Dolby TrueHD",
    "Dolby Digital Plus",
    "Dolby Digital",
    "DTS:X",
    "DTS-HD Master Audio",
    "DTS-HD High-Resolution Audio",
    "DTS",
    "LPCM",
    "PCM",
)
KNOWN_HDR = ("Dolby Vision", "HDR10+", "HDR10", "HLG")


def parse_hdr(hdr_text: str) -> list[str]:
    out: list[str] = []
    for token in (hdr_text or "").split(","):
        candidate = token.strip()
        if candidate in KNOWN_HDR and candidate not in out:
            out.append(candidate)
    return out


def _strip_freq(fmt: str) -> str:
    return _FREQ_RE.sub("", fmt).strip()


def _abbreviate(fmt: str) -> str:
    for long_name, short_name in _ABBREVIATIONS:
        if fmt.startswith(long_name):
            return short_name + fmt[len(long_name) :]
    return fmt


def normalize_bluray_audio(tracks: list[tuple[str, str]]) -> list[str]:
    """Canonicalize (language, raw_format) audio tracks into a deduped list."""
    by_language: dict[str, list[str]] = {}
    for language, raw_format in tracks:
        fmt = _abbreviate(_strip_freq(raw_format))
        by_language.setdefault(language, []).append(fmt)

    result: list[str] = []
    for formats in by_language.values():
        has_atmos = "Dolby Atmos" in formats
        has_dtsx = "DTS:X" in formats
        language_out: list[str] = []
        for fmt in formats:
            if fmt in ("Dolby Atmos", "DTS:X"):
                continue  # merged into the core track below
            if has_atmos and fmt.startswith("Dolby TrueHD "):
                fmt = (
                    "Dolby TrueHD/Atmos " + fmt[len("Dolby TrueHD ") :]
                )  # pragma: no cover
            elif has_atmos and fmt.startswith("DD+ "):
                fmt = "DD+/Atmos " + fmt[len("DD+ ") :]  # pragma: no cover
            elif has_dtsx and fmt.startswith("DTS-HD MA "):
                fmt = "DTS:X " + fmt[len("DTS-HD MA ") :]
            language_out.append(fmt)
        if has_atmos and not any("Atmos" in f for f in language_out):
            language_out.append(
                "Dolby Atmos"
            )  # pragma: no cover - standalone Atmos fallback
        if has_dtsx and not any(f.startswith("DTS:X") for f in language_out):
            language_out.append("DTS:X")  # pragma: no cover - standalone DTS:X fallback
        for fmt in language_out:
            if fmt not in result:
                result.append(fmt)
    return result


_AUDIO_BLOCK_RE = re.compile(r'<div id="longaudio"[^>]*>(.*?)</div>', re.S)
_HDR_RE = re.compile(r"HDR:\s*([^<]+)")
_RELEASE_DATE_RE = re.compile(r"Release Date ([A-Z][a-z]+ \d{1,2}, \d{4})")
_TAG_RE = re.compile(r"<[^>]+>")
_SEARCH_URL = (
    "https://www.blu-ray.com/search/?quicksearch=1&section=bluraymovies"
    "&quicksearch_keyword={keyword}"
)
_RESULT_ANCHOR_RE = re.compile(
    r'href="(https://www\.blu-ray\.com/movies/[A-Za-z0-9][^"]*?-4K-Blu-ray/\d+/)"'
    r'[^>]*?title="([^"]*)"',
    re.S,
)
_DIRECT_4K_URL_RE = re.compile(
    r"^https://www\.blu-ray\.com/movies/([^/?#]+)-4K-Blu-ray/\d+/?$"
)
_YEAR_IN_TITLE_RE = re.compile(r"\((\d{4})\)")
DEFAULT_BLURAY_CACHE = Path(".cache/bluray.json")
BLURAY_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


@dataclass(frozen=True)
class BlurayDetails:
    url: str
    bluray_release_date: str = ""
    audio_formats: list[str] = field(default_factory=list)
    audio_languages: list[str] = field(default_factory=list)
    hdr_formats: list[str] = field(default_factory=list)


def _parse_release_date(text: str) -> str:
    try:
        return datetime.strptime(text, "%B %d, %Y").strftime("%Y-%m-%d")
    except ValueError:  # pragma: no cover - malformed release-date string
        return ""


def fetch_bluray_details(client: httpx.Client, url: str) -> BlurayDetails:
    html_text = _get_with_retry(client, url).text

    hdr_match = _HDR_RE.search(html_text)
    hdr_formats = parse_hdr(hdr_match.group(1)) if hdr_match else []

    tracks: list[tuple[str, str]] = []
    block = _AUDIO_BLOCK_RE.search(html_text)
    if block:
        for line in re.split(r"<br\s*/?>", block.group(1)):
            text = html.unescape(_TAG_RE.sub("", line)).replace("\xa0", " ").strip()
            if ":" not in text:
                continue
            language, fmt = text.split(":", 1)
            language, fmt = language.strip(), fmt.strip()
            if not fmt.startswith(KNOWN_CODECS):
                continue  # pragma: no cover - unrecognized audio codec skip
            tracks.append((language, fmt))

    date_match = _RELEASE_DATE_RE.search(html_text)
    return BlurayDetails(
        url=url,
        bluray_release_date=(
            _parse_release_date(date_match.group(1)) if date_match else ""
        ),
        audio_formats=normalize_bluray_audio(tracks),
        audio_languages=list(dict.fromkeys(lang for lang, _ in tracks)),
        hdr_formats=hdr_formats,
    )


def search_bluray(client: httpx.Client, title: str, year: str) -> str | None:
    """Return the blu-ray.com 4K Blu-ray URL for a confident match."""
    for keyword in _search_keywords(title):
        url = _SEARCH_URL.format(keyword=urllib.parse.quote(keyword))
        match = _search_bluray_keyword(client, url, title, year)
        if match is not None:
            return match
    return None


def _search_keywords(title: str) -> list[str]:
    keywords = [title]
    if "4k" not in title.casefold():
        keywords.append(f"{title} 4K")
    return keywords


def _search_bluray_keyword(
    client: httpx.Client, url: str, title: str, year: str
) -> str | None:
    response = _get_with_retry(client, url, follow_redirects=True)
    direct_url = _direct_4k_url_if_confident(str(response.url), title)
    if direct_url is not None:
        return direct_url
    html_text = response.text
    want_title = canonical_title_key(title)
    want_year = int(year[:4]) if year[:4].isdigit() else None

    for href, anchor_title in _RESULT_ANCHOR_RE.findall(
        html_text
    ):  # pragma: no cover - search-result fallback
        slug = href.rsplit("/movies/", 1)[1].rsplit("-4K-Blu-ray/", 1)[0]
        if canonical_title_key(slug.replace("-", " ")) != want_title:
            continue
        year_match = _YEAR_IN_TITLE_RE.search(anchor_title)
        if want_year and year_match:
            if abs(int(year_match.group(1)) - want_year) > 1:
                continue
        return href
    return None


def _direct_4k_url_if_confident(url: str, title: str) -> str | None:
    match = _DIRECT_4K_URL_RE.match(url)
    if match is None:
        return None
    slug = match.group(1).replace("-", " ")
    if canonical_title_key(slug) != canonical_title_key(title):
        return None  # pragma: no cover - direct-URL title mismatch
    return url


class BlurayMatcher(Protocol):
    def resolve(self, title: str, year: str) -> BlurayDetails | None: ...


class StaticBlurayResolver:
    def __init__(self, records: dict[tuple[str, str], BlurayDetails]) -> None:
        self.records = records

    def resolve(self, title: str, year: str) -> BlurayDetails | None:
        return self.records.get((title, year))


def _details_to_record(details: BlurayDetails | None) -> dict[str, object] | None:
    if details is None:
        return None  # pragma: no cover - unresolved blu-ray match
    return {
        "url": details.url,
        "bluray_release_date": details.bluray_release_date,
        "audio_formats": details.audio_formats,
        "audio_languages": details.audio_languages,
        "hdr_formats": details.hdr_formats,
    }


def _details_from_record(record: dict[str, object] | None) -> BlurayDetails | None:
    if record is None:
        return None  # pragma: no cover - cached None-result
    return BlurayDetails(
        url=str(record.get("url") or ""),
        bluray_release_date=str(record.get("bluray_release_date") or ""),
        audio_formats=list(record.get("audio_formats") or []),
        audio_languages=list(record.get("audio_languages") or []),
        hdr_formats=list(record.get("hdr_formats") or []),
    )


class BlurayResolver:
    def __init__(
        self,
        client: httpx.Client | None = None,
        cache_path: Path = DEFAULT_BLURAY_CACHE,
        delay_seconds: float = 0.025,
    ) -> None:
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(20.0),
            headers={"User-Agent": BLURAY_USER_AGENT},
        )
        self._owns_client = client is None
        self.cache_path = cache_path
        self.delay_seconds = delay_seconds
        self.cache: dict[str, dict[str, object] | None] = self._read_cache()

    def resolve(self, title: str, year: str) -> BlurayDetails | None:
        key = f"{title}\0{year}"
        if key in self.cache:
            return _details_from_record(self.cache[key])
        details: BlurayDetails | None = None
        url = search_bluray(self.client, title, year)
        if url is not None:
            details = fetch_bluray_details(self.client, url)
        self.cache[key] = _details_to_record(details)
        self._write_cache()
        time.sleep(self.delay_seconds)
        return details

    def _read_cache(
        self,
    ) -> dict[str, dict[str, object] | None]:  # pragma: no cover - cache read
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

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> BlurayResolver:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
