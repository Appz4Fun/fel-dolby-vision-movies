from reddit_source import parse_reddit_releases


REDDIT_HTML = """
<html><body>
<div class="usertext-body">
<div class="md">
<p>List of P7-FEL films:</p>
<p>Nosferatu [2024]</p>
<p>The Northman (2022)</p>
<p>Old MEL Movie [2019]</p>
</div>
</div>
<div class="usertext-body">
<div class="md"><p>You forgot Sicario [2015]</p></div>
</div>
</body></html>
"""


def test_parse_reddit_releases_extracts_list_and_comment_titles():
    releases = parse_reddit_releases(REDDIT_HTML, "https://reddit.test/fel")

    titles = [r.movie_title for r in releases]
    assert "Nosferatu" in titles
    assert "The Northman" in titles
    assert "Sicario" in titles
    assert "Old MEL Movie" not in titles

    nosferatu = next(r for r in releases if r.movie_title == "Nosferatu")
    assert nosferatu.release_date == "2024"
    assert nosferatu.source_url == "https://reddit.test/fel"
    assert nosferatu.fel_evidence.evidence_type == "reddit-list"


def test_parse_reddit_releases_dedupes_repeated_titles():
    html = (
        '<div class="usertext-body"><div class="md">'
        "<p>Dune [2021]</p><p>Dune [2021]</p>"
        "</div></div>"
    )
    releases = parse_reddit_releases(html, "https://reddit.test/fel")
    assert [r.movie_title for r in releases] == ["Dune"]


def test_parse_reddit_releases_keeps_numeric_titles():
    html = (
        '<div class="usertext-body"><div class="md">'
        "<p>1917 [2019]</p><p>10 Cloverfield Lane [2016]</p>"
        "</div></div>"
    )
    releases = parse_reddit_releases(html, "https://reddit.test/fel")
    assert [r.movie_title for r in releases] == ["1917", "10 Cloverfield Lane"]


def test_parse_reddit_releases_filters_mel_but_keeps_mel_substring_titles():
    html = (
        '<div class="usertext-body"><div class="md">'
        "<p>This disc is MEL only [2010]</p>"
        "<p>Melancholia [2011]</p>"
        "<p>Dune [2021]<br>Heat [1995]</p>"
        "</div></div>"
    )
    releases = parse_reddit_releases(html, "https://reddit.test/fel")
    titles = [r.movie_title for r in releases]
    assert "Melancholia" in titles
    assert "Dune" in titles
    assert "Heat" in titles
    assert not any("MEL only" in t for t in titles)


def test_parse_reddit_releases_strips_varied_comment_prefixes():
    html = (
        '<div class="usertext-body"><div class="md">'
        "<p>Missing: Tenet [2020]</p>"
        "<p>Also Arrival [2016]</p>"
        "</div></div>"
    )
    releases = parse_reddit_releases(html, "https://reddit.test/fel")
    assert [r.movie_title for r in releases] == ["Tenet", "Arrival"]


def test_parse_reddit_releases_splits_comma_joined_multi_title_line():
    html = (
        '<div class="usertext-body"><div class="md">'
        "<p>Walking Tall (2004), Tron: Ares (2025)</p>"
        "</div></div>"
    )
    releases = parse_reddit_releases(html, "https://reddit.test/fel")
    assert [(r.movie_title, r.release_date) for r in releases] == [
        ("Walking Tall", "2004"),
        ("Tron: Ares", "2025"),
    ]


def test_parse_reddit_releases_rejects_line_with_trailing_non_title_text():
    html = (
        '<div class="usertext-body"><div class="md">'
        "<p>Great movie (2024), really loved the audio</p>"
        "</div></div>"
    )
    releases = parse_reddit_releases(html, "https://reddit.test/fel")
    assert releases == []
