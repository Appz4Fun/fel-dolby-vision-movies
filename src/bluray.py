from __future__ import annotations

import re

_FREQ_RE = re.compile(r"\s*\([^)]*\)")
_ABBREVIATIONS = (
    ("Dolby Digital Plus", "DD+"),
    ("Dolby Digital", "DD"),
    ("DTS-HD Master Audio", "DTS-HD MA"),
    ("DTS-HD High-Resolution Audio", "DTS-HD HRA"),
)
# An audio line is a real track only when its format starts with one of these.
KNOWN_CODECS = (
    "Dolby Atmos",
    "Dolby TrueHD",
    "Dolby Digital Plus",
    "Dolby Digital",
    "DTS:X",
    "DTS-HD Master Audio",
    "DTS-HD High-Resolution Audio",
    "DTS",
    "LPCM",
    "PCM",
)
KNOWN_HDR = ("Dolby Vision", "HDR10+", "HDR10", "HLG")


def parse_hdr(hdr_text: str) -> list[str]:
    out: list[str] = []
    for token in (hdr_text or "").split(","):
        candidate = token.strip()
        if candidate in KNOWN_HDR and candidate not in out:
            out.append(candidate)
    return out


def _strip_freq(fmt: str) -> str:
    return _FREQ_RE.sub("", fmt).strip()


def _abbreviate(fmt: str) -> str:
    for long_name, short_name in _ABBREVIATIONS:
        if fmt.startswith(long_name):
            return short_name + fmt[len(long_name) :]
    return fmt


def normalize_bluray_audio(tracks: list[tuple[str, str]]) -> list[str]:
    """Canonicalize (language, raw_format) audio tracks into a deduped list."""
    by_language: dict[str, list[str]] = {}
    for language, raw_format in tracks:
        fmt = _abbreviate(_strip_freq(raw_format))
        by_language.setdefault(language, []).append(fmt)

    result: list[str] = []
    for formats in by_language.values():
        has_atmos = "Dolby Atmos" in formats
        has_dtsx = "DTS:X" in formats
        language_out: list[str] = []
        for fmt in formats:
            if fmt in ("Dolby Atmos", "DTS:X"):
                continue  # merged into the core track below
            if has_atmos and fmt.startswith("Dolby TrueHD "):
                fmt = "Dolby TrueHD/Atmos " + fmt[len("Dolby TrueHD ") :]
            elif has_atmos and fmt.startswith("DD+ "):
                fmt = "DD+/Atmos " + fmt[len("DD+ ") :]
            elif has_dtsx and fmt.startswith("DTS-HD MA "):
                fmt = "DTS:X " + fmt[len("DTS-HD MA ") :]
            language_out.append(fmt)
        if has_atmos and not any("Atmos" in f for f in language_out):
            language_out.append("Dolby Atmos")
        if has_dtsx and not any(f.startswith("DTS:X") for f in language_out):
            language_out.append("DTS:X")
        for fmt in language_out:
            if fmt not in result:
                result.append(fmt)
    return result
