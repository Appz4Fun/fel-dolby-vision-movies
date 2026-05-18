from __future__ import annotations

from pathlib import Path


def read_source_urls(path: Path | str) -> list[str]:
    source_path = Path(path)
    if not source_path.exists():
        return []
    seen: set[str] = set()
    urls: list[str] = []
    for raw_line in source_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line in seen:
            continue
        seen.add(line)
        urls.append(line)
    return urls


def write_source_urls(path: Path | str, urls: list[str]) -> None:
    source_path = Path(path)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    unique = list(dict.fromkeys(urls))
    text = "\n".join(unique)
    if text:
        text += "\n"
    source_path.write_text(text, encoding="utf-8")


def merge_confirmed_sources(path: Path | str, confirmed_urls: list[str]) -> bool:
    current = read_source_urls(path)
    merged = list(dict.fromkeys([*current, *confirmed_urls]))
    if merged == current:
        return False
    write_source_urls(path, merged)
    return True
