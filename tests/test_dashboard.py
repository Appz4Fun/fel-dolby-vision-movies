import json
from pathlib import Path

from dashboard import build_dashboard
from models import FelEvidence, FelRelease


def release(title: str, date: str, group: str = "GROUP") -> FelRelease:
    return FelRelease(
        movie_title=title,
        release_date=date,
        audio_formats=["TrueHD Atmos"],
        english_audio="Yes",
        fel_evidence=FelEvidence(
            source_url=f"https://example.test/{title}",
            quote=f"{title} is Profile 7 FEL",
            evidence_type="fixture",
        ),
        additional_characteristics={"release_group": group},
    )


def test_dashboard_writes_index_and_copied_json(tmp_path: Path):
    matrix = release("The Matrix", "1999")

    build_dashboard([matrix], output_dir=tmp_path / "dist")

    html = (tmp_path / "dist/index.html").read_text(encoding="utf-8")
    assert "The Matrix" in html
    assert "TrueHD Atmos" in html
    assert "Filter" in html
    assert "poster-placeholder" in html
    assert (tmp_path / "dist/releases.json").exists()


def test_dashboard_sorts_newest_first_and_omits_groups_from_html(tmp_path: Path):
    build_dashboard(
        [
            release("Older", "2020", group="OLDR"),
            release("Unknown Date", "Unknown", group="UNKN"),
            release("Newer", "2026-05-01", group="NEWR"),
        ],
        output_dir=tmp_path / "dist",
    )

    html = (tmp_path / "dist/index.html").read_text(encoding="utf-8")
    assert html.index("Newer") < html.index("Older") < html.index("Unknown Date")
    assert "NEWR" not in html
    assert "OLDR" not in html
    assert "UNKN" not in html

    data = json.loads((tmp_path / "dist/releases.json").read_text(encoding="utf-8"))
    assert [item["movie_title"] for item in data] == [
        "Newer",
        "Older",
        "Unknown Date",
    ]
