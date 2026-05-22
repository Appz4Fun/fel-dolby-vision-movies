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


def _enriched_release() -> FelRelease:
    return FelRelease(
        movie_title="Nosferatu",
        release_date="2024",
        fel_evidence=FelEvidence(
            source_url="https://reddit.test/fel",
            quote="Nosferatu FEL",
            evidence_type="reddit-list",
        ),
        tmdb_id="426063",
        poster_path="data/posters/426063.jpg",
        release_url="https://www.themoviedb.org/movie/426063",
    )


def test_dashboard_renders_poster_image_and_both_links(tmp_path: Path):
    poster_src = tmp_path / "data" / "posters"
    poster_src.mkdir(parents=True)
    (poster_src / "426063.jpg").write_bytes(b"jpeg")

    build_dashboard(
        [_enriched_release()],
        output_dir=tmp_path / "dist",
        poster_src=poster_src,
    )

    html = (tmp_path / "dist" / "index.html").read_text(encoding="utf-8")
    assert 'src="posters/426063.jpg"' in html
    assert 'href="https://reddit.test/fel"' in html
    assert 'href="https://www.themoviedb.org/movie/426063"' in html
    assert (tmp_path / "dist" / "posters" / "426063.jpg").exists()


def test_dashboard_has_total_count_and_sortable_list(tmp_path: Path):
    build_dashboard(
        [_enriched_release(), _enriched_release()],
        output_dir=tmp_path / "dist",
        poster_src=tmp_path / "data" / "posters",
    )

    html = (tmp_path / "dist" / "index.html").read_text(encoding="utf-8")
    assert "2 confirmed Profile 7 FEL releases" in html
    assert 'id="view-cards"' in html
    assert 'id="view-list"' in html
    assert "<table" in html and "sortTable" in html


def test_dashboard_defaults_to_responsive_list_without_year_or_fel_columns(
    tmp_path: Path,
):
    build_dashboard(
        [_enriched_release()],
        output_dir=tmp_path / "dist",
        poster_src=tmp_path / "data" / "posters",
    )

    html = (tmp_path / "dist" / "index.html").read_text(encoding="utf-8")
    assert 'id="view-list" class="active"' in html
    assert 'id="view-cards" class="active"' not in html
    assert '["Poster"' in html
    assert "posterThumb(r)" in html
    assert '["Release Date"' in html
    assert '["Blu-ray Date"' in html
    assert (
        html.index('["Poster"')
        < html.index('["Movie"')
        < html.index('["Release Date"')
        < html.index('["Blu-ray Date"')
    )
    assert '["Year"' not in html
    assert '["FEL"' not in html
    assert '["BR Link"' in html
    assert '["Src Link"' in html
    assert '["TMDB"' in html
    assert "@media (max-width:" in html


def test_dashboard_escapes_release_text_and_split_link_labels(tmp_path: Path):
    item = FelRelease(
        movie_title='Alien <script>alert("x")</script>',
        release_date="1979",
        fel_evidence=FelEvidence(
            source_url="https://source.test/?q=<unsafe>",
            quote="Alien FEL",
            evidence_type="fixture",
        ),
        bluray_url="https://blu-ray.test/Alien",
        release_url="https://tmdb.test/movie/1",
    )

    build_dashboard([item], output_dir=tmp_path / "dist")

    html = (tmp_path / "dist/index.html").read_text(encoding="utf-8")
    assert "<script>alert" not in html
    assert "Alien &lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in html
    assert "\\u003cunsafe>" in html
    assert 'href="https://source.test/?q=<unsafe>"' not in html
    assert (
        '<a href="https://source.test/?q=&lt;unsafe&gt;" rel="noreferrer">Src</a>'
    ) in html
    assert '["BR Link"' in html
    assert '["Src Link"' in html
    assert '["TMDB"' in html
    assert '<a href="https://blu-ray.test/Alien" rel="noreferrer">BR</a>' in html
    assert '<a href="https://tmdb.test/movie/1" rel="noreferrer">TMDB</a>' in html
