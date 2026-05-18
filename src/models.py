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
            "fel_evidence": {
                "source_url": self.fel_evidence.source_url,
                "quote": self.fel_evidence.quote,
                "evidence_type": self.fel_evidence.evidence_type,
                "location": self.fel_evidence.location,
            },
            "collected_at": self.collected_at,
        }
