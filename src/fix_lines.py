from __future__ import annotations

from pathlib import Path


def strip_leading_commas(path: Path = Path("reddit.txt")) -> int:
    lines = path.read_text(encoding="utf-8").splitlines()
    cleaned_lines = []
    for line in lines:
        cleaned = line.strip()
        while cleaned.startswith(",") or cleaned.startswith(" "):
            cleaned = cleaned[1:].strip()
        cleaned_lines.append(cleaned)
    path.write_text("\n".join(cleaned_lines) + "\n", encoding="utf-8")
    return len(cleaned_lines)


def main() -> int:
    strip_leading_commas()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
