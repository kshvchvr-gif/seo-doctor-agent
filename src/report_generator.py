"""Builds the human-readable weekly SEO Doctor treatment report."""
import datetime


def _fmt_pct(x):
    return f"{x:.2%}"


def _bullets(items, limit=8):
    return "\n".join(f"  - {x}" for x in (items or [])[:limit]) or "  - None"


def build_report(priority_pages, content_gaps, link_suggestions, traffic_sources,
                 total_clicks, total_impressions, treatments=None, pages=None) -> str:
    today = datetime.date.today().isoformat()
    treatments = treatments or {}
    pages = pages or []
    overall_ctr = (total_clicks / total_impressions) if total_impressions else 0
    technical_issues = sum(len(p.get("technical_issues", [])) for p in pages)
    lines = [f"# 🩺 SEO Doctor Report — {today}", "", "## Executive Diagnosis"]
    lines += [
        f"- Total clicks (last 90d): **{total_clicks}**",
        f"- Total impressions (last 90d): **{total_impressions}**",
        f"- Overall CTR: **{_fmt_pct(overall_ctr)}**",
        f"- Priority pages: **{len(priority_pages)}**",
        f"- Content-gap opportunities: **{len(content_gaps)}**",
        f"- Technical issues found: **{technical_issues}**",
        "",
        "> **Important:** CTR curves are heuristics for prioritization, not Google's private ranking formula. Recommendations should be validated after publishing.",
        "",
    ]

    if traffic_sources:
        lines += ["## Traffic Sources (last 28 days, GA4)", "| Source | Sessions | Users |", "|---|---:|---:|"]
        for s in sorted(traffic_sources, key=lambda x: x["sessions"], reverse=True)[:10]:
            lines.append(f"| {s['source_medium']} | {s['sessions']} | {s['active_users']} |")
        lines.append("")

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
            lines += [f"- **Why this treatment:** {treatment.get('expected_reason', '')}", f"- **Caution:** {treatment.get('caution', '')}"]
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
