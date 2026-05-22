import json
from pathlib import Path

import httpx

import artifacts
import enrich
from bluray import BlurayDetails, StaticBlurayResolver
from fel_ingest import parse_fel_txt
from merge import canonical_key, dedupe_releases
from tmdb import StaticTmdbResolver


def test_full_pipeline_ingest_merge_enrich_publish(tmp_path: Path):
    releases = dedupe_releases(
        parse_fel_txt("Nosferatu,2024,https://reddit.test/list\n"), canonical_key
    )
    resolver = StaticTmdbResolver(
        {
            ("Nosferatu", "2024"): {
                "tmdb_id": "426063",
                "title": "Nosferatu",
                "year": "2024",
                "imdb_id": "tt5040012",
            }
        }
    )
    bluray = StaticBlurayResolver(
        {
            ("Nosferatu", "2024"): BlurayDetails(
                url="https://www.blu-ray.com/movies/Nosferatu-4K-Blu-ray/1/",
                bluray_release_date="2025-02-18",
                audio_formats=["Dolby TrueHD/Atmos 7.1"],
                audio_languages=["English"],
                hdr_formats=["Dolby Vision", "HDR10"],
            )
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3/movie/426063":
            return httpx.Response(
                200,
                json={
                    "poster_path": "/p.jpg",
                    "release_date": "2024-12-25",
                    "production_companies": [{"name": "Focus Features"}],
                },
            )
        return httpx.Response(200, content=b"jpeg")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    enrich.enrich_releases(
        releases,
        resolver,
        client=client,
        api_key="x",
        poster_dir=tmp_path / "data" / "posters",
        bluray_resolver=bluray,
    )
    client.close()

    artifacts.publish_outputs(releases, output_dir=tmp_path)

    data = json.loads((tmp_path / "data/releases.json").read_text(encoding="utf-8"))
    assert len(data) == 1
    entry = data[0]
    assert entry["tmdb_id"] == "426063"
    assert entry["hdr_formats"] == ["Dolby Vision", "HDR10"]
    assert entry["bluray_url"].endswith("/1/")

    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "Nosferatu" in readme
    assert "Dolby Vision, HDR10" in readme

    index = (tmp_path / "dist/index.html").read_text(encoding="utf-8")
    assert "1 confirmed Profile 7 FEL releases" in index
    assert 'id="view-list"' in index
