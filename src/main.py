"""
Entry point. Run with: python -m src.main
Orchestrates: GSC fetch -> GA4 fetch -> sitemap crawl -> embeddings (cached)
-> scoring -> RAG linking -> report -> save + print.
"""
import os
import json
import time
import hashlib
import datetime
import numpy as np

from . import config
from . import gsc_client
from . import ga4_client
from . import sitemap_crawler
from . import embeddings as emb
from . import scoring
from . import rag_linking
from . import report_generator
from . import ai_treatment

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "cache")
REPORTS_DIR = os.path.join(os.path.dirname(__file__), "..", "reports")
EMBED_CACHE_FILE = os.path.join(CACHE_DIR, "embeddings_cache.json")


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _load_embed_cache() -> dict:
    if os.path.exists(EMBED_CACHE_FILE):
        with open(EMBED_CACHE_FILE, "r") as f:
            raw = json.load(f)
        return {
            url: {"hash": v["hash"], "vector": np.array(v["vector"], dtype=np.float32)}
            for url, v in raw.items()
        }
    return {}


def _save_embed_cache(cache: dict):
    os.makedirs(CACHE_DIR, exist_ok=True)
    serializable = {
        url: {"hash": v["hash"], "vector": v["vector"].tolist()}
        for url, v in cache.items()
    }
    with open(EMBED_CACHE_FILE, "w") as f:
        json.dump(serializable, f)


def get_embeddings_with_cache(pages: list) -> dict:
    """
    Only re-embeds pages whose content changed since last run (saves API
    calls + time on the free tier). New pages get embedded fresh.
    """
    cache = _load_embed_cache()
    result = {}
    to_embed = []

    for page in pages:
        content = f"{page.get('title','')}\n\n{page.get('text','')[:3000]}"
        h = _content_hash(content)
        cached = cache.get(page["url"])
        if cached and cached["hash"] == h:
            result[page["url"]] = cached["vector"]
        else:
            to_embed.append(page)

    print(f"Embedding {len(to_embed)} pages ({len(pages) - len(to_embed)} served from cache)...")
    for i, page in enumerate(to_embed):
        content = f"{page.get('title','')}\n\n{page.get('text','')[:3000]}"
        vec = emb.embed_text(content)
        result[page["url"]] = vec
        cache[page["url"]] = {"hash": _content_hash(content), "vector": vec}
        # save progress incrementally so a mid-run quota failure never loses work
        if (i + 1) % 5 == 0 or i == len(to_embed) - 1:
            _save_embed_cache(cache)
        print(f"   embedded {i + 1}/{len(to_embed)}: {page['url']}")
        time.sleep(1.5)  # pace below free-tier requests-per-minute limit

    _save_embed_cache(cache)
    return result


def run():
    print("1/7 Fetching Search Console data...")
    query_rows = gsc_client.fetch_query_data()
    total_clicks = sum(r["clicks"] for r in query_rows)
    total_impressions = sum(r["impressions"] for r in query_rows)
    print(f"   {len(query_rows)} query/page rows, {total_clicks} clicks, {total_impressions} impressions")

    print("2/7 Fetching GA4 traffic sources...")
    traffic_sources = ga4_client.fetch_traffic_sources()
    print(f"   {len(traffic_sources)} source/medium rows")

    print("3/7 Crawling sitemap...")
    pages = sitemap_crawler.crawl_all_pages()
    print(f"   {len(pages)} pages crawled successfully")

    print("4/7 Building embeddings (cached where possible)...")
    page_embeddings = get_embeddings_with_cache(pages)

    print("5/7 Scoring pages + finding content gaps...")
    diagnosed = scoring.diagnose_all(query_rows)
    priority_pages = scoring.rank_priority_pages(diagnosed)
    content_gaps = scoring.find_content_gaps(query_rows)

    # Attach on-page technical issues to crawled pages.
    page_by_url = {p["url"]: p for p in pages}
    for p in pages:
        p["technical_issues"] = scoring.technical_audit(p)

    for pd in priority_pages:
        pd["technical_issues"] = page_by_url.get(pd["page"], {}).get("technical_issues", [])

    print("6/7 Generating internal linking suggestions...")
    link_suggestions = rag_linking.suggest_links(pages, page_embeddings)

    # AI treatment: only the highest-opportunity pages get generation calls,
    # keeping the weekly run lightweight and free-tier friendly.
    print(f"7/7 Writing AI treatment for up to {config.AI_TREATMENT_MAX_PAGES} priority pages...")
    related_by_page = {}
    for s in link_suggestions:
        related_by_page.setdefault(s["from_url"], []).append({"url": s["to_url"], "title": s["to_title"]})
    treatments = {}
    for page_diag in priority_pages[:config.AI_TREATMENT_MAX_PAGES]:
        page = page_by_url.get(page_diag["page"], {"url": page_diag["page"]})
        treatments[page_diag["page"]] = ai_treatment.generate_treatment(
            page, page_diag, related_by_page.get(page_diag["page"], [])
        )

    report = report_generator.build_report(
        priority_pages=priority_pages,
        content_gaps=content_gaps,
        link_suggestions=link_suggestions,
        traffic_sources=traffic_sources,
        total_clicks=total_clicks,
        total_impressions=total_impressions,
        treatments=treatments,
        pages=pages,
    )

    os.makedirs(REPORTS_DIR, exist_ok=True)
    report_path = os.path.join(REPORTS_DIR, f"{datetime.date.today().isoformat()}.md")
    with open(report_path, "w") as f:
        f.write(report)

    # also write a "latest.md" so the GitHub Action can easily read it for the issue body
    with open(os.path.join(REPORTS_DIR, "latest.md"), "w") as f:
        f.write(report)

    print(f"\nReport saved to {report_path}")
    print(report[:2000])


if __name__ == "__main__":
    run()
