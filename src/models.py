from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


UNKNOWN = "Unknown"


@dataclass(frozen=True)
class FelEvidence:
    source_url: str
    quote: str
    evidence_type: str
    location: str = UNKNOWN


@dataclass
class FelRelease:
    movie_title: str
    fel_evidence: FelEvidence
    release_date: str = UNKNOWN
    studio: str = UNKNOWN
    audio_formats: list[str] = field(default_factory=list)
    english_audio: str = UNKNOWN
    additional_characteristics: dict[str, Any] = field(default_factory=dict)
    source_label: str = UNKNOWN
    collected_at: str = UNKNOWN
    fel_confirmed: bool = True
    tmdb_id: str = ""
    # "movie" or "tv". TMDB movie and TV ids are independent numeric
    # sequences, so a bare tmdb_id only names a work together with its
    # media type; rows written before media typing existed are movies.
    media_type: str = "movie"
    imdb_id: str = ""
    poster_path: str = ""
    release_url: str = ""
    bluray_url: str = ""
    bluray_release_date: str = ""
    hdr_formats: list[str] = field(default_factory=list)
    audio_languages: list[str] = field(default_factory=list)

    @property
    def source_url(self) -> str:
        return self.fel_evidence.source_url

    @property
    def tmdb_identity(self) -> str:
        """Namespaced TMDB identity ("movie/603", "tv/1399"); "" unresolved."""
        if not self.tmdb_id:
            return ""
        return f"{self.media_type}/{self.tmdb_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "movie_title": self.movie_title,
            "fel_confirmed": self.fel_confirmed,
            "release_date": self.release_date,
            "studio": self.studio,
            "audio_formats": self.audio_formats,
            "english_audio": self.english_audio,
            "additional_characteristics": self.additional_characteristics,
            "source_url": self.source_url,
            "source_label": self.source_label,
            "tmdb_id": self.tmdb_id,
            "media_type": self.media_type,
            "imdb_id": self.imdb_id,
            "poster_path": self.poster_path,
            "release_url": self.release_url,
            "bluray_url": self.bluray_url,
            "bluray_release_date": self.bluray_release_date,
            "hdr_formats": self.hdr_formats,
            "audio_languages": self.audio_languages,
            "fel_evidence": {
                "source_url": self.fel_evidence.source_url,
                "quote": self.fel_evidence.quote,
                "evidence_type": self.fel_evidence.evidence_type,
                "location": self.fel_evidence.location,
            },
            "collected_at": self.collected_at,
        }


def release_from_dict(data: dict[str, Any]) -> FelRelease:
    evidence = data.get("fel_evidence") or {}
    return FelRelease(
        movie_title=data["movie_title"],
        fel_evidence=FelEvidence(
            source_url=evidence.get("source_url", ""),
            quote=evidence.get("quote", ""),
            evidence_type=evidence.get("evidence_type", UNKNOWN),
            location=evidence.get("location", UNKNOWN),
        ),
        release_date=data.get("release_date", UNKNOWN),
        studio=data.get("studio", UNKNOWN),
        audio_formats=list(data.get("audio_formats", [])),
        english_audio=data.get("english_audio", UNKNOWN),
        additional_characteristics=dict(data.get("additional_characteristics", {})),
        source_label=data.get("source_label", UNKNOWN),
        collected_at=data.get("collected_at", UNKNOWN),
        fel_confirmed=data.get("fel_confirmed", True),
        tmdb_id=data.get("tmdb_id", ""),
        media_type=data.get("media_type") or "movie",
        imdb_id=data.get("imdb_id", ""),
        poster_path=data.get("poster_path", ""),
        release_url=data.get("release_url", ""),
        bluray_url=data.get("bluray_url", ""),
        bluray_release_date=data.get("bluray_release_date", ""),
        hdr_formats=list(data.get("hdr_formats", [])),
        audio_languages=list(data.get("audio_languages", [])),
    )
