from bluray import normalize_bluray_audio, parse_hdr


def test_parse_hdr_keeps_known_formats_in_order():
    assert parse_hdr("Dolby Vision, HDR10") == ["Dolby Vision", "HDR10"]
    assert parse_hdr("HDR10+, HDR10") == ["HDR10+", "HDR10"]
    assert parse_hdr("") == []
    assert parse_hdr("SDR, junk") == []


def test_normalize_audio_strips_freqs_and_abbreviates():
    tracks = [
        ("English", "Dolby Digital 5.1 (640 kbps)"),
        ("French", "DTS-HD Master Audio 5.1 (48kHz, 24-bit)"),
        ("German", "Dolby Digital Plus 7.1"),
    ]
    assert normalize_bluray_audio(tracks) == [
        "DD 5.1",
        "DTS-HD MA 5.1",
        "DD+ 7.1",
    ]


def test_normalize_audio_combines_atmos_and_dtsx_with_core():
    atmos = [
        ("English", "Dolby Atmos"),
        ("English", "Dolby TrueHD 7.1 (48kHz, 24-bit)"),
    ]
    assert normalize_bluray_audio(atmos) == ["Dolby TrueHD/Atmos 7.1"]

    dtsx = [
        ("English", "DTS:X"),
        ("English", "DTS-HD Master Audio 7.1 (48kHz, 24-bit)"),
    ]
    assert normalize_bluray_audio(dtsx) == ["DTS:X 7.1"]


def test_normalize_audio_dedupes_across_languages():
    tracks = [
        ("English", "Dolby Digital 5.1"),
        ("Spanish", "Dolby Digital 5.1"),
    ]
    assert normalize_bluray_audio(tracks) == ["DD 5.1"]
