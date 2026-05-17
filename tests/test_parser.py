from fel_dolby_vision_movies.parser import parse_fel_releases


def test_parses_table_row_with_direct_fel_correlation():
    html = """
    <table>
      <tr><th>Title</th><th>DV</th><th>Audio</th></tr>
      <tr><td>The Matrix</td><td>Profile 7 FEL</td><td>English TrueHD Atmos</td></tr>
    </table>
    """
    releases = parse_fel_releases(html, "https://example.test/thread")
    assert [release.movie_title for release in releases] == ["The Matrix"]
    assert releases[0].audio_formats == ["TrueHD Atmos"]
    assert releases[0].english_audio == "Yes"


def test_rejects_generic_fel_chatter_without_title_binding():
    html = """
    <p>I love FEL when discs include it.</p>
    <ul><li>The Matrix</li><li>Alien</li><li>Blade Runner</li></ul>
    """
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_parses_direct_sentence_with_title_and_profile_7_fel():
    html = "<p>Alien (1979) is confirmed as Dolby Vision Profile 7 FEL with DTS-HD MA.</p>"
    releases = parse_fel_releases(html, "https://example.test/thread")
    assert releases[0].movie_title == "Alien"
    assert releases[0].release_date == "1979"
    assert releases[0].audio_formats == ["DTS-HD MA"]


def test_rejects_profile_7_without_fel():
    html = "<p>Movie A has Dolby Vision Profile 7 but this post does not identify FEL.</p>"
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_rejects_mel_even_when_fel_appears_elsewhere():
    html = "<p>Movie A is Profile 7 MEL. Another user asked about FEL-capable players.</p>"
    assert parse_fel_releases(html, "https://example.test/thread") == []
