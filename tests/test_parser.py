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


def test_rejects_status_cell_when_row_title_is_prefix_of_longer_title():
    examples = (
        ("It", "It Follows: Profile 7 FEL"),
        ("Alien", "Alien 3: Profile 7 FEL"),
        ("The Matrix", "The Matrix Reloaded: Profile 7 FEL"),
    )
    for row_title, evidence in examples:
        html = f"""
        <table>
          <tr><th>Title</th><th>DV</th></tr>
          <tr><td>{row_title}</td><td>{evidence}</td></tr>
        </table>
        """
        assert parse_fel_releases(html, "https://example.test/thread") == []


def test_rejects_title_specific_cell_when_row_title_is_prefix_of_longer_title():
    examples = (
        ("It", "It Follows: Profile 7 FEL"),
        ("Alien", "Alien 3: Profile 7 FEL"),
        ("The Matrix", "The Matrix Reloaded: Profile 7 FEL"),
    )
    for row_title, evidence in examples:
        html = f"""
        <table>
          <tr><th>Title</th><th>Evidence</th></tr>
          <tr><td>{row_title}</td><td>{evidence}</td></tr>
        </table>
        """
        assert parse_fel_releases(html, "https://example.test/thread") == []


def test_accepts_title_specific_cell_when_same_short_title_is_explicit():
    html = """
    <table>
      <tr><th>Title</th><th>Notes</th></tr>
      <tr><td>It</td><td>It is Profile 7 FEL.</td></tr>
    </table>
    """
    releases = parse_fel_releases(html, "https://example.test/thread")
    assert [release.movie_title for release in releases] == ["It"]


def test_rejects_unrecognized_header_when_row_title_is_prefix_of_longer_title():
    examples = (
        ("It", "Details", "It Follows: Profile 7 FEL"),
        ("Alien", "Info", "Alien 3: Profile 7 FEL"),
        ("The Matrix", "Release", "The Matrix Reloaded: Profile 7 FEL"),
    )
    for row_title, header, evidence in examples:
        html = f"""
        <table>
          <tr><th>Title</th><th>{header}</th></tr>
          <tr><td>{row_title}</td><td>{evidence}</td></tr>
        </table>
        """
        assert parse_fel_releases(html, "https://example.test/thread") == []


def test_accepts_unrecognized_header_when_same_title_is_explicit():
    html = """
    <table>
      <tr><th>Title</th><th>Details</th></tr>
      <tr><td>The Matrix</td><td>The Matrix: Profile 7 FEL</td></tr>
    </table>
    """
    releases = parse_fel_releases(html, "https://example.test/thread")
    assert [release.movie_title for release in releases] == ["The Matrix"]


def test_rejects_title_specific_suffix_binding_to_longer_title():
    html = """
    <table>
      <tr><th>Title</th><th>Evidence</th></tr>
      <tr><td>It</td><td>Profile 7 FEL confirmed for It Follows.</td></tr>
    </table>
    """
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_accepts_title_specific_suffix_binding_to_same_title():
    html = """
    <table>
      <tr><th>Title</th><th>Evidence</th></tr>
      <tr><td>It</td><td>Profile 7 FEL confirmed for It.</td></tr>
    </table>
    """
    releases = parse_fel_releases(html, "https://example.test/thread")
    assert [release.movie_title for release in releases] == ["It"]


def test_accepts_suffix_binding_to_same_title_before_audio_metadata():
    html = """
    <table>
      <tr><th>Title</th><th>Evidence</th></tr>
      <tr><td>It</td><td>Profile 7 FEL confirmed for It with TrueHD.</td></tr>
    </table>
    """
    releases = parse_fel_releases(html, "https://example.test/thread")
    assert [release.movie_title for release in releases] == ["It"]


def test_rejects_suffix_binding_to_longer_title_before_audio_metadata():
    html = """
    <table>
      <tr><th>Title</th><th>Evidence</th></tr>
      <tr><td>It</td><td>Profile 7 FEL confirmed for It Follows with TrueHD.</td></tr>
    </table>
    """
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_accepts_proof_metadata_without_title_binding():
    html = """
    <table>
      <tr><th>Title</th><th>Evidence</th></tr>
      <tr><td>Alien</td><td>Profile 7 FEL confirmed by disc scan.</td></tr>
    </table>
    """
    releases = parse_fel_releases(html, "https://example.test/thread")
    assert [release.movie_title for release in releases] == ["Alien"]


def test_rejects_unrecognized_header_suffix_binding_to_longer_title():
    html = """
    <table>
      <tr><th>Title</th><th>Details</th></tr>
      <tr><td>Alien</td><td>Profile 7 FEL confirmed for Alien 3.</td></tr>
    </table>
    """
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_rejects_status_cell_suffix_binding_to_longer_title():
    html = """
    <table>
      <tr><th>Title</th><th>DV</th></tr>
      <tr><td>The Matrix</td><td>Profile 7 FEL confirmed for The Matrix Reloaded.</td></tr>
    </table>
    """
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_rejects_headerless_cell_suffix_binding_to_longer_title():
    html = """
    <table>
      <tr><td>The Matrix</td><td>Profile 7 FEL confirmed for The Matrix Reloaded.</td></tr>
    </table>
    """
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_accepts_non_title_status_prefixes_before_fel_evidence():
    for evidence in (
        "Confirmed Profile 7 FEL",
        "Yes - Profile 7 FEL",
        "MediaInfo confirms Profile 7 FEL",
    ):
        html = f"""
        <table>
          <tr><th>Title</th><th>DV</th></tr>
          <tr><td>The Matrix</td><td>{evidence}</td></tr>
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


def test_rejects_sentence_with_demonstrative_source_prose_prefix():
    html = (
        "<p>This spreadsheet says Alien is confirmed as Dolby Vision "
        "Profile 7 FEL.</p>"
    )
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_rejects_sentence_with_attributed_source_prefix():
    html = (
        "<p>According to the spreadsheet, Alien is confirmed as Dolby Vision "
        "Profile 7 FEL.</p>"
    )
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_rejects_sentence_with_list_entry_prefix():
    html = "<p>List entry: Alien is confirmed as Dolby Vision Profile 7 FEL.</p>"
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_rejects_sentence_with_qualified_source_prose_prefix():
    html = (
        "<p>The Blu-ray.com post says Alien is confirmed as Dolby Vision "
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
