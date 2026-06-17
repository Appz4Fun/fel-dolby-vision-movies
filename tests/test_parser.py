from parser import parse_fel_releases


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


def test_parses_only_verified_fel_release_from_multi_title_forum_table():
    html = """
    <article class="post">
      <p>Latest UHD confirmations copied from the forum thread.</p>
      <table>
        <tr><th>Movie</th><th>Video</th><th>Audio</th></tr>
        <tr>
          <td>Alien</td>
          <td>Dolby Vision Profile 7 FEL confirmed by disc scan.</td>
          <td>English Dolby TrueHD 7.1 Atmos</td>
        </tr>
        <tr>
          <td>The Matrix</td>
          <td>Dolby Vision Profile 7 REMUX, enhancement layer not identified.</td>
          <td>English DTS-HD MA 5.1</td>
        </tr>
      </table>
    </article>
    """
    releases = parse_fel_releases(html, "https://example.test/forum/post-1")
    assert [release.movie_title for release in releases] == ["Alien"]
    assert releases[0].audio_formats == ["TrueHD Atmos"]
    assert releases[0].english_audio == "Yes"


def test_parses_recoverable_malformed_forum_table_without_promoting_generic_dv():
    html = """
    <div class="postbody"><blockquote>
      <table class="bbcode">
        <tr><th>Title</th><th>DV proof</th><th>Sound</th></tr>
        <tr>
          <td>Blade Runner</td>
          <td>Profile 7 FEL confirmed via MediaInfo</td>
          <td>DTS:X</td>
        </tr>
        <tr><td>Heat</td><td>Dolby Vision REMUX</td><td>TrueHD</td></tr>
      </table
      <p>Footer text from a truncated forum scrape.
    </div>
    """
    releases = parse_fel_releases(html, "https://example.test/forum/broken")
    assert [release.movie_title for release in releases] == ["Blade Runner"]
    assert releases[0].audio_formats == ["DTS:X"]
    assert releases[0].fel_evidence.evidence_type == "table-row"


def test_parses_table_when_title_column_is_not_first():
    html = """
    <table>
      <tr><th>Group</th><th>Title</th><th>DV</th></tr>
      <tr><td>HDT</td><td>Alien</td><td>Profile 7 FEL</td></tr>
    </table>
    """
    releases = parse_fel_releases(html, "https://example.test/thread")
    assert [release.movie_title for release in releases] == ["Alien"]


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


def test_accepts_suffix_binding_to_same_title_before_proof_metadata():
    for evidence in (
        "Profile 7 FEL confirmed for Alien via MediaInfo.",
        "Profile 7 FEL confirmed for Alien by disc scan.",
    ):
        html = f"""
        <table>
          <tr><th>Title</th><th>Evidence</th></tr>
          <tr><td>Alien</td><td>{evidence}</td></tr>
        </table>
        """
        releases = parse_fel_releases(html, "https://example.test/thread")
        assert [release.movie_title for release in releases] == ["Alien"]


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


def test_accepts_from_proof_metadata_without_title_binding():
    for evidence in (
        "Profile 7 FEL from disc scan.",
        "Profile 7 FEL from MediaInfo.",
    ):
        html = f"""
        <table>
          <tr><th>Title</th><th>Evidence</th></tr>
          <tr><td>Alien</td><td>{evidence}</td></tr>
        </table>
        """
        releases = parse_fel_releases(html, "https://example.test/thread")
        assert [release.movie_title for release in releases] == ["Alien"]


def test_rejects_separator_suffix_binding_to_longer_title():
    examples = (
        ("It", "Profile 7 FEL - It Follows."),
        ("It", "Profile 7 FEL - It Follows (2014)."),
        ("It", "Profile 7 FEL (It Follows)."),
        ("It", "Profile 7 FEL (It Follows (2014))."),
        ("Alien", "Profile 7 FEL: Alien 3."),
        ("Alien", "Profile 7 FEL: Alien 3 (1992)."),
    )
    for row_title, evidence in examples:
        html = f"""
        <table>
          <tr><th>Title</th><th>Evidence</th></tr>
          <tr><td>{row_title}</td><td>{evidence}</td></tr>
        </table>
        """
        assert parse_fel_releases(html, "https://example.test/thread") == []


def test_rejects_after_evidence_binding_to_longer_title():
    examples = (
        ("It", "Profile 7 FEL in It Follows."),
        ("It", "Profile 7 FEL confirmed for It Follows (2014)."),
        ("It", 'Profile 7 FEL confirmed for "It Follows" (2014).'),
        ("Alien", "Profile 7 FEL in Alien 3 (1992)."),
        ("It", "Profile 7 FEL confirmed: It Follows."),
        ("Alien", "Profile 7 FEL confirmed: Alien 3 (1992)."),
        ("Alien", "Profile 7 FEL applies to Alien 3."),
        ("Alien", "Profile 7 FEL applies to Alien 3 (1992)."),
    )
    for row_title, evidence in examples:
        html = f"""
        <table>
          <tr><th>Title</th><th>Evidence</th></tr>
          <tr><td>{row_title}</td><td>{evidence}</td></tr>
        </table>
        """
        assert parse_fel_releases(html, "https://example.test/thread") == []


def test_accepts_after_evidence_binding_to_same_title():
    examples = (
        ("It", "Profile 7 FEL in It."),
        ("Alien", "Profile 7 FEL confirmed: Alien."),
        ("Alien", "Profile 7 FEL applies to Alien."),
    )
    for row_title, evidence in examples:
        html = f"""
        <table>
          <tr><th>Title</th><th>Evidence</th></tr>
          <tr><td>{row_title}</td><td>{evidence}</td></tr>
        </table>
        """
        releases = parse_fel_releases(html, "https://example.test/thread")
        assert [release.movie_title for release in releases] == [row_title]


def test_rejects_quoted_suffix_binding_to_longer_title():
    html = """
    <table>
      <tr><th>Title</th><th>Evidence</th></tr>
      <tr><td>It</td><td>Profile 7 FEL confirmed for "It Follows".</td></tr>
    </table>
    """
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_accepts_separator_suffix_binding_to_same_title():
    examples = (
        ("It", "Profile 7 FEL - It."),
        ("Alien", "Profile 7 FEL: Alien."),
    )
    for row_title, evidence in examples:
        html = f"""
        <table>
          <tr><th>Title</th><th>Evidence</th></tr>
          <tr><td>{row_title}</td><td>{evidence}</td></tr>
        </table>
        """
        releases = parse_fel_releases(html, "https://example.test/thread")
        assert [release.movie_title for release in releases] == [row_title]


def test_accepts_separator_proof_metadata_without_title_binding():
    html = """
    <table>
      <tr><th>Title</th><th>Evidence</th></tr>
      <tr><td>Alien</td><td>Profile 7 FEL - confirmed by disc scan.</td></tr>
    </table>
    """
    releases = parse_fel_releases(html, "https://example.test/thread")
    assert [release.movie_title for release in releases] == ["Alien"]


def test_accepts_separator_proof_source_metadata_without_title_binding():
    for evidence in (
        "Profile 7 FEL - MediaInfo.",
        "Profile 7 FEL - MediaInfo confirms FEL.",
        "Profile 7 FEL: MediaInfo.",
    ):
        html = f"""
        <table>
          <tr><th>Title</th><th>Evidence</th></tr>
          <tr><td>Alien</td><td>{evidence}</td></tr>
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


def test_parses_forum_list_item_with_fel_bitrate_evidence():
    html = """
    <ul>
      <li>
        10 Cloverfield Lane (2016)
        <b><a href="https://example.test/post">FEL</a></b>
        <b><font size="1">- 5.86 Mb/s</font></b>
      </li>
    </ul>
    """
    releases = parse_fel_releases(html, "https://example.test/thread")

    assert [release.movie_title for release in releases] == ["10 Cloverfield Lane"]
    assert releases[0].release_date == "2016"
    assert releases[0].fel_evidence.evidence_type == "list-item"
    assert releases[0].additional_characteristics == {
        "enhancement_bitrate_mbps": "5.86"
    }


def test_parses_forum_list_item_when_fel_is_alternate_to_mel_release():
    html = """
    <ul>
      <li>
        Robin Hood (2018) <b>MEL</b>
        <b><font size="1">- 0.07 Mb/s Lionsgate</font></b>
        (<b>FEL</b> <b><font size="1">- 12.02 Mb/s Studio Canal</font></b>)
      </li>
    </ul>
    """
    releases = parse_fel_releases(html, "https://example.test/thread")

    assert [release.movie_title for release in releases] == ["Robin Hood"]
    assert releases[0].release_date == "2018"
    assert releases[0].additional_characteristics == {
        "enhancement_bitrate_mbps": "12.02"
    }


def test_parses_forum_list_item_title_containing_and_or():
    html = """
    <ul>
      <li>Hell or High Water (2016) <b>FEL</b> <b>- 7.72 Mb/s</b></li>
      <li>
        Valerian and the City of a Thousand Planets (2017)
        <b>FEL</b> <b>- 8.26 Mb/s</b>
      </li>
    </ul>
    """
    releases = parse_fel_releases(html, "https://example.test/thread")

    assert [release.movie_title for release in releases] == [
        "Hell or High Water",
        "Valerian and the City of a Thousand Planets",
    ]


def test_parses_forum_list_item_numeric_title():
    html = """
    <ul>
      <li>1917 (2019) <b>FEL</b> <b>- 7.27 Mb/s</b></li>
    </ul>
    """
    releases = parse_fel_releases(html, "https://example.test/thread")

    assert [release.movie_title for release in releases] == ["1917"]
    assert releases[0].release_date == "2019"
    assert releases[0].additional_characteristics == {
        "enhancement_bitrate_mbps": "7.27"
    }


def test_rejects_forum_list_item_without_title_year_bitrate_correlation():
    html = """
    <ul>
      <li>I love FEL when discs include it.</li>
      <li>Forrest Gump's FEL is discussed with 7.44 Mb/s.</li>
      <li>Alien (1979) FEL</li>
    </ul>
    """
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_parses_direct_sentence_with_title_and_profile_7_fel():
    html = (
        "<p>Alien (1979) is confirmed as Dolby Vision Profile 7 FEL with DTS-HD MA.</p>"
    )
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
    html = (
        "<p>The disc for Alien (1979) is confirmed as Dolby Vision Profile 7 FEL.</p>"
    )
    releases = parse_fel_releases(html, "https://example.test/thread")
    assert [release.movie_title for release in releases] == ["Alien"]


def test_rejects_sentence_with_ambiguous_prose_prefix():
    html = (
        "<p>The spreadsheet says Alien is confirmed as Dolby Vision Profile 7 FEL.</p>"
    )
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_rejects_collection_count_sentence_as_title():
    html = """
    <p>Here are 456 verified P7 FEL dolby vision films in my collection.</p>
    <p>215 Knowing (2009)</p>
    <p>216 Krampus (2015)</p>
    """
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_rejects_forum_timestamp_as_title():
    html = """
    <p>Post by WEZZEBE » Mon Jan 29, 2024 4:36 pm</p>
    <p>Is there anything that can play Dolby Vision Profile 7 FEL from mkv-files
    with high quality Audio as well?</p>
    """
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_rejects_sentence_with_demonstrative_source_prose_prefix():
    html = (
        "<p>This spreadsheet says Alien is confirmed as Dolby Vision Profile 7 FEL.</p>"
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


def test_rejects_sentence_with_review_source_prose_prefix():
    html = "<p>The review says Alien is confirmed as Dolby Vision Profile 7 FEL.</p>"
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_rejects_sentence_with_source_prose_colon_prefix():
    html = "<p>The post says: Alien is confirmed as Dolby Vision Profile 7 FEL.</p>"
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_rejects_hardware_device_prose_sentence_as_title():
    html = (
        "<p>The single biggest reason this device is revered is its rare ability "
        "to correctly process TV-led Dolby Vision Profile 7, including the Full "
        "Enhancement Layer (FEL).</p>"
    )
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_rejects_sentence_with_source_prose_comma_prefix():
    html = "<p>The post says, Alien is confirmed as Dolby Vision Profile 7 FEL.</p>"
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_rejects_sentence_with_in_source_prefix():
    html = (
        "<p>In the spreadsheet, Alien is confirmed as Dolby Vision Profile 7 FEL.</p>"
    )
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_rejects_sentence_with_contextual_for_prefix():
    html = "<p>For The Matrix, Alien is confirmed as Dolby Vision Profile 7 FEL.</p>"
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_rejects_coordinated_multi_title_sentence():
    html = "<p>Alien and The Matrix are confirmed as Dolby Vision Profile 7 FEL.</p>"
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_rejects_ambiguous_title_list_even_with_shared_fel_evidence():
    html = """
    <table>
      <tr><th>Title</th><th>Evidence</th></tr>
      <tr>
        <td>Alien and Aliens</td>
        <td>Both entries are listed as Dolby Vision Profile 7 FEL.</td>
      </tr>
    </table>
    """
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_parser_output_normalizes_multiple_audio_formats_from_fel_evidence():
    html = """
    <table>
      <tr><th>Title</th><th>Evidence</th></tr>
      <tr>
        <td>Pacific Rim</td>
        <td>
          Pacific Rim: Profile 7 FEL confirmed by BDInfo with
          English E-AC3 Atmos and DTS-HD Master Audio tracks.
        </td>
      </tr>
    </table>
    """
    releases = parse_fel_releases(html, "https://example.test/thread")
    assert [release.movie_title for release in releases] == ["Pacific Rim"]
    assert releases[0].audio_formats == ["DD+ Atmos", "DTS-HD MA"]
    assert releases[0].english_audio == "Yes"


def test_rejects_profile_7_without_fel():
    html = (
        "<p>Movie A has Dolby Vision Profile 7 but this post does not identify FEL.</p>"
    )
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_rejects_mel_even_when_fel_appears_elsewhere():
    html = (
        "<p>Movie A is Profile 7 MEL. Another user asked about FEL-capable players.</p>"
    )
    assert parse_fel_releases(html, "https://example.test/thread") == []


def test_accepts_fel_when_not_mel_clarifies_layer_type():
    html = "<p>Alien is confirmed as Dolby Vision Profile 7 FEL, not MEL.</p>"
    releases = parse_fel_releases(html, "https://example.test/thread")
    assert [release.movie_title for release in releases] == ["Alien"]


def test_accepts_fel_when_not_dolby_vision_mel_clarifies_layer_type():
    html = (
        "<p>Alien is confirmed as Dolby Vision Profile 7 FEL and definitely "
        "not Dolby Vision MEL.</p>"
    )
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


def test_rejects_fel_question_with_trailing_denial():
    html = """
    <table>
      <tr><th>Title</th><th>DV</th></tr>
      <tr><td>Alien</td><td>Profile 7 FEL? No.</td></tr>
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
