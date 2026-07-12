from __future__ import annotations

import html
import re


def normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_audio(raw_string: str) -> list[str]:
    cleaned = normalize_title(raw_string)
    if not cleaned:
        return []  # pragma: no cover - empty audio string guard

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
    if not any(
        value.startswith("DD+") for value in matches
    ) and (  # pragma: no cover - bare DD+ fallback
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


# Wiki-style medium disambiguators ("Hamilton (musical)", "Dune (2021 film)")
# never appear on the disc itself and defeat TMDB resolution, so they are
# stripped. Only this closed set of medium words qualifies: arbitrary trailing
# parentheticals ("Only the Brave (No Way Out)") are part of the real title.
_TRAILING_DISAMBIGUATOR_RE = re.compile(
    r"\s*\((?:(?:19|20)\d{2}\s+)?(?:musical|film|movie|tv\s+series|miniseries)\)\s*$",
    re.IGNORECASE,
)

_FEL_TITLE_PREFIXES = ("L.E. ", "EDIT: ", "EDIT ", "--", "-")
_BARE_LIST_ORDINAL_RE = re.compile(r"^\s*(?P<number>[1-9]\d{2})\s+(?P<title>[A-Z].*)")
_NUMERIC_TITLE_PREFIXES = (
    "100 Streets",
    "101 Dalmatians",
    "102 Dalmatians",
    "127 Hours",
    "200 Cigarettes",
    "300 Rise",
    "365 Days",
)


def _strip_bare_list_ordinal(title: str) -> str:
    match = _BARE_LIST_ORDINAL_RE.match(title)
    if match is None:
        return title
    if any(
        title.casefold().startswith(prefix.casefold())
        for prefix in _NUMERIC_TITLE_PREFIXES
    ):
        return title
    return match.group("title")


def normalize_fel_title(value: str) -> str:
    title = html.unescape(value).strip()
    title = _strip_bare_list_ordinal(title)
    changed = True
    while changed:
        changed = False
        for prefix in _FEL_TITLE_PREFIXES:
            if title.startswith(prefix):
                title = title[len(prefix) :].strip()
                changed = True
    if " AKA " in title:
        title = title.split(" AKA ", 1)[0].strip()
    title = _TRAILING_DISAMBIGUATOR_RE.sub("", title)
    return normalize_title(title).strip(",- ").strip()
