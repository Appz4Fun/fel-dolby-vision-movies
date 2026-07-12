from __future__ import annotations

from collections.abc import Iterator
import csv
from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import io
import json
import os
from pathlib import Path
import re
import html
import sys
from typing import Any

import httpx
from bs4 import BeautifulSoup

import fetcher
import google_sheets
from parser import (
    FEL_SUBJECT_DELIMITER_RE,
    FEL_TRAILING_DENIAL_RE,
    fel_clause_residue,
    fel_subject_clauses,
    has_leading_fel_denial,
    has_unnegated_mel,
    is_allowed_fel_clause_residue,
    is_device_title,
)
import sources
from models import FelRelease
from normalize import normalize_fel_title


DEFAULT_AI_MODEL = "gpt-5.5"
DEFAULT_AI_REASONING_EFFORT = "xhigh"
DEFAULT_AI_BASE_URL = "https://api.theclawbay.com/backend-api/codex"
MAX_AI_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_AI_CANDIDATE_ITEMS = 500
AI_RETRYABLE_CLIENT_HTTP_STATUS_CODES = frozenset({408, 409, 425, 429})
SSE_TERMINAL_FAILURE_EVENTS = frozenset(
    {
        "response.failed",
        "response.incomplete",
        "response.cancelled",
        "response.canceled",
        "response.error",
        "error",
    }
)
# response.failed error.type values that mean "the service told us to retry",
# not "this request/response is permanently invalid".
_TRANSIENT_AI_ERROR_TYPES = frozenset({"server_error"})
AI_EXTRACTION_SYSTEM_PROMPT = (
    "Extract only confirmed Dolby Vision Profile 7 FEL movie entries. "
    "Return JSON only with items: title, year, evidence. "
    "Use the real movie title only. Do not include list numbering, bullets, "
    "row indexes, or ordinal prefixes in titles; for example, if the source "
    "line is '281 Nobody (2021)', return title 'Nobody'. "
    "Return the exact source excerpt containing the title and affirmative FEL marker. "
    "Year must be explicitly present in that same excerpt; use Unknown when absent and never infer. "
    "Exclude MEL-only, generic REMUX, negated FEL, cross-release, or ambiguous entries. "
    "Titles must be films. Never return playback hardware - media players, "
    "set-top boxes, TVs, chipsets, or AV equipment (for example Ugoos, Zidoo, "
    "Dune HD) - even when a page calls the device Profile 7 FEL capable."
)


class AIResponseFormatError(httpx.HTTPError):
    """A malformed successful AI response whose body must never be exposed."""

    def __init__(self) -> None:
        super().__init__("AI response format is invalid")


class AIGlobalHTTPError(httpx.HTTPError):
    """A global AI configuration/authentication failure with redacted context."""

    def __init__(self, status_code: int | None = None) -> None:
        self.status_code = status_code
        message = "AI request configuration is invalid"
        if status_code is not None:
            message += f" status={status_code}"
        super().__init__(message)


class AIServiceUnavailableError(httpx.HTTPError):
    """A transient upstream failure (e.g. an SSE response.failed server_error)
    the caller should retry, distinct from a permanently malformed response."""

    def __init__(self) -> None:
        super().__init__("AI service is temporarily unavailable")


def ai_http_status_code(exc: Exception) -> int | None:
    if isinstance(exc, AIGlobalHTTPError):
        return exc.status_code
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    return None


def global_ai_http_error(exc: Exception) -> AIGlobalHTTPError | None:
    if isinstance(exc, AIGlobalHTTPError):
        return exc
    if isinstance(exc, (httpx.UnsupportedProtocol, httpx.InvalidURL)):
        return AIGlobalHTTPError()
    status_code = ai_http_status_code(exc)
    is_global_status = status_code is not None and (
        300 <= status_code < 400
        or (
            400 <= status_code < 500
            and status_code not in AI_RETRYABLE_CLIENT_HTTP_STATUS_CODES
        )
        or status_code in {501, 505, 511}
    )
    if not is_global_status:
        return None
    return AIGlobalHTTPError(status_code)


def safe_ai_http_error_diagnostic(exc: Exception) -> str:
    status_code = ai_http_status_code(exc)
    diagnostic = exc.__class__.__name__
    if status_code is not None:
        diagnostic += f" status={status_code}"
    return diagnostic


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
            print(
                "warning: dotenv configuration could not be loaded",
                file=sys.stderr,
            )
        api_key = next(
            (
                value
                for name in (
                    "OPENAI_API_KEY",
                    "CODEX_API_KEY",
                    "THECLAWBAY_API_KEY",
                )
                if (value := os.environ.get(name, "").strip())
            ),
            "",
        )
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY, CODEX_API_KEY, or THECLAWBAY_API_KEY is required "
                "when --use-ai is set"
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
        try:
            self.client = httpx.Client(
                timeout=httpx.Timeout(90.0, connect=20.0, read=90.0),
                headers={
                    "Authorization": f"Bearer {settings.api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "fel-dolby-vision-movies/0.1",
                },
            )
        except (ValueError, ImportError):
            raise AIGlobalHTTPError() from None

    def _post_response(self, payload: dict[str, Any]) -> str:
        try:
            with self.client.stream(
                "POST",
                f"{self.settings.base_url.rstrip('/')}/responses",
                json=payload,
            ) as response:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    if global_error := global_ai_http_error(exc):
                        raise global_error from None
                    raise
                return _read_ai_response_body(response)
        except (httpx.UnsupportedProtocol, httpx.InvalidURL):
            raise AIGlobalHTTPError() from None

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
        response_text = self._post_response(payload)
        if _sse_transient_failure(response_text):
            raise AIServiceUnavailableError()
        candidates, valid = _parse_ai_candidate_response_text(response_text, source_url)
        if not valid:
            raise AIResponseFormatError()
        return candidates

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
        response_text = self._post_response(payload)
        if _sse_transient_failure(response_text):
            raise AIServiceUnavailableError()
        text, valid = _parse_response_text(response_text)
        if not valid:
            raise AIResponseFormatError()
        return text

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> AIClient:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()


def _read_ai_response_body(response: httpx.Response) -> str:  # pragma: no cover
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError:
            declared_length = 0
        if declared_length > MAX_AI_RESPONSE_BYTES:
            raise AIResponseFormatError()

    chunks: list[bytes] = []
    decoded_length = 0
    try:
        for chunk in response.iter_bytes():
            decoded_length += len(chunk)
            if decoded_length > MAX_AI_RESPONSE_BYTES:
                raise AIResponseFormatError()
            chunks.append(chunk)
    except httpx.DecodingError:
        raise AIResponseFormatError() from None

    encoding = response.encoding or "utf-8"
    try:
        return b"".join(chunks).decode(encoding)
    except (UnicodeError, LookupError):
        raise AIResponseFormatError() from None


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
            print(f"ai extract {index}/{total}", file=sys.stderr)
            fetch_url = source_url
            if "docs.google.com/spreadsheets/" in source_url:
                fetch_url = google_sheets.google_sheet_csv_url(source_url)
            result = html_fetcher.fetch(fetch_url, raise_on_error=False)
            if result.error:
                errors.append("FetchError")
                continue
            try:
                rejection_diagnostics: list[str] = []
                candidates.extend(
                    validate_ai_candidates(
                        ai_client.extract_candidates(source_url, result.text),
                        result.text,
                        rejection_diagnostics,
                    )
                )
                errors.extend(
                    f"{source_url}\tai-rejected:{reason}"
                    for reason in rejection_diagnostics
                )
            except (httpx.HTTPError, httpx.InvalidURL) as exc:
                if global_error := global_ai_http_error(exc):
                    raise global_error from None
                errors.append(safe_ai_http_error_diagnostic(exc))
                continue
    if errors:
        error_path = cache_dir.parent / "ai_compare_errors.txt"
        error_path.write_text("\n".join(errors) + "\n", encoding="utf-8")
    return _dedupe_candidates(candidates)


def _normalized_source(text: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text).strip()


_HTML_TAG_RE = re.compile(r"<[A-Za-z][^>]*>")
_SEMANTIC_RECORD_TAGS = frozenset({"tr", "li", "p"})
_LEAF_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "dd",
        "div",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "main",
        "nav",
        "section",
        "td",
        "th",
    }
)
_RECORD_TAGS = _SEMANTIC_RECORD_TAGS | _LEAF_BLOCK_TAGS
_PROFILE_7_EVIDENCE_RE = re.compile(r"\b(?:Profile[\s-]*7|P7)\b", re.I)
_FEL_EVIDENCE_RE = re.compile(r"\bFEL\b", re.I)
_NUMERIC_TITLE_BEFORE_YEAR_RE = re.compile(
    r"(?<!\w)(?:19|20)\d{2}(?=\s*\(\s*(?:19|20)\d{2}\s*\))"
)


def _normalized_source_records(text: str) -> list[str]:
    if not _HTML_TAG_RE.search(text):
        return [
            record for line in text.splitlines() if (record := _normalized_source(line))
        ]

    soup = BeautifulSoup(text, "html.parser")
    for line_break in soup.find_all("br"):
        line_break.replace_with("\n")
    records: list[str] = []
    seen_records: set[str] = set()
    record_tags = (
        tag
        for tag in soup.find_all(_RECORD_TAGS)
        if (
            tag.name == "tr"
            or (
                not tag.find(_SEMANTIC_RECORD_TAGS)
                if tag.name in _SEMANTIC_RECORD_TAGS
                else not tag.find(_RECORD_TAGS)
            )
        )
    )
    for tag in record_tags:
        for line in tag.get_text(" ").splitlines():
            record = _normalized_source(line)
            if record and record not in seen_records:
                records.append(record)
                seen_records.add(record)
    if records:
        return records
    return [
        record
        for line in soup.get_text(" ").splitlines()
        if (record := _normalized_source(line))
    ]


def _bound_year_after_title(
    evidence: str, title_match: re.Match[str], candidate_year: str
) -> re.Match[str] | None:
    match = re.match(
        r"\s*(?:\(\s*(?P<parenthesized_year>(?:19|20)\d{2})\s*\)|"
        r"(?P<standalone_year>(?:19|20)\d{2})(?!\d))",
        evidence[title_match.end() :],
    )
    if match is None:
        return None
    bound_year = match.group("parenthesized_year") or match.group("standalone_year")
    if re.fullmatch(r"(?:19|20)\d{2}", candidate_year):
        return match if bound_year == candidate_year else None
    return match


def _title_match_has_fel_clause(evidence: str, title_match: re.Match[str]) -> bool:
    delimiter = FEL_SUBJECT_DELIMITER_RE.search(evidence, title_match.end())
    clause_end = delimiter.start() if delimiter else len(evidence)
    clause = evidence[title_match.end() : clause_end]
    return bool(
        _PROFILE_7_EVIDENCE_RE.search(clause) and _FEL_EVIDENCE_RE.search(clause)
    )


def _mask_bound_candidate(
    evidence: str, title: str, candidate_year: str
) -> tuple[str, str | None]:
    title_matches = list(
        re.finditer(rf"(?<!\w){re.escape(title)}(?!\w)", evidence, re.I)
    )
    if not title_matches:
        return evidence, None

    bound_title = next(
        (
            match
            for match in title_matches
            if _bound_year_after_title(evidence, match, candidate_year)
        ),
        None,
    )
    if bound_title is None:
        bound_title = next(
            (
                match
                for match in title_matches
                if _title_match_has_fel_clause(evidence, match)
            ),
            title_matches[0],
        )

    spans = [bound_title.span()]
    bound_year_match = _bound_year_after_title(evidence, bound_title, candidate_year)
    bound_year = None
    if bound_year_match:
        spans.append(
            (
                bound_title.end() + bound_year_match.start(),
                bound_title.end() + bound_year_match.end(),
            )
        )
        bound_year = bound_year_match.group(
            "parenthesized_year"
        ) or bound_year_match.group("standalone_year")
    characters = list(evidence)
    for start, end in spans:
        characters[start:end] = " " * (end - start)
    return "".join(characters), bound_year


def _evidence_years(clause: str, candidate_title: str = "") -> list[str]:
    without_numeric_titles = _NUMERIC_TITLE_BEFORE_YEAR_RE.sub(" ", clause)
    if re.fullmatch(r"(?:19|20)\d{2}", candidate_title):
        without_numeric_titles = re.sub(
            rf"(?<!\w){re.escape(candidate_title)}(?!\w)",
            " ",
            without_numeric_titles,
        )
    return re.findall(r"\b(?:19|20)\d{2}\b", without_numeric_titles)


def _analyze_evidence_clauses(
    contextual_evidence: str,
    bound_year: str | None = None,
    candidate_title: str = "",
) -> tuple[bool, list[str]]:
    years = [bound_year] if bound_year else []
    has_bound_fel_evidence = False
    has_other_subject = False
    for clause in fel_subject_clauses(contextual_evidence, candidate_title):
        has_profile_7 = bool(_PROFILE_7_EVIDENCE_RE.search(clause))
        has_fel = bool(_FEL_EVIDENCE_RE.search(clause))
        residue = fel_clause_residue(clause)
        if residue and not is_allowed_fel_clause_residue(residue, candidate_title):
            has_other_subject = True
        years.extend(_evidence_years(clause, candidate_title))
        has_bound_fel_evidence |= has_profile_7 and has_fel
    return has_other_subject or not has_bound_fel_evidence, years


def _classify_evidence_rejection(
    title: str,
    evidence: str,
    evidence_casefold: str,
    contextual_evidence: str,
    source_casefold: str,
    source_records: tuple[str, ...],
) -> str | None:
    """Return why a candidate's raw evidence text fails binding, or None."""
    if not title or not evidence or evidence_casefold not in source_casefold:
        return "evidence-not-found"
    if not any(evidence_casefold in record for record in source_records):
        return "cross-release-evidence"
    if not re.search(rf"(?<!\w){re.escape(title)}(?!\w)", evidence, re.I):
        return "title-not-bound"
    if has_unnegated_mel(contextual_evidence) or re.search(
        r"\bREMUX\b", contextual_evidence, re.I
    ):
        return "excluded-format"
    if has_leading_fel_denial(contextual_evidence) or FEL_TRAILING_DENIAL_RE.search(
        contextual_evidence
    ):
        return "negated-fel"
    if not re.search(
        r"\b(?:Profile\s*7|P7)\b", contextual_evidence, re.I
    ) or not re.search(r"\bFEL\b", contextual_evidence, re.I):
        return "missing-affirmative-fel"
    return None


def _resolve_candidate_year(
    candidate: FoundCandidate, bound_year: str | None, years: list[str]
) -> tuple[str, str | None]:
    """Return (year, rejection_reason) for a candidate that passed evidence checks."""
    candidate_has_valid_year = bool(re.fullmatch(r"(?:19|20)\d{2}", candidate.year))
    year = candidate.year if candidate_has_valid_year else bound_year or "Unknown"
    if not candidate_has_valid_year and bound_year is None and years:
        return year, "year-not-in-evidence"
    if year != "Unknown" and year not in years:
        return year, "year-not-in-evidence"
    return year, None


def validate_ai_candidates(
    candidates: list[FoundCandidate],
    source_text: str,
    diagnostics: list[str] | None = None,
) -> list[FoundCandidate]:
    """Keep only candidates whose exact evidence binds to affirmative FEL source text."""
    source_casefold = _normalized_source(source_text).casefold()
    source_records = tuple(
        record.casefold() for record in _normalized_source_records(source_text)
    )
    accepted: list[FoundCandidate] = []
    for candidate in candidates:
        evidence = _normalized_source(candidate.evidence)
        evidence_casefold = evidence.casefold()
        title = _normalized_source(candidate.title)
        if title and is_device_title(title):
            if diagnostics is not None:
                diagnostics.append("device-title")
            continue
        contextual_evidence, bound_year = (
            _mask_bound_candidate(evidence, title, candidate.year)
            if title
            else (evidence, None)
        )
        reason = _classify_evidence_rejection(
            title,
            evidence,
            evidence_casefold,
            contextual_evidence,
            source_casefold,
            source_records,
        )
        if reason:
            if diagnostics is not None:
                diagnostics.append(reason)
            continue
        cross_release_evidence, years = _analyze_evidence_clauses(
            contextual_evidence, bound_year, title
        )
        if len(set(years)) > 1:
            if diagnostics is not None:
                diagnostics.append("ambiguous-year")
            continue
        if cross_release_evidence:
            if diagnostics is not None:
                diagnostics.append("cross-release-evidence")
            continue
        year, year_reason = _resolve_candidate_year(candidate, bound_year, years)
        if year_reason:
            if diagnostics is not None:
                diagnostics.append(year_reason)
            continue
        accepted.append(
            FoundCandidate(
                candidate.title,
                year,
                candidate.source_url,
                candidate.evidence,
                candidate.extraction_method,
            )
        )
    return accepted


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
    response_json: Any, source_url: str
) -> list[FoundCandidate]:
    text, valid = _parse_response_json(response_json)
    if not valid or not text:
        return []
    candidates, _ = _parse_candidates_from_payload_text(text, source_url)
    return candidates


def _candidates_from_ai_response_text(  # pragma: no cover - AI response parsing helpers
    response_text: str, source_url: str
) -> list[FoundCandidate]:
    candidates, _ = _parse_ai_candidate_response_text(response_text, source_url)
    return candidates


def _parse_ai_candidate_response_text(  # pragma: no cover - AI response parsing
    response_text: str, source_url: str
) -> tuple[list[FoundCandidate], bool]:
    response_text = response_text.removeprefix("\ufeff").strip()
    if not response_text:
        return [], False
    if _looks_like_sse(response_text):
        text, valid = _parse_response_text_from_sse(response_text)
        if not valid:
            return [], False
        if not text:
            return [], True
        return _parse_candidates_from_payload_text(text, source_url)
    try:
        response_json = json.loads(response_text)
    except (ValueError, RecursionError):
        return [], False
    if isinstance(response_json, dict) and not _has_valid_response_status(
        response_json
    ):
        return [], False
    if isinstance(response_json, list) or (
        isinstance(response_json, dict) and "items" in response_json
    ):
        return _parse_candidate_payload(response_json, source_url)
    text, valid = _parse_response_json(response_json)
    if not valid:
        return [], False
    if not text:
        return [], True
    return _parse_candidates_from_payload_text(text, source_url)


def _extract_response_text(
    response_text: str,
) -> str:  # pragma: no cover - AI response parsing
    """Return the model's text output from a raw /responses HTTP body."""
    text, _ = _parse_response_text(response_text)
    return text


def _parse_response_text(  # pragma: no cover - AI response parsing
    response_text: str,
) -> tuple[str, bool]:
    response_text = response_text.removeprefix("\ufeff").strip()
    if not response_text:
        return "", False
    if _looks_like_sse(response_text):
        return _parse_response_text_from_sse(response_text)
    try:
        response_json = json.loads(response_text)
    except (ValueError, RecursionError):
        return "", False
    return _parse_response_json(response_json)


def _candidates_from_payload_text(
    text: str, source_url: str
) -> list[FoundCandidate]:  # pragma: no cover
    candidates, _ = _parse_candidates_from_payload_text(text, source_url)
    return candidates


def _parse_candidates_from_payload_text(  # pragma: no cover - AI response parsing
    text: str, source_url: str
) -> tuple[list[FoundCandidate], bool]:
    try:
        payload = json.loads(text)
    except (ValueError, RecursionError):
        return [], False
    if isinstance(payload, dict) and not _has_valid_response_status(payload):
        return [], False
    return _parse_candidate_payload(payload, source_url)


def _parse_candidate_payload(  # pragma: no cover - AI response parsing
    payload: Any, source_url: str
) -> tuple[list[FoundCandidate], bool]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("items"), list):
        items = payload["items"]
    else:
        return [], False
    if len(items) > MAX_AI_CANDIDATE_ITEMS:
        return [], False
    candidates = _candidates_from_payload(payload, source_url)
    return candidates, not items or bool(candidates)


def _candidates_from_payload(
    payload: Any, source_url: str
) -> list[FoundCandidate]:  # pragma: no cover
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("items", [])
    else:
        items = []
    if not isinstance(items, list):
        return []
    candidates = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title_value = item.get("title")
        year_value = item.get("year", "Unknown")
        evidence_value = item.get("evidence", "")
        if isinstance(year_value, int) and not isinstance(year_value, bool):
            year_value = str(year_value)
        if not all(
            isinstance(value, str)
            for value in (title_value, year_value, evidence_value)
        ):
            continue
        title = normalize_fel_title(title_value.strip())
        year = year_value.strip() or "Unknown"
        evidence = evidence_value.strip()
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


def _looks_like_sse(response_text: str) -> bool:  # pragma: no cover - SSE parser
    return any(
        line.strip().startswith(("event:", "data:"))
        for line in io.StringIO(response_text)
    )


def _sse_transient_failure(response_text: str) -> bool:  # pragma: no cover - SSE parser
    """True if the stream terminated with a transient, retryable service error
    (e.g. response.failed/server_error) rather than a permanently invalid one."""
    if not _looks_like_sse(response_text):
        return False
    for current_event, data in _sse_records(response_text):
        if current_event not in SSE_TERMINAL_FAILURE_EVENTS:
            continue
        try:
            event = json.loads(data)
        except (ValueError, RecursionError):
            continue
        if not isinstance(event, dict):
            continue
        response = event.get("response")
        error = (
            response.get("error") if isinstance(response, dict) else event.get("error")
        )
        if isinstance(error, dict) and error.get("type") in _TRANSIENT_AI_ERROR_TYPES:
            return True
    return False


def _response_text_from_sse(response_text: str) -> str:  # pragma: no cover - SSE parser
    text, _ = _parse_response_text_from_sse(response_text)
    return text


def _parse_response_text_from_sse(  # pragma: no cover - SSE parser
    response_text: str,
) -> tuple[str, bool]:
    delta_chunks: list[str] = []
    done_chunks: list[str] = []
    item_done_chunks: list[str] = []
    part_done_chunks: list[str] = []
    done_coordinates: set[tuple[tuple[str, str | int], ...]] = set()
    item_done_coordinates: set[tuple[tuple[str, str | int], ...]] = set()
    part_done_coordinates: set[tuple[tuple[str, str | int], ...]] = set()
    completed_text = ""
    has_delta = False
    has_done = False
    has_item_done = False
    has_part_done = False
    has_completed = False
    invalid_terminal = False
    for current_event, data in _sse_records(response_text):
        if current_event in SSE_TERMINAL_FAILURE_EVENTS:
            invalid_terminal = True
            continue
        has_completed_header = current_event == "response.completed"
        if data == "[DONE]":
            if has_completed_header:
                invalid_terminal = True
            continue
        try:
            event = json.loads(data)
        except (ValueError, RecursionError):
            if has_completed_header:
                invalid_terminal = True
            continue
        if not isinstance(event, dict):
            if has_completed_header:
                invalid_terminal = True
            continue
        payload_type = event.get("type")
        if "type" in event and not isinstance(payload_type, str):
            if has_completed_header:
                invalid_terminal = True
            continue
        if payload_type in SSE_TERMINAL_FAILURE_EVENTS:
            invalid_terminal = True
            continue
        if current_event and "type" in event and current_event != payload_type:
            if has_completed_header:
                invalid_terminal = True
            continue
        event_type = current_event or payload_type
        if event_type == "response.completed":
            nested_response = event.get("response")
            nested_is_completed = (
                isinstance(nested_response, dict)
                and nested_response.get("status") == "completed"
                and _has_valid_response_status(nested_response)
            )
            completed_text, valid = _parse_response_json(nested_response)
            if nested_is_completed and valid:
                has_completed = True
            else:
                invalid_terminal = True
            continue
        if event_type == "response.output_text.done":
            text = event.get("text")
            if isinstance(text, str):
                has_done = True
                _append_sse_event_text(done_chunks, done_coordinates, event, text)
            continue
        if event_type == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                has_delta = True
                delta_chunks.append(delta)
            continue
        if event_type == "response.output_item.done":
            text, valid = _parse_sse_done_event_text(event, event_type)
            if valid:
                has_item_done = True
                _append_sse_event_text(
                    item_done_chunks, item_done_coordinates, event, text
                )
            continue
        if event_type == "response.content_part.done":
            text, valid = _parse_sse_done_event_text(event, event_type)
            if valid:
                has_part_done = True
                _append_sse_event_text(
                    part_done_chunks, part_done_coordinates, event, text
                )
    if invalid_terminal:
        return "", False
    if has_done:
        return "".join(done_chunks), True
    if has_completed:
        return completed_text, True
    if has_item_done:
        return "".join(item_done_chunks), True
    if has_part_done:
        return "".join(part_done_chunks), True
    if has_delta:
        return "".join(delta_chunks), True
    return "", False


def _sse_records(  # pragma: no cover - SSE parser
    response_text: str,
) -> Iterator[tuple[str, str]]:
    current_event = ""
    data_lines: list[str] = []
    for raw_line in io.StringIO(response_text):
        line = raw_line.rstrip("\r\n")
        if not line.strip():
            if data_lines or current_event:
                yield current_event, "\n".join(data_lines)
            current_event = ""
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if not separator:
            value = ""
        elif value.startswith(" "):
            value = value[1:]
        if field == "event":
            current_event = value
        elif field == "data":
            data_lines.append(value)
    if data_lines or current_event:
        yield current_event, "\n".join(data_lines)


def _sse_event_coordinate(  # pragma: no cover - SSE parser
    event: dict[str, Any],
) -> tuple[tuple[str, str | int], ...] | None:
    coordinate_values: dict[str, str | int] = {}
    for field in ("item_id", "output_index", "content_index"):
        value = event.get(field)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            coordinate_values[field] = value
    item = event.get("item")
    if "item_id" not in coordinate_values and isinstance(item, dict):
        item_id = item.get("id")
        if isinstance(item_id, str):
            coordinate_values["item_id"] = item_id
    if not coordinate_values:
        sequence_number = event.get("sequence_number")
        if isinstance(sequence_number, int) and not isinstance(sequence_number, bool):
            coordinate_values["sequence_number"] = sequence_number
    coordinate = tuple(
        (field, coordinate_values[field])
        for field in ("item_id", "output_index", "content_index", "sequence_number")
        if field in coordinate_values
    )
    return coordinate or None


def _append_sse_event_text(  # pragma: no cover - SSE parser
    chunks: list[str],
    seen_coordinates: set[tuple[tuple[str, str | int], ...]],
    event: dict[str, Any],
    text: str,
) -> None:
    coordinate = _sse_event_coordinate(event)
    if coordinate is not None:
        if coordinate in seen_coordinates:
            return
        seen_coordinates.add(coordinate)
    chunks.append(text)


def _text_from_sse_done_event(
    event: Any,
) -> str:  # pragma: no cover - SSE parser
    event_type = event.get("type", "") if isinstance(event, dict) else ""
    text, _ = _parse_sse_done_event_text(event, event_type)
    return text


def _parse_sse_done_event_text(  # pragma: no cover - SSE parser
    event: Any, event_type: str
) -> tuple[str, bool]:
    if not isinstance(event, dict):
        return "", False
    if event_type in {"", "response.content_part.done"}:
        part = event.get("part")
        text, valid = _parse_output_text_content(part)
        if valid:
            return text, True
        if event_type == "response.content_part.done":
            return "", False
    item = event.get("item")
    if not isinstance(item, dict):
        return "", False
    if not _has_valid_response_status(item):
        return "", False
    if "type" in item and item.get("type") != "message":
        return "", False
    content_items = item.get("content")
    if not isinstance(content_items, list):
        return "", False
    return _parse_output_text_contents(content_items)


def _response_text(
    response_json: Any,
) -> str:  # pragma: no cover - AI response parsing
    text, _ = _parse_response_json(response_json)
    return text


def _has_valid_response_status(response_json: dict[str, Any]) -> bool:
    status_is_valid = (
        "status" not in response_json or response_json["status"] == "completed"
    )
    has_failure_details = (
        response_json.get("error") is not None
        or response_json.get("incomplete_details") is not None
    )
    return status_is_valid and not has_failure_details


def _parse_output_text_content(content: Any) -> tuple[str, bool]:
    if not isinstance(content, dict):
        return "", False
    if not _has_valid_response_status(content):
        return "", False
    if "type" in content and content.get("type") != "output_text":
        return "", False
    text = content.get("text")
    if not isinstance(text, str):
        return "", False
    return text, True


def _parse_output_text_contents(content_items: list[Any]) -> tuple[str, bool]:
    chunks: list[str] = []
    valid_content = False
    for content in content_items:
        text, valid = _parse_output_text_content(content)
        if not valid:
            continue
        valid_content = True
        chunks.append(text)
    return "".join(chunks), valid_content


def _parse_response_json(  # pragma: no cover - AI response parsing
    response_json: Any,
) -> tuple[str, bool]:
    if not isinstance(response_json, dict):
        return "", False
    if not _has_valid_response_status(response_json):
        return "", False
    if "output_text" in response_json:
        output_text = response_json.get("output_text")
        if isinstance(output_text, str):
            return output_text, True
        return "", False
    if "output" not in response_json:
        return "", False
    output_items = response_json.get("output")
    if not isinstance(output_items, list):
        return "", False
    if not output_items:
        return "", True
    chunks: list[str] = []
    valid_output = False
    for output in output_items:
        if not isinstance(output, dict):
            continue
        if not _has_valid_response_status(output):
            return "", False
        if "type" in output and output.get("type") != "message":
            continue
        content_items = output.get("content")
        if not isinstance(content_items, list):
            continue
        text, valid_content = _parse_output_text_contents(content_items)
        if valid_content:
            chunks.append(text)
        valid_output |= valid_content
    return "".join(chunks), valid_output


def _always_fel_path_for(source_path: Path) -> Path:
    return source_path.with_name("sources_always_fel.txt")
