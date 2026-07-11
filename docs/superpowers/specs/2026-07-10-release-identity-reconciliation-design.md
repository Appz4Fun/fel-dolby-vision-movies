# Release Identity Reconciliation Design

**Date:** 2026-07-10

**Status:** Approved for implementation

## Problem

The daily refresh branch currently contains 1,274 releases versus 1,107 on
`main`. It contains 94 `release_date="Unknown"` rows. Of those, 89 have exactly
one same-title dated record on `main`, `Scream` has two dated records on `main`,
and four use unmatched title variants (`Divergent`, `Evil Dead II`, `F9`, and
`Schindlers List`). The existing PR summary therefore reports many catalog
entries as new even though they are rediscoveries.

This happens because the pipeline uses normalized title plus year as its
fallback identity. An always-FEL Google Sheet row or AI candidate without a
year becomes `(title, "")`; enrichment deliberately refuses to guess a TMDB
movie for it; and it never obtains a TMDB ID, IMDb ID, or Blu-ray URL. The row
therefore survives publication and is disjoint from the dated catalog entry in
`release_delta.py`.

AI extraction is not itself a new-release detector. It extracts every supported
FEL entry it sees in a source, including old catalog entries. Newness must be a
deterministic comparison against the current catalog after extraction. Prompting
the model to remember or suppress existing titles is neither complete nor
stable enough to be the correctness boundary.

## Goals

1. Use one deterministic identity implementation for Python publication, AI
   publication, and refresh-PR delta calculation.
2. Merge a yearless candidate into a dated release only when the target is
   unambiguous.
3. Never publish or count an unresolved or ambiguous yearless candidate as a
   new release.
4. Preserve distinct remakes, cuts, editions, seasons, and discs.
5. Keep deterministic FEL evidence when AI rediscovers an existing release.
6. Require AI output to carry source-backed evidence and a source-backed year.
7. Retain rejected candidates in CI review artifacts so suppression is
   observable rather than silent.
8. Prove the fix against synthetic edge cases and the current
   `origin/daily-fel-refresh` dataset.

## Non-goals

- Do not ask the AI model to decide catalog newness.
- Do not use fuzzy title matching or popularity-ranked TMDB guesses as an
  identity guarantee.
- Do not hand-edit `data/releases.json` or the refresh branch.
- Do not collapse different physical editions solely because they share a
  TMDB or IMDb movie ID.
- Do not add release groups to public output.

## Architecture

### Shared reconciler

Create `src/reconcile.py` as the only component allowed to classify incoming
rows as an existing release, a genuinely new release, or a review-only
candidate. It will expose:

```python
@dataclass(frozen=True)
class ReviewItem:
    release: FelRelease
    reason: str
    candidate_titles: tuple[str, ...] = ()


@dataclass
class ReconciliationResult:
    releases: list[FelRelease]
    additions: list[FelRelease]
    review_items: list[ReviewItem]
    merged_count: int


def reconcile_releases(
    existing: Iterable[FelRelease],
    incoming: Iterable[FelRelease],
) -> ReconciliationResult: ...
```

The function processes incoming rows in stable input order and never mutates
the caller's lists. Existing rows remain the authority. When an incoming row
matches one existing row, `merge_releases(existing, incoming)` is used so the
existing deterministic evidence wins over AI evidence under the invariant
already enforced in `src/merge.py`.

### Match hierarchy

The reconciler applies the following tiers in order:

1. Canonical Blu-ray URL. A single exact URL match identifies the physical
   release.
2. TMDB or IMDb ID. A single matching catalog row is usable. If a movie ID
   matches multiple physical editions, canonical title/year and Blu-ray URL may
   narrow it; otherwise the candidate is review-only.
3. Canonical title plus year. Full dates and bare years compare by extracted
   four-digit year.
4. Yearless canonical title. This tier is allowed only when exactly one dated
   catalog row has that canonical title.

Canonical URLs ignore query strings, fragments, trailing slashes, host case,
and scheme differences. Canonical titles use the repository's existing
Unicode, punctuation, case, and whitespace normalization. Apostrophes are
removed rather than converted to a token boundary, so `Schindler's` and
`Schindlers` compare identically.

No fuzzy or substring tier is added. Consequently, unmatched variants such as
`F9` remain visible in review output but cannot become false public additions.

### Classification rules

- A unique match is merged into the existing target and is not an addition.
- A known-year row with no match is a genuine addition and is published.
- A yearless row with no match is excluded with reason `missing-year-no-match`.
- A yearless row with more than one possible target is excluded with reason
  `ambiguous-yearless-title`.
- Conflicting strong identifiers are excluded with reason `identity-conflict`.
- A candidate that matches multiple physical editions without a decisive
  Blu-ray URL is excluded with reason `ambiguous-edition`.

Review-only rows are absent from `ReconciliationResult.releases` and
`ReconciliationResult.additions`. This makes the publication and PR-newness
contracts agree by construction.

### Artifact publication

`artifacts.write_artifacts` loads existing JSON, normalizes both sides, invokes
the reconciler, and then runs the existing canonical/Blu-ray/TMDB dedupe passes
as defensive cleanup. `publish_outputs` accepts an optional
`review_output_path`. When supplied, it writes a stable JSON object containing
counts and each review item's reason, candidate, and possible target titles.

The deterministic `run` command and `ai-scrape` command each gain an optional
`--review-output PATH` argument. The scheduled workflow writes separate Python
and AI review files beneath `$RUNNER_TEMP` and uploads them as a
`fel-reconciliation-review` workflow artifact. Review files are diagnostic and
are never committed or published by Pages.

### AI extraction validation

The extraction prompt will say that:

- `evidence` must be an exact source excerpt linking the named release to
  Profile 7 FEL;
- `year` must appear in that excerpt and must not be inferred from model
  knowledge;
- missing years must be returned as `Unknown`;
- ambiguous, MEL-only, and generic REMUX statements are excluded.

The Python boundary remains authoritative. Before converting a
`FoundCandidate` to `FelRelease`, it will:

1. require a non-empty title and evidence excerpt;
2. normalize HTML entities, tags, case, and whitespace and verify that the
   evidence excerpt occurs in the fetched source text;
3. accept a year only when it is a four-digit 19xx/20xx year present in the
   evidence; otherwise normalize it to `Unknown`.

Unsupported AI candidates are skipped with non-secret aggregate diagnostics.
Valid but unresolved yearless candidates proceed to reconciliation and appear
in the review artifact.

### PR delta calculation

`release_delta.added_releases(base, head)` invokes the same reconciler with the
base as existing and the head as incoming, then returns only
`result.additions`. This replaces its private identity implementation. A
yearless rediscovery can therefore never be suppressed during publication but
reappear as a PR addition, or vice versa.

## Data flow

```text
deterministic parsers ----\
                          +--> enrichment --> shared reconciliation --> releases.json
AI extraction + validation/                                  |              |
                                                             |              +--> PR delta
                                                             +--> review JSON artifact
```

The deterministic run publishes first. AI extraction then loads that published
catalog as its existing side, enriches only AI candidates, and invokes the same
reconciler. Existing evidence and metadata remain authoritative.

## Testing strategy

### Unit tests

- unique yearless title merges into the dated row;
- punctuation and apostrophe variants merge;
- `Scream | Unknown` is quarantined when 1996 and 2022 exist;
- unmatched `F9 | Unknown` is quarantined rather than added;
- same TMDB ID across two editions is ambiguous without a Blu-ray URL;
- exact Blu-ray URL selects one edition;
- known-year new title is published as an addition;
- conflicting TMDB/IMDb data is quarantined;
- deterministic evidence survives either AI/existing input order;
- review JSON is stable and includes reasons and possible targets;
- AI evidence must be a normalized substring of the source;
- AI years absent from evidence become `Unknown`;
- missing AI evidence is rejected.

### Integration tests

- `artifacts.write_artifacts` removes the duplicate `Unknown` row while
  retaining the existing dated metadata and evidence;
- `release_delta.added_releases` reports the same classification;
- CLI arguments reach deterministic and AI publication paths;
- the workflow writes and uploads both review files without staging them;
- existing distinct-edition tests remain green.

### Current-data regression

Run the reconciler with `data/releases.json` from `origin/main` as the existing
catalog and `data/releases.json` from `origin/daily-fel-refresh` as incoming.
The verification must prove:

- zero `Unknown` rows are classified as additions;
- all 94 current `Unknown` rows are either merged or review-only;
- the dated base rows remain present;
- no new duplicate normalized title/year identities are emitted.

Finally run `just ci`, which covers Ruff lint/format, the full pytest suite with
100% coverage, and the parser benchmark.

## Safety and observability

- Review artifacts contain public release metadata and source URLs only; no
  environment values, API keys, cookies, fetched HTML, or credentials.
- Reconciliation reports aggregate merged, added, and review counts in CLI
  output.
- The workflow still opens or updates a refresh PR only when dated,
  publication-safe additions exist.
- Failures writing an explicitly requested review file fail the command rather
  than silently discarding diagnostics.

## Acceptance criteria

1. The current 94 yearless refresh rows produce zero false additions.
2. `Scream` remains two dated movies and gains no yearless third row.
3. Existing deterministic evidence cannot be replaced by AI evidence.
4. A genuinely new, source-supported, known-year AI result remains publishable.
5. AI output without exact evidence or a source-backed year cannot bypass the
   Python gate.
6. Python publication and PR delta tests demonstrate identical classification.
7. Review artifacts are uploaded and are not committed.
8. `just ci` passes with 100% coverage and the benchmark green.
