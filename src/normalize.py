from __future__ import annotations

import re


def normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_audio(raw_string: str) -> list[str]:
    cleaned = normalize_title(raw_string)
    if not cleaned:
        return []

    lowered = cleaned.lower()
    matches: list[str] = []

    def add(value: str) -> None:
        if value not in matches:
            matches.append(value)

    if "atmos" in lowered and (
        "truehd" in lowered or "true hd" in lowered or "dolby truehd" in lowered
    ):
        add("TrueHD Atmos")
    if "atmos" in lowered and (
        "dd+" in lowered
        or "digital plus" in lowered
        or "e-ac3" in lowered
        or "eac3" in lowered
    ):
        add("DD+ Atmos")
    if "dts:x" in lowered or "dts-x" in lowered:
        add("DTS:X")
    if (
        "dts-hd ma" in lowered
        or "dts-hd master audio" in lowered
        or "dts-ma" in lowered
    ):
        add("DTS-HD MA")
    if not any(value.startswith("TrueHD") for value in matches) and (
        "truehd" in lowered or "true hd" in lowered or "dolby truehd" in lowered
    ):
        add("TrueHD")
    if not any(value.startswith("DD+") for value in matches) and (
        "dd+" in lowered
        or "digital plus" in lowered
        or "e-ac3" in lowered
        or "eac3" in lowered
    ):
        add("DD+")

    if matches:
        return matches
    if _looks_like_audio_label(cleaned):
        return [cleaned]
    return []


def _looks_like_audio_label(value: str) -> bool:
    if len(value) > 80 or re.search(r"\d{3,}", value):
        return False
    lowered = value.lower()
    return bool(
        re.search(
            r"\b(?:aac|ac-?3|audio|channel|dts|dolby|dual\s+mono|english|"
            r"flac|japanese|lpcm|mono|pcm|stereo|surround)\b",
            lowered,
        )
    )
