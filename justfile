set dotenv-load := true
set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

export PYTHONPATH := "src"

default:
    @just --list

search-for-sources:
    @test -f src/main.py || { echo "search-for-sources is not implemented yet"; exit 1; }
    uv run --with-requirements requirements.txt python -m main search-for-sources --sources forums.txt

scrape-for-titles:
    @test -f src/main.py || { echo "scrape-for-titles is not implemented yet"; exit 1; }
    uv run --with-requirements requirements.txt python -m main scrape-for-titles --sources forums.txt

run:
    @test -f src/main.py || { echo "run is not implemented yet"; exit 1; }
    uv run --with-requirements requirements.txt python -m main run --sources forums.txt
    uv run --with-requirements requirements.txt python -m main clean-fel --input FEL.txt --output FEL.txt

compare-found *args:
    @test -f src/main.py || { echo "compare-found is not implemented yet"; exit 1; }
    uv run --with-requirements requirements.txt python -m main compare-found --sources forums.txt {{args}}

clean-fel *args:
    @test -f src/main.py || { echo "clean-fel is not implemented yet"; exit 1; }
    uv run --with-requirements requirements.txt python -m main clean-fel {{args}}

test:
    @if test -d tests; then \
        uv run --with-requirements requirements-dev.txt pytest --cov=src --cov-report=term-missing; \
    else \
        echo "tests are not implemented yet"; \
    fi

lint:
    @paths="src"; test ! -d tests || paths="$paths tests"; uv run --with-requirements requirements-dev.txt ruff check $paths
    @paths="src"; test ! -d tests || paths="$paths tests"; uv run --with-requirements requirements-dev.txt ruff format --check $paths

ci:
    just lint
    just test
    @if test -f src/benchmark.py && test -f tests/fixtures/benchmark_cases.json; then \
        uv run --with-requirements requirements-dev.txt python -m benchmark tests/fixtures/benchmark_cases.json; \
    else \
        echo "benchmark is not implemented yet"; \
    fi
