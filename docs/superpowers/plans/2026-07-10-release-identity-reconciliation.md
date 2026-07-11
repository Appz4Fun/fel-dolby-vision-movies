# Release Identity Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop deterministic and AI refreshes from publishing or reporting already-cataloged yearless movies as new while quarantining every identity ambiguity for review.

**Architecture:** Add a shared `reconcile.py` classification boundary used by artifact publication and PR delta calculation. Strengthen the AI boundary with source-backed evidence/year validation, then expose separate deterministic and AI review JSON files through the scheduled workflow without committing them.

**Tech Stack:** Python 3.13+, dataclasses, pytest, Ruff, GitHub Actions, `uv`, and `just`.

---

## File map

- Create `src/reconcile.py`: release matching, classification, and review JSON.
- Create `tests/test_reconcile.py`: exhaustive unit tests for identity decisions.
- Modify `src/merge.py`: canonical apostrophe/URL normalization and public edition-descriptor helper.
- Modify `tests/test_merge.py`: canonicalization regressions.
- Modify `src/artifacts.py`: shared reconciliation before defensive dedupe.
- Modify `tests/test_artifacts.py`: publication and review-output integration.
- Modify `src/release_delta.py`: use reconciler additions rather than a private identity set.
- Modify `tests/test_release_delta.py`: yearless and edition delta regressions.
- Modify `src/compare.py`: stricter AI instructions and candidate payload validation.
- Modify `src/ai_scrape.py`: source-backed AI validation and reconciled publication.
- Modify `tests/test_compare.py` and `tests/test_ai_scrape.py`: AI prompt, evidence, year, and merge regressions.
- Modify `src/main.py` and `tests/test_main.py`: `--review-output` plumbing.
- Modify `.github/workflows/pages.yml` and `tests/test_workflows.py`: create and upload review artifacts.
- Create `tests/fixtures/yearless_duplicate_cases.json`: compact PR #29/current-refresh regression corpus.

### Task 1: Build the shared release reconciler

**Files:**
- Create: `src/reconcile.py`
- Create: `tests/test_reconcile.py`
- Modify: `src/merge.py:41-70`
- Modify: `tests/test_merge.py`

- [ ] **Step 1: Write canonicalization regressions**

Add tests proving apostrophes do not create a token boundary and URL schemes,
queries, fragments, and trailing slashes do not create different identities:

```python
from merge import canonical_title_key, canonical_url_key


def test_canonical_title_key_collapses_possessive_apostrophes():
    assert canonical_title_key("Schindler's List") == canonical_title_key(
        "Schindlers List"
    )


def test_canonical_url_key_ignores_transport_and_tracking_parts():
    left = "http://WWW.BLU-RAY.COM/movies/Alien/123/?ref=list#details"
    right = "https://www.blu-ray.com/movies/Alien/123"
    assert canonical_url_key(left) == canonical_url_key(right)
```

- [ ] **Step 2: Run the canonicalization tests and verify RED**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_merge.py -q
```

Expected: both new assertions fail against the current normalizers.

- [ ] **Step 3: Make canonicalization deterministic**

In `src/merge.py`, remove apostrophes before other punctuation and normalize
valid HTTP(S) URLs to an HTTPS host/path identity. Rename
`_has_edition_descriptor` to `has_edition_descriptor` and update its internal
callers so reconciliation can share the physical-edition rule:

```python
def canonical_title_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    normalized = normalized.casefold().replace("&", " and ")
    normalized = re.sub(r"['`´’]", "", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def canonical_url_key(value: str) -> str:
    parsed = urllib.parse.urlparse(value.strip())
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        return ""
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunparse(("https", parsed.hostname.lower(), path, "", "", ""))
```

- [ ] **Step 4: Write failing reconciler behavior tests**

Create `tests/test_reconcile.py` with a small `release(...)` factory and these
independent behaviors:

```python
def test_unique_yearless_title_merges_into_dated_catalog_row():
    base = release("Atomic Blonde", "2017-07-26", tmdb_id="341013")
    candidate = release("Atomic Blonde", "Unknown", evidence_type="google-sheet-list")
    result = reconcile_releases([base], [candidate])
    assert len(result.releases) == 1
    assert result.releases[0].release_date == "2017-07-26"
    assert result.additions == []
    assert result.review_items == []
    assert result.merged_count == 1


def test_yearless_remake_title_is_review_only():
    result = reconcile_releases(
        [release("Scream", "1996"), release("Scream", "2022")],
        [release("Scream", "Unknown")],
    )
    assert [item.release_date for item in result.releases] == ["1996", "2022"]
    assert result.additions == []
    assert result.review_items[0].reason == "ambiguous-yearless-title"


def test_unmatched_yearless_title_is_review_only():
    result = reconcile_releases([], [release("F9", "Unknown")])
    assert result.releases == []
    assert result.additions == []
    assert result.review_items[0].reason == "missing-year-no-match"


def test_known_year_without_match_is_a_real_addition():
    candidate = release("New Film", "2026")
    result = reconcile_releases([], [candidate])
    assert result.releases == [candidate]
    assert result.additions == [candidate]


def test_exact_bluray_url_selects_one_of_two_editions():
    theatrical = release(
        "Movie: Theatrical Edition", "2000", tmdb_id="1",
        bluray_url="https://www.blu-ray.com/movies/Movie/1/",
    )
    extended = release(
        "Movie: Extended Edition", "2000", tmdb_id="1",
        bluray_url="https://www.blu-ray.com/movies/Movie-Extended/2/",
    )
    candidate = release(
        "Movie", "Unknown", tmdb_id="1",
        bluray_url="http://www.blu-ray.com/movies/Movie-Extended/2/?src=list",
    )
    result = reconcile_releases([theatrical, extended], [candidate])
    assert result.review_items == []
    assert result.merged_count == 1
    assert len(result.releases) == 2


def test_movie_id_without_edition_identity_is_review_only():
    result = reconcile_releases(
        [
            release("Movie: Theatrical Edition", "2000", tmdb_id="1", bluray_url="https://disc/1"),
            release("Movie: Extended Edition", "2000", tmdb_id="1", bluray_url="https://disc/2"),
        ],
        [release("Movie", "Unknown", tmdb_id="1")],
    )
    assert result.review_items[0].reason == "ambiguous-edition"


def test_conflicting_strong_ids_are_review_only():
    result = reconcile_releases(
        [
            release("One", "2001", tmdb_id="1", imdb_id="tt0000001"),
            release("Two", "2002", tmdb_id="2", imdb_id="tt0000002"),
        ],
        [release("One", "2001", tmdb_id="1", imdb_id="tt0000002")],
    )
    assert result.review_items[0].reason == "identity-conflict"


def test_distinct_known_year_edition_remains_an_addition():
    base = release(
        "Avatar", "2009", tmdb_id="19995",
        bluray_url="https://www.blu-ray.com/movies/Avatar/1/",
    )
    extended = release(
        "Avatar: Extended Collector's Edition", "2009", tmdb_id="19995",
        bluray_url="https://www.blu-ray.com/movies/Avatar-Extended/2/",
    )
    result = reconcile_releases([base], [extended])
    assert result.additions == [extended]
    assert len(result.releases) == 2
```

Also assert that merging an AI candidate into deterministic evidence keeps the
deterministic `FelEvidence` in either encounter order.

Add existing-catalog sanitation cases:

```python
def test_existing_yearless_row_is_reconciled_before_incoming():
    result = reconcile_releases(
        [release("Atomic Blonde", "2017"), release("Atomic Blonde", "Unknown")],
        [],
    )
    assert [(item.movie_title, item.release_date) for item in result.releases] == [
        ("Atomic Blonde", "2017")
    ]


def test_existing_unmatched_yearless_row_is_quarantined():
    result = reconcile_releases([release("F9", "Unknown")], [])
    assert result.releases == []
    assert result.review_items[0].reason == "missing-year-no-match"
```

Add URL-match and title/year-match cases with conflicting nonblank TMDB/IMDb
IDs, and a same-title/year case with two different non-empty Blu-ray URLs. The
latter must remain two releases. Extend the existing TMDB-dedupe tests so
different-title AKA rows may collapse but same-title rows with different URLs
do not.

- [ ] **Step 5: Run reconciler tests and verify RED**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_reconcile.py tests/test_merge.py -q
```

Expected: import failure for the not-yet-created `reconcile` module after the
canonicalization tests pass.

- [ ] **Step 6: Implement `src/reconcile.py` minimally**

Implement `ReviewItem`, `ReconciliationResult`, `reconcile_releases`, and
private helpers. Build the initial catalog from dated existing rows first,
classify existing yearless rows second, then process incoming rows with this
decision sequence:

```python
for candidate in incoming:
    decision = _match_candidate(candidate, catalog)
    if decision.reason:
        review_items.append(
            ReviewItem(candidate, decision.reason, decision.candidate_titles)
        )
    elif decision.index is not None:
        catalog[decision.index] = merge_releases(catalog[decision.index], candidate)
        merged_count += 1
    elif not _year(candidate.release_date):
        review_items.append(ReviewItem(candidate, "missing-year-no-match"))
    else:
        catalog.append(candidate)
        additions.append(candidate)
```

Before every match tier, reject different nonblank TMDB IDs, IMDb IDs, or
canonical Blu-ray URLs as `identity-conflict`; URL-first must never override an
ID conflict. Matching then uses exact canonical Blu-ray URL, consistent
TMDB/IMDb sets, canonical title/year, and unique canonical title for yearless
candidates. Different non-empty Blu-ray URLs remain distinct even when
title/year or movie ID matches. A known-year candidate with a distinct URL is
an addition; a yearless candidate without a decisive URL target is
`ambiguous-edition`.

- [ ] **Step 7: Verify GREEN and commit**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_reconcile.py tests/test_merge.py -q
git diff --check
git add src/reconcile.py src/merge.py tests/test_reconcile.py tests/test_merge.py
git commit -S -m "feat: reconcile release identities"
```

Expected: targeted tests pass and the commit has a valid `xbmc4lyfe` SSH
signature.

### Task 2: Make publication and PR deltas share reconciliation

**Files:**
- Modify: `src/artifacts.py`
- Modify: `src/release_delta.py`
- Modify: `tests/test_artifacts.py`
- Modify: `tests/test_release_delta.py`

- [ ] **Step 1: Write failing artifact integration tests**

Add tests that seed `data/releases.json` with a dated deterministic row and
publish a yearless Google Sheet or AI rediscovery:

```python
def test_write_artifacts_reconciles_yearless_candidate_with_existing(tmp_path):
    existing = release("Atomic Blonde", "2017-07-26")
    write_artifacts([existing], output_dir=tmp_path)
    duplicate = release("Atomic Blonde", "Unknown")
    duplicate.fel_evidence = FelEvidence(
        source_url="https://sheet.test/list",
        quote="Atomic Blonde is FEL",
        evidence_type="google-sheet-list",
    )
    written = write_artifacts([duplicate], output_dir=tmp_path)
    assert [(item.movie_title, item.release_date) for item in written] == [
        ("Atomic Blonde", "2017-07-26")
    ]


def test_write_artifacts_writes_review_json_for_ambiguous_yearless_row(tmp_path):
    review_path = tmp_path / "review.json"
    written = write_artifacts(
        [release("Scream", "Unknown")],
        output_dir=tmp_path,
        review_output_path=review_path,
    )
    payload = json.loads(review_path.read_text(encoding="utf-8"))
    assert written == []
    assert payload["review_count"] == 1
    assert payload["items"][0]["reason"] == "missing-year-no-match"
```

Add a second review test with two dated `Scream` rows already in JSON and
assert `candidate_titles` is stable and sorted in catalog order.
Update the existing artifact test that currently expects an unmatched
`Unknown Date` row to remain public: it must now assert that the row is omitted
and represented in the review JSON. Add a test starting with an already-written
dated row plus an already-written yearless duplicate and assert the stale row
is removed on the next write.

- [ ] **Step 2: Write failing PR delta tests**

Add:

```python
def test_added_releases_does_not_count_unique_yearless_rediscovery():
    base = [release("Atomic Blonde", "2017")]
    head = [*base, release("Atomic Blonde", "Unknown")]
    assert release_delta.added_releases(base, head) == []


def test_added_releases_does_not_count_ambiguous_yearless_rediscovery():
    base = [release("Scream", "1996"), release("Scream", "2022")]
    head = [*base, release("Scream", "Unknown")]
    assert release_delta.added_releases(base, head) == []


def test_added_releases_keeps_known_year_new_title():
    alien = release("Alien", "1979")
    assert release_delta.added_releases([], [alien]) == [alien]
```

- [ ] **Step 3: Run integration tests and verify RED**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_artifacts.py tests/test_release_delta.py -q
```

Expected: the artifact API rejects `review_output_path` and yearless delta
tests fail under the private key-set implementation.

- [ ] **Step 4: Integrate the shared reconciler**

Change `write_artifacts` and `publish_outputs` to accept
`review_output_path: Path | None = None`. After title normalization and stale
sheet filtering, call:

```python
result = reconcile_releases(existing, releases)
merged = dedupe_releases(result.releases, title_bluray_key)
merged = dedupe_tmdb_releases(merged)
if review_output_path is not None:
    write_review_output(review_output_path, result)
```

Implement deterministic JSON serialization in `src/reconcile.py` with this
top-level shape:

```json
{
  "merged_count": 1,
  "addition_count": 0,
  "review_count": 1,
  "items": [
    {
      "reason": "ambiguous-yearless-title",
      "candidate": {},
      "candidate_titles": ["Scream", "Scream"]
    }
  ]
}
```

Do not run a canonical title/year-only pass before URL-aware cleanup. Confirm
the adjusted `dedupe_tmdb_releases` from Task 1 cannot collapse same-title rows
with different non-empty Blu-ray URLs.

Keep `write_artifacts` returning its existing list API. Factor an internal
helper that also returns the `ReconciliationResult`; `publish_outputs` uses it
to print:

```text
reconciliation complete; merged=<n> additions=<n> review=<n>
```

Add a `capsys` assertion proving this aggregate line is emitted even when no
review path is requested.

Replace `release_delta.added_releases` with:

```python
def added_releases(base_releases, head_releases):
    return reconcile_releases(base_releases, head_releases).additions
```

Delete the now-private duplicate identity-set helpers and unused imports.

- [ ] **Step 5: Verify GREEN and commit**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_reconcile.py tests/test_artifacts.py tests/test_release_delta.py -q
git diff --check
git add src/artifacts.py src/reconcile.py src/release_delta.py tests/test_artifacts.py tests/test_release_delta.py tests/test_reconcile.py
git commit -S -m "fix: unify publication and release deltas"
```

Expected: all targeted tests pass and the signed author/committer is
`xbmc4lyfe`.

### Task 3: Harden AI extraction and reconcile AI publication

**Files:**
- Modify: `src/compare.py`
- Modify: `src/ai_scrape.py`
- Modify: `tests/test_compare.py`
- Modify: `tests/test_ai_scrape.py`

- [ ] **Step 1: Write failing prompt and payload tests**

Extend `tests/test_compare.py`:

```python
def test_ai_prompt_requires_exact_source_backed_evidence_and_year():
    prompt = compare.AI_EXTRACTION_SYSTEM_PROMPT
    assert "exact source excerpt" in prompt
    assert "must appear in the evidence excerpt" in prompt
    assert "never infer" in prompt


def test_ai_payload_parser_rejects_blank_evidence():
    payload = json.dumps({"items": [{"title": "Alien", "year": "1979", "evidence": ""}]})
    assert compare._candidates_from_payload_text(payload, "https://source.test") == []
```

- [ ] **Step 2: Write failing source-validation tests**

Extend `tests/test_ai_scrape.py` with real `FoundCandidate` objects:

```python
def test_ai_extract_releases_requires_evidence_from_source_text():
    client = FakeAIClient(candidates=[
        FoundCandidate("Alien", "1979", "https://src.test", "Alien is Profile 7 FEL", "ai")
    ])
    assert ai_extract_releases(client, [("https://src.test", "Unrelated page")]) == []


def test_ai_extract_releases_accepts_html_normalized_exact_evidence():
    client = FakeAIClient(candidates=[
        FoundCandidate("Alien", "1979", "https://src.test", "Alien (1979) is Profile 7 FEL", "ai")
    ])
    releases = ai_extract_releases(
        client,
        [("https://src.test", "<p>Alien&nbsp;(1979) is <b>Profile 7 FEL</b></p>")],
    )
    assert [(item.movie_title, item.release_date) for item in releases] == [("Alien", "1979")]


def test_ai_year_not_present_in_evidence_becomes_unknown():
    client = FakeAIClient(candidates=[
        FoundCandidate("Alien", "1979", "https://src.test", "Alien is Profile 7 FEL", "ai")
    ])
    releases = ai_extract_releases(
        client,
        [("https://src.test", "Alien is Profile 7 FEL")],
    )
    assert releases[0].release_date == "Unknown"


def test_ai_extract_releases_requires_title_and_fel_marker_in_evidence():
    candidates = [
        FoundCandidate("Alien", "1979", "https://src.test", "Heat (1995) is FEL", "ai"),
        FoundCandidate("Alien", "1979", "https://src.test", "Alien (1979) is a remux", "ai"),
    ]
    client = FakeAIClient(candidates=candidates)
    assert ai_extract_releases(client, [("https://src.test", "Heat (1995) is FEL\nAlien (1979) is a remux")]) == []
```

- [ ] **Step 3: Run AI tests and verify RED**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_compare.py tests/test_ai_scrape.py -q
```

Expected: prompt assertions fail, blank evidence is accepted, unsupported
evidence is converted, and non-source-backed years are retained.

- [ ] **Step 4: Implement the AI validation boundary**

Update the prompt to require exact evidence, explicit source-backed years, and
no inference. In `_candidates_from_payload_text`, require both normalized title
and nonblank evidence.

In `src/ai_scrape.py`, normalize source and evidence with `html.unescape`, HTML
tag removal, case folding, punctuation normalization through
`canonical_title_key`, and whitespace collapse. Validate one local normalized
source row/line/block at a time. A candidate is supported only when the complete
normalized title phrase matches on token boundaries and that same excerpt
contains an affirmative `FEL` marker:

```python
title_phrase = f" {canonical_title_key(candidate.title)} "
evidence_phrase = f" {canonical_title_key(candidate.evidence)} "
supported = (
    normalized_evidence in normalized_source
    and title_phrase in evidence_phrase
    and re.search(r"\bfel\b", normalized_evidence)
)
```

Retain a candidate year only when it fully matches `(?:19|20)\d{2}` and the
same year appears in its evidence; otherwise replace it with `Unknown` before
conversion. Reject evidence that borrows the title, year, or FEL marker from a
different row/block. `P7 MEL`, `Profile 7 MEL`, and generic REMUX without an
affirmative FEL marker must fail. Print only aggregate rejection counts by
reason. Add adversarial tests for `Up` inside `setup`, MEL-only evidence, and a
multi-release excerpt whose FEL marker belongs to another title.

Remove AI's pre-publication `dedupe_releases([*existing, *unique_ai], ...)`
merge. Call `artifacts.publish_outputs(unique_ai, ...)` directly so the shared
artifact reconciler owns all existing-vs-incoming decisions exactly once.
The current `run_ai_scrape` entrypoint is marked `# pragma: no cover`; extract
its existing-load/enrich/publish orchestration into a small covered helper (or
remove that pragma) and test the helper with a temporary catalog so the new
reconciliation forwarding is actually covered.

- [ ] **Step 5: Add AI publication regression tests**

Test the callable publication seam (factor a small non-live helper from
`run_ai_scrape` if necessary) to prove:

```python
# Existing deterministic Atomic Blonde + AI Atomic Blonde Unknown => one dated row.
# Existing deterministic evidence remains selected.
# New AI title with exact evidence and a source-backed year => one addition.
# Scream Unknown against two catalog years => review-only.
```

Use monkeypatched enrichment and temporary JSON; do not call a live API.

- [ ] **Step 6: Verify GREEN and commit**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_compare.py tests/test_ai_scrape.py tests/test_artifacts.py tests/test_reconcile.py -q
git diff --check
git add src/ai_scrape.py src/compare.py tests/test_ai_scrape.py tests/test_compare.py
git commit -S -m "fix: validate and reconcile AI extraction"
```

Expected: targeted tests pass and the commit is signed by `xbmc4lyfe`.

### Task 4: Expose reconciliation review files in CLI and CI

**Files:**
- Modify: `src/main.py`
- Modify: `src/ai_scrape.py`
- Modify: `tests/test_main.py`
- Modify: `.github/workflows/pages.yml`
- Modify: `tests/test_workflows.py`

- [ ] **Step 1: Write failing CLI-plumbing tests**

Add tests that monkeypatch `_scrape_for_titles` and `run_ai_scrape`, invoke the
CLI with `--review-output`, and assert exact `Path` propagation:

```python
def test_run_passes_review_output_to_publication(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(main, "_search_for_sources", lambda _: 0)
    monkeypatch.setattr(
        main,
        "_scrape_for_titles",
        lambda *args, **kwargs: captured.update(kwargs) or 0,
    )
    review = tmp_path / "python-review.json"
    assert main.main(["run", "--review-output", str(review)]) == 0
    assert captured["review_output_path"] == review


def test_ai_scrape_passes_review_output_to_runner(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(
        main.ai_scrape,
        "run_ai_scrape",
        lambda *args, **kwargs: captured.update(kwargs) or 0,
    )
    review = tmp_path / "ai-review.json"
    assert main.main(["ai-scrape", "--review-output", str(review)]) == 0
assert captured["review_output_path"] == review
```

Also assert deterministic and AI CLI output contains the aggregate
`merged=... additions=... review=...` reconciliation line emitted by their
publication call. Cover the `scrape-for-titles` command separately. Add a
non-live test of the extracted AI orchestration helper that creates the review
file and calls publication with a temporary catalog; do not leave the entire
`run_ai_scrape` function under `# pragma: no cover`.

- [ ] **Step 2: Write failing workflow-shape test**

Add a workflow test that asserts:

```python
workflow = Path(".github/workflows/pages.yml").read_text(encoding="utf-8")
assert '--review-output "$RUNNER_TEMP/python-reconciliation-review.json"' in workflow
assert '--review-output "$RUNNER_TEMP/ai-reconciliation-review.json"' in workflow
assert "- name: Upload reconciliation review" in workflow
assert "if: always()" in workflow
assert "uses: actions/upload-artifact@v7" in workflow
assert "name: fel-reconciliation-review" in workflow
assert "if-no-files-found: ignore" in workflow
```

- [ ] **Step 3: Run CLI/workflow tests and verify RED**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_main.py tests/test_workflows.py -q
```

Expected: argparse rejects `--review-output` and the workflow markers are
missing.

- [ ] **Step 4: Thread the optional path through Python**

Add `--review-output` with `type=Path` and default `None` to
`scrape-for-titles`, `run`, and `ai-scrape`. Extend `_scrape_for_titles` and
`run_ai_scrape` with keyword argument `review_output_path: Path | None = None`
and pass it to `artifacts.publish_outputs`.

Update every affected test fake to accept the explicit keyword. Do not use
catch-all arguments where asserting the path makes the test clearer.

- [ ] **Step 5: Add the workflow review upload**

Pass separate files to the two commands and add this step after AI scraping:

```yaml
      - name: Upload reconciliation review
        if: always()
        uses: actions/upload-artifact@v7
        with:
          name: fel-reconciliation-review
          path: |
            ${{ runner.temp }}/python-reconciliation-review.json
            ${{ runner.temp }}/ai-reconciliation-review.json
          if-no-files-found: ignore
          retention-days: 14
```

The self-hosted runner already executes Node 24 actions (`checkout@v6` and
`setup-python@v6`), satisfying the current official action runtime requirement.
Do not add either review file to any `git add` command.

When `OPENAI_API_KEY`/`CODEX_API_KEY` is absent, an explicitly requested AI
review path must still receive an empty valid review JSON before the command
returns zero; test this skip path.

- [ ] **Step 6: Verify GREEN and commit**

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_main.py tests/test_workflows.py tests/test_ai_scrape.py -q
git diff --check
git add src/main.py src/ai_scrape.py tests/test_main.py .github/workflows/pages.yml tests/test_workflows.py
git commit -S -m "ci: upload release reconciliation reviews"
```

Expected: targeted tests pass and the commit is signed by `xbmc4lyfe`.

### Task 5: Pin the current-refresh regression and verify end to end

**Files:**
- Create: `tests/fixtures/yearless_duplicate_cases.json`
- Modify: `tests/test_reconcile.py`

- [ ] **Step 1: Add a compact current-refresh fixture**

Create JSON containing dated base rows and yearless incoming rows for:

```json
[
  {"title": "Atomic Blonde", "base_years": ["2017"], "expected": "merged"},
  {"title": "John Wick: Chapter 2", "base_years": ["2017"], "expected": "merged"},
  {"title": "Schindler's List", "incoming_title": "Schindlers List", "base_years": ["1993"], "expected": "merged"},
  {"title": "Scream", "base_years": ["1996", "2022"], "expected": "ambiguous-yearless-title"},
  {"title": "F9", "base_years": [], "expected": "missing-year-no-match"},
  {"title": "Evil Dead II", "base_years": [], "expected": "missing-year-no-match"}
]
```

- [ ] **Step 2: Write and run the fixture-driven regression**

Generate `FelRelease` rows from each case, run reconciliation, and assert that
no yearless row is an addition, merge cases retain their dated base row, and
review cases have the exact expected reason.

Run:

```bash
PYTHONPATH=src uv run --with-requirements requirements-dev.txt pytest tests/test_reconcile.py -q
```

Expected: PASS.

- [ ] **Step 3: Validate against the full current remote dataset**

Fetch both authoritative files without changing tracked data and run a one-off
Python verifier:

```bash
git fetch origin main daily-fel-refresh
git show origin/main:data/releases.json > /tmp/fel-main-releases.json
git show origin/daily-fel-refresh:data/releases.json > /tmp/fel-refresh-releases.json
PYTHONPATH=src uv run python - <<'PY'
import json
from pathlib import Path
from models import release_from_dict
from reconcile import reconcile_releases

def load(path):
    return [release_from_dict(item) for item in json.loads(Path(path).read_text())]

base = load("/tmp/fel-main-releases.json")
head = load("/tmp/fel-refresh-releases.json")
yearless = [item for item in head if item.release_date == "Unknown"]
result = reconcile_releases(base, head)
yearless_additions = [item for item in result.additions if item.release_date == "Unknown"]
assert len(yearless) == 94, len(yearless)
assert yearless_additions == []
for candidate in yearless:
    decision = reconcile_releases(base, [candidate])
    assert decision.additions == []
    assert (decision.merged_count, len(decision.review_items)) in {(1, 0), (0, 1)}
assert all(item.release_date != "Unknown" for item in result.releases)
assert {
    (item.movie_title, item.release_date) for item in base
} <= {(item.movie_title, item.release_date) for item in result.releases}
print({
    "refresh_yearless": len(yearless),
    "yearless_additions": len(yearless_additions),
    "merged": result.merged_count,
    "review": len(result.review_items),
})
PY
```

Expected: `refresh_yearless=94`, `yearless_additions=0`, `merged=90`, and
`review=4`. Record this split in the PR body.

- [ ] **Step 4: Run the full repository gate**

Run:

```bash
just ci
```

Expected: Ruff lint and format checks pass, pytest reaches 100% coverage with
zero failures, and the parser benchmark reports 3/3 cases passed.

- [ ] **Step 5: Commit the fixture and final verification adjustment**

```bash
git diff --check
git add tests/fixtures/yearless_duplicate_cases.json tests/test_reconcile.py
git commit -S -m "test: pin yearless refresh regressions"
```

Expected: signed `xbmc4lyfe` commit. If formatting during `just ci` changed any
intentional implementation file, include it only after inspecting its diff and
rerun `just ci` before committing.

### Task 6: Final review, signature audit, and PR

**Files:**
- Review all files changed from `origin/main`.

- [ ] **Step 1: Run spec-compliance and code-quality reviews**

Dispatch a fresh spec reviewer against
`docs/superpowers/specs/2026-07-10-release-identity-reconciliation-design.md`,
then a fresh code-quality reviewer against `origin/main..HEAD`. Fix every
Critical or Important issue through the task implementer and repeat the
relevant review until approved.

- [ ] **Step 2: Re-run final verification after review fixes**

```bash
just ci
git diff --check origin/main...HEAD
git status --short --branch
```

Expected: all gates pass and the worktree contains no uncommitted changes.

- [ ] **Step 3: Audit every commit identity and signature**

```bash
git log origin/main..HEAD --format='%H%x09%an%x09%ae%x09%cn%x09%ce%x09%G?'
```

Expected: every author and committer is `xbmc4lyfe` with
`273732874+xbmc4lyfe@users.noreply.github.com`, and every `%G?` is `G`.

- [ ] **Step 4: Push and open the PR as xbmc4lyfe**

```bash
gh auth switch --hostname github.com --user xbmc4lyfe
gh auth status --hostname github.com
git push -u origin feat/fix-ai-duplicate-identity
gh pr create \
  --base main \
  --head feat/fix-ai-duplicate-identity \
  --title "fix: reconcile duplicate FEL release identities" \
  --body-file /tmp/fel-release-reconciliation-pr.md
```

The PR body must include the root cause, shared reconciliation design, AI
validation boundary, full current-refresh merged/review counts, `just ci`
evidence, and validation gaps (if any). Verify the returned PR author is
`xbmc4lyfe` with `gh pr view --json author,url,headRefName,baseRefName`.
