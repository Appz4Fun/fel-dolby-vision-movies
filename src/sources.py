from __future__ import annotations

from pathlib import Path
import re
import urllib.parse

_AVSFORUM_THREAD_RE = re.compile(r"^/threads/[^/]*\.(\d+)(?:/)?$")


def _read_source_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    lines: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def _dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for url in urls:
        key = _canonical_source_key(url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(url)
    return unique


def _canonical_source_key(url: str) -> str:
    parsed = urllib.parse.urlparse(url.strip())
    scheme = (parsed.scheme or "https").lower()
    hostname = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    if _is_reddit_host(hostname):
        thread_id = _reddit_thread_id(path)
        if thread_id:
            return f"reddit:{thread_id}"
        hostname = "reddit.com"
        return urllib.parse.urlunparse((scheme, hostname, path, "", "", ""))
    if hostname in {"avsforum.com", "www.avsforum.com"}:
        thread_id = _avsforum_thread_id(path)
        if thread_id:
            return f"avsforum:{thread_id}"
        hostname = "avsforum.com"
    if hostname == "forum.makemkv.com" and path.endswith("/viewtopic.php"):
        params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        significant = [
            (name, value) for name, value in params if name in {"p", "start", "t"}
        ]
        query = urllib.parse.urlencode(sorted(significant))
        return urllib.parse.urlunparse((scheme, hostname, path, "", query, ""))
    query = urllib.parse.urlencode(
        sorted(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    )
    return urllib.parse.urlunparse((scheme, hostname, path, "", query, ""))


def _is_reddit_host(hostname: str) -> bool:
    return hostname in {"reddit.com", "www.reddit.com", "old.reddit.com"}


def _reddit_thread_id(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    try:
        comments_index = parts.index("comments")
    except ValueError:
        return ""
    if comments_index + 1 >= len(parts):
        return ""
    return parts[comments_index + 1].casefold()


def _avsforum_thread_id(path: str) -> str:
    match = _AVSFORUM_THREAD_RE.match(path)
    if match is None:
        return ""
    return match.group(1)


def read_source_urls(path: Path | str) -> list[str]:
    source_path = Path(path)
    return _dedupe_urls(_read_source_lines(source_path))


def write_source_urls(path: Path | str, urls: list[str]) -> None:
    source_path = Path(path)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    unique = _dedupe_urls(urls)
    text = "\n".join(unique)
    if text:
        text += "\n"
    source_path.write_text(text, encoding="utf-8")


def merge_confirmed_sources(path: Path | str, confirmed_urls: list[str]) -> bool:
    source_path = Path(path)
    raw_current = _read_source_lines(source_path)
    current = _dedupe_urls(raw_current)
    merged = _dedupe_urls([*current, *confirmed_urls])
    if merged == raw_current:
        return False
    write_source_urls(source_path, merged)
    return True
