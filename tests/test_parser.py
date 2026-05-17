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


def test_rejects_table_row_when_fel_mentions_different_title():
    html = """
    <table>
      <tr><th>Title</th><th>DV</th><th>Notes</th></tr>
      <tr><td>The Matrix</td><td>Profile 7</td><td>Other title is FEL.</td></tr>
    </table>
    """
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_rejects_status_cell_when_fel_names_different_title():
    html = """
    <table>
      <tr><th>Title</th><th>DV</th></tr>
      <tr><td>The Matrix</td><td>Alien is Profile 7 FEL.</td></tr>
    </table>
    """
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_accepts_status_cell_when_fel_names_same_title():
    html = """
    <table>
      <tr><th>Title</th><th>DV</th></tr>
      <tr><td>The Matrix</td><td>The Matrix is Profile 7 FEL.</td></tr>
    </table>
    """
    releases = parse_fel_releases(html, "https://example.test/thread")
    assert [release.movie_title for release in releases] == ["The Matrix"]


def test_rejects_short_title_match_inside_unrelated_word():
    html = """
    <table>
      <tr><th>Title</th><th>Notes</th></tr>
      <tr><td>It</td><td>This title is Profile 7 FEL.</td></tr>
    </table>
    """
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_accepts_short_title_when_mentioned_as_phrase():
    html = """
    <table>
      <tr><th>Title</th><th>Notes</th></tr>
      <tr><td>It</td><td>It is Profile 7 FEL.</td></tr>
    </table>
    """
    releases = parse_fel_releases(html, "https://example.test/thread")
    assert [release.movie_title for release in releases] == ["It"]


def test_rejects_status_cell_when_different_title_prefix_uses_separator():
    for evidence in (
        "Alien - Profile 7 FEL",
        "Alien: Profile 7 FEL",
        "Alien Profile 7 FEL",
    ):
        html = f"""
        <table>
          <tr><th>Title</th><th>DV</th></tr>
          <tr><td>The Matrix</td><td>{evidence}</td></tr>
        </table>
        """
        assert parse_fel_releases(html, "https://example.test/thread") == []


def test_accepts_status_cell_when_same_title_prefix_uses_separator():
    html = """
    <table>
      <tr><th>Title</th><th>DV</th></tr>
      <tr><td>The Matrix</td><td>The Matrix: Profile 7 FEL</td></tr>
    </table>
    """
    releases = parse_fel_releases(html, "https://example.test/thread")
    assert [release.movie_title for release in releases] == ["The Matrix"]


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


def test_accepts_title_containing_mel_token_as_part_of_word():
    html = "<p>Amelie is confirmed as Dolby Vision Profile 7 FEL with TrueHD.</p>"
    releases = parse_fel_releases(html, "https://example.test/thread")
    assert [release.movie_title for release in releases] == ["Amelie"]


def test_rejects_sentence_with_generic_dolby_vision_fel_without_profile_7():
    html = "<p>Movie A is confirmed as Dolby Vision FEL with TrueHD.</p>"
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_strips_known_prose_prefix_from_sentence_title():
    html = "<p>The disc for Alien (1979) is confirmed as Dolby Vision Profile 7 FEL.</p>"
    releases = parse_fel_releases(html, "https://example.test/thread")
    assert [release.movie_title for release in releases] == ["Alien"]


def test_rejects_sentence_with_ambiguous_prose_prefix():
    html = (
        "<p>The spreadsheet says Alien is confirmed as Dolby Vision "
        "Profile 7 FEL.</p>"
    )
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_rejects_profile_7_without_fel():
    html = "<p>Movie A has Dolby Vision Profile 7 but this post does not identify FEL.</p>"
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_rejects_mel_even_when_fel_appears_elsewhere():
    html = "<p>Movie A is Profile 7 MEL. Another user asked about FEL-capable players.</p>"
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_accepts_fel_when_not_mel_clarifies_layer_type():
    html = "<p>Alien is confirmed as Dolby Vision Profile 7 FEL, not MEL.</p>"
    releases = parse_fel_releases(html, "https://example.test/thread")
    assert [release.movie_title for release in releases] == ["Alien"]


def test_rejects_fel_with_trailing_denial():
    html = """
    <table>
      <tr><th>Title</th><th>DV</th></tr>
      <tr><td>Alien</td><td>Profile 7 FEL: No.</td></tr>
    </table>
    """
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_preserves_distinct_same_title_evidence_on_one_source_page():
    html = """
    <table>
      <tr><th>Title</th><th>DV</th></tr>
      <tr><td>Alien</td><td>Profile 7 FEL confirmed by disc scan.</td></tr>
      <tr><td>Alien</td><td>Profile 7 FEL confirmed by MediaInfo.</td></tr>
      <tr><td>Alien</td><td>Profile 7 FEL confirmed by disc scan.</td></tr>
    </table>
    """
    releases = parse_fel_releases(html, "https://example.test/thread")
    assert [release.fel_evidence.quote for release in releases] == [
        "Alien Profile 7 FEL confirmed by disc scan.",
        "Alien Profile 7 FEL confirmed by MediaInfo.",
    ]
