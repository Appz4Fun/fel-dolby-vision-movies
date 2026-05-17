set dotenv-load := true
set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

export PYTHONPATH := "src"

default:
    @just --list

search-for-sources:
    @test -f src/fel_dolby_vision_movies/main.py || { echo "search-for-sources is not implemented yet"; exit 1; }
    uv run --with-requirements requirements.txt python -m fel_dolby_vision_movies.main search-for-sources --sources forums.txt

scrape-for-titles:
    @test -f src/fel_dolby_vision_movies/main.py || { echo "scrape-for-titles is not implemented yet"; exit 1; }
    uv run --with-requirements requirements.txt python -m fel_dolby_vision_movies.main scrape-for-titles --sources forums.txt

run:
    @test -f src/fel_dolby_vision_movies/main.py || { echo "run is not implemented yet"; exit 1; }
    uv run --with-requirements requirements.txt python -m fel_dolby_vision_movies.main run --sources forums.txt

test:
    @if test -d tests; then \
        uv run --with-requirements requirements-dev.txt pytest --cov=src/fel_dolby_vision_movies --cov-report=term-missing; \
    else \
        echo "tests are not implemented yet"; \
    fi

lint:
    @paths="src"; test ! -d tests || paths="$paths tests"; uv run --with-requirements requirements-dev.txt ruff check $paths
    @paths="src"; test ! -d tests || paths="$paths tests"; uv run --with-requirements requirements-dev.txt ruff format --check $paths

ci:
    just lint
    just test
    @if test -f src/fel_dolby_vision_movies/benchmark.py && test -f tests/fixtures/benchmark_cases.json; then \
        uv run --with-requirements requirements-dev.txt python -m fel_dolby_vision_movies.benchmark tests/fixtures/benchmark_cases.json; \
    else \
        echo "benchmark is not implemented yet"; \
    fi
