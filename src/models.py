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
    imdb_id: str = ""
    poster_path: str = ""
    release_url: str = ""

    @property
    def source_url(self) -> str:
        return self.fel_evidence.source_url

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
            "imdb_id": self.imdb_id,
            "poster_path": self.poster_path,
            "release_url": self.release_url,
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
        imdb_id=data.get("imdb_id", ""),
        poster_path=data.get("poster_path", ""),
        release_url=data.get("release_url", ""),
    )
