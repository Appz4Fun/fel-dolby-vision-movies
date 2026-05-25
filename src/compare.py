from __future__ import annotations

import csv
from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

import httpx

import fetcher
import google_sheets
import sources
from models import FelRelease
from normalize import normalize_fel_title


DEFAULT_AI_MODEL = "gpt-5.5"
DEFAULT_AI_REASONING_EFFORT = "xhigh"
DEFAULT_AI_BASE_URL = "https://api.theclawbay.com/backend-api/codex"
AI_EXTRACTION_SYSTEM_PROMPT = (
    "Extract confirmed Dolby Vision Profile 7 FEL movie entries. "
    "Return JSON only with items: title, year, evidence. "
    "Use the real movie title only. Do not include list numbering, bullets, "
    "row indexes, or ordinal prefixes in titles; for example, if the source "
    "line is '281 Nobody (2021)', return title 'Nobody'. "
    "Do not include MEL-only, generic REMUX, or ambiguous entries."
)


@dataclass(frozen=True)
class FoundCandidate:
    title: str
    year: str
    source_url: str
    evidence: str
    extraction_method: str

    @property
    def label(self) -> str:
        return f"{self.title} ({self.year})"


@dataclass(frozen=True)
class AISettings:
    api_key: str
    base_url: str = DEFAULT_AI_BASE_URL
    model: str = DEFAULT_AI_MODEL
    reasoning_effort: str = DEFAULT_AI_REASONING_EFFORT

    @classmethod
    def from_env(cls) -> AISettings:
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except Exception:  # pragma: no cover - dotenv import/parse failures
            pass
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get(
            "THECLAWBAY_API_KEY"
        )
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is required when --use-ai is set"
            )  # pragma: no cover
        return cls(
            api_key=api_key,
            base_url=os.environ.get("OPENAI_BASE_URL", DEFAULT_AI_BASE_URL),
            model=os.environ.get("OPENAI_MODEL", DEFAULT_AI_MODEL),
            reasoning_effort=os.environ.get(
                "OPENAI_REASONING_EFFORT", DEFAULT_AI_REASONING_EFFORT
            ),
        )


class AIClient:  # pragma: no cover - exercised via live OpenAI-compatible API only
    def __init__(self, settings: AISettings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=httpx.Timeout(90.0, connect=20.0, read=90.0),
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "fel-dolby-vision-movies/0.1",
            },
        )

    def extract_candidates(self, source_url: str, text: str) -> list[FoundCandidate]:
        payload = {
            "model": self.settings.model,
            "reasoning": {"effort": self.settings.reasoning_effort},
            "input": [
                {
                    "role": "system",
                    "content": AI_EXTRACTION_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": text[:20000],
                },
            ],
        }
        response = self.client.post(
            f"{self.settings.base_url.rstrip('/')}/responses",
            json=payload,
        )
        response.raise_for_status()
        return _candidates_from_ai_response_text(response.text, source_url)

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Send a single prompt to the model and return its text output."""
        payload = {
            "model": self.settings.model,
            "reasoning": {"effort": self.settings.reasoning_effort},
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        response = self.client.post(
            f"{self.settings.base_url.rstrip('/')}/responses",
            json=payload,
        )
        response.raise_for_status()
        return _extract_response_text(response.text)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> AIClient:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()


def compare_found(
    source_path: Path,
    output_dir: Path,
    cache_dir: Path,
    workers: int,
    use_ai: bool,
    ai_limit: int | None = None,
) -> dict[str, int]:
    # Import lazily to avoid a hard main->compare->main cycle during tests.
    import main

    ai_candidates: list[FoundCandidate] = []
    if use_ai:  # pragma: no cover - requires live AI credentials
        settings = AISettings.from_env()
        source_urls = _source_urls_for_ai(source_path, cache_dir)
        if ai_limit is not None:
            source_urls = source_urls[:ai_limit]
        with AIClient(settings) as ai_client:
            ai_candidates = _extract_ai_candidates(source_urls, cache_dir, ai_client)
    if not use_ai and (output_dir / "AI_found.csv").exists():
        ai_candidates = _read_candidates_csv(output_dir / "AI_found.csv")
    if not use_ai and not ai_candidates and (output_dir / "AI_found.txt").exists():
        ai_candidates = _read_legacy_candidates_txt(
            output_dir / "AI_found.txt", cache_dir
        )

    scrape_output = output_dir
    main._scrape_for_titles(source_path, scrape_output, cache_dir, workers)
    data_path = scrape_output / "data" / "releases.json"
    py_releases = _releases_from_json(data_path)
    return write_comparison_outputs(ai_candidates, py_releases, output_dir)


def write_comparison_outputs(
    ai_candidates: list[FoundCandidate],
    py_releases: list[FelRelease],
    output_dir: Path | str,
) -> dict[str, int]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    py_candidates = [_candidate_from_release(release) for release in py_releases]
    ai_rows = _rows_with_matches(ai_candidates, py_candidates)
    py_rows = _rows_with_matches(py_candidates, ai_candidates)

    _write_csv(root / "AI_found.csv", ai_rows)
    _write_csv(root / "PY_found.csv", py_rows)
    _write_txt(root / "AI_found.txt", ai_rows)
    _write_txt(root / "PY_found.txt", py_rows)
    _write_txt(
        root / "AI_PY_overlap.txt",
        [row for row in ai_rows if row["match_percent"] == "100"],
    )
    _write_txt(
        root / "AI_only.txt",
        [row for row in ai_rows if row["match_percent"] != "100"],
    )
    _write_txt(
        root / "PY_only.txt",
        [row for row in py_rows if row["match_percent"] != "100"],
    )

    ai_keys = {_canonical_key(candidate) for candidate in ai_candidates}
    py_keys = {_canonical_key(candidate) for candidate in py_candidates}
    summary = {
        "AI_found": len(ai_keys),
        "PY_found": len(py_keys),
        "overlap": len(ai_keys & py_keys),
        "AI_only": len(ai_keys - py_keys),
        "PY_only": len(py_keys - ai_keys),
    }
    (root / "found_diff_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def _candidate_from_release(release: FelRelease) -> FoundCandidate:
    return FoundCandidate(
        title=release.movie_title,
        year=release.release_date,
        source_url=release.source_url,
        evidence=release.fel_evidence.quote,
        extraction_method=release.fel_evidence.evidence_type,
    )


def _rows_with_matches(
    candidates: list[FoundCandidate], other_candidates: list[FoundCandidate]
) -> list[dict[str, str]]:
    rows = []
    for candidate in sorted(
        candidates,
        key=lambda item: (
            -_best_match_percent(item, other_candidates),
            item.label.casefold(),
        ),
    ):
        match_percent = _best_match_percent(candidate, other_candidates)
        rows.append(
            {
                "title": candidate.title,
                "year": candidate.year,
                "label": candidate.label,
                "match_percent": str(match_percent),
                "source_url": candidate.source_url,
                "extraction_method": candidate.extraction_method,
                "evidence": candidate.evidence,
            }
        )
    return rows


def _source_urls_for_ai(source_path: Path, cache_dir: Path) -> list[str]:
    expanded_urls = _read_expanded_urls(cache_dir)
    if expanded_urls:
        return expanded_urls
    urls = sources.read_source_urls(source_path)
    urls.extend(sources.read_source_urls(_always_fel_path_for(source_path)))
    return list(dict.fromkeys(urls))


def _read_expanded_urls(cache_dir: Path) -> list[str]:
    expanded_path = cache_dir.parent / "ai_expanded_urls.txt"
    if not expanded_path.exists():
        return []
    return sources.read_source_urls(expanded_path)


def _extract_ai_candidates(  # pragma: no cover - requires live AI credentials
    source_urls: list[str], cache_dir: Path, ai_client: AIClient
) -> list[FoundCandidate]:
    candidates: list[FoundCandidate] = []
    errors: list[str] = []
    with fetcher.Fetcher(
        cache_dir=cache_dir,
        cookie_header=os.environ.get("FORUM_COOKIE_HEADER"),
    ) as html_fetcher:
        total = len(source_urls)
        for index, source_url in enumerate(source_urls, start=1):
            print(f"ai extract {index}/{total}: {source_url}", file=sys.stderr)
            fetch_url = source_url
            if "docs.google.com/spreadsheets/" in source_url:
                fetch_url = google_sheets.google_sheet_csv_url(source_url)
            result = html_fetcher.fetch(fetch_url, raise_on_error=False)
            if result.error:
                errors.append(f"{source_url}\t{result.error}")
                continue
            try:
                candidates.extend(ai_client.extract_candidates(source_url, result.text))
            except httpx.HTTPError as exc:
                errors.append(f"{source_url}\t{exc.__class__.__name__}")
                continue
    if errors:
        error_path = cache_dir.parent / "ai_compare_errors.txt"
        error_path.write_text("\n".join(errors) + "\n", encoding="utf-8")
    return _dedupe_candidates(candidates)


def _dedupe_candidates(candidates: list[FoundCandidate]) -> list[FoundCandidate]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[FoundCandidate] = []
    for candidate in candidates:
        key = (_canonical_key(candidate), candidate.source_url, candidate.evidence)
        if key in seen:
            continue  # pragma: no cover - duplicate AI candidate skip
        seen.add(key)
        unique.append(candidate)
    return unique


def _best_match_percent(
    candidate: FoundCandidate, other_candidates: list[FoundCandidate]
) -> int:
    if not other_candidates:
        return 0
    candidate_key = _canonical_key(candidate)
    if any(candidate_key == _canonical_key(other) for other in other_candidates):
        return 100
    return max(_token_overlap_percent(candidate, other) for other in other_candidates)


def _token_overlap_percent(candidate: FoundCandidate, other: FoundCandidate) -> int:
    candidate_tokens = set(_normalize_for_match(candidate.title).split())
    other_tokens = set(_normalize_for_match(other.title).split())
    if not candidate_tokens or not other_tokens:
        return round(  # pragma: no cover - empty-token fallback
            SequenceMatcher(None, _match_text(candidate), _match_text(other)).ratio()
            * 100
        )
    overlap = len(candidate_tokens & other_tokens)
    denominator = max(len(candidate_tokens), len(other_tokens))
    return round((overlap / denominator) * 100)


def _canonical_key(candidate: FoundCandidate) -> str:
    return f"{_normalize_for_match(candidate.title)} ({candidate.year})"


def _match_text(
    candidate: FoundCandidate,
) -> str:  # pragma: no cover - only called by fallback above
    return _normalize_for_match(candidate.label)


def _normalize_for_match(value: str) -> str:
    value = value.casefold().replace("’", "'")
    value = re.sub(r"(?<!\d)\.(?!\d)", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "title",
        "year",
        "label",
        "match_percent",
        "source_url",
        "extraction_method",
        "evidence",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_txt(path: Path, rows: list[dict[str, str]]) -> None:
    lines = [
        f"{row['label']} | match={row['match_percent']}% | source={row['source_url']}"
        for row in rows
    ]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _read_candidates_csv(path: Path) -> list[FoundCandidate]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            FoundCandidate(
                title=row["title"],
                year=row["year"],
                source_url=row["source_url"],
                evidence=row["evidence"],
                extraction_method=row["extraction_method"],
            )
            for row in csv.DictReader(handle)
        ]


def _read_legacy_candidates_txt(path: Path, cache_dir: Path) -> list[FoundCandidate]:
    source_index = _build_source_index(cache_dir)
    candidates: list[FoundCandidate] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        label = line.split(" | ", 1)[0].strip()
        match = re.search(r"^(?P<title>.+?)\s+\((?P<year>\d{4}|Unknown)\)$", label)
        if not match:
            continue  # pragma: no cover - malformed legacy line skip
        title = match.group("title").strip()
        year = match.group("year")
        source_url, evidence = _source_for_legacy_label(title, year, source_index)
        candidates.append(
            FoundCandidate(
                title=title,
                year=year,
                source_url=source_url,
                evidence=evidence,
                extraction_method="legacy-ai-text",
            )
        )
    return _dedupe_candidates(candidates)


def _build_source_index(cache_dir: Path) -> dict[str, tuple[str, str]]:
    indexed: dict[str, tuple[str, str]] = {}
    for source_url in _read_expanded_urls(cache_dir):
        cache_path = _cache_path_for_url(cache_dir, source_url)
        if cache_path.exists():
            text = cache_path.read_text(encoding="utf-8")
            for title, year in _labels_in_text(text):
                key = _canonical_key(
                    FoundCandidate(
                        title=title,
                        year=year,
                        source_url=source_url,
                        evidence=f"{title} ({year})",
                        extraction_method="legacy-ai-text",
                    )
                )
                indexed.setdefault(key, (source_url, f"{title} ({year})"))
    return indexed


def _labels_in_text(text: str) -> list[tuple[str, str]]:
    labels: list[tuple[str, str]] = []
    pattern = re.compile(
        r"(?P<title>[A-Z0-9][A-Za-z0-9:'&.,!?\-’ ]{1,100}?)\s+"
        r"\((?P<year>19\d{2}|20\d{2})\)"
    )
    for match in pattern.finditer(text):
        title = re.sub(r"\s+", " ", match.group("title")).strip(" ,-")
        year = match.group("year")
        if title:
            labels.append((title, year))
    return labels


def _cache_path_for_url(cache_dir: Path, url: str) -> Path:
    cache_key = f"public\0{url}"
    digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.html"


def _source_for_legacy_label(
    title: str, year: str, source_index: dict[str, tuple[str, str]]
) -> tuple[str, str]:
    title_year = f"{title} ({year})"
    key = _canonical_key(
        FoundCandidate(
            title=title,
            year=year,
            source_url="unknown",
            evidence=title_year,
            extraction_method="legacy-ai-text",
        )
    )
    return source_index.get(key, ("unknown", title_year))


def _releases_from_json(path: Path) -> list[FelRelease]:
    from models import FelEvidence, FelRelease

    rows = json.loads(path.read_text(encoding="utf-8"))
    releases: list[FelRelease] = []
    for row in rows:
        evidence = row["fel_evidence"]
        releases.append(
            FelRelease(
                movie_title=row["movie_title"],
                release_date=row["release_date"],
                fel_evidence=FelEvidence(
                    source_url=evidence["source_url"],
                    quote=evidence["quote"],
                    evidence_type=evidence["evidence_type"],
                    location=evidence.get("location", "Unknown"),
                ),
            )
        )
    return releases


def _candidates_from_ai_response(  # pragma: no cover - AI response parsing helpers
    response_json: dict[str, Any], source_url: str
) -> list[FoundCandidate]:
    text = _response_text(response_json)
    return _candidates_from_payload_text(text, source_url)


def _candidates_from_ai_response_text(  # pragma: no cover - AI response parsing helpers
    response_text: str, source_url: str
) -> list[FoundCandidate]:
    response_text = response_text.strip()
    if not response_text:
        return []
    if response_text.startswith("event:") or "\nevent:" in response_text:
        text = _response_text_from_sse(response_text)
        return _candidates_from_payload_text(text, source_url)
    try:
        response_json = json.loads(response_text)
    except json.JSONDecodeError:
        return _candidates_from_payload_text(response_text, source_url)
    return _candidates_from_ai_response(response_json, source_url)


def _extract_response_text(
    response_text: str,
) -> str:  # pragma: no cover - AI response parsing
    """Return the model's text output from a raw /responses HTTP body."""
    response_text = response_text.strip()
    if not response_text:
        return ""
    if response_text.startswith("event:") or "\nevent:" in response_text:
        return _response_text_from_sse(response_text)
    try:
        response_json = json.loads(response_text)
    except json.JSONDecodeError:
        return response_text
    return _response_text(response_json)


def _candidates_from_payload_text(
    text: str, source_url: str
) -> list[FoundCandidate]:  # pragma: no cover
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("items", [])
    else:
        items = []
    candidates = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = normalize_fel_title(str(item.get("title", "")).strip())
        year = str(item.get("year", "Unknown")).strip() or "Unknown"
        evidence = str(item.get("evidence", "")).strip()
        if title:
            candidates.append(
                FoundCandidate(
                    title=title,
                    year=year,
                    source_url=source_url,
                    evidence=evidence,
                    extraction_method="ai",
                )
            )
    return candidates


def _response_text_from_sse(response_text: str) -> str:  # pragma: no cover - SSE parser
    current_event = ""
    delta_chunks: list[str] = []
    done_chunks: list[str] = []
    fallback_done_chunks: list[str] = []
    for raw_line in response_text.splitlines():
        line = raw_line.strip()
        if line.startswith("event:"):
            current_event = line.removeprefix("event:").strip()
            continue
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        if current_event == "response.output_text.done":
            text = event.get("text")
            if isinstance(text, str):
                done_chunks.append(text)
            continue
        if current_event == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                delta_chunks.append(delta)
            continue
        if current_event in {"response.content_part.done", "response.output_item.done"}:
            text = _text_from_sse_done_event(event)
            if text:
                fallback_done_chunks.append(text)
    if done_chunks:
        return done_chunks[-1]
    if fallback_done_chunks:
        return fallback_done_chunks[-1]
    return "".join(delta_chunks)


def _text_from_sse_done_event(
    event: dict[str, Any],
) -> str:  # pragma: no cover - SSE parser
    part = event.get("part")
    if isinstance(part, dict) and isinstance(part.get("text"), str):
        return part["text"]
    item = event.get("item")
    if isinstance(item, dict):
        for content in item.get("content", []):
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                return content["text"]
    return ""


def _response_text(
    response_json: dict[str, Any],
) -> str:  # pragma: no cover - AI response parsing
    if isinstance(response_json.get("output_text"), str):
        return response_json["output_text"]
    chunks: list[str] = []
    for output in response_json.get("output", []):
        for content in output.get("content", []):
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "".join(chunks)


def _always_fel_path_for(source_path: Path) -> Path:
    return source_path.with_name("sources_always_fel.txt")
