from google_sheets import parse_always_fel_sheet
from list_sources import (
    letterboxd_page_count,
    parse_discourse_list,
    parse_github_md_list,
    parse_letterboxd_list,
)


def test_parse_github_md_list_extracts_numbered_entries():
    markdown = "# FEL\n1. 10 Cloverfield Lane [2016]\n2. 1917 [2019]\nnot a list line\n"
    releases = parse_github_md_list(markdown, "https://github.com/iammarxg/FEL")

    assert [(r.movie_title, r.release_date) for r in releases] == [
        ("10 Cloverfield Lane", "2016"),
        ("1917", "2019"),
    ]
    assert releases[0].source_label == "github"
    assert releases[0].fel_evidence.evidence_type == "github-list"


def test_parse_github_md_list_skips_mel_and_prose():
    markdown = (
        "1. Real Movie [2020]\n"
        "2. Some MEL Movie [2021]\n"
        "I watched Heat (1995) yesterday\n"
    )
    titles = [r.movie_title for r in parse_github_md_list(markdown, "u")]

    assert titles == ["Real Movie"]


def test_parse_discourse_list_strips_html_tags():
    html = "<ol><li>The Matrix [1999]</li>\n<li>Heat (1995)</li></ol>"
    releases = parse_discourse_list(html, "https://web.archive.org/x")

    assert [r.movie_title for r in releases] == ["The Matrix", "Heat"]
    assert releases[0].source_label == "discourse"


def test_parse_discourse_list_strips_bare_archived_ordinals():
    html = """
    <p>281 Nobody (2021)</p>
    <p>354 Scream (2022)</p>
    <p>10 Cloverfield Lane (2016)</p>
    <p>1917 (2019)</p>
    """
    releases = parse_discourse_list(html, "https://web.archive.org/x")

    assert [r.movie_title for r in releases] == [
        "Nobody",
        "Scream",
        "10 Cloverfield Lane",
        "1917",
    ]


def test_parse_letterboxd_list_reads_item_display_names():
    html = (
        '<li data-item-full-display-name="The Matrix (1999)"></li>'
        '<li data-item-full-display-name="Dune (2021)"></li>'
    )
    releases = parse_letterboxd_list(html, "https://letterboxd.com/list/")

    assert [(r.movie_title, r.release_date) for r in releases] == [
        ("The Matrix", "1999"),
        ("Dune", "2021"),
    ]
    assert releases[0].source_label == "letterboxd"


def test_letterboxd_page_count_reads_highest_page_link():
    html = '<a href="/list/x/page/2/">2</a><a href="/list/x/page/7/">7</a>'

    assert letterboxd_page_count(html) == 7
    assert letterboxd_page_count("<html></html>") == 1


def test_parse_always_fel_sheet_takes_every_titled_row():
    csv_text = "Film,Notes\nBack to the Future,ok\nHeat 1995,fine\n"
    releases = parse_always_fel_sheet(csv_text, "https://docs.google.com/sheet")

    assert [r.movie_title for r in releases] == ["Back to the Future", "Heat"]
    assert releases[0].fel_evidence.evidence_type == "google-sheet-list"
    assert releases[0].source_label == "google-sheet"
