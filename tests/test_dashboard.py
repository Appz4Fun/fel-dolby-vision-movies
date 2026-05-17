from pathlib import Path

from fel_dolby_vision_movies.dashboard import build_dashboard
from fel_dolby_vision_movies.models import FelEvidence, FelRelease


def test_dashboard_writes_index_and_copied_json(tmp_path: Path):
    release = FelRelease(
        movie_title="The Matrix",
        release_date="1999",
        audio_formats=["TrueHD Atmos"],
        english_audio="Yes",
        fel_evidence=FelEvidence(
            source_url="https://example.test/thread",
            quote="The Matrix is Profile 7 FEL",
            evidence_type="fixture",
        ),
    )

    build_dashboard([release], output_dir=tmp_path / "dist")

    html = (tmp_path / "dist/index.html").read_text(encoding="utf-8")
    assert "The Matrix" in html
    assert "TrueHD Atmos" in html
    assert "Filter" in html
    assert "poster-placeholder" in html
    assert (tmp_path / "dist/releases.json").exists()
