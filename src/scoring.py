"""
The 'diagnosis' engine. Takes raw GSC query+page rows and turns each
significant (query, page) pair into a diagnosis + prescription, then
ranks pages by how much click-opportunity is being lost.

Expected CTR by position is a well-established rough industry curve
(varies by vertical, but good enough for prioritization purposes -
we only care about *relative* under/over performance, not absolute truth).
"""
from . import config

# rough expected-CTR-by-position curve (organic, all-device blended)
EXPECTED_CTR_CURVE = {
    1: 0.28, 2: 0.15, 3: 0.11, 4: 0.08, 5: 0.06,
    6: 0.05, 7: 0.04, 8: 0.03, 9: 0.025, 10: 0.02,
    11: 0.018, 12: 0.016, 15: 0.012, 20: 0.008,
    30: 0.004, 50: 0.002, 100: 0.001,
}


def _expected_ctr(position: float) -> float:
    positions = sorted(EXPECTED_CTR_CURVE.keys())
    if position <= positions[0]:
        return EXPECTED_CTR_CURVE[positions[0]]
    if position >= positions[-1]:
        return EXPECTED_CTR_CURVE[positions[-1]]
    for i in range(len(positions) - 1):
        lo, hi = positions[i], positions[i + 1]
        if lo <= position <= hi:
            lo_ctr, hi_ctr = EXPECTED_CTR_CURVE[lo], EXPECTED_CTR_CURVE[hi]
            frac = (position - lo) / (hi - lo)
            return lo_ctr + frac * (hi_ctr - lo_ctr)
    return 0.01


def diagnose_row(row: dict) -> dict:
    """
    row: dict with query, page, clicks, impressions, ctr, position
    Returns row + diagnosis, prescription, lost_click_estimate
    """
    impressions = row["impressions"]
    position = row["position"]
    ctr = row["ctr"]
    expected = _expected_ctr(position)
    lost_clicks = max(0, (expected - ctr) * impressions)

    diagnosis, prescription = "HEALTHY", "No action needed."

    if impressions < config.MIN_IMPRESSIONS_TO_CONSIDER:
        diagnosis, prescription = "LOW_DATA", "Not enough impressions yet to diagnose reliably."
    elif position <= config.GOOD_POSITION_THRESHOLD and ctr < config.SNIPPET_LOSS_MAX_CTR:
        diagnosis = "SNIPPET_LOSS"
        prescription = (
            "Google likely answers this directly in the SERP (featured snippet / "
            "People Also Ask), so users don't need to click. Add something the "
            "snippet can't show: a 'today's context' section, a short video, or "
            "a comparison table. Don't just rewrite the title."
        )
    elif position <= config.GOOD_POSITION_THRESHOLD and ctr < expected * 0.6:
        diagnosis = "TITLE_WEAK"
        prescription = (
            f"Position {position:.1f} is decent but CTR ({ctr:.2%}) is well below "
            f"the ~{expected:.2%} expected here. Rewrite the title/meta description "
            "with a stronger hook (a question, a number, or a specific outcome)."
        )
    elif position > config.GOOD_POSITION_THRESHOLD and ctr >= expected * 0.8:
        diagnosis = "RANK_LOW"
        prescription = (
            "When this page IS shown, people click at a healthy rate - the content "
            "and title work. The problem is it isn't ranking high enough. This is "
            "an authority/depth issue: expand the content (more sections, FAQs, "
            "internal links pointing to it) rather than touching the title."
        )
    elif position > config.GOOD_POSITION_THRESHOLD:
        diagnosis = "RANK_LOW_AND_WEAK"
        prescription = (
            "Low position AND below-expected CTR when shown. Lowest priority to "
            "fix directly - likely needs the page rebuilt/expanded, not a quick edit."
        )

    return {
        **row,
        "diagnosis": diagnosis,
        "prescription": prescription,
        "expected_ctr": round(expected, 4),
        "lost_click_estimate": round(lost_clicks, 1),
    }


def diagnose_all(query_rows: list) -> list:
    return [diagnose_row(r) for r in query_rows]


def rank_priority_pages(diagnosed_rows: list, top_n: int = None) -> list:
    """
    Aggregates by page (a page may have many queries), sums lost_click_estimate,
    and returns the pages worth fixing first.
    """
    top_n = top_n or config.TOP_N_PRIORITY_PAGES
    by_page = {}
    for row in diagnosed_rows:
        if row["diagnosis"] in ("HEALTHY", "LOW_DATA"):
            continue
        page = row["page"]
        if page not in by_page:
            by_page[page] = {
                "page": page,
                "total_lost_clicks": 0.0,
                "total_impressions": 0,
                "top_queries": [],
                "diagnoses": set(),
            }
        entry = by_page[page]
        entry["total_lost_clicks"] += row["lost_click_estimate"]
        entry["total_impressions"] += row["impressions"]
        entry["top_queries"].append(row)
        entry["diagnoses"].add(row["diagnosis"])

    pages = list(by_page.values())
    for p in pages:
        p["top_queries"].sort(key=lambda r: r["lost_click_estimate"], reverse=True)
        p["top_queries"] = p["top_queries"][:3]
        p["diagnoses"] = list(p["diagnoses"])

    pages.sort(key=lambda p: p["total_lost_clicks"], reverse=True)
    return pages[:top_n]


def find_content_gaps(query_rows: list, min_impressions: int = 20) -> list:
    """
    Queries with real search demand where NO page ranks in a healthy spot
    (avg position > 20 across all pages that show for it) = content gap,
    i.e. nothing on the site currently satisfies this demand well.
    """
    by_query = {}
    for row in query_rows:
        q = row["query"]
        if q not in by_query:
            by_query[q] = {"query": q, "impressions": 0, "best_position": 999, "pages": set()}
        entry = by_query[q]
        entry["impressions"] += row["impressions"]
        entry["best_position"] = min(entry["best_position"], row["position"])
        entry["pages"].add(row["page"])

    gaps = [
        v for v in by_query.values()
        if v["impressions"] >= min_impressions and v["best_position"] > 20
    ]
    gaps.sort(key=lambda v: v["impressions"], reverse=True)
    return gaps


def technical_audit(page: dict) -> list:
    """Fast on-page checks that do not pretend to know Google's private algorithm."""
    issues = []
    title_len = page.get("title_length", len(page.get("title", "")))
    meta_len = page.get("meta_length", len(page.get("meta_description", "")))
    h1_count = page.get("h1_count", 0)
    if not page.get("title"):
        issues.append("MISSING_TITLE")
    elif title_len < 30 or title_len > 70:
        issues.append("TITLE_LENGTH")
    if not page.get("meta_description"):
        issues.append("MISSING_META")
    elif meta_len < 100 or meta_len > 180:
        issues.append("META_LENGTH")
    if h1_count == 0:
        issues.append("MISSING_H1")
    elif h1_count > 1:
        issues.append("MULTIPLE_H1")
    if page.get("canonical") == "":
        issues.append("MISSING_CANONICAL")
    if page.get("image_missing_alt", 0) > 0:
        issues.append(f"MISSING_IMAGE_ALT:{page['image_missing_alt']}")
    if page.get("word_count", 0) < 500:
        issues.append("THIN_CONTENT_CHECK")
    return issues
