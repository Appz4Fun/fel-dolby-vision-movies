from fel_ingest import parse_fel_txt, parse_raw_fel_txt


def test_parse_fel_txt_reads_title_year_and_urls():
    text = (
        "Nosferatu,2024,https://reddit.test/list|https://bluray.test/disc\n"
        "Drop,2025,\n"
        ",2025,https://orphan.test\n"
        "L.E. The Northman,2022,https://reddit.test/list\n"
    )
    releases = parse_fel_txt(text)

    titles = [r.movie_title for r in releases]
    assert titles == ["Nosferatu", "Drop", "The Northman"]

    nosferatu = releases[0]
    assert nosferatu.release_date == "2024"
    assert nosferatu.source_url == "https://reddit.test/list"
    assert nosferatu.fel_evidence.evidence_type == "fel-list"
    assert nosferatu.additional_characteristics["source_urls"] == [
        "https://reddit.test/list",
        "https://bluray.test/disc",
    ]

    drop = releases[1]
    assert drop.source_url == "FEL.txt (curated Profile 7 FEL list)"
    assert "source_urls" not in drop.additional_characteristics


def test_parse_raw_fel_txt_extracts_only_fel_bitrate_lines():
    text = (
        "Work in progress - prose line about MEL and FEL layers\n"
        "10 Cloverfield Lane (2016) FEL - 5.86 Mb/s\n"
        "Apocalypse Now (1979) FEL - 7.58 Mb/s Final Cut\n"
        "Fifth Element MEL - 0.09 Mb/s\n"
        "Just a sentence with no movie pattern.\n"
    )
    releases = parse_raw_fel_txt(text)

    assert [r.movie_title for r in releases] == [
        "10 Cloverfield Lane",
        "Apocalypse Now",
    ]
    cloverfield = releases[0]
    assert cloverfield.release_date == "2016"
    assert cloverfield.additional_characteristics["enhancement_bitrate_mbps"] == 5.86
    assert cloverfield.fel_evidence.evidence_type == "fel-bitrate-list"
