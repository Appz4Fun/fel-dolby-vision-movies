set dotenv-load := true
set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

export PYTHONPATH := "src"

default:
    @just --list

search-for-sources:
    uv run --with-requirements requirements.txt python -m fel_dolby_vision_movies.main search-for-sources --sources forums.txt

scrape-for-titles:
    uv run --with-requirements requirements.txt python -m fel_dolby_vision_movies.main scrape-for-titles --sources forums.txt

run:
    uv run --with-requirements requirements.txt python -m fel_dolby_vision_movies.main run --sources forums.txt

test:
    uv run --with-requirements requirements-dev.txt pytest --cov=src/fel_dolby_vision_movies --cov-report=term-missing

lint:
    uv run --with-requirements requirements-dev.txt ruff check src tests
    uv run --with-requirements requirements-dev.txt ruff format --check src tests

ci:
    just lint
    just test
    uv run --with-requirements requirements-dev.txt python -m fel_dolby_vision_movies.benchmark tests/fixtures/benchmark_cases.json
