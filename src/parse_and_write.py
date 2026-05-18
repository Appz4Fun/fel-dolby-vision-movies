from __future__ import annotations

from pathlib import Path
import re


MOVIE_PATTERN = re.compile(
    r"([A-Za-z0-9:.,'\-!&éèàçêëîïôöûüÿ\s]+)(?:\[|\()(\d{4})(?:\]|\))"
)


def parse_reddit_dump(
    dump_path: Path = Path("reddit_dump.txt"), output_path: Path = Path("reddit.txt")
) -> int:
    lines = dump_path.read_text(encoding="utf-8").splitlines()
    main_list: list[str] = []
    in_list = False
    for line in lines:
        line = line.strip()
        if line == "List of P7-FEL films:":
            in_list = True
            continue
        if in_list and line == "---":
            break
        if in_list and line:
            main_list.append(line)

    new_movies: list[str] = []
    try:
        separator_index = lines.index("---")
        comment_lines = lines[separator_index + 1 :]
    except ValueError:
        comment_lines = lines

    for line in comment_lines:
        line = line.strip()
        if (
            "MEL" in line
            or "pruned" in line.lower()
            or "wrong" in line.lower()
            or "issue" in line.lower()
        ):
            continue
        for match in MOVIE_PATTERN.finditer(line):
            title = match.group(1).strip()
            for prefix in ["L.E. ", "EDIT: ", "--", "-"]:
                if title.startswith(prefix):
                    title = title[len(prefix) :].strip()
            year = match.group(2)
            formatted_movie = f"{title} [{year}]"
            if _is_in_main_list(title, main_list):
                continue
            if formatted_movie not in new_movies:
                new_movies.append(formatted_movie)

    final_list = main_list.copy()
    for movie in new_movies:
        if any(skip in movie for skip in ("Nightbreed", "La Haine", "Rain Man")):
            continue
        final_list.append(movie)
    final_list.sort(key=lambda item: item.lower())
    output_path.write_text("\n".join(final_list) + "\n", encoding="utf-8")
    return len(final_list)


def _is_in_main_list(title: str, main_list: list[str]) -> bool:
    for movie in main_list:
        for part in movie.split(" AKA "):
            clean_part = re.sub(r"\[\d{4}\]", "", part).strip()
            if clean_part.lower() == title.lower():
                return True
        clean_movie = re.sub(r"\[\d{4}\]", "", movie).strip().lower()
        if title.lower() in clean_movie or clean_movie in title.lower():
            return True
    return False


def main() -> int:
    count = parse_reddit_dump()
    print(f"Wrote {count} movies to reddit.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
