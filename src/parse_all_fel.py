from __future__ import annotations

import csv
from pathlib import Path
import re


DEFAULT_INPUTS = (
    Path("raw_fel.txt"),
    Path("reddit.txt"),
    Path("AI_found.txt"),
    Path("PY_found.txt"),
)


def normalize_title_for_fel_list(title: str) -> tuple[str, str]:
    for prefix in ["L.E. ", "EDIT: ", "EDIT ", "--", "-", "EDIT: "]:
        if title.startswith(prefix):
            title = title[len(prefix) :].strip()

    if " AKA " in title:
        title = title.split(" AKA ", 1)[0].strip()

    cleaned = re.sub(r"\s+", " ", title.replace(".", " "))
    normalized = cleaned.lower().strip(",- ")
    if normalized.endswith(", the"):
        normalized = "the " + normalized[:-5]
    return title.strip(",- "), normalized


def parse_all_fel(
    input_paths: tuple[Path, ...] = DEFAULT_INPUTS,
    output_path: Path = Path("FEL.txt"),
) -> int:
    movies: dict[str, dict[str, str]] = {}
    for path in input_paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            print(f"Error reading {path}: {exc}")
            continue
        for line in lines:
            match = re.search(r"^(.*?)\s*(?:\[|\()(19\d{2}|20\d{2})(?:\]|\))", line)
            if not match:
                continue
            original_title = match.group(1).strip()
            year = match.group(2)
            if re.match(r"^\d{2}\s+[A-Za-z]{3}$", original_title):
                continue
            if path.name == "AI_found.txt":
                number_match = re.match(r"^(\d+)\s+(.*)", original_title)
                if number_match:
                    index = int(number_match.group(1))
                    if index >= 100 or index in range(1, 10):
                        original_title = number_match.group(2)

            display_title, normalized_title = normalize_title_for_fel_list(
                original_title
            )
            if not normalized_title:
                continue
            existing = movies.get(normalized_title)
            if existing is None:
                movies[normalized_title] = {"title": display_title, "year": year}
                continue
            if display_title.count(".") < existing["title"].count("."):
                existing["title"] = display_title
            if year < existing["year"]:
                existing["year"] = year

    sorted_movies = sorted(
        movies.values(), key=lambda item: (item["title"].lower(), item["year"])
    )
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        for movie in sorted_movies:
            writer.writerow([movie["title"], movie["year"]])
    return len(sorted_movies)


def main() -> int:
    count = parse_all_fel()
    print(f"Total unique movies found: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
