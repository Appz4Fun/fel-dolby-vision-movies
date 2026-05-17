from __future__ import annotations

from html import escape
import json
from pathlib import Path

from .models import UNKNOWN, FelRelease


def build_dashboard(
    releases: list[FelRelease], output_dir: Path | str = "dist"
) -> None:
    dist = Path(output_dir)
    dist.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        [release.to_dict() for release in releases],
        indent=2,
        ensure_ascii=False,
    )
    cards = "\n".join(_render_card(release) for release in releases)

    (dist / "releases.json").write_text(payload + "\n", encoding="utf-8")
    (dist / "index.html").write_text(_render_html(cards, payload), encoding="utf-8")


def _render_card(release: FelRelease) -> str:
    audio_formats = release.audio_formats or [UNKNOWN]
    audio_badges = "".join(
        f'<span class="badge">{escape(audio)}</span>' for audio in audio_formats
    )
    search_text = " ".join(
        [
            release.movie_title,
            release.release_date,
            release.studio,
            release.english_audio,
            " ".join(audio_formats),
        ]
    ).lower()
    return f"""<article data-card data-search="{escape(search_text, quote=True)}">
  <div class="poster-placeholder">{escape(release.movie_title)}</div>
  <div class="body">
    <h2>{escape(release.movie_title)}</h2>
    <div class="meta">{escape(release.release_date)} - {escape(release.studio)}</div>
    <div class="badges">
      {audio_badges}
      <span class="badge">English: {escape(release.english_audio)}</span>
      <span class="badge">FEL</span>
    </div>
    <a href="{escape(release.source_url, quote=True)}" rel="noreferrer">Source</a>
  </div>
</article>"""


def _render_html(cards: str, payload: str) -> str:
    escaped_payload = escape(payload)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FEL Dolby Vision Movies</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #101214;
      --panel: #191d21;
      --text: #eef2f4;
      --muted: #aab4bd;
      --accent: #4dd3c9;
      --line: #2c343a;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system,
        BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      width: min(1180px, calc(100% - 32px));
      margin: 0 auto;
      padding: 32px 0;
    }}
    header {{ display: grid; gap: 16px; margin-bottom: 24px; }}
    h1 {{ margin: 0; font-size: 34px; letter-spacing: 0; }}
    label {{ color: var(--muted); display: grid; gap: 8px; max-width: 420px; }}
    input {{
      width: 100%;
      border: 1px solid var(--line);
      background: #0c0e10;
      color: var(--text);
      border-radius: 8px;
      padding: 11px 12px;
      font: inherit;
    }}
    #cards {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
      gap: 16px;
    }}
    article {{
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      overflow: hidden;
      min-height: 100%;
    }}
    .poster-placeholder {{
      aspect-ratio: 2 / 3;
      display: grid;
      place-items: center;
      background: #222a30;
      color: var(--muted);
      font-weight: 700;
      text-align: center;
      padding: 16px;
    }}
    .body {{ padding: 14px; display: grid; gap: 10px; }}
    h2 {{ margin: 0; font-size: 18px; letter-spacing: 0; }}
    .meta {{ color: var(--muted); font-size: 14px; }}
    .badges {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .badge {{
      border: 1px solid rgba(77, 211, 201, .45);
      background: rgba(77, 211, 201, .12);
      color: var(--text);
      border-radius: 999px;
      padding: 4px 8px;
      font-size: 12px;
      white-space: nowrap;
    }}
    a {{ color: var(--accent); }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>FEL Dolby Vision Movies</h1>
      <label>Filter <input id="filter" type="search" placeholder="Title, studio, audio"></label>
    </header>
    <section id="cards">{cards}</section>
  </main>
  <script type="application/json" id="release-data">{escaped_payload}</script>
  <script>
    const filter = document.getElementById("filter");
    filter.addEventListener("input", () => {{
      const query = filter.value.trim().toLowerCase();
      document.querySelectorAll("[data-card]").forEach(card => {{
        card.hidden = query && !card.dataset.search.includes(query);
      }});
    }});
  </script>
</body>
</html>
"""
