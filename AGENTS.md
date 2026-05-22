# Agent Instructions

This repository is for `fel-dolby-vision-movies`: a Python automation pipeline
that discovers physical-media forum sources, validates Dolby Vision Profile 7
FEL Blu-ray releases, normalizes release metadata, and publishes Markdown plus
GitHub Pages artifacts.

## Superpowers System

Superpowers skills are installed through Codex native skill discovery:

```text
~/.agents/skills/superpowers -> ~/.codex/superpowers/skills
```

Codex discovers these skills at startup. Restart Codex after installing or
updating the symlink.

## Operating Rules

- Use `CONTEXT.md` as the historical project source until the spec and
  implementation plan are written.
- Use superpowers brainstorming before changing product behavior or widening
  scope.
- Keep `.env`, API keys, tokens, cookies, cached HTML, and local credentials
  out of git.
- Do not print secrets back into chat or commit logs.
- Treat source discovery and forum scraping as brittle network behavior:
  prefer deterministic tests with mocked HTML before relying on live sites.
- Separate verified FEL evidence from inferred Dolby Vision or generic REMUX
  evidence.
- Do not list a movie unless the parser can establish direct one-to-one
  correlation between the specific release and Profile 7 FEL.

## Project Requirements

- Bootstrap this as a Python project with focused modules under `src/`.
- Maintain `forums.txt` as the source registry and allow automatic source
  discovery to update it.
- Use optional Brave Search discovery when `BRAVE_SEARCH_API_KEY` is available.
- Normalize audio formats into stable values such as `TrueHD Atmos`,
  `DD+ Atmos`, `TrueHD`, `DTS-HD MA`, `DD+`, and `DTS:X`.
- Generate `README.md`, `links.md`, machine-readable release data, and a
  static GitHub Pages dashboard.
- Sort published release tables by release date, newest first.
- Exclude release groups from user-facing outputs unless a later spec
  explicitly adds them.

## Validation

- Write tests before parser behavior where practical.
- Use mocked forum HTML for correlation, false-positive, malformed HTML, audio
  normalization, and artifact-generation tests.
- Target near-complete coverage for deterministic parser and normalization
  logic.
- The benchmark suite should compare parser outcomes against curated expected
  labels.
- AI-assisted extraction (codex, via `ai-scrape`) now runs as part of the daily
  GitHub Actions pipeline alongside the deterministic Python scrape; it
  discovers sources and extracts FEL releases tagged `ai-extracted`. Tests must
  still pass without any AI/LLM secret (the test suite mocks the AI client).
- Before claiming completion, report the commands run and any validation gaps.

## Git And CI

- Check `git status --short --branch` before editing in a git repository.
- Stage only files intentionally changed for the task.
- The daily GitHub Actions workflow should run tests before scraping.
- CI must not require optional API secrets to pass basic tests.
- GitHub Pages deployment should use the modern branchless Pages artifact flow.
