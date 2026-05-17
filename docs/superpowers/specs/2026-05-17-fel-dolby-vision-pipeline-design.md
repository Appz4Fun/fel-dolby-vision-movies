# FEL Dolby Vision Pipeline Design

## Purpose

Build `fel-dolby-vision-movies` as a Python automation pipeline that discovers,
scrapes, validates, and publishes confirmed Dolby Vision Profile 7 FEL physical
media releases.

The project must prefer under-inclusion over overclaiming. A title is published
only when the system can prove a direct one-to-one relationship between a
specific movie or release and Profile 7 FEL evidence.

## Current Repo State

- Remote: `git@github.com:Appz4Fun/fel-dolby-vision-movies.git`
- Seed sources live in `forums.txt`.
- Local secrets live in `.env`, which is ignored.
- Fetch/debug snapshots live under `.cache/`, which is ignored.
- Brainstorming companion files live under `.superpowers/`, which is ignored.
- `BRAVE_SEARCH_API_KEY` exists as a repository Actions secret.
- Existing workflow `secret-smoke.yml` only verifies that the Brave secret is
  configured. It is not the production pipeline.
- `justfile` already exposes:
  - `just search-for-sources`
  - `just scrape-for-titles`

## Source Discovery

`forums.txt` is the committed source registry. The initial seeds are:

- `https://www.reddit.com/r/CoreELEC/comments/1jamlw6/list_of_dolby_vision_p7fel_films/`
- `https://github.com/iammarxg/FEL`
- `https://forum.blu-ray.com/showthread.php?t=276448`

The pipeline should automatically discover more candidate sources. Brave Search
is used when `BRAVE_SEARCH_API_KEY` is present. Missing Brave credentials must
disable search expansion, not fail the scraper.

Discovered URLs are not committed merely because they mention Dolby Vision,
REMUX, Blu-ray, or FEL. A new URL is added to `forums.txt` only when it produces
at least one confirmed Profile 7 FEL release under the strict correlation rules.

Discovery hits that are interesting but unconfirmed may be kept in ignored cache
or diagnostic output, but they must not churn the source registry.

## Fetching And Cache

Use `httpx` for network fetching and BeautifulSoup for tolerant HTML parsing.

Fetcher requirements:

- clear project user-agent
- bounded concurrency
- redirects and compressed responses
- explicit request timeouts
- per-domain rate limits
- retry only transient failures such as timeouts, 429, and 5xx
- capped exponential backoff
- configurable cache TTL, defaulting to 24 hours
- prefer fresh cached HTML over refetching

Raw fetched HTML snapshots may be stored under `.cache/` for debugging,
regression fixture creation, and replay. Cache files are local-only and must not
be committed.

The scraper supports one optional raw cookie header through
`FORUM_COOKIE_HEADER`, loaded from `.env` locally or GitHub Secrets in CI. This
value is treated as opaque. It must never be printed, logged, committed, or
included in generated artifacts.

## Strict FEL Correlation

The parser must distinguish page-level discussion from release-level proof.

Valid publish evidence includes:

- a dedicated release row where title and Profile 7 FEL value are structurally
  tied together
- a release-specific post block where one title is explicitly described as
  Profile 7 FEL
- a MediaInfo block tied to one release/title and explicitly indicating FEL
- a direct sentence that binds one specific movie/release to Profile 7 FEL

Invalid evidence includes:

- generic FEL mentions
- hardware capability discussions
- page-level Dolby Vision chatter
- unstructured movie lists with FEL nearby but not tied to a single title
- generic DV, Profile 7, MEL, REMUX, or UHD Blu-ray claims
- release-group names or release handles without independent FEL correlation

When evidence is ambiguous, reject the release.

## Data Model

Canonical generated data lives in `data/releases.json`.

Each published record represents one validated release and should include:

- `movie_title`
- `fel_confirmed: true`
- `release_date`, or `Unknown`
- `studio`, or `Unknown`
- `audio_formats`, as a list of normalized values
- `english_audio`, as `Yes`, `No`, or `Unknown`
- `additional_characteristics`, for bitrate, size, disc details, edition, or
  region when available
- `source_url`
- `source_label`
- `fel_evidence`, as structured audit data, not README prose
- `collected_at`

Minimum publish gate:

- movie title is solid
- FEL evidence is solid

Missing secondary metadata does not block publishing. Unknown release dates sort
below known release dates.

Release groups stay out of user-facing output unless a later spec explicitly
adds them.

## Audio Normalization

Audio strings from forums must normalize into stable display values.

Required target values include:

- `TrueHD Atmos`
- `DD+ Atmos`
- `TrueHD`
- `DTS-HD MA`
- `DD+`
- `DTS:X`

Examples:

- `Dolby TrueHD Atmos`, `TrueHD 7.1 Atmos`, `Atmos (TrueHD)` -> `TrueHD Atmos`
- `DD+ Atmos`, `Dolby Digital Plus Atmos`, `E-AC3 Atmos` -> `DD+ Atmos`
- `Dolby Digital Plus`, `E-AC3` -> `DD+`
- `DTS-HD Master Audio`, `DTS-MA 7.1` -> `DTS-HD MA`

If multiple audio formats are clearly present, preserve them as multiple
normalized values. If English audio is not established, publish `Unknown`, not
`No`.

## Generated Artifacts

Generated artifacts:

- `data/releases.json`
- `README.md`
- `links.md`
- `dist/` for GitHub Pages artifact upload

`README.md` is a clean human-facing registry. It should not include evidence
snippets. Evidence belongs in `data/releases.json` and source links.

README columns should stay concise:

- movie title
- FEL
- release date
- studio
- audio
- English audio
- additional characteristics
- source link

`links.md` lists source URLs that contributed confirmed FEL evidence.

Known release dates sort newest first. Unknown release dates sort last. Primary
published tables must not sort alphabetically.

## GitHub Pages Dashboard

Generate a polished static dashboard from `data/releases.json`.

Dashboard requirements:

- responsive layout
- release-date sorting
- client-side filters
- normalized audio badges
- English-audio indicators
- source links
- optional poster images
- fallback placeholder when poster lookup fails or credentials are missing

Poster lookup is enrichment only. API failure, missing credentials, or rate
limits must not block scraping, parsing, Markdown generation, or Pages
deployment.

`dist/` is uploaded as a branchless GitHub Pages artifact. It is not committed.

## Testing And Benchmarking

Tests are required from the first implementation even though the first feature
slice prioritizes live scraping.

Use deterministic mocked HTML for:

- valid one-to-one Profile 7 FEL correlation
- false positives where FEL appears without release-level proof
- malformed forum HTML
- audio normalization variants
- Brave discovery with and without `BRAVE_SEARCH_API_KEY`
- cache behavior
- optional cookie handling without logging secrets
- missing metadata
- artifact generation
- date sorting and unknown-date placement

Parser and normalization logic should target near-complete coverage.

Benchmarking must be deterministic. Use curated fixtures and expected labels,
not live network results, as the benchmark authority. AI-assisted extraction may
help create or review those fixtures offline, but CI must not call live AI models
or require AI credentials.

Benchmark output should report:

- precision
- recall
- false positives
- false negatives
- mismatched fields

CI must run tests and deterministic benchmarks before scraping, committing
generated artifacts, or deploying Pages.

## Developer Workflow

Use requirements files instead of Poetry or Pipenv:

- `requirements.txt` for runtime dependencies
- `requirements-dev.txt` for tests, linting, coverage, and local tooling

Use `uv` as the preferred local runner and required CI install/run path.

Use Python 3.11 unless a later implementation constraint requires a newer
version.

Use `ruff` for linting and formatting checks.

Required `just` commands:

- `just search-for-sources`
- `just scrape-for-titles`
- `just test`
- `just lint`
- `just run`
- `just ci`

The existing two `just` commands may fail clearly until `src/main.py` exists.
The implementation should replace that placeholder behavior with real commands.

## GitHub Actions

Production workflow requirements:

- daily schedule
- `workflow_dispatch`
- checkout
- setup Python and `uv` with `astral-sh/setup-uv`
- install dependencies with `uv`
- run lint, tests, and deterministic benchmark
- run scrape/discovery only after validation passes
- pass `BRAVE_SEARCH_API_KEY` into discovery when present
- pass optional `FORUM_COOKIE_HEADER` when configured
- generate `data/releases.json`, `README.md`, `links.md`, and `dist/`
- commit generated publish artifacts only when they changed
- commit `forums.txt` only when new discovered URLs produced confirmed FEL
  evidence
- deploy `dist/` through modern branchless GitHub Pages artifact deployment

Workflow permissions should be minimal but sufficient:

- `contents: write`
- `pages: write`
- `id-token: write`

Basic CI must pass without optional API secrets.

## Security Rules

- Never commit `.env`.
- Never commit `.cache/`.
- Never print API keys, cookies, or private headers.
- Never put secrets into generated JSON, README, links, dashboard HTML, logs, or
  cache metadata.
- Redact credentials in exceptions and diagnostics.
- Treat cached authenticated HTML as sensitive local debug material.

## Risks

- Strict evidence rules may initially miss valid releases.
- Forum markup will vary and may need source-specific parser adapters.
- Authenticated pages can break CI if cookies expire.
- Public FEL evidence can be sparse; the project should under-include rather
  than publish unproven titles.
- Poster enrichment and search APIs are external dependencies and must remain
  non-blocking.

## Out Of Scope For This Spec

- live AI extraction in CI
- release-group publication
- committing `dist/`
- paid search or poster API hard dependency
- private forum credential management beyond one optional raw cookie header
