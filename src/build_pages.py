from __future__ import annotations

import json
from pathlib import Path

from dashboard import build_dashboard
from models import release_from_dict


def build_pages(
    data_path: Path | str = "data/releases.json",
    output_dir: Path | str = "dist",
    poster_src: Path | str = "data/posters",
) -> int:
    """Render the GitHub Pages site from the committed release data.

    No scraping, AI extraction, or network access: this only re-renders the
    dashboard so changes pushed to main reach the live site quickly. Daily
    data refreshes stay owned by the "Scrape and Deploy Pages" workflow.
    """
    releases = [
        release_from_dict(item)
        for item in json.loads(Path(data_path).read_text(encoding="utf-8"))
    ]
    build_dashboard(releases, output_dir=output_dir, poster_src=poster_src)
    return len(releases)


if __name__ == "__main__":
    count = build_pages()
    print(f"built dist/ from {count} releases")
