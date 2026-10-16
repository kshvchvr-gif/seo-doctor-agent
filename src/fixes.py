"""
Phase 3 — safe, minimal-diff on-page fixes (plan + apply).

Design rules (deliberately conservative):
  * Only low-risk things are touched: <title>, meta description, canonical
    link, image alt text, and (opt-in) one appended "Also read" link block.
  * Every change is an EXACT string replacement on a unique anchor. If the
    anchor is not found exactly once, the fix is skipped — never a blind
    whole-file rewrite. Diffs stay tiny and reviewable.
  * This module never talks to the network or to git — auto_fix.py does that —
    so the whole fix logic is unit-testable offline against real site HTML.

URL -> file mapping follows the site (Firebase cleanUrls):
  https://krishnbhakti.com/                       -> index.html
  https://krishnbhakti.com/english-blogs/<slug>   -> english-blogs/<slug>.html
  https://krishnbhakti.com/panchang               -> panchang.html
"""
import html as _html
import os
import re
from urllib.parse import urlparse

# length guards mirror scoring.technical_audit()
TITLE_MIN, TITLE_MAX = 30, 70
META_MIN, META_MAX = 100, 180
# what we aim for when we generate text ourselves
TITLE_TARGET_MAX = 65
META_TARGET_MAX = 165


def url_to_repo_path(url: str) -> str:
    """Map an absolute site URL to its repo-relative HTML file."""
    path = urlparse(url).path.strip("/")
    if not path:
        return "index.html"
    if path.endswith(".html"):
        return path
    # cleanUrls: /english-blogs/foo and /english-blogs/foo.html both -> .../foo.html
    return f"{path}.html"


def _esc_text(s: str) -> str:
    return _html.escape(s or "", quote=False)


def _esc_attr(s: str) -> str:
    # attributes are always written double-quoted, so only &, <, >, " need escaping
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# --------------------------------------------------------------------------- #
# individual replace helpers (each returns (new_html, change_dict_or_None))
# --------------------------------------------------------------------------- #
def _replace_title(html: str, new_title: str):
    m = re.search(r"<title>(.*?)</title>", html, flags=re.S | re.I)
    if not m:
        return html, None
    old = m.group(1).strip()
    if old == new_title.strip():
        return html, None
    new_html = html[:m.start(1)] + _esc_text(new_title) + html[m.end(1):]
    return new_html, {"type": "TITLE", "old": old, "new": new_title.strip()}


def _replace_meta_description(html: str, new_desc: str):
    # NOTE: the closing quote must be the SAME character as the opening one
    # (\2 backreference), otherwise an apostrophe inside the text (e.g.
    # "Krishna's") is mistaken for the closing quote and corrupts the value.
    pat = re.compile(
        r'(<meta\s+name=["\']description["\']\s+content=)(["\'])(.*?)\2',
        flags=re.I | re.S,
    )
    m = pat.search(html)
    if not m:
        return html, None
    old = m.group(3)
    if old.strip() == new_desc.strip():
        return html, None
    new_html = html[:m.start(3)] + _esc_attr(new_desc) + html[m.end(3):]
    return new_html, {"type": "META_DESCRIPTION", "old": old.strip(), "new": new_desc.strip()}


def _insert_meta_description(html: str, new_desc: str):
    """Insert a description meta right after </title> when the page has none."""
    if re.search(r'<meta\s+name=["\']description["\']', html, flags=re.I):
        return html, None
    m = re.search(r"</title>", html, flags=re.I)
    if not m:
        return html, None
    ins = f'\n    <meta name="description" content="{_esc_attr(new_desc)}">'
    new_html = html[:m.end()] + ins + html[m.end():]
    return new_html, {"type": "META_DESCRIPTION", "old": "", "new": new_desc.strip()}


# A leftover line like `    content="...">` — the remains of a
# `<meta name="description" content="...">` whose opening tag was lost by an
# earlier bulk edit. It has no '<', so browsers treat it as body text and it
# shows up on the rendered page.
_BROKEN_META_RE = re.compile(r"(?m)^[ \t]*content=[\"'].*$")


def _has_valid_meta_description(html: str) -> bool:
    return bool(re.search(r'<meta\s+name=["\']description["\']', html, flags=re.I))


def _repair_meta_remnant(html: str, new_desc):
    """
    Fix a malformed meta-remnant line.

    If the page already has a valid description the remnant is simply deleted;
    otherwise it is replaced in place with a well-formed description meta. This
    makes the repair idempotent: on a fresh clone it replaces, and on a branch
    that already has an inserted description it deletes the leftover.
    Returns (html, change_or_None).
    """
    m = _BROKEN_META_RE.search(html)
    if not m:
        return html, None
    indent = re.match(r"[ \t]*", m.group(0)).group(0)
    old = m.group(0).strip()
    if _has_valid_meta_description(html) or not new_desc:
        start, end = m.start(), m.end()
        if end < len(html) and html[end] == "\n":
            end += 1
        return html[:start] + html[end:], {"type": "BROKEN_META_REMOVED", "old": old, "new": ""}
    new_line = f'{indent}<meta name="description" content="{_esc_attr(new_desc)}">'
    return html[:m.start()] + new_line + html[m.end():], {
        "type": "META_DESCRIPTION_REPAIRED", "old": old, "new": new_line.strip(),
    }


def _mirror_og(html: str, prop: str, value: str):
    pat = re.compile(
        r'(<meta\s+property=["\']og:' + prop + r'["\']\s+content=)(["\'])(.*?)\2',
        flags=re.I | re.S,
    )
    m = pat.search(html)
    if not m or m.group(3).strip() == value.strip():
        return html, None
    new_html = html[:m.start(3)] + _esc_attr(value) + html[m.end(3):]
    return new_html, {"type": f"OG_{prop.upper()}", "old": m.group(3).strip(), "new": value.strip()}


def _ensure_canonical(html: str, url: str):
    if re.search(r'<link\s+rel=["\']canonical["\']', html, flags=re.I):
        return html, None
    m = re.search(r"</title>", html, flags=re.I)
    if not m:
        return html, None
    ins = f'\n    <link rel="canonical" href="{_esc_attr(url)}" />'
    new_html = html[:m.end()] + ins + html[m.end():]
    return new_html, {"type": "CANONICAL", "old": "", "new": url}


def _add_image_alts(html: str, alt_text: str, max_images: int = 5):
    """Add alt to content <img> tags (src under /images/) that lack one.
    Returns (html, changes). Decorative images are left untouched."""
    changes = []
    if not alt_text:
        return html, changes

    def repl(m):
        if len(changes) >= max_images:
            return m.group(0)
        tag = m.group(0)
        if re.search(r'\balt\s*=', tag, flags=re.I):
            return tag
        if "/images/" not in tag:
            return tag  # only content images, skip icons/logos
        new_tag = tag[: -len(m.group(1))] + f' alt="{_esc_attr(alt_text)}"' + m.group(1)
        changes.append({"type": "IMAGE_ALT", "old": tag, "new": new_tag})
        return new_tag

    new_html = re.sub(r"<img\b[^>]*(\s*/?>)", repl, html, flags=re.I)
    return new_html, changes


def _append_internal_links(html: str, links: list):
    """Append a small 'Also read' block inside .blog-content (no nested divs)."""
    if not links:
        return html, None
    open_m = re.search(r'<div\s+class=["\']blog-content["\'][^>]*>', html, flags=re.I)
    if not open_m:
        return html, None
    close_idx = html.find("</div>", open_m.end())
    if close_idx < 0 or "<div" in html[open_m.end():close_idx].lower():
        return html, None  # nested divs -> unsafe insertion point, skip
    items = " · ".join(f'<a href="{_esc_attr(u)}">{_esc_text(t)}</a>' for u, t in links[:3])
    block = (f'\n<p style="margin-top:40px; padding:15px; background:#fffaf0; '
             f'border-radius:8px; border-left:4px solid #d35400;">📖 '
             f'<b>Also Read:</b> {items}</p>')
    new_html = html[:close_idx] + block + html[close_idx:]
    return new_html, {"type": "INTERNAL_LINKS", "old": "", "new": items}


def _apply_description(html: str, explicit, ensure_meta: bool):
    """
    Handle the meta description for one page, in a safe order:

      1. repair/delete a malformed remnant line (see _repair_meta_remnant);
      2. replace an existing description — only when an explicit value is given
         (ensure_meta is FILL-ONLY and never overwrites);
      3. insert a fresh tag if the page still has none;
      4. keep og:description in sync whenever a description was set.
    Returns (html, changes).
    """
    changes = []
    if explicit:
        target = explicit
    elif ensure_meta:
        target = _derive_meta_from_html(html)
    else:
        return html, changes
    if not target:
        return html, changes

    set_desc = False
    html, c = _repair_meta_remnant(html, target)
    if c:
        changes.append(c)
        set_desc = c["type"] == "META_DESCRIPTION_REPAIRED"

    if explicit:
        html, c = _replace_meta_description(html, target)
        if c:
            changes.append(c)
            set_desc = True

    if not _has_valid_meta_description(html):
        html, c = _insert_meta_description(html, target)
        if c:
            changes.append(c)
            set_desc = True

    if set_desc:
        html, c = _mirror_og(html, "description", target)
        if c:
            changes.append(c)
    return html, changes


# --------------------------------------------------------------------------- #
# high-level apply
# --------------------------------------------------------------------------- #
def apply_page_fixes(html, url, title=None, meta_description=None, ensure_meta=False,
                     ensure_canonical=False, image_alt=None, internal_links=None):
    """Apply the requested fixes to one page's HTML. Returns (new_html, [changes])."""
    changes = []
    if title:
        html, c = _replace_title(html, title)
        if c:
            changes.append(c)
            # keep og:title in sync when the tag exists
            html, c2 = _mirror_og(html, "title", title)
            if c2:
                changes.append(c2)
    if meta_description or ensure_meta:
        html, desc_changes = _apply_description(html, meta_description, ensure_meta)
        changes.extend(desc_changes)
    if ensure_canonical:
        html, c = _ensure_canonical(html, url)
        if c:
            changes.append(c)
    if image_alt:
        html, cs = _add_image_alts(html, image_alt)
        changes.extend(cs)
    if internal_links:
        html, c = _append_internal_links(html, internal_links)
        if c:
            changes.append(c)
    return html, changes


def apply_plan(site_root: str, plan: dict, dry_run: bool = False):
    """
    Apply a fix plan to a checked-out site repo.
    plan = {"fixes": [{"file","url","title","meta_description","ensure_meta",
                       "ensure_canonical","image_alt","internal_links","reason"}, ...]}
    Returns {"applied": [...], "skipped": [...], "files_changed": [...]}.
    """
    applied, skipped, files_changed = [], [], []
    for item in plan.get("fixes", []):
        path = os.path.join(site_root, item["file"])
        if not os.path.exists(path):
            skipped.append({**item, "skip_reason": "file not found"})
            continue
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
        new_html, changes = apply_page_fixes(
            html,
            url=item.get("url", ""),
            title=item.get("title"),
            meta_description=item.get("meta_description"),
            ensure_meta=item.get("ensure_meta", False),
            ensure_canonical=item.get("ensure_canonical", False),
            image_alt=item.get("image_alt"),
            internal_links=item.get("internal_links"),
        )
        if not changes or new_html == html:
            skipped.append({**item, "skip_reason": "no change needed / anchor not found"})
            continue
        if not dry_run:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_html)
        files_changed.append(item["file"])
        applied.append({
            "file": item["file"], "url": item.get("url", ""),
            "reason": item.get("reason", ""), "changes": changes,
        })
    return {"applied": applied, "skipped": skipped, "files_changed": sorted(set(files_changed))}


# --------------------------------------------------------------------------- #
# plan building (from the SEO Doctor pipeline outputs)
# --------------------------------------------------------------------------- #
def _trim_meta(s: str, max_len: int = META_TARGET_MAX) -> str:
    if len(s) <= max_len:
        return s
    cut = s[:max_len]
    # prefer to end at a sentence boundary (Devanagari danda or latin) if far enough in
    for sep in ("।", ".", "!", "?"):
        idx = cut.rfind(sep)
        if idx >= META_MIN // 2:
            return cut[:idx + 1].strip()
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > 0 else cut).strip()


def _derive_meta_from_html(html: str) -> str:
    """
    Derive a plausible meta description from the page's own content paragraphs.

    We start AFTER the <h1> so nav/footer/`<span class="date">` text (all before
    the heading on this site) is excluded, and only use <p> elements — the crawler
    text is too noisy to trust for this.
    """
    h1 = re.search(r"<h1\b", html, flags=re.I)
    body = html[h1.start():] if h1 else html
    picked = []
    total = 0
    for pm in re.finditer(r"<p\b[^>]*>(.*?)</p>", body, flags=re.I | re.S):
        t = re.sub(r"<[^>]+>", " ", pm.group(1))
        t = re.sub(r"\s+", " ", _html.unescape(t)).strip()
        if len(t) < 40:  # skip breadcrumb / lead-in crumbs like "सोचो ज़रा —"
            continue
        picked.append(t)
        total += len(t) + 1
        if total >= META_MIN or len(picked) >= 4:
            break
    if not picked:
        return ""
    return _trim_meta(" ".join(picked))


def build_fix_plan(priority_pages, treatments, pages, link_suggestions=None,
                   include_internal_links=False):
    """
    Turn pipeline outputs into a reviewable fix plan. Only emits a fix when it
    is confident (validated lengths, exact target known). All fixes for the same
    file are merged into a single plan entry.
    """
    treatments = treatments or {}
    link_suggestions = link_suggestions or []
    page_by_url = {p.get("url"): p for p in (pages or [])}

    by_file = {}
    order = []
    ai_meta_urls = set()

    def add(url, reason, **kw):
        file = url_to_repo_path(url)
        entry = by_file.get(file)
        if entry is None:
            entry = {"url": url, "file": file, "_reasons": []}
            by_file[file] = entry
            order.append(file)
        if reason and reason not in entry["_reasons"]:
            entry["_reasons"].append(reason)
        for k, v in kw.items():
            if v and not entry.get(k):
                entry[k] = v
        return entry

    # 1) AI-assisted title/meta for priority pages (bounded by treatments dict)
    for page_diag in (priority_pages or []):
        url = page_diag.get("page")
        treatment = treatments.get(url)
        if not treatment:
            continue
        cur = page_by_url.get(url, {})
        cur_title = (cur.get("title") or "").strip()
        cur_meta = (cur.get("meta_description") or "").strip()
        kw = {}
        rt = (treatment.get("recommended_title") or "").strip()
        # skip no-ops (the deterministic fallback returns the current title)
        if rt and rt != cur_title and TITLE_MIN <= len(rt) <= TITLE_MAX:
            kw["title"] = rt
        rm = (treatment.get("recommended_meta_description") or "").strip()
        if rm and rm != cur_meta and META_MIN <= len(rm) <= META_MAX:
            kw["meta_description"] = rm
        alt = (treatment.get("image_seo") or {}).get("alt_text")
        if alt:
            kw["image_alt"] = alt.strip()
        if kw:
            if "meta_description" in kw:
                ai_meta_urls.add(url)
            add(url, f"AI treatment ({treatment.get('_source', '')})", **kw)

    # 2) deterministic: pages with no meta description get one derived from their
    #    own content paragraphs at apply time (see _derive_meta_from_html). We pass
    #    a flag rather than text so the plan never carries noisy crawler text.
    for p in (pages or []):
        url = p.get("url")
        if "MISSING_META" in p.get("technical_issues", []) and url not in ai_meta_urls:
            add(url, "deterministic: derive missing meta description from page content",
                ensure_meta=True)

    # 3) deterministic: ensure canonical exists
    for p in (pages or []):
        url = p.get("url")
        if "MISSING_CANONICAL" in p.get("technical_issues", []):
            add(url, "deterministic: add missing canonical link", ensure_canonical=True)

    # 4) opt-in: internal links (off by default — layout-affecting)
    if include_internal_links:
        by_from = {}
        for s in link_suggestions:
            by_from.setdefault(s["from_url"], []).append((s["to_url"], s["to_title"]))
        for url, links in by_from.items():
            if url in page_by_url:
                add(url, "deterministic: append 'Also read' internal links",
                    internal_links=links[:3])

    fixes = []
    for file in order:
        entry = by_file[file]
        entry["reason"] = "; ".join(entry.pop("_reasons"))
        fixes.append(entry)
    return {"fixes": fixes}
