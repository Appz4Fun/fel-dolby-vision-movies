from __future__ import annotations

from html import escape
import json
from pathlib import Path
import shutil

from models import UNKNOWN, FelRelease


RELEASE_GROUP_KEYS = frozenset({"group", "release_group", "release group"})


def build_dashboard(
    releases: list[FelRelease],
    output_dir: Path | str = "dist",
    poster_src: Path | str = "data/posters",
) -> None:
    dist = Path(output_dir)
    dist.mkdir(parents=True, exist_ok=True)
    sorted_releases = sorted(releases, key=_sort_key)
    payload = json.dumps(
        [_to_public_dict(release) for release in sorted_releases],
        indent=2,
        ensure_ascii=False,
    )
    cards = "\n".join(
        _render_card(release, index) for index, release in enumerate(sorted_releases)
    )

    (dist / "releases.json").write_text(payload + "\n", encoding="utf-8")
    (dist / "index.html").write_text(
        _render_html(cards, payload, len(sorted_releases)), encoding="utf-8"
    )

    poster_source = Path(poster_src)
    if poster_source.is_dir():
        shutil.copytree(poster_source, dist / "posters", dirs_exist_ok=True)


def _sort_key(release: FelRelease) -> tuple[int, str]:
    if release.release_date == UNKNOWN:
        return (1, "")
    return (0, _invert_date_text(release.release_date))


def _invert_date_text(value: str) -> str:
    return "".join(chr(255 - ord(character)) for character in value)


def _to_public_dict(release: FelRelease) -> dict[str, object]:
    data = release.to_dict()
    additional = data.get("additional_characteristics", {})
    if isinstance(additional, dict):
        data["additional_characteristics"] = {
            key: value
            for key, value in additional.items()
            if key.lower().replace("-", "_") not in RELEASE_GROUP_KEYS
        }
    return data


def _evidence_urls(release: FelRelease) -> list[str]:
    """Distinct http(s) source URLs for a release, primary (source_url) first.

    Non-URL provenance strings (e.g. "FEL.txt (curated Profile 7 FEL list)")
    are excluded so they never render as broken links.
    """
    urls: list[str] = []
    seen: set[str] = set()
    additional = release.additional_characteristics or {}
    extra = additional.get("source_urls") or []
    if not isinstance(extra, list):
        extra = []
    for url in [release.source_url, *extra]:
        if (
            isinstance(url, str)
            and url.startswith(("http://", "https://"))
            and url not in seen
        ):
            seen.add(url)
            urls.append(url)
    return urls


def _render_card(release: FelRelease, index: int) -> str:
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
            " ".join(release.audio_languages),
            " ".join(release.hdr_formats),
        ]
    ).lower()
    if release.poster_path:
        poster_file = Path(release.poster_path).name
        poster = (
            f'<img class="poster" src="posters/{escape(poster_file, quote=True)}" '
            f'alt="{escape(release.movie_title, quote=True)}" loading="lazy">'
        )
    else:
        poster = f'<div class="poster-placeholder">{escape(release.movie_title)}</div>'
    tmdb_link = ""
    if release.release_url:
        tmdb_link = (
            f'<a href="{escape(release.release_url, quote=True)}" '
            f'rel="noreferrer">TMDB</a>'
        )
    bluray_link = ""
    if release.bluray_url:
        bluray_link = (
            f'<a href="{escape(release.bluray_url, quote=True)}" '
            f'rel="noreferrer">BR</a>'
        )
    evidence_urls = _evidence_urls(release)
    if len(evidence_urls) > 1:
        src_control = (
            '<button type="button" class="src-toggle" data-evidence>Src '
            f'<span class="src-count">({len(evidence_urls)})</span></button>'
        )
    elif evidence_urls:
        src_control = (
            f'<a href="{escape(evidence_urls[0], quote=True)}" rel="noreferrer">Src</a>'
        )
    else:
        label = release.source_label
        if not label or label == UNKNOWN:
            label = "list only"
        src_control = (
            '<button type="button" class="src-toggle" data-evidence>'
            f"{escape(label)}</button>"
        )
    return f"""<article data-card data-idx="{index}" \
data-search="{escape(search_text, quote=True)}">
  {poster}
  <div class="body">
    <h2>{escape(release.movie_title)}</h2>
    <div class="meta">{escape(release.release_date)} - {escape(release.studio)}</div>
    <div class="badges">
      {audio_badges}
      <span class="badge">English: {escape(release.english_audio)}</span>
    </div>
    <div class="links">
      {src_control}
      {tmdb_link}
      {bluray_link}
    </div>
  </div>
</article>"""


def _render_html(cards: str, payload: str, total: int) -> str:
    script_payload = payload.replace("<", "\\u003c")
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
      width: calc(100% - 32px);
      margin: 0 auto;
      padding: 32px 0;
    }}
    header {{ display: grid; gap: 16px; margin-bottom: 24px; }}
    h1 {{ margin: 0; font-size: 34px; letter-spacing: 0; }}
    .count {{ color: var(--muted); font-size: 15px; }}
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
    .views {{ display: flex; gap: 8px; margin-bottom: 16px; }}
    .views button {{
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--text);
      border-radius: 8px;
      padding: 8px 14px;
      font: inherit;
      cursor: pointer;
    }}
    .views button.active {{ border-color: var(--accent); color: var(--accent); }}
    #cards {{
      display: none;
      grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
      gap: 16px;
    }}
    #list {{ display: block; }}
    table {{
      width: 100%;
      min-width: 780px;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      text-align: left;
      padding: 8px 10px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
      overflow-wrap: anywhere;
    }}
    th {{ cursor: pointer; user-select: none; white-space: nowrap; }}
    th:hover {{ color: var(--accent); }}
    table img.poster-thumb {{ width: 46px; height: 69px; object-fit: cover; display: block; }}
    .poster-thumb-placeholder {{
      width: 46px;
      height: 69px;
      display: grid;
      place-items: center;
      background: #222a30;
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      text-align: center;
      padding: 4px;
    }}
    td a {{ white-space: nowrap; }}
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
    img.poster {{
      width: 100%;
      aspect-ratio: 2 / 3;
      object-fit: cover;
      display: block;
    }}
    .links {{ display: flex; gap: 12px; }}
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
    .src-toggle {{
      background: none;
      border: 0;
      color: var(--accent);
      font: inherit;
      cursor: pointer;
      padding: 0;
      white-space: nowrap;
    }}
    .src-toggle::after {{ content: " ▸"; font-size: 10px; }}
    .src-toggle.open::after {{ content: " ▾"; }}
    .src-count {{ color: var(--muted); }}
    .evidence-row td.evidence-cell {{ background: #0c0e10; padding: 6px 10px; }}
    .evidence-box {{
      margin: 6px 0;
      border: 1px solid var(--line);
      background: #0c0e10;
      border-radius: 8px;
      padding: 12px;
      display: grid;
      gap: 10px;
      max-width: 760px;
    }}
    .evidence-box h3 {{
      margin: 0;
      font-size: 11px;
      letter-spacing: .05em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .evidence-quote {{
      color: var(--muted);
      font-size: 13px;
      font-style: italic;
      border-left: 2px solid var(--accent);
      padding-left: 10px;
    }}
    .evidence-note {{ color: var(--muted); font-size: 13px; }}
    .evidence-note strong {{ color: var(--text); font-weight: 600; }}
    .evidence-list {{
      margin: 0;
      padding: 0;
      list-style: none;
      display: grid;
      gap: 7px;
      max-height: 260px;
      overflow-y: auto;
    }}
    .evidence-item {{
      display: flex;
      gap: 8px;
      align-items: baseline;
      font-size: 13px;
    }}
    .evidence-item a {{ overflow-wrap: anywhere; }}
    .evidence-tag {{
      border: 1px solid rgba(77, 211, 201, .45);
      background: rgba(77, 211, 201, .14);
      color: var(--accent);
      border-radius: 999px;
      padding: 1px 7px;
      font-size: 10px;
      white-space: nowrap;
      flex: none;
    }}
    a {{ color: var(--accent); }}
    @media (max-width: 980px) {{
      .priority-5 {{ display: none; }}
      table {{ min-width: 680px; }}
    }}
    @media (max-width: 820px) {{
      main {{ width: min(100% - 24px, 1180px); padding-top: 24px; }}
      .priority-4 {{ display: none; }}
      table {{ min-width: 580px; font-size: 13px; }}
      th, td {{ padding: 7px 8px; }}
    }}
    @media (max-width: 660px) {{
      h1 {{ font-size: 28px; }}
      .views button {{ flex: 1; }}
      .priority-3 {{ display: none; }}
      table {{ min-width: 0; }}
    }}
    @media (max-width: 520px) {{
      main {{ width: min(100% - 20px, 1180px); }}
      .priority-2 {{ display: none; }}
      th, td {{ padding: 7px 6px; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>FEL Dolby Vision Movies</h1>
      <div class="count">{total} confirmed Profile 7 FEL releases</div>
      <label>Filter <input id="filter" type="search" placeholder="Title, studio, audio"></label>
    </header>
    <div class="views">
      <button id="view-list" class="active">List</button>
      <button id="view-cards">Cards</button>
    </div>
    <div id="list"></div>
    <section id="cards">{cards}</section>
  </main>
  <script type="application/json" id="release-data">{script_payload}</script>
  <script>
    const data = JSON.parse(document.getElementById("release-data").textContent);
    const filter = document.getElementById("filter");
    const cardsView = document.getElementById("cards");
    const listView = document.getElementById("list");
    const btnCards = document.getElementById("view-cards");
    const btnList = document.getElementById("view-list");

    function escapeHtml(value) {{
      return String(value == null ? "" : value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }}
    function link(url, label) {{
      return url
        ? `<a href="${{escapeHtml(url)}}" rel="noreferrer">${{label}}</a>`
        : "";
    }}
    function evidenceUrls(row) {{
      const out = [];
      const seen = new Set();
      const extra = (row.additional_characteristics
        && row.additional_characteristics.source_urls) || [];
      for (const url of [row.source_url, ...extra]) {{
        const isUrl = typeof url === "string"
          && (url.startsWith("http://") || url.startsWith("https://"));
        if (isUrl && !seen.has(url)) {{
          seen.add(url);
          out.push(url);
        }}
      }}
      return out;
    }}
    function sourceLabel(url) {{
      try {{
        const u = new URL(url);
        const host = u.hostname.replace(/^www\\./, "");
        const t = u.searchParams.get("t");
        const p = u.searchParams.get("p");
        const page = u.searchParams.get("page");
        if (host.indexOf("blu-ray.com") !== -1 && (t || p)) {{
          let label = "blu-ray.com forum";
          if (t) label += " · thread " + t;
          if (page) label += " · p." + page;
          if (p && !t) label += " · post " + p;
          return label;
        }}
        if (host.indexOf("reddit.com") !== -1) {{
          const parts = u.pathname.split("/").filter(Boolean);
          return "reddit · " + parts.slice(0, 2).join("/");
        }}
        if (host.indexOf("web.archive.org") !== -1) return "web.archive.org snapshot";
        if (host.indexOf("docs.google.com") !== -1) return "Google Sheet";
        const seg = u.pathname.split("/").filter(Boolean).pop();
        return seg ? host + " · " + decodeURIComponent(seg) : host;
      }} catch (error) {{
        return url;
      }}
    }}
    function renderEvidenceBox(row) {{
      const urls = evidenceUrls(row);
      const quote = row.fel_evidence && row.fel_evidence.quote;
      let body;
      if (urls.length) {{
        const items = urls.map((url, index) =>
          `<li class="evidence-item">` +
          `<span class="evidence-tag"` +
          `${{index === 0 ? "" : ' style="visibility:hidden"'}}>primary</span>` +
          `<a href="${{escapeHtml(url)}}" target="_blank" rel="noreferrer">` +
          `${{escapeHtml(sourceLabel(url))}}</a>` +
          `</li>`).join("");
        body = `<ul class="evidence-list">${{items}}</ul>`;
      }} else {{
        const raw = row.source_url;
        const provenance = (typeof raw === "string" && !raw.startsWith("http"))
          ? raw
          : ((row.source_label && row.source_label !== "Unknown")
            ? row.source_label : "a curated list");
        body = `<div class="evidence-note">Listed in ` +
          `<strong>${{escapeHtml(provenance)}}</strong> — ` +
          `no direct source link.</div>`;
      }}
      return `<div class="evidence-box">` +
        `<h3>Source evidence${{urls.length ? " (" + urls.length + ")" : ""}}</h3>` +
        (quote ? `<div class="evidence-quote">${{escapeHtml(quote)}}</div>` : "") +
        body +
        `</div>`;
    }}
    function srcControl(row) {{
      const urls = evidenceUrls(row);
      if (urls.length > 1) {{
        return `<button type="button" class="src-toggle" data-evidence>Src ` +
          `<span class="src-count">(${{urls.length}})</span></button>`;
      }}
      if (urls.length === 1) return link(urls[0], "Src");
      const label = row.source_label && row.source_label !== "Unknown"
        ? row.source_label : "list only";
      return `<button type="button" class="src-toggle" data-evidence>` +
        `${{escapeHtml(label)}}</button>`;
    }}
    function posterThumb(row) {{
      if (!row.poster_path) {{
        return `<div class="poster-thumb-placeholder">${{escapeHtml(row.movie_title || "No poster")}}</div>`;
      }}
      const posterFile = String(row.poster_path).split(/[\\\\/]/).pop();
      return `<img class="poster-thumb" src="posters/${{escapeHtml(posterFile)}}"
        alt="${{escapeHtml(row.movie_title || "")}}" loading="lazy">`;
    }}
    function dateSort(value) {{
      return value && value !== "Unknown" ? value : "";
    }}
    const columns = [
      ["Poster", r => posterThumb(r), "priority-1", r => r.movie_title || ""],
      ["Movie", r => escapeHtml(r.movie_title || ""), "priority-1"],
      ["Release Date", r => escapeHtml(r.release_date || ""), "priority-1", r => dateSort(r.release_date)],
      ["Blu-ray Date", r => escapeHtml(r.bluray_release_date || ""), "priority-2", r => dateSort(r.bluray_release_date)],
      ["Studio", r => escapeHtml(r.studio || ""), "priority-4"],
      ["Audio", r => escapeHtml((r.audio_formats || []).join(", ")), "priority-3"],
      ["Language", r => escapeHtml((r.audio_languages || []).join(", ")), "priority-5"],
      ["HDR", r => escapeHtml((r.hdr_formats || []).join(", ")), "priority-4"],
      ["BR Link", r => link(r.bluray_url, "BR"), "priority-1"],
      ["Src Link", r => srcControl(r), "priority-2"],
      ["TMDB", r => link(r.release_url, "TMDB"), "priority-2"],
    ];
    let sortCol = 2;
    let sortAsc = false;
    let currentRows = [];

    function filteredRows() {{
      const query = filter.value.trim().toLowerCase();
      return data.filter(r => !query ||
        `${{r.movie_title}} ${{r.studio || ""}} ${{(r.audio_formats || []).join(" ")}} ${{(r.hdr_formats || []).join(" ")}}`
          .toLowerCase()
          .includes(query));
    }}

    function renderTable() {{
      const rows = filteredRows().slice().sort((a, b) => {{
        const sortValue = columns[sortCol][3] || columns[sortCol][1];
        const va = sortValue(a)
          .toString()
          .replace(/<[^>]*>/g, "")
          .toLowerCase();
        const vb = sortValue(b)
          .toString()
          .replace(/<[^>]*>/g, "")
          .toLowerCase();
        return (va < vb ? -1 : va > vb ? 1 : 0) * (sortAsc ? 1 : -1);
      }});
      const head = "<tr>" + columns.map((column, i) =>
        `<th class="${{column[2]}}" data-col="${{i}}">${{column[0]}}${{i === sortCol
          ? (sortAsc ? " ▲" : " ▼") : ""}}</th>`).join("") + "</tr>";
      currentRows = rows;
      const body = rows.map((row, index) => `<tr data-idx="${{index}}">` +
        columns.map(column => `<td class="${{column[2]}}">${{column[1](row)}}</td>`)
          .join("") +
        "</tr>").join("");
      listView.innerHTML = `<table><thead>${{head}}</thead><tbody>${{body}}</tbody></table>`;
      listView.querySelectorAll("th").forEach(th => th.addEventListener("click", () => {{
        const col = Number(th.dataset.col);
        if (col === sortCol) {{
          sortAsc = !sortAsc;
        }} else {{
          sortCol = col;
          sortAsc = true;
        }}
        renderTable();
      }}));
      listView.querySelectorAll(".src-toggle[data-evidence]").forEach(button =>
        button.addEventListener("click", () => {{
          const tr = button.closest("tr");
          const next = tr.nextElementSibling;
          if (next && next.classList.contains("evidence-row")) {{
            next.remove();
            button.classList.remove("open");
            return;
          }}
          const row = currentRows[Number(tr.dataset.idx)];
          tr.insertAdjacentHTML("afterend",
            `<tr class="evidence-row"><td class="evidence-cell" ` +
            `colspan="${{columns.length}}">` + renderEvidenceBox(row) + `</td></tr>`);
          button.classList.add("open");
        }}));
    }}
    function sortTable() {{ renderTable(); }}

    filter.addEventListener("input", () => {{
      const query = filter.value.trim().toLowerCase();
      document.querySelectorAll("[data-card]").forEach(card => {{
        card.hidden = query && !card.dataset.search.includes(query);
      }});
      if (listView.style.display !== "none") {{
        renderTable();
      }}
    }});
    btnCards.addEventListener("click", () => {{
      cardsView.style.display = "grid";
      listView.style.display = "none";
      btnCards.classList.add("active");
      btnList.classList.remove("active");
    }});
    btnList.addEventListener("click", () => {{
      cardsView.style.display = "none";
      listView.style.display = "block";
      btnList.classList.add("active");
      btnCards.classList.remove("active");
      renderTable();
    }});
    cardsView.addEventListener("click", event => {{
      const button = event.target.closest(".src-toggle[data-evidence]");
      if (!button) return;
      const article = button.closest("[data-card]");
      const existing = article.querySelector(".evidence-box");
      if (existing) {{
        existing.remove();
        button.classList.remove("open");
        return;
      }}
      const row = data[Number(article.dataset.idx)];
      article.querySelector(".body")
        .insertAdjacentHTML("beforeend", renderEvidenceBox(row));
      button.classList.add("open");
    }});
    renderTable();
  </script>
</body>
</html>
"""
