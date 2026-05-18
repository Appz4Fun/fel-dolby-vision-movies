from __future__ import annotations

from pathlib import Path
import re


def find_comment_movies(dump_path: Path = Path("reddit_dump.txt")) -> list[str]:
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

    try:
        separator_index = lines.index("---")
        comment_lines = lines[separator_index + 1 :]
    except ValueError:
        comment_lines = lines

    comment_movies: list[str] = []
    for line in comment_lines:
        line = line.strip()
        if (
            re.search(r"(\[|\()\d{4}(\]|\))$", line) or re.search(r"\[\d{4}\]", line)
        ) and (
            line not in main_list
            and not line.startswith("--")
            and not line.startswith("-")
            and "MEL" not in line
        ):
            comment_movies.append(line)
    return comment_movies


def main() -> int:
    movies = find_comment_movies()
    print(f"Potential additions from comments: {len(movies)}")
    for movie in movies:
        print(movie)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
