"""
Uses page embeddings to find semantically related pages that AREN'T
already internally linked to each other - i.e. genuine missing-link
opportunities, not just keyword-matched ones.
"""
from . import config
from . import embeddings as emb


def suggest_links(pages: list, page_embeddings: dict, min_similarity: float = 0.72) -> list:
    """
    pages: list of dicts with 'url' and 'internal_links' (set of already-linked urls)
    Returns list of dicts: {from_url, to_url, similarity, reason}
    """
    suggestions = []
    pages_by_url = {p["url"]: p for p in pages}

    for page in pages:
        url = page["url"]
        if url not in page_embeddings:
            continue
        similar = emb.top_similar(url, page_embeddings, top_n=config.TOP_N_LINK_SUGGESTIONS_PER_PAGE + 3)
        existing_links = page.get("internal_links", set())

        added = 0
        for candidate_url, score in similar:
            if added >= config.TOP_N_LINK_SUGGESTIONS_PER_PAGE:
                break
            if score < min_similarity:
                continue
            # normalize for comparison (trailing slash differences etc.)
            def _norm(u):
                return u.rstrip("/").removesuffix(".html")
            normalized_existing = {_norm(u) for u in existing_links}
            if _norm(candidate_url) in normalized_existing:
                continue  # already linked, skip
            candidate_title = pages_by_url.get(candidate_url, {}).get("title", candidate_url)
            suggestions.append({
                "from_url": url,
                "from_title": page.get("title", url),
                "to_url": candidate_url,
                "to_title": candidate_title,
                "similarity": round(score, 3),
            })
            added += 1

    suggestions.sort(key=lambda s: s["similarity"], reverse=True)
    return suggestions
