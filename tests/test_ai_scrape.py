import json

from ai_scrape import (
    _candidate_to_release,
    _load_existing_releases,
    _parse_url_list,
    ai_discover_sources,
    ai_extract_releases,
)
from compare import FoundCandidate
from models import FelEvidence, FelRelease


class FakeAIClient:
    def __init__(self, complete_text: str = "", candidates=None) -> None:
        self._complete_text = complete_text
        self._candidates = candidates or []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        return self._complete_text

    def extract_candidates(self, source_url: str, text: str):
        return list(self._candidates)


def test_parse_url_list_handles_plain_and_fenced_json():
    assert _parse_url_list('["https://a.test", "https://b.test"]') == [
        "https://a.test",
        "https://b.test",
    ]
    assert _parse_url_list('```json\n["https://c.test"]\n```') == ["https://c.test"]
    assert _parse_url_list("not json at all") == []


def test_candidate_to_release_marks_ai_extracted():
    candidate = FoundCandidate(
        title="Nosferatu",
        year="2024",
        source_url="https://src.test/list",
        evidence="Nosferatu (2024) FEL",
        extraction_method="ai",
    )
    release = _candidate_to_release(candidate, "2026-05-22T00:00:00+00:00")
    assert release.movie_title == "Nosferatu"
    assert release.release_date == "2024"
    assert release.fel_evidence.evidence_type == "ai-extracted"
    assert release.source_url == "https://src.test/list"
    assert release.source_label == "codex-ai"


def test_ai_discover_sources_keeps_new_well_formed_urls():
    client = FakeAIClient(
        complete_text=json.dumps(
            [
                "https://forum.blu-ray.com/showthread.php?t=999",
                "https://known.test/list",
                "not-a-url",
            ]
        )
    )
    result = ai_discover_sources(client, ["https://known.test/list"])
    assert result == ["https://forum.blu-ray.com/showthread.php?t=999"]


def test_ai_extract_releases_converts_nonblank_candidates():
    candidates = [
        FoundCandidate("Drop", "2025", "https://src.test", "Drop FEL", "ai"),
        FoundCandidate("", "2020", "https://src.test", "blank", "ai"),
    ]
    client = FakeAIClient(candidates=candidates)
    releases = ai_extract_releases(client, [("https://src.test", "<html>")])
    assert [r.movie_title for r in releases] == ["Drop"]
    assert releases[0].fel_evidence.evidence_type == "ai-extracted"


def test_load_existing_releases_round_trips(tmp_path):
    release = FelRelease(
        movie_title="Dune",
        release_date="2021",
        fel_evidence=FelEvidence(
            source_url="https://src.test",
            quote="Dune FEL",
            evidence_type="forum-post",
        ),
        source_label="forums",
        collected_at="2026-05-21T00:00:00+00:00",
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "releases.json").write_text(
        json.dumps([release.to_dict()]), encoding="utf-8"
    )

    loaded = _load_existing_releases(tmp_path)

    assert [r.movie_title for r in loaded] == ["Dune"]


def test_load_existing_releases_missing_file_returns_empty(tmp_path):
    assert _load_existing_releases(tmp_path) == []
