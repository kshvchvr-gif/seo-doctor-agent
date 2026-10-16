"""
Phase 2 — Traffic decline diagnosis (read-only).

Period-over-period comparison: current window vs the previous window of the
same length (last 28d vs prior 28d, and last 7d vs prior 7d). Flags the
pages/queries with the biggest drops (and rises), and for each top decline
answers five questions:

  1. What changed (the metric deltas)?
  2. Which pages/queries?
  3. Likely cause (deterministic heuristic from the delta pattern)?
  4. Evidence (the numbers behind the cause)?
  5. Next action?

Evidence only — no live-site edits, and no invented causes. GSC data lags
~2 days (both windows end 2 days ago, so both are complete). Short windows
are noisy on a low-traffic site; treat single-window moves as signals, not
proof. Seasonality (festival/observance cycles for devotional content) is
noted qualitatively, never guessed at numerically.
"""
import datetime

from . import config
from . import gsc_client
from . import ga4_client

LAG_DAYS = 2  # keep in sync with gsc_client._date_range

# thresholds tuned for a low-traffic site: absolute floors first, then relative
MIN_IMPRESSION_DROP_ABS = 5
MIN_IMPRESSION_DROP_PCT = 0.30
MIN_CLICKS_DROP_ABS = 1
MIN_CLICKS_DROP_PCT = 0.30
MIN_POSITION_WORSENED = 2.0
MIN_PREV_IMPRESSIONS_FOR_POSITION = 10
TOP_N_DECLINING = 5
TOP_N_RISING = 5


def _window(days: int, lag: int = LAG_DAYS):
    """(start, end) ISO dates for a window ending `lag` days ago."""
    end = datetime.date.today() - datetime.timedelta(days=lag)
    start = end - datetime.timedelta(days=days)
    return start.isoformat(), end.isoformat()


def _previous_window(days: int):
    """The window immediately before the current one, same length."""
    cur_start, _ = _window(days)
    prev_end = (datetime.date.fromisoformat(cur_start) - datetime.timedelta(days=1)).isoformat()
    prev_start = (datetime.date.fromisoformat(prev_end) - datetime.timedelta(days=days)).isoformat()
    return prev_start, prev_end


def _pct_delta(cur, prev):
    if not prev:
        return None
    return (cur - prev) / prev


def _weighted_position(rows):
    """Impressions-weighted average position across page rows."""
    impressions = sum(r["impressions"] for r in rows)
    if not impressions:
        return None
    return sum(r["position"] * r["impressions"] for r in rows) / impressions


def _totals(page_rows):
    clicks = sum(r["clicks"] for r in page_rows)
    impressions = sum(r["impressions"] for r in page_rows)
    return {
        "clicks": clicks,
        "impressions": impressions,
        "ctr": (clicks / impressions) if impressions else 0.0,
        "position": _weighted_position(page_rows),
    }


def _by(rows, key):
    return {r[key]: r for r in rows}


def _delta(cur, prev):
    """Metric deltas between two row dicts (missing side treated as zeros)."""
    c = cur or {"clicks": 0, "impressions": 0, "ctr": 0.0, "position": None}
    p = prev or {"clicks": 0, "impressions": 0, "ctr": 0.0, "position": None}
    return {
        "clicks_delta": c["clicks"] - p["clicks"],
        "clicks_pct": _pct_delta(c["clicks"], p["clicks"]),
        "impressions_delta": c["impressions"] - p["impressions"],
        "impressions_pct": _pct_delta(c["impressions"], p["impressions"]),
        "ctr_delta": c["ctr"] - p["ctr"],
        "position_delta": (c["position"] - p["position"])
        if (c["position"] is not None and p["position"] is not None) else None,
    }


def _is_declining(d, prev_row):
    if not prev_row:
        return False
    imp_drop = -d["impressions_delta"] >= max(MIN_IMPRESSION_DROP_ABS,
                                              prev_row["impressions"] * MIN_IMPRESSION_DROP_PCT)
    clk_drop = -d["clicks_delta"] >= max(MIN_CLICKS_DROP_ABS,
                                         prev_row["clicks"] * MIN_CLICKS_DROP_PCT)
    pos_worse = (d["position_delta"] is not None
                 and d["position_delta"] >= MIN_POSITION_WORSENED
                 and prev_row["impressions"] >= MIN_PREV_IMPRESSIONS_FOR_POSITION)
    return imp_drop or clk_drop or pos_worse


def _is_rising(d, prev_row):
    if not prev_row:
        return False
    imp_gain = d["impressions_delta"] >= max(MIN_IMPRESSION_DROP_ABS,
                                             prev_row["impressions"] * MIN_IMPRESSION_DROP_PCT)
    clk_gain = d["clicks_delta"] >= max(MIN_CLICKS_DROP_ABS,
                                        prev_row["clicks"] * MIN_CLICKS_DROP_PCT)
    pos_better = (d["position_delta"] is not None
                  and d["position_delta"] <= -MIN_POSITION_WORSENED
                  and prev_row["impressions"] >= MIN_PREV_IMPRESSIONS_FOR_POSITION)
    return imp_gain or clk_gain or pos_better


def _classify_cause(cur_row, prev_row, d):
    """
    Deterministic cause heuristic + evidence + next action, derived only from
    how the metrics moved relative to each other. Never invents external causes.
    Returns (cause, evidence, action).
    """
    c = cur_row or {"clicks": 0, "impressions": 0, "ctr": 0.0, "position": None}
    p = prev_row or {"clicks": 0, "impressions": 0, "ctr": 0.0, "position": None}

    # Page vanished from search data entirely
    if c["impressions"] == 0 and p["impressions"] > 0:
        return (
            "Page lost all impressions — possible deindexing, removal, or a crawl/technical problem.",
            f"impressions {p['impressions']} -> 0 across the window",
            "Check the Website Health report and GSC coverage/indexing for this URL before touching content.",
        )

    # Ranking loss: position worsened AND impressions fell
    if (d["position_delta"] is not None and d["position_delta"] >= MIN_POSITION_WORSENED
            and d["impressions_delta"] < 0):
        return (
            "Ranking drop — average position slipped, so the page is shown lower and earns fewer impressions/clicks.",
            f"position {p['position']:.1f} -> {c['position']:.1f} ({d['position_delta']:+.1f}), "
            f"impressions {p['impressions']} -> {c['impressions']} ({d['impressions_delta']:+d})",
            "Deepen the content, add internal links pointing here (see link suggestions), and re-check next run before rewriting titles.",
        )

    # Demand/seasonality: position held but impressions fell
    if (d["impressions_delta"] < 0
            and (d["position_delta"] is None or abs(d["position_delta"]) < 1)
            and -d["impressions_delta"] >= p["impressions"] * MIN_IMPRESSION_DROP_PCT):
        if p["position"] is not None and c["position"] is not None:
            evidence = (f"position stable ({p['position']:.1f} -> {c['position']:.1f}), "
                        f"impressions {p['impressions']} -> {c['impressions']} ({d['impressions_pct']:+.0%})")
        else:
            evidence = f"impressions {p['impressions']} -> {c['impressions']} ({d['impressions_pct']:+.0%})"
        return (
            "Search demand fell while ranking held — likely seasonality or a shift in query interest, not a page-quality problem.",
            evidence,
            "Do not rewrite the page for a one-window demand dip. Confirm with Google Trends; keep the page, revisit after 2-3 runs.",
        )

    # Snippet/title loss: impressions held, CTR fell
    if (d["impressions_delta"] >= 0 and d["ctr_delta"] < 0 and c["impressions"] >= 10):
        return (
            "Snippet/CTR loss — the page is shown as often as before but fewer people click (title/meta no longer matches intent, or a richer SERP result took the click).",
            f"impressions {p['impressions']} -> {c['impressions']}, CTR {p['ctr']:.2%} -> {c['ctr']:.2%} ({d['ctr_delta']:+.2%})",
            "Rewrite title/meta description for the top queries of this page (AI treatment in this report has exact suggestions).",
        )

    if p["position"] is not None and c["position"] is not None:
        mixed_evidence = (f"clicks {p['clicks']} -> {c['clicks']}, "
                          f"impressions {p['impressions']} -> {c['impressions']}, "
                          f"position {p['position']} -> {c['position']}")
    else:
        mixed_evidence = (f"clicks {p['clicks']} -> {c['clicks']}, "
                          f"impressions {p['impressions']} -> {c['impressions']}")
    return (
        "Mixed movement — more than one metric moved; needs manual review rather than a single fix.",
        mixed_evidence,
        "Read the query-level rows for this page in Search Console before changing anything.",
    )


def _top_declining_queries_for_page(query_rows_cur, query_rows_prev, page_url, limit=3):
    """Top declining queries for one page, with deltas."""
    cur = _by([r for r in query_rows_cur if r["page"] == page_url], "query")
    prev = _by([r for r in query_rows_prev if r["page"] == page_url], "query")
    out = []
    for q in set(cur) | set(prev):
        d = _delta(cur.get(q), prev.get(q))
        prev_row = prev.get(q)
        if prev_row and _is_declining(d, prev_row):
            out.append({
                "query": q,
                "cur": cur.get(q), "prev": prev_row,
                **d,
            })
    out.sort(key=lambda x: (x["clicks_delta"], x["impressions_delta"]))
    return out[:limit]


def _rank_movement(cur_by, prev_by, classifier, top_n):
    """Shared declining/rising ranking over a keyed map of rows."""
    out = []
    for key in set(cur_by) | set(prev_by):
        cur_row, prev_row = cur_by.get(key), prev_by.get(key)
        d = _delta(cur_row, prev_row)
        if classifier(d, prev_row):
            out.append({"key": key, "cur": cur_row, "prev": prev_row, **d})
    # biggest absolute losses/gains first; clicks matter most, then impressions
    out.sort(key=lambda x: (x["clicks_delta"], x["impressions_delta"]))
    return out[:top_n]


def _annotate_pages(moves, query_rows_cur, query_rows_prev):
    """Attach the 5-answer explanation to each page-level movement."""
    for m in moves:
        cause, evidence, action = _classify_cause(m["cur"], m["prev"], m)
        m["cause"] = cause
        m["evidence"] = evidence
        m["action"] = action
        m["top_queries"] = _top_declining_queries_for_page(
            query_rows_cur, query_rows_prev, m["key"])
    return moves


def _ga4_sessions(start, end):
    try:
        rows = ga4_client.fetch_traffic_sources(start_date=start, end_date=end)
        return sum(r["sessions"] for r in rows), rows
    except Exception as e:  # GA4 failure must not kill the GSC diagnosis
        print(f"   trends: GA4 fetch failed for {start}..{end}: {e}")
        return None, []


def analyze():
    """
    Run period-over-period comparison for the 28d and 7d windows.
    Returns {'windows': [window_dict, ...], 'notes': [str, ...]}.
    """
    windows = []
    for days in (28, 7):
        cur_start, cur_end = _window(days)
        prev_start, prev_end = _previous_window(days)

        pages_cur = gsc_client.fetch_page_data(start_date=cur_start, end_date=cur_end)
        pages_prev = gsc_client.fetch_page_data(start_date=prev_start, end_date=prev_end)
        queries_cur = gsc_client.fetch_query_data(start_date=cur_start, end_date=cur_end)
        queries_prev = gsc_client.fetch_query_data(start_date=prev_start, end_date=prev_end)

        cur_totals = _totals(pages_cur)
        prev_totals = _totals(pages_prev)
        sessions_cur, _ = _ga4_sessions(cur_start, cur_end)
        sessions_prev, _ = _ga4_sessions(prev_start, prev_end)

        declining_pages = _annotate_pages(
            _rank_movement(_by(pages_cur, "page"), _by(pages_prev, "page"),
                           _is_declining, TOP_N_DECLINING),
            queries_cur, queries_prev)
        rising_pages = _rank_movement(
            _by(pages_cur, "page"), _by(pages_prev, "page"), _is_rising, TOP_N_RISING)

        # query-level: aggregate across pages by query text
        def _agg_queries(rows):
            agg = {}
            for r in rows:
                a = agg.setdefault(r["query"], {"query": r["query"], "clicks": 0,
                                                "impressions": 0, "ctr": 0.0, "position": 0.0})
                a["clicks"] += r["clicks"]
                a["impressions"] += r["impressions"]
            for a in agg.values():
                a["ctr"] = a["clicks"] / a["impressions"] if a["impressions"] else 0.0
            return agg

        q_cur, q_prev = _agg_queries(queries_cur), _agg_queries(queries_prev)
        # position for aggregated queries: impressions-weighted
        def _fill_pos(agg, rows):
            weighted = {}
            for r in rows:
                w = weighted.setdefault(r["query"], [0.0, 0])
                w[0] += r["position"] * r["impressions"]
                w[1] += r["impressions"]
            for q, a in agg.items():
                if weighted.get(q) and weighted[q][1]:
                    a["position"] = weighted[q][0] / weighted[q][1]
            return agg
        _fill_pos(q_cur, queries_cur)
        _fill_pos(q_prev, queries_prev)

        declining_queries = _rank_movement(q_cur, q_prev, _is_declining, TOP_N_DECLINING)
        rising_queries = _rank_movement(q_cur, q_prev, _is_rising, TOP_N_RISING)

        windows.append({
            "label": f"{days}d",
            "days": days,
            "current": {"start": cur_start, "end": cur_end, **cur_totals,
                        "ga4_sessions": sessions_cur},
            "previous": {"start": prev_start, "end": prev_end, **prev_totals,
                         "ga4_sessions": sessions_prev},
            "declining_pages": declining_pages,
            "rising_pages": rising_pages,
            "declining_queries": declining_queries,
            "rising_queries": rising_queries,
        })
        print(f"   trends {days}d: {len(pages_cur)} pages current vs {len(pages_prev)} previous; "
              f"{len(declining_pages)} declining, {len(rising_pages)} rising")

    notes = [
        f"Windows end {LAG_DAYS} days ago (Search Console lag); both windows are complete.",
        "Single-window moves on a low-traffic site are signals, not proof — confirm over 2-3 runs.",
        "Devotional/seasonal query demand cycles (festivals, observances) can move impressions without any page change.",
        "Causes below are deterministic heuristics from metric deltas, not Google algorithm guesses.",
    ]
    return {"windows": windows, "notes": notes}
