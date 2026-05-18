from __future__ import annotations

import hashlib
from pathlib import Path


def count_public_cache_hits(
    expanded_urls_path: Path = Path(".cache/ai_expanded_urls.txt"),
    cache_dir: Path = Path(".cache/html"),
) -> tuple[int, int, int]:
    urls = expanded_urls_path.read_text(encoding="utf-8").splitlines()
    files = list(cache_dir.glob("*.html"))
    found = 0
    for url in urls:
        cache_key = f"public\0{url}"
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        if (cache_dir / f"{digest}.html").exists():
            found += 1
    return len(urls), len(files), found


def main() -> int:
    url_count, file_count, found = count_public_cache_hits()
    print(f"URLs count: {url_count}, HTML files count: {file_count}")
    print(f"Found {found} files matching public hashing out of {url_count} URLs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
