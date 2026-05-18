from __future__ import annotations

import csv
import hashlib
from pathlib import Path


def cache_path_for_url(cache_dir: Path, url: str) -> Path:
    cache_key = f"public\0{url}"
    digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.html"


def append_cached_urls_to_fel(
    fel_path: Path = Path("FEL.txt"),
    expanded_urls_path: Path = Path(".cache/ai_expanded_urls.txt"),
    cache_dir: Path = Path(".cache/html"),
) -> int:
    movies: list[dict[str, object]] = []
    with fel_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if len(row) >= 2:
                movies.append(
                    {"title": row[0].strip(), "year": row[1].strip(), "urls": []}
                )

    for url in expanded_urls_path.read_text(encoding="utf-8").splitlines():
        url = url.strip()
        if not url:
            continue
        cache_file = cache_path_for_url(cache_dir, url)
        if not cache_file.exists():
            continue
        content = cache_file.read_text(encoding="utf-8", errors="ignore")
        for movie in movies:
            title = str(movie["title"])
            year = str(movie["year"])
            p1 = f"{title} [{year}]"
            p2 = f"{title} ({year})"
            urls = movie["urls"]
            if not isinstance(urls, list):
                continue
            if len(title) > 4 and title in content:
                urls.append(url)
            elif p1 in content or p2 in content:
                urls.append(url)

    with fel_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        for movie in movies:
            urls = movie["urls"]
            url_str = "|".join(sorted(set(urls if isinstance(urls, list) else [])))
            writer.writerow([movie["title"], movie["year"], url_str])
    return len(movies)


def main() -> int:
    append_cached_urls_to_fel()
    print("Finished appending URLs to FEL.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
