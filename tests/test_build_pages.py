import json
from pathlib import Path

from build_pages import build_pages


def test_build_pages_renders_site_from_release_data(tmp_path: Path):
    releases = [
        {
            "movie_title": "The Matrix",
            "release_date": "1999",
            "fel_evidence": {
                "source_url": "https://example.test/matrix",
                "quote": "The Matrix is Profile 7 FEL",
                "evidence_type": "fixture",
            },
        }
    ]
    data_path = tmp_path / "releases.json"
    data_path.write_text(json.dumps(releases), encoding="utf-8")

    count = build_pages(
        data_path=data_path,
        output_dir=tmp_path / "dist",
        poster_src=tmp_path / "posters",
    )

    assert count == 1
    html = (tmp_path / "dist" / "index.html").read_text(encoding="utf-8")
    assert "The Matrix" in html
    assert (tmp_path / "dist" / "releases.json").exists()
