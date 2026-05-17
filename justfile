set dotenv-load := true
set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

default:
    @just --list

# Search the web and known pages for additional source URLs.
search-for-sources:
    @if [ -f src/main.py ]; then \
        uv run python -m src.main search-for-sources --sources forums.txt; \
    else \
        echo "search-for-sources is not implemented yet; expected src/main.py"; \
        exit 1; \
    fi

# Scrape seeded sources for confirmed Dolby Vision Profile 7 FEL titles.
scrape-for-titles:
    @if [ -f src/main.py ]; then \
        uv run python -m src.main scrape-for-titles --sources forums.txt; \
    else \
        echo "scrape-for-titles is not implemented yet; expected src/main.py"; \
        exit 1; \
    fi
