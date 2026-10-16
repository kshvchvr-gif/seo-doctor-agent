"""
Phase 4 — Dashboard.

Builds a static HTML dashboard + a small JSON index from the machine-readable
artifacts this agent already produces:

  reports/YYYY-MM-DD.md          weekly SEO Doctor reports (markdown)
  reports/health/YYYY-MM-DD.md   daily website-health reports (markdown)
  reports/fix_plan.json          latest planned auto-fixes (Phase 3)
  fixes_history.json             every applied fix + its verification (Phase 3)

Output (default ``docs/``, deployed to GitHub Pages by
``.github/workflows/dashboard.yml``):

  docs/index.html                the dashboard
  docs/data/index.json           the small JSON index (all parsed data)
  docs/reports/index.html        list of all reports
  docs/reports/<date>.html       a weekly report, rendered
  docs/reports/health/<date>.html

Parsing is defensive: a report that is missing a section simply yields ``None``
for it, and nothing here is allowed to raise on malformed input.
"""
import argparse
import datetime
import glob
import html
import json
import os
import re

try:  # optional: only used to render the full markdown reports
    import markdown as _markdown
except Exception:  # pragma: no cover - fallback keeps the generator usable
    _markdown = None

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_REPO = os.environ.get("GITHUB_REPOSITORY", "kshvchvr-gif/seo-doctor-agent")
SITE_URL = os.environ.get("SITE_URL", "https://krishnbhakti.com")

ESC = html.escape


class _Safe(str):
    """Marker: HTML that is already escaped/sanitised and must NOT be escaped again.

    `_table()` decides per-cell whether to escape using this marker instead of
    sniffing at the string (a value like "<h1>T</h1>" must be escaped, not
    trusted, because it can legitimately start with '<').
    """


# --------------------------------------------------------------------------- #
# small parsing helpers
# --------------------------------------------------------------------------- #
def _cells(line: str):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _is_sep(line: str) -> bool:
    s = line.strip()
    return bool(s) and "-" in s and set(s) <= set("|:- ")


def _num(value):
    """Best-effort number from a markdown cell ('**5**', '+3', '1.17%', 'n/a')."""
    if value is None:
        return None
    t = str(value).strip().strip("*").replace(",", "").strip()
    if t in ("", "n/a", "-", "—", "None", "±0%"):
        return None
    t = t.replace("%", "")
    if t.startswith("+"):
        t = t[1:]
    try:
        return int(t)
    except ValueError:
        try:
            return float(t)
        except ValueError:
            return None


def _sections(md: str):
    """Split markdown into level-2 sections: [{'title','lines'}] (order kept)."""
    out = []
    cur = None
    for line in md.splitlines():
        if re.match(r"^##\s+", line):
            cur = {"title": line[3:].strip(), "lines": []}
            out.append(cur)
        elif cur is not None:
            cur["lines"].append(line)
    return out


def _tables(lines):
    """All markdown tables in `lines` -> [(header, rows)]."""
    tables = []
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("|") and i + 1 < len(lines) and _is_sep(lines[i + 1]):
            header = _cells(lines[i])
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append(_cells(lines[j]))
                j += 1
            tables.append((header, rows))
            i = j
        else:
            i += 1
    return tables


def _tables_with_labels(lines):
    """Like _tables but also returns the nearest preceding **Bold label**."""
    out = []
    label = None
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        m = re.match(r"^\*\*(.+?)\*\*", stripped)
        if m:
            label = m.group(1)
        if stripped.startswith("|") and i + 1 < len(lines) and _is_sep(lines[i + 1]):
            header = _cells(lines[i])
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append(_cells(lines[j]))
                j += 1
            out.append((label, header, rows))
            i = j
            continue
        i += 1
    return out


def _rows_as_dicts(header, rows):
    return [dict(zip(header, r)) for r in rows]


# --------------------------------------------------------------------------- #
# report + health parsers
# --------------------------------------------------------------------------- #
def parse_report(md: str) -> dict:
    report = {
        "date": None, "clicks90": None, "impressions90": None, "ctr": None,
        "movement": None, "priority_pages_count": None, "content_gaps_count": None,
        "technical_flags": None, "issue_types": None, "technical_issues": [],
        "traffic_sources": [], "windows": [], "priority_pages": [],
        "content_gaps": [], "internal_link_suggestions": None,
    }
    m = re.search(r"#\s*🩺?\s*SEO Doctor Report\s*—\s*(\d{4}-\d{2}-\d{2})", md)
    if m:
        report["date"] = m.group(1)

    sec = {s["title"]: s["lines"] for s in _sections(md)}
    exec_lines = None
    for title, lines in sec.items():
        if title.startswith("Executive Diagnosis"):
            exec_lines = lines
            break
    if exec_lines:
        txt = "\n".join(exec_lines)
        def grab(pat):
            mm = re.search(pat, txt)
            return mm.group(1) if mm else None
        report["clicks90"] = _num(grab(r"Total clicks \(last 90d\):\s*\*\*([^*]+)\*\*"))
        report["impressions90"] = _num(grab(r"Total impressions \(last 90d\):\s*\*\*([^*]+)\*\*"))
        ctr = grab(r"Overall CTR:\s*\*\*([\d.]+)%\*\*")
        report["ctr"] = float(ctr) if ctr else None
        report["priority_pages_count"] = _num(grab(r"Priority pages:\s*\*\*(\d+)\*\*"))
        report["content_gaps_count"] = _num(grab(r"Content-gap opportunities:\s*\*\*(\d+)\*\*"))
        report["technical_flags"] = _num(grab(r"Technical flags:\s*\*\*(\d+)\*\*"))
        report["issue_types"] = _num(grab(r"Technical flags:.*across\s*\*\*(\d+)\*\*"))
        mm = re.search(
            r"Traffic \(28d vs prior 28d\):\s*clicks\s*([+-]?\d+),\s*impressions\s*([+-]?\d+)\s*([↗↘→↑↓])",
            txt,
        )
        if mm:
            report["movement"] = {
                "clicks_delta": _num(mm.group(1)),
                "impressions_delta": _num(mm.group(2)),
                "direction": mm.group(3),
            }

    for title, lines in sec.items():
        if title.startswith("🧱") or "Technical Issues" in title:
            for header, rows in _tables(lines):
                if header and header[0].lower().startswith("issue"):
                    report["technical_issues"] = [
                        {"type": r[0], "pages": _num(r[1]), "example": r[2] if len(r) > 2 else ""}
                        for r in rows
                    ]
        elif title.startswith("Traffic Sources"):
            for header, rows in _tables(lines):
                report["traffic_sources"] = [
                    {"source": r[0], "sessions": _num(r[1]), "users": _num(r[2]) if len(r) > 2 else None}
                    for r in rows
                ]
        elif "Traffic Movement" in title:
            report["windows"] = _parse_windows(lines)
        elif "Treatment Plan" in title:
            report["priority_pages"] = _parse_priority_pages(lines)
        elif title.startswith("🔎") or "Content Gaps" in title:
            for header, rows in _tables(lines):
                if header and header[0].lower().startswith("query"):
                    report["content_gaps"] = [
                        {"query": r[0], "impressions": _num(r[1]), "best_position": _num(r[2]) if len(r) > 2 else None}
                        for r in rows
                    ]
        elif "Internal Linking" in title:
            for header, rows in _tables(lines):
                report["internal_link_suggestions"] = len(rows)

    return report


def _parse_windows(lines) -> list:
    windows = []
    starts = [i for i, l in enumerate(lines) if re.match(r"^###\s+Window:\s*last\s+\d+d", l)]
    for k, i in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(lines)
        head = re.match(r"^###\s+Window:\s*last\s+(\d+)d vs previous\s+(\d+)d", lines[i])
        if not head:
            continue
        w = {"days": int(head.group(1)), "metrics": {}, "declining_pages": [],
             "rising_pages": [], "declining_queries": []}
        for label, header, rows in _tables_with_labels(lines[i + 1:end]):
            if header and header[0].strip().lower() == "metric":
                for r in rows:
                    name = r[0].strip().lower()
                    w["metrics"][name] = {
                        "current": _num(r[1]) if len(r) > 1 else None,
                        "previous": _num(r[2]) if len(r) > 2 else None,
                        "change": r[3] if len(r) > 3 else None,
                    }
            elif header and header[0].strip().lower() == "page":
                items = [{
                    "page": r[0],
                    "clicks_delta": _num(r[1]) if len(r) > 1 else None,
                    "impressions_delta": _num(r[2]) if len(r) > 2 else None,
                    "position_delta": _num(r[3]) if len(r) > 3 else None,
                    "cause": r[4] if len(r) > 4 else None,
                } for r in rows]
                if label and "rising" in label.lower():
                    w["rising_pages"] = items
                else:
                    w["declining_pages"] = items
            elif header and header[0].strip().lower() == "query":
                w["declining_queries"] = [{
                    "query": r[0], "clicks_delta": _num(r[1]) if len(r) > 1 else None,
                    "impressions_delta": _num(r[2]) if len(r) > 2 else None,
                } for r in rows]
        windows.append(w)
    return windows


def _parse_priority_pages(lines) -> list:
    pages = []
    cur = None
    for line in lines:
        m = re.match(r"^###\s+\d+\.\s+\[([^\]]+)\]\(([^)]+)\)", line.strip())
        if m:
            cur = {"url": m.group(2), "label": m.group(1), "diagnosis": None,
                   "lost_clicks": None, "impressions": None,
                   "recommended_title": None, "recommended_meta": None}
            pages.append(cur)
            continue
        if cur is not None:
            m2 = re.match(r"^-\s+\*\*(.+?):\*\*\s*(.*)$", line.strip())
            if m2:
                key, val = m2.group(1).strip(), m2.group(2).strip()
                if key == "Diagnosis":
                    cur["diagnosis"] = val or None
                elif key == "Estimated lost clicks":
                    cur["lost_clicks"] = _num(val)
                elif key == "Impressions":
                    cur["impressions"] = _num(val)
                elif key == "Recommended title":
                    cur["recommended_title"] = val or None
                elif key == "Recommended meta description":
                    cur["recommended_meta"] = val or None
    return pages


def parse_health(md: str) -> dict:
    health = {
        "date": None, "status": None, "ok": None, "pages_checked": None,
        "ssl_days": None, "availability": [], "broken_links": None,
        "redirect_chains": None, "sitemap_urls": None,
    }
    m = re.search(r"#\s*🏥?\s*Website Health Report\s*—\s*(\d{4}-\d{2}-\d{2})", md)
    if m:
        health["date"] = m.group(1)
    sec = {s["title"]: s["lines"] for s in _sections(md)}
    for title, lines in sec.items():
        txt = "\n".join(lines)
        if title.lower().startswith("status"):
            mm = re.search(r"Overall:\s*\*\*(.+?)\*\*", txt)
            if mm:
                health["status"] = mm.group(1).strip()
                health["ok"] = "🔴" not in health["status"] and "critical" not in health["status"].lower()
            health["pages_checked"] = _num((re.search(r"Pages checked:\s*\*\*(\d+)\*\*", txt) or [None, None])[1])
            health["ssl_days"] = _num((re.search(r"SSL certificate:\s*\*\*(\d+)\s*days", txt) or [None, None])[1])
        elif "Availability" in title:
            for header, rows in _tables(lines):
                health["availability"] = [{"url": r[0], "status": r[1] if len(r) > 1 else ""} for r in rows]
        elif "Broken internal links" in title:
            health["broken_links"] = 0 if re.search(r"None found", txt) else len([l for l in lines if l.strip().startswith("|") and "-" not in l])
        elif "Redirect chains" in title:
            health["redirect_chains"] = 0 if re.search(r"None found", txt) else len([l for l in lines if l.strip().startswith("|") and "-" not in l])
        elif "robots.txt & sitemap" in title or "sitemap.xml" in title.lower():
            mm = re.search(r"URLs listed:\s*\*\*(\d+)\*\*", txt)
            if mm:
                health["sitemap_urls"] = _num(mm.group(1))
    return health


# --------------------------------------------------------------------------- #
# index builder
# --------------------------------------------------------------------------- #
def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def build_index(reports_dir: str, history_path: str, repo: str) -> dict:
    reports = []
    dated = sorted(glob.glob(os.path.join(reports_dir, "[0-9]*-[0-9]*-[0-9]*.md")))
    latest_md = os.path.join(reports_dir, "latest.md")
    if not dated and os.path.exists(latest_md):
        dated = [latest_md]
    for path in dated:
        md = _read(path)
        if not md:
            continue
        parsed = parse_report(md)
        if not parsed.get("date"):
            parsed["date"] = os.path.splitext(os.path.basename(path))[0]
        parsed["source"] = os.path.basename(path)
        reports.append(parsed)
    reports.sort(key=lambda r: r["date"])

    health = None
    hdated = sorted(glob.glob(os.path.join(reports_dir, "health", "[0-9]*-[0-9]*-[0-9]*.md")))
    hlatest = os.path.join(reports_dir, "health", "latest.md")
    if not hdated and os.path.exists(hlatest):
        hdated = [hlatest]
    if hdated:
        md = _read(hdated[-1])
        if md:
            health = parse_health(md)
            if not health.get("date"):
                health["date"] = os.path.splitext(os.path.basename(hdated[-1]))[0]
    health_history = []
    for path in hdated:
        md = _read(path)
        if not md:
            continue
        h = parse_health(md)
        if not h.get("date"):
            h["date"] = os.path.splitext(os.path.basename(path))[0]
        health_history.append(h)

    fix_plan = None
    fp_path = os.path.join(reports_dir, "fix_plan.json")
    if os.path.exists(fp_path):
        try:
            fix_plan = json.loads(_read(fp_path) or "null")
        except ValueError:
            fix_plan = None

    fixes_history = []
    if os.path.exists(history_path):
        try:
            fixes_history = json.loads(_read(history_path) or "[]")
        except ValueError:
            fixes_history = []

    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repo": repo,
        "site": SITE_URL,
        "latest_report": reports[-1]["date"] if reports else None,
        "reports": reports,
        "health": health,
        "health_history": health_history,
        "fix_plan": fix_plan,
        "fixes_history": fixes_history,
    }


# --------------------------------------------------------------------------- #
# rendering helpers
# --------------------------------------------------------------------------- #
def _delta_chip(value):
    """Colored +/- chip for a numeric delta (None -> em dash)."""
    if value is None:
        return _Safe('<span class="muted">—</span>')
    cls = "up" if value > 0 else ("down" if value < 0 else "flat")
    sign = "+" if value > 0 else ""
    arrow = "▲" if value > 0 else ("▼" if value < 0 else "•")
    return _Safe(f'<span class="chip {cls}">{arrow} {sign}{value}</span>')


def _trend_chip(change_text):
    if not change_text:
        return _Safe('<span class="muted">—</span>')
    t = str(change_text).strip()
    low = t.lower()
    if t in ("±0%", "0", "+0", "-0"):
        return _Safe(f'<span class="chip flat">• {ESC(t)}</span>')
    if low.startswith("-"):
        return _Safe(f'<span class="chip down">▼ {ESC(t)}</span>')
    if low.startswith("+"):
        return _Safe(f'<span class="chip up">▲ {ESC(t)}</span>')
    return _Safe(f'<span class="muted">{ESC(t)}</span>')


def _sparkline(values, color="#6366f1", width=150, height=36):
    vals = [v for v in values if isinstance(v, (int, float))]
    if len(vals) < 2:
        return _Safe('<span class="muted">not enough data</span>')
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    n = len(vals)
    pts = []
    for i, v in enumerate(vals):
        x = 3 + i * (width - 6) / (n - 1)
        y = (height - 4) - (v - lo) * (height - 8) / rng + 2
        pts.append(f"{x:.1f},{y:.1f}")
    last = pts[-1].split(",")
    return _Safe(
        f'<svg class="spark" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'preserveAspectRatio="none" role="img" aria-hidden="true">'
        f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{last[0]}" cy="{last[1]}" r="2.6" fill="{color}"/></svg>'
    )


def _chart(values, labels, color="#6366f1", width=720, height=180):
    vals = [v for v in values if isinstance(v, (int, float))]
    if len(vals) < 2:
        return '<p class="muted">Not enough history yet — charts appear after a few weekly runs.</p>'
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    n = len(vals)
    pad_l, pad_r, pad_t, pad_b = 44, 12, 14, 26
    iw, ih = width - pad_l - pad_r, height - pad_t - pad_b
    pts = []
    for i, v in enumerate(vals):
        x = pad_l + i * iw / (n - 1)
        y = pad_t + ih - (v - lo) * ih / rng
        pts.append((x, y))
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}"/>' for x, y in pts)
    grid = ""
    for frac in (0, 0.5, 1):
        gy = pad_t + ih * frac
        grid += f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}" stroke="#8883" stroke-width="1"/>'
    ymax = f'<text x="{pad_l - 6}" y="{pad_t + 4}" text-anchor="end" class="axis">{hi:g}</text>'
    ymin = f'<text x="{pad_l - 6}" y="{pad_t + ih + 4}" text-anchor="end" class="axis">{lo:g}</text>'
    xlab = ""
    if labels:
        xlab = (f'<text x="{pad_l}" y="{height - 8}" class="axis">{ESC(str(labels[0]))}</text>'
                f'<text x="{width - pad_r}" y="{height - 8}" text-anchor="end" class="axis">{ESC(str(labels[-1]))}</text>')
    area = f'<polygon points="{pad_l},{pad_t + ih} {line} {pad_l + iw},{pad_t + ih}" fill="{color}" opacity="0.08"/>'
    return (f'<svg class="chart" viewBox="0 0 {width} {height}" width="100%" height="{height}" '
            f'role="img">{grid}{area}'
            f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>'
            f'{dots}{ymax}{ymin}{xlab}</svg>')


def _table(headers, rows, cls=""):
    if not rows:
        return '<p class="muted">No data.</p>'
    th = "".join(f"<th>{ESC(str(h))}</th>" for h in headers)
    body = []
    for r in rows:
        tds = "".join(
            f"<td>{c if isinstance(c, _Safe) else ESC(str(c))}</td>" for c in r
        )
        body.append(f"<tr>{tds}</tr>")
    return f'<div class="tw"><table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def _linkify(url, text=None):
    text = text if text is not None else url
    return _Safe(f'<a href="{ESC(url)}" target="_blank" rel="noopener">{ESC(text)}</a>')


def _short(url, n=58):
    url = re.sub(r"^https?://(www\.)?", "", url or "")
    return url if len(url) <= n else url[: n - 1] + "…"


# --------------------------------------------------------------------------- #
# dashboard HTML
# --------------------------------------------------------------------------- #
CSS = """
:root{--bg:#f6f7fb;--panel:#fff;--ink:#1e2230;--muted:#6b7280;--line:#e6e8f0;
--up:#0f9d58;--down:#d93025;--flat:#8a8f9c;--accent:#4f46e5;--accent2:#0ea5e9;}
@media (prefers-color-scheme:dark){:root{--bg:#0f1117;--panel:#171a22;--ink:#e8eaf1;
--muted:#9aa2b1;--line:#272b36;--up:#34d399;--down:#f87171;--flat:#9aa2b1;--accent:#818cf8;--accent2:#38bdf8;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Inter,sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
header.top{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;padding:26px 20px 22px}
header.top .wrap{max-width:1080px;margin:0 auto}
h1{margin:0 0 4px;font-size:23px;letter-spacing:.2px}
header.top .sub{opacity:.9;font-size:13px}
header.top .sub a{color:#fff;text-decoration:underline}
main{max-width:1080px;margin:0 auto;padding:18px 20px 60px}
section{margin:24px 0}
h2{font-size:17px;margin:0 0 12px;padding-bottom:7px;border-bottom:2px solid var(--line)}
h3{font-size:14px;margin:16px 0 8px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 15px}
.card .label{font-size:11.5px;text-transform:uppercase;letter-spacing:.5px;color:var(--muted)}
.card .value{font-size:25px;font-weight:700;margin:5px 0 3px}
.card .foot{font-size:12px;color:var(--muted)}
.chip{display:inline-block;font-size:12px;font-weight:600;border-radius:999px;padding:1px 8px}
.chip.up{background:color-mix(in srgb,var(--up) 16%,transparent);color:var(--up)}
.chip.down{background:color-mix(in srgb,var(--down) 16%,transparent);color:var(--down)}
.chip.flat{background:color-mix(in srgb,var(--flat) 16%,transparent);color:var(--flat)}
.muted{color:var(--muted)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.health{display:flex;flex-wrap:wrap;gap:22px;align-items:center}
.health .big{font-size:22px;font-weight:700}
.tw{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--panel)}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{padding:8px 11px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
th{background:color-mix(in srgb,var(--accent) 7%,transparent);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.3px;color:var(--muted)}
td.wrap,th.wrap{white-space:normal;min-width:240px}
tr:last-child td{border-bottom:none}
td.num,th.num{text-align:right}
svg.chart{max-width:100%;display:block}
svg.spark{vertical-align:middle}
text.axis{fill:var(--muted);font-size:11px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:760px){.grid2{grid-template-columns:1fr}}
.fix{border-left:3px solid var(--accent);padding:2px 0 2px 12px;margin:12px 0}
.fix .meta{font-size:12px;color:var(--muted);margin-bottom:4px}
.diff{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;margin:2px 0}
.diff .old{color:var(--down)}.diff .new{color:var(--up)}
footer{color:var(--muted);font-size:12px;text-align:center;padding:22px}
nav.crumbs{max-width:1080px;margin:0 auto;padding:10px 20px;font-size:13px}
.note{background:color-mix(in srgb,var(--accent) 8%,transparent);border:1px solid var(--line);border-radius:10px;padding:10px 14px;font-size:13px}
"""


def _page(title, body, repo, extra_head=""):
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{ESC(title)}</title>
<style>{CSS}</style>{extra_head}
</head><body>
{body}
<footer>Generated by <a href="https://github.com/{ESC(repo)}">KrishnBhakti SEO Doctor Agent</a> · Phase 4 dashboard</footer>
</body></html>"""


def _kpi_cards(index, latest, w28, w7):
    def metric(w, name, key="current"):
        if not w:
            return None
        return (w["metrics"].get(name) or {}).get(key)

    def card(label, value, delta_html="", foot=""):
        return (f'<div class="card"><div class="label">{ESC(label)}</div>'
                f'<div class="value">{value}</div>'
                f'<div class="foot">{delta_html} {foot}</div></div>')

    cards = []
    h = index.get("health")
    if h:
        status = h.get("status") or "unknown"
        cards.append(card("Website health", f'<span class="big">{ESC(status)}</span>',
                          foot=f'checked {h.get("pages_checked") or "?"} pages'))
    if w28:
        cur_clicks = metric(w28, "clicks")
        cur_imp = metric(w28, "impressions")
        cards.append(card("Clicks (28d)", f"{cur_clicks:g}" if cur_clicks is not None else "—",
                          _trend_chip((w28["metrics"].get("clicks") or {}).get("change")), "vs prior 28d"))
        cards.append(card("Impressions (28d)", f"{cur_imp:g}" if cur_imp is not None else "—",
                          _trend_chip((w28["metrics"].get("impressions") or {}).get("change")), "vs prior 28d"))
        pos = metric(w28, "avg position")
        cards.append(card("Avg position (28d)", f"{pos:g}" if pos is not None else "—",
                          _trend_chip((w28["metrics"].get("avg position") or {}).get("change")), "lower is better"))
        sess = metric(w28, "ga4 sessions")
        if sess is not None:
            cards.append(card("GA4 sessions (28d)", f"{sess:g}",
                              _trend_chip((w28["metrics"].get("ga4 sessions") or {}).get("change")), "vs prior 28d"))
    if latest:
        tf = latest.get("technical_flags")
        cards.append(card("Technical flags", f"{tf:g}" if tf is not None else "—",
                          foot=f'across {latest.get("issue_types") or "?"} issue types'))
    if w7:
        c7 = metric(w7, "clicks")
        if c7 is not None:
            cards.append(card("Clicks (7d)", f"{c7:g}",
                              _trend_chip((w7["metrics"].get("clicks") or {}).get("change")), "vs prior 7d"))
    return '<div class="cards">' + "".join(cards) + "</div>"


def build_dashboard_html(index) -> str:
    repo = index["repo"]
    reports = index["reports"]
    latest = reports[-1] if reports else {}
    w28 = next((w for w in latest.get("windows", []) if w["days"] == 28), None)
    w7 = next((w for w in latest.get("windows", []) if w["days"] == 7), None)
    generated = index.get("generated_at", "")

    # ---- header
    nav = (f'<a href="#overview">Overview</a> · <a href="#trends">Trends</a> · '
           f'<a href="#technical">Technical</a> · <a href="#pages">Priority pages</a> · '
           f'<a href="#gaps">Content gaps</a> · <a href="#declines">Declines</a> · '
           f'<a href="#fixes">Fix history</a> · <a href="reports/index.html">All reports</a> · '
           f'<a href="https://github.com/{ESC(repo)}">Repo</a>')
    header = (f'<header class="top"><div class="wrap"><h1>🩺 KrishnBhakti SEO Dashboard</h1>'
              f'<div class="sub">{ESC(index.get("site",""))} · latest report '
              f'<strong>{ESC(str(latest.get("date") or "—"))}</strong> · updated {ESC(generated)}'
              f'</div></div></header>')

    parts = [header, f'<nav class="crumbs">{nav}</nav>', '<main>']

    # ---- overview
    parts.append('<section id="overview"><h2>Overview</h2>')
    parts.append(_kpi_cards(index, latest, w28, w7))
    h = index.get("health")
    if h:
        avail_bad = [a for a in h.get("availability", []) if str(a.get("status")) != "200"]
        parts.append('<h3>Website health</h3><div class="panel health">'
                     f'<div><div class="big">{ESC(h.get("status") or "—")}</div>'
                     f'<div class="muted">as of {ESC(str(h.get("date") or "—"))}</div></div>'
                     f'<div><div class="muted">SSL expires in</div><div class="big">{h.get("ssl_days") if h.get("ssl_days") is not None else "—"} days</div></div>'
                     f'<div><div class="muted">Critical URLs</div><div class="big">{len(h.get("availability", [])) - len(avail_bad)}/{len(h.get("availability", []))} ok</div></div>'
                     f'<div><div class="muted">Broken links</div><div class="big">{h.get("broken_links") if h.get("broken_links") is not None else "—"}</div></div>'
                     f'<div><div class="muted">Redirect chains</div><div class="big">{h.get("redirect_chains") if h.get("redirect_chains") is not None else "—"}</div></div>'
                     '</div>')
    parts.append("</section>")

    # ---- trends
    parts.append('<section id="trends"><h2>Trends across runs</h2>')
    if len(reports) >= 2:
        labels = [r["date"] for r in reports]

        def metric28(r, name):
            ww = next((w for w in r["windows"] if w["days"] == 28), None) or {}
            return (ww.get("metrics", {}).get(name) or {}).get("current")

        def avail(vals):
            return sum(1 for v in vals if isinstance(v, (int, float)))

        c28 = [metric28(r, "clicks") for r in reports]
        i28 = [metric28(r, "impressions") for r in reports]
        p28 = [metric28(r, "avg position") for r in reports]
        c90 = [r.get("clicks90") for r in reports]
        i90 = [r.get("impressions90") for r in reports]
        flags = [r.get("technical_flags") for r in reports]
        has28 = avail(c28) >= 2

        # The 28-day window only exists once Phase 2 trends have run a few times;
        # fall back to the rolling 90-day totals so the chart is never empty.
        charts = []
        if avail(c28) >= 3:
            charts.append(("28-day clicks", c28, "#4f46e5"))
        else:
            charts.append(("Clicks — rolling 90d", c90, "#4f46e5"))
        if avail(i28) >= 3:
            charts.append(("28-day impressions", i28, "#0ea5e9"))
        else:
            charts.append(("Impressions — rolling 90d", i90, "#0ea5e9"))
        charts += [("Average position (lower is better)", p28, "#0f9d58"),
                   ("Technical flags", flags, "#d93025")]
        renderable = [(t, s, c) for t, s, c in charts if avail(s) >= 2]
        if renderable:
            parts.append('<div class="grid2">')
            for title, series, color in renderable:
                parts.append(f'<div class="panel"><h3>{ESC(title)}</h3>{_chart(series, labels, color)}</div>')
            parts.append("</div>")
        else:
            parts.append('<p class="muted">Charts appear after a few weekly runs.</p>')

        def mv(m, name):
            v = (m.get(name) or {}).get("current")
            return f"{v:g}" if isinstance(v, (int, float)) else "—"

        headers = ["Run", "Clicks (90d)", "Impr (90d)", "CTR"]
        if has28:
            headers += ["Clicks 28d", "Δ", "Impr 28d", "Pos 28d"]
        headers += ["Tech flags", "Priority"]

        rows = []
        for r in reversed(reports):
            rr = next((w for w in r["windows"] if w["days"] == 28), None) or {}
            m = rr.get("metrics", {})
            row = [
                _linkify(f"reports/{r['date']}.html", r["date"]),
                r.get("clicks90") if r.get("clicks90") is not None else "—",
                r.get("impressions90") if r.get("impressions90") is not None else "—",
                f'{r.get("ctr")}%' if r.get("ctr") is not None else "—",
            ]
            if has28:
                row += [mv(m, "clicks"), _trend_chip((m.get("clicks") or {}).get("change")),
                        mv(m, "impressions"), mv(m, "avg position")]
            row += [r.get("technical_flags") if r.get("technical_flags") is not None else "—",
                    r.get("priority_pages_count") if r.get("priority_pages_count") is not None else "—"]
            rows.append(row)
        parts.append('<h3>Run-by-run table</h3>')
        parts.append(_table(headers, rows))
    else:
        parts.append('<p class="muted">Charts and run history appear after a few weekly runs.</p>')
    parts.append("</section>")

    # ---- technical
    parts.append('<section id="technical"><h2>Technical issues</h2>')
    tech = latest.get("technical_issues") or []
    if tech:
        parts.append(_table(["Issue type", "Pages affected", "Example page"],
                            [[t["type"], t.get("pages", ""), _linkify(t.get("example", ""), _short(t.get("example", "")))] for t in tech]))
        # trend by type
        types = [t["type"] for t in tech]
        if len(reports) >= 2:
            rows = []
            for t in types:
                counts = []
                for r in reports:
                    c = next((x.get("pages") for x in (r.get("technical_issues") or []) if x["type"] == t), 0)
                    counts.append(c if isinstance(c, (int, float)) else 0)
                rows.append([t, _sparkline(counts, "#d93025"), counts[-1]])
            parts.append('<h3>Trend by issue type</h3>')
            parts.append(_table(["Issue type", "Recent trend", "Latest"], rows))
    else:
        parts.append('<p class="muted">No technical issues flagged in the latest report.</p>')
    parts.append("</section>")

    # ---- priority pages
    parts.append('<section id="pages"><h2>Priority pages — what to fix</h2>')
    pp = latest.get("priority_pages") or []
    if pp:
        rows = []
        for p in pp:
            rows.append([
                _linkify(p["url"], _short(p["url"], 50)),
                p.get("diagnosis") or "—",
                p.get("lost_clicks") if p.get("lost_clicks") is not None else "—",
                p.get("impressions") if p.get("impressions") is not None else "—",
                p.get("recommended_title") or "—",
            ])
        parts.append(_table(["Page", "Diagnosis", "Est. lost clicks", "Impr", "Recommended title"], rows))
    else:
        parts.append('<p class="muted">No priority pages in the latest report.</p>')
    parts.append("</section>")

    # ---- content gaps
    parts.append('<section id="gaps"><h2>Content gaps — new article opportunities</h2>')
    gaps = latest.get("content_gaps") or []
    if gaps:
        parts.append(_table(["Query", "Impressions", "Best position", "Action"],
                            [[_Safe(f"<code>{ESC(g['query'])}</code>"), g.get("impressions", ""),
                              g.get("best_position", ""), "Create/strengthen a dedicated page"] for g in gaps]))
    else:
        parts.append('<p class="muted">No content-gap opportunities flagged.</p>')
    parts.append("</section>")

    # ---- declines
    parts.append('<section id="declines"><h2>Declining pages</h2>')
    if w28:
        decl = w28.get("declining_pages") or []
        if decl:
            parts.append(_table(
                ["Page", "Clicks Δ", "Impressions Δ", "Position Δ", "Likely cause"],
                [[_linkify(d["page"], _short(d["page"], 46)), _delta_chip(d.get("clicks_delta")),
                  _delta_chip(d.get("impressions_delta")),
                  (_Safe('<span class="chip flat">n/a</span>') if d.get("position_delta") is None else _delta_chip(d["position_delta"])),
                  _Safe(f'<span class="wrap">{ESC((d.get("cause") or "")[:110])}</span>')] for d in decl],
                cls=""))
        else:
            parts.append('<p class="muted">No declining pages flagged in the latest 28-day window. ✅</p>')
        ris = w28.get("rising_pages") or []
        if ris:
            parts.append('<h3>Rising pages</h3>')
            parts.append(_table(
                ["Page", "Clicks Δ", "Impressions Δ", "Position Δ"],
                [[_linkify(r["page"], _short(r["page"], 46)), _delta_chip(r.get("clicks_delta")),
                  _delta_chip(r.get("impressions_delta")),
                  (_Safe('<span class="chip flat">n/a</span>') if r.get("position_delta") is None else _delta_chip(r["position_delta"]))] for r in ris]))
    else:
        parts.append('<p class="muted">No period-over-period data available.</p>')
    parts.append("</section>")

    # ---- fix history
    parts.append('<section id="fixes"><h2>Fix history</h2>')
    plan = index.get("fix_plan")
    if plan and plan.get("fixes"):
        groups = plan["fixes"]
        total_changes = sum(len(g.get("changes", [])) for g in groups)
        parts.append(f'<p class="note">Latest fix plan: <strong>{len(groups)}</strong> page(s), '
                     f'<strong>{total_changes}</strong> change(s) queued for the next auto-fix PR '
                     f'(generated {ESC(str(plan.get("generated_at") or "—"))}).</p>')
    history = index.get("fixes_history") or []
    if history:
        for entry in reversed(history):
            status = entry.get("status", "?")
            direction = entry.get("direction", "pending")
            badge = {"merged": "up", "open": "flat", "closed": "flat", "reverted": "down"}.get(status, "flat")
            parts.append('<div class="fix">')
            parts.append(f'<div class="meta">{ESC(str(entry.get("date") or ""))} · '
                         f'PR <a href="{ESC(entry.get("pr_url") or entry.get("url") or "#")}" target="_blank" rel="noopener">'
                         f'#{entry.get("pr")}</a> · '
                         f'<span class="chip {badge}">{ESC(status)}</span> · '
                         f'verification: <strong>{ESC(direction)}</strong> · '
                         f'{len(entry.get("pages") or [])} page(s)</div>')
            for fix in entry.get("fixes", []):
                parts.append(f'<div><a href="{ESC(fix.get("url") or "#")}" target="_blank" rel="noopener">{ESC(_short(fix.get("url") or "", 70))}</a></div>')
                for ch in fix.get("changes", []):
                    old = (ch.get("old") or "").strip()
                    new = (ch.get("new") or "").strip()
                    parts.append(f'<div class="diff"><strong>{ESC(ch.get("type") or "")}:</strong> '
                                 + (f'<span class="old">− {ESC(old[:160])}</span> ' if old else '<span class="muted">(added)</span> ')
                                 + f'<span class="new">→ {ESC(new[:160])}</span></div>')
            parts.append('</div>')
    else:
        parts.append('<p class="muted">No fixes have been proposed yet.</p>')
    parts.append("</section>")

    parts.append("</main>")
    return _page("KrishnBhakti SEO Dashboard", "".join(parts), repo)


# --------------------------------------------------------------------------- #
# markdown report rendering
# --------------------------------------------------------------------------- #
def _md_to_html(md: str) -> str:
    if _markdown is not None:
        return _markdown.markdown(md, extensions=["tables", "fenced_code", "sane_lists"])
    return f'<pre class="raw">{ESC(md)}</pre>'  # graceful fallback (no `markdown` installed)


def _render_report_page(title, md, repo):
    body = (f'<header class="top"><div class="wrap"><h1>{ESC(title)}</h1></div></header>'
            f'<nav class="crumbs"><a href="../index.html">← Dashboard</a> · '
            f'<a href="index.html">All reports</a></nav>'
            f'<main><div class="panel report">{_md_to_html(md)}</div></main>')
    return _page(title, body, repo, extra_head=f"<style>.report table{{width:auto;min-width:60%}}"
                                               f".report th,.report td{{white-space:normal}}"
                                               f".report h2{{border-bottom:1px solid var(--line)}}"
                                               f"pre.raw{{white-space:pre-wrap}}</style>")


def _reports_index_page(reports, health_reports, repo):
    rows = []
    for r in reversed(reports):
        rows.append([_linkify(f"{r['date']}.html", r["date"]),
                     r.get("clicks90", "—"), r.get("impressions90", "—"),
                     f'{r.get("ctr")}%' if r.get("ctr") is not None else "—",
                     r.get("priority_pages_count", "—"), r.get("technical_flags", "—")])
    hrows = []
    for h in reversed(health_reports):
        hrows.append([_linkify(f"health/{h['date']}.html", h["date"]),
                      h.get("status") or "—", h.get("pages_checked", "—"), h.get("ssl_days", "—")])
    body = ('<header class="top"><div class="wrap"><h1>📚 All reports</h1></div></header>'
            '<nav class="crumbs"><a href="../index.html">← Dashboard</a></nav><main>'
            '<section><h2>Weekly SEO Doctor reports</h2>'
            + _table(["Date", "Clicks (90d)", "Impr (90d)", "CTR", "Priority pages", "Tech flags"], rows)
            + '</section><section><h2>Website health reports</h2>'
            + _table(["Date", "Status", "Pages checked", "SSL days"], hrows)
            + "</section></main>")
    return _page("All reports · KrishnBhakti SEO", body, repo)


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def build(reports_dir: str, history_path: str, out_dir: str, repo: str) -> dict:
    index = build_index(reports_dir, history_path, repo)
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, "data"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "reports", "health"), exist_ok=True)

    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(build_dashboard_html(index))
    with open(os.path.join(out_dir, "data", "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)

    # full reports, rendered
    for r in index["reports"]:
        src = os.path.join(reports_dir, r.get("source") or f"{r['date']}.md")
        md = _read(src)
        if not md:
            continue
        with open(os.path.join(out_dir, "reports", f"{r['date']}.html"), "w", encoding="utf-8") as f:
            f.write(_render_report_page(f"SEO Doctor Report — {r['date']}", md, repo))
    for h in index.get("health_history", []):
        src = os.path.join(reports_dir, "health", f"{h['date']}.md")
        md = _read(src)
        if not md:
            continue
        with open(os.path.join(out_dir, "reports", "health", f"{h['date']}.html"), "w", encoding="utf-8") as f:
            f.write(_render_report_page(f"Website Health — {h['date']}", md, repo))
    with open(os.path.join(out_dir, "reports", "index.html"), "w", encoding="utf-8") as f:
        f.write(_reports_index_page(index["reports"], index.get("health_history", []), repo))

    return index


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the static SEO dashboard.")
    ap.add_argument("--reports-dir", default=os.path.join(ROOT, "reports"))
    ap.add_argument("--history", default=os.path.join(ROOT, "fixes_history.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "docs"))
    ap.add_argument("--repo", default=DEFAULT_REPO)
    args = ap.parse_args(argv)

    index = build(args.reports_dir, args.history, args.out, args.repo)
    n_rep = len(index["reports"])
    n_health = len(index.get("health_history", []))
    print(f"Dashboard written to {args.out}")
    print(f"  {n_rep} weekly report(s), {n_health} health report(s)")
    print(f"  latest: {index.get('latest_report')} · health: {(index.get('health') or {}).get('status')}")
    print(f"  fix plan: {'yes' if index.get('fix_plan') else 'no'} · "
          f"fix history: {len(index.get('fixes_history') or [])} entr(ies)")


if __name__ == "__main__":
    main()
