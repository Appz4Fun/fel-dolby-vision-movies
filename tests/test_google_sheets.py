from google_sheets import (
    google_sheet_csv_url,
    parse_google_sheet_releases,
)
import pytest


def test_google_sheet_csv_url_preserves_gid_from_edit_url():
    url = (
        "https://docs.google.com/spreadsheets/d/15i0a84uiBtWiHZ5CXZZ7wygLFXwYOd84/"
        "edit?gid=828864432#gid=828864432"
    )

    assert google_sheet_csv_url(url) == (
        "https://docs.google.com/spreadsheets/d/15i0a84uiBtWiHZ5CXZZ7wygLFXwYOd84/"
        "gviz/tq?tqx=out:csv&gid=828864432"
    )


def test_google_sheet_csv_url_uses_fragment_gid_and_rejects_non_sheet_urls():
    url = "https://docs.google.com/spreadsheets/d/sheet-id/edit#gid=12345"

    assert google_sheet_csv_url(url).endswith("/gviz/tq?tqx=out:csv&gid=12345")
    with pytest.raises(ValueError, match="not a Google Sheets URL"):
        google_sheet_csv_url("https://docs.google.com/spreadsheets/u/0/")


def test_parse_google_sheet_releases_detects_fel_rows_from_header_block():
    csv_text = """List of movies that can't be converted to profile 8 Title,DV Source,Notes
A.Quiet.Place.2018,BD FEL,Only FEL devices
Alien.1979,MEL,Not enough
1917 (2019),BD-FEL,
"""

    releases = parse_google_sheet_releases(csv_text, "https://docs.example.test/sheet")

    assert [release.movie_title for release in releases] == [
        "A Quiet Place",
        "1917",
    ]
    assert [release.release_date for release in releases] == ["2018", "2019"]
    assert [release.fel_evidence.evidence_type for release in releases] == [
        "google-sheet-row",
        "google-sheet-row",
    ]
    assert "BD FEL" in releases[0].fel_evidence.quote


def test_parse_google_sheet_releases_detects_multiple_table_blocks():
    csv_text = """Ignored,DV Source
No Title,FEL
Title,Layer
The Matrix 1999,P7 FEL
The Matrix Reloaded 2003,P7 MEL
Film,Dolby Vision
Bridesmaids.2011,BD FEL
"""

    releases = parse_google_sheet_releases(csv_text, "https://docs.example.test/sheet")

    assert [release.movie_title for release in releases] == [
        "The Matrix",
        "Bridesmaids",
    ]
    assert [release.release_date for release in releases] == ["1999", "2011"]


def test_parse_google_sheet_releases_extracts_embedded_year_before_sheet_tags():
    csv_text = """Movie Name,DV Source
Jurassic.Park.1993 NEW 2025,BD FEL
Now.You.See.Me.2013 FRA,BD-FEL
RESERVOIR DOGS - 1992 ITA,BD FEL
Source Code 2011 US BD,BD FEL
Star.Trek.First.Contact,BD-FEL
M3GAN 2.0 2025,BD FEL
"""

    releases = parse_google_sheet_releases(csv_text, "https://docs.example.test/sheet")

    assert [release.movie_title for release in releases] == [
        "Jurassic Park",
        "Now You See Me",
        "RESERVOIR DOGS",
        "Source Code",
        "Star Trek First Contact",
        "M3GAN 2.0",
    ]
    assert [release.release_date for release in releases] == [
        "1993",
        "2013",
        "1992",
        "2011",
        "Unknown",
        "2025",
    ]


def test_parse_google_sheet_releases_rejects_collections_and_trailing_year_dots():
    csv_text = """Movie Name,DV Source,Notes
Godfather Trilogy,BD-FEL,Collection row is not one specific release
Rango.2011.,BD FEL,Specific disc row
Nobody.2.2025.,BD FEL,Specific disc row
"""

    releases = parse_google_sheet_releases(csv_text, "https://docs.example.test/sheet")

    assert [(release.movie_title, release.release_date) for release in releases] == [
        ("Rango", "2011"),
        ("Nobody 2", "2025"),
    ]


def test_parse_google_sheet_releases_requires_fel_token_in_source_column():
    csv_text = """Movie Name,DV Source,Notes
Ambiguous Movie,Profile 7,No FEL token
False Positive,Felix release group,Not a DV source
Specific Movie,BD FEL,Good
"""

    releases = parse_google_sheet_releases(csv_text, "https://docs.example.test/sheet")

    assert [release.movie_title for release in releases] == ["Specific Movie"]


def test_parse_google_sheet_releases_ignores_rows_without_usable_title_or_source():
    csv_text = """prelude,BD FEL
Movie Name,DV Source,Notes
,BD FEL,blank title
Too Short
Valid Movie 2020,BD FEL,usable row
"""

    releases = parse_google_sheet_releases(csv_text, "https://docs.example.test/sheet")

    assert [(release.movie_title, release.release_date) for release in releases] == [
        ("Valid Movie", "2020")
    ]


def test_parse_google_sheet_releases_labels_rows_as_google_sheet():
    # Regression: row-based sheet parsing built FelEvidence with
    # evidence_type="google-sheet-row" but never set source_label, so it
    # silently defaulted to "Unknown" instead of "google-sheet" -- unlike the
    # sibling parse_always_fel_sheet(), which already labels its releases
    # correctly. A release whose own label is "Unknown" lets merge_releases'
    # provenance fallback pull in a *different* source's label later,
    # producing a source_label that names the wrong provider for this URL.
    csv_text = """Title,DV Source
Specific Movie 2020,BD FEL
"""

    releases = parse_google_sheet_releases(csv_text, "https://docs.example.test/sheet")

    assert releases[0].source_label == "google-sheet"
