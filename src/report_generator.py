"""Builds the human-readable weekly SEO Doctor treatment report."""
import datetime
from collections import Counter, defaultdict


def _fmt_pct(x):
    return f"{x:.2%}"


def aggregate_technical_issues(pages):
    """
    Roll the per-page technical flags into a ranked, de-duplicated summary so the
    report shows "META_LENGTH: 40 pages" instead of 145 raw one-line flags.
    Returns list of (issue_type, pages_affected, [example_urls]).
    """
    counts = Counter()
    examples = defaultdict(list)
    for p in pages:
        for issue in p.get("technical_issues", []):
            base = issue.split(":", 1)[0]  # normalise "MISSING_IMAGE_ALT:3" -> "MISSING_IMAGE_ALT"
            counts[base] += 1
            if len(examples[base]) < 3:
                examples[base].append(p.get("url", ""))
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return [(name, n, examples[name]) for name, n in ranked]


def _fmt_delta(x, pct=False, decimals=None):
    if x is None:
        return "n/a"
    if pct:
        return f"{x:+.0%}" if abs(x) >= 0.005 else "±0%"
    if decimals is not None or isinstance(x, float):
        return f"{x:+.{decimals if decimals is not None else 1}f}"
    return f"{x:+d}"


def _bullets(items, limit=8):
    return "\n".join(f"  - {x}" for x in (items or [])[:limit]) or "  - None"


def _trends_section(trends) -> list:
    """Phase 2: period-over-period movement with a 5-answer explanation per top decline."""
    if not trends or not trends.get("windows"):
        return []
    lines = ["## 📉 Traffic Movement — period-over-period", ""]
    for note in trends.get("notes", [])[:2]:
        lines.append(f"> {note}")
    lines.append("")

    for w in trends["windows"]:
        cur, prev = w["current"], w["previous"]
        lines += [f"### Window: last {w['days']}d vs previous {w['days']}d", "",
                  f"* Current: {cur['start']} → {cur['end']} | Previous: {prev['start']} → {prev['end']}", "",
                  "| Metric | Current | Previous | Change |", "|---|---:|---:|---:|"]
        ctr_prev = prev["ctr"] if prev["impressions"] else None
        pos_cur = f"{cur['position']:.1f}" if cur["position"] is not None else "n/a"
        pos_prev = f"{prev['position']:.1f}" if prev["position"] is not None else "n/a"
        lines += [
            f"| Clicks | {cur['clicks']} | {prev['clicks']} | {_fmt_delta(cur['clicks'] - prev['clicks'])} |",
            f"| Impressions | {cur['impressions']} | {prev['impressions']} | {_fmt_delta(cur['impressions'] - prev['impressions'])} |",
            f"| CTR | {_fmt_pct(cur['ctr'])} | {_fmt_pct(ctr_prev) if ctr_prev is not None else 'n/a'} | {_fmt_delta(cur['ctr'] - prev['ctr'], pct=True)} |",
            f"| Avg position | {pos_cur} | {pos_prev} | {_fmt_delta(cur['position'] - prev['position']) if (cur['position'] is not None and prev['position'] is not None) else 'n/a'} |",
        ]
        if cur.get("ga4_sessions") is not None and prev.get("ga4_sessions") is not None:
            lines.append(f"| GA4 sessions | {cur['ga4_sessions']} | {prev['ga4_sessions']} | {_fmt_delta(cur['ga4_sessions'] - prev['ga4_sessions'])} |")
        lines.append("")

        if w["declining_pages"]:
            lines += ["**Declining pages (biggest drops first):**", "",
                      "| Page | Clicks Δ | Impressions Δ | Position Δ | Likely cause |", "|---|---:|---:|---:|---|"]
            for m in w["declining_pages"]:
                pos_d = f"{m['position_delta']:+.1f}" if m["position_delta"] is not None else "n/a"
                lines.append(f"| {m['key']} | {_fmt_delta(m['clicks_delta'])} | {_fmt_delta(m['impressions_delta'])} | {pos_d} | {m['cause'][:80]} |")
            lines.append("")

            lines += ["**Top declines — the 5 answers each:**", ""]
            for i, m in enumerate(w["declining_pages"][:3], 1):
                lines += [f"#### {i}. {m['key']}",
                          f"1. **Kya badla (what changed):** clicks {_fmt_delta(m['clicks_delta'])}, impressions {_fmt_delta(m['impressions_delta'])}, CTR {_fmt_delta(m['ctr_delta'], pct=True)}"
                          + (f", position {m['position_delta']:+.1f}" if m["position_delta"] is not None else "") + ".",
                          f"2. **Kaunse pages/queries:** page `{m['key']}`"]
                for q in m.get("top_queries", []):
                    lines.append(f"   - query `{q['query']}` — impressions {_fmt_delta(q['impressions_delta'])}, position {q['prev']['position']:.1f} → {q['cur']['position']:.1f}" if q.get("cur") and q["cur"].get("position") is not None else f"   - query `{q['query']}` — impressions {_fmt_delta(q['impressions_delta'])}")
                lines += [f"3. **Likely cause:** {m['cause']}",
                          f"4. **Evidence:** {m['evidence']}",
                          f"5. **Next action:** {m['action']}",
                          ""]
        else:
            lines += ["**Declining pages:** none flagged this run ✅", ""]

        if w["rising_pages"]:
            lines += ["**Rising pages (keep doing whatever changed):**", "",
                      "| Page | Clicks Δ | Impressions Δ | Position Δ |", "|---|---:|---:|---:|"]
            for m in w["rising_pages"]:
                pos_d = f"{m['position_delta']:+.1f}" if m["position_delta"] is not None else "n/a"
                lines.append(f"| {m['key']} | {_fmt_delta(m['clicks_delta'])} | {_fmt_delta(m['impressions_delta'])} | {pos_d} |")
            lines.append("")

        if w["declining_queries"]:
            lines += ["**Declining queries:**", "",
                      "| Query | Clicks Δ | Impressions Δ |", "|---|---:|---:|"]
            for m in w["declining_queries"]:
                lines.append(f"| `{m['key']}` | {_fmt_delta(m['clicks_delta'])} | {_fmt_delta(m['impressions_delta'])} |")
            lines.append("")

    return lines


def build_report(priority_pages, content_gaps, link_suggestions, traffic_sources,
                 total_clicks, total_impressions, treatments=None, pages=None,
                 trends=None) -> str:
    today = datetime.date.today().isoformat()
    treatments = treatments or {}
    pages = pages or []
    overall_ctr = (total_clicks / total_impressions) if total_impressions else 0
    technical_issues = sum(len(p.get("technical_issues", [])) for p in pages)
    tech_ranked = aggregate_technical_issues(pages)
    lines = [f"# 🩺 SEO Doctor Report — {today}", "", "## Executive Diagnosis"]
    # one-line movement headline from the 28d window, if Phase 2 ran
    movement_line = ""
    if trends and trends.get("windows"):
        w28 = next((w for w in trends["windows"] if w["days"] == 28), None)
        if w28:
            cur, prev = w28["current"], w28["previous"]
            clk = _fmt_delta(cur["clicks"] - prev["clicks"])
            imp = _fmt_delta(cur["impressions"] - prev["impressions"])
            direction = "↘" if (cur["clicks"] < prev["clicks"] or cur["impressions"] < prev["impressions"]) else "↗"
            movement_line = f"- Traffic (28d vs prior 28d): clicks {clk}, impressions {imp} {direction}"
    lines += [
        f"- Total clicks (last 90d): **{total_clicks}**",
        f"- Total impressions (last 90d): **{total_impressions}**",
        f"- Overall CTR: **{_fmt_pct(overall_ctr)}**",
    ]
    if movement_line:
        lines.append(movement_line)
    lines += [
        f"- Priority pages: **{len(priority_pages)}**",
        f"- Content-gap opportunities: **{len(content_gaps)}**",
        f"- Technical flags: **{technical_issues}** across **{len(tech_ranked)}** issue types",
        "",
        "> **Important:** CTR curves are heuristics for ranking priorities, not Google's private algorithm. Recommendations should be validated before publishing.",
        "",
    ]

    if tech_ranked:
        lines += ["## 🧱 Technical Issues — grouped & ranked (fix the top of this list first)",
                  "| Issue type | Pages affected | Example page |", "|---|---:|---|"]
        for name, count, ex in tech_ranked:
            lines.append(f"| {name} | {count} | {ex[0] if ex else ''} |")
        lines.append("")

    if traffic_sources:
        lines += ["## Traffic Sources (last 28 days, GA4)", "| Source | Sessions | Users |", "|---|---:|---:|"]
        for s in sorted(traffic_sources, key=lambda x: x["sessions"], reverse=True)[:10]:
            lines.append(f"| {s['source_medium']} | {s['sessions']} | {s['active_users']} |")
        lines.append("")

    lines += _trends_section(trends)

    lines += ["## 🩺 Treatment Plan — Fix These First", ""]
    for i, page in enumerate(priority_pages, 1):
        url = page["page"]
        treatment = treatments.get(url, {})
        lines += [f"### {i}. [{url}]({url})", f"- **Diagnosis:** {', '.join(page['diagnoses'])}",
                  f"- **Estimated lost clicks:** {page['total_lost_clicks']:.1f}",
                  f"- **Impressions:** {page['total_impressions']}"]
        if page.get("technical_issues"):
            lines.append(f"- **Technical issues:** {', '.join(page['technical_issues'])}")
        lines.append("- **Top query evidence:**")
        for q in page["top_queries"]:
            lines.append(f"  - `{q['query']}` — position {q['position']:.1f}, CTR {_fmt_pct(q['ctr'])}, expected ~{_fmt_pct(q['expected_ctr'])}")
        if treatment:
            lines += [
                "", "#### Exact Prescription",
                f"- **Primary keyword:** {treatment.get('primary_keyword', '')}",
                f"- **Secondary keywords:** {', '.join(treatment.get('secondary_keywords', [])[:8]) or 'None'}",
                f"- **Recommended title:** {treatment.get('recommended_title', '')}",
                f"- **Recommended meta description:** {treatment.get('recommended_meta_description', '')}",
                f"- **Recommended H1:** {treatment.get('recommended_h1', '')}",
                f"- **Priority:** {treatment.get('priority', 'HIGH')}",
                "- **Content changes:**",
                _bullets(treatment.get('content_changes')),
                "- **FAQ questions:**",
                _bullets(treatment.get('faq_questions')),
                f"- **Schema:** {treatment.get('schema_recommendation', '')}",
            ]
            image = treatment.get("image_seo", {}) or {}
            if image.get("filename") or image.get("alt_text"):
                lines += [f"- **Image filename:** {image.get('filename', '')}", f"- **Image alt text:** {image.get('alt_text', '')}"]
            links = treatment.get("internal_link_plan", []) or []
            if links:
                lines.append("- **Internal links to add:**")
                for link in links[:5]:
                    lines.append(f"  - [{link.get('anchor', 'related page')}]({link.get('url', '')})")
            source = treatment.get("_source", "")
            if source.startswith("gemini:"):
                model = source.split(":", 1)[1]
                why = f"Generated by Gemini ({model}). {treatment.get('expected_reason', '')}"
            else:
                why = treatment.get('expected_reason', 'Deterministic fallback used because AI generation was unavailable.')
            lines += [f"- **Why this treatment:** {why}", f"- **Caution:** {treatment.get('caution', '')}"]
        else:
            lines.append(f"- **Prescription:** {page['top_queries'][0]['prescription']}")
        lines.append("")

    if content_gaps:
        lines += ["## 🔎 Content Gaps — New Article Opportunities", "| Query | Impressions | Best position | Recommended action |", "|---|---:|---:|---|"]
        for g in content_gaps[:20]:
            lines.append(f"| {g['query']} | {g['impressions']} | {g['best_position']:.0f} | Create/strengthen a dedicated page targeting this intent |")
        lines.append("")

    if link_suggestions:
        lines += ["## 🔗 Internal Linking Opportunities", "| From | Link to | Similarity |", "|---|---|---:|"]
        for s in link_suggestions[:30]:
            lines.append(f"| [{s['from_title'][:50]}]({s['from_url']}) | [{s['to_title'][:50]}]({s['to_url']}) | {s['similarity']} |")
        lines.append("")

    lines += ["## Next Follow-up", "- Re-run Search Console/GA4 after meaningful changes.", "- Compare CTR, impressions, clicks and average position against this report.", "- Keep changes that improve performance; revert changes that clearly worsen it.", "", "---", "_Generated automatically by KrishnBhakti SEO Doctor Agent._"]
    return "\n".join(lines)
