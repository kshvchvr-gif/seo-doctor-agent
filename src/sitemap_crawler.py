"""
Crawls the sitemap, fetches every page, extracts:
  - title
  - main text content (for embeddings)
  - existing outbound internal links (so we don't suggest links that already exist)
"""
import re
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse

from . import config

HEADERS = {"User-Agent": "KrishnBhaktiSEODoctorBot/1.0 (+https://krishnbhakti.com)"}


def get_sitemap_urls(sitemap_url: str = None):
    sitemap_url = sitemap_url or config.SITEMAP_URL
    resp = requests.get(sitemap_url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, "xml")

    # handle sitemap index files (a sitemap of sitemaps)
    sub_sitemaps = [sm.find("loc").text.strip() for sm in soup.find_all("sitemap") if sm.find("loc")]
    if sub_sitemaps:
        urls = []
        for sm in sub_sitemaps:
            urls.extend(get_sitemap_urls(sm))
        return urls

    urls = [loc.text for loc in soup.find_all("loc")]
    # skip non-page resources (rss feeds, images, pdfs) that sometimes sneak into sitemaps
    return [u for u in urls if not u.lower().endswith(
        (".xml", ".txt", ".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"))]


def fetch_page_content(url: str, site_netloc: str):
    """Returns dict: url, title, meta_description, text, internal_links (set of urls)"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        return {"url": url, "error": str(e)}

    soup = BeautifulSoup(resp.content, "html.parser")

    title_tag = soup.find("title")
    title = title_tag.text.strip() if title_tag else ""
    h1_tags = soup.find_all("h1")
    h1_text = " | ".join(h.get_text(" ", strip=True) for h in h1_tags[:3])
    canonical_tag = soup.find("link", rel=lambda v: v and "canonical" in v)
    canonical = canonical_tag.get("href", "").strip() if canonical_tag else ""
    robots_tag = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
    robots = robots_tag.get("content", "").strip() if robots_tag else ""
    images = soup.find_all("img")
    image_missing_alt = sum(1 for img in images if not (img.get("alt") or "").strip())

    meta_desc_tag = soup.find("meta", attrs={"name": "description"})
    meta_description = meta_desc_tag["content"].strip() if meta_desc_tag and meta_desc_tag.get("content") else ""

    # remove nav/footer/script noise before extracting body text
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    body = soup.find("main") or soup.find("article") or soup.body or soup
    text = re.sub(r"\s+", " ", body.get_text(separator=" ")).strip()

    internal_links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        parsed = urlparse(href)
        if parsed.netloc and parsed.netloc != site_netloc:
            continue  # external link, skip
        # normalize relative links
        if not parsed.netloc:
            href = requests.compat.urljoin(url, href)
        internal_links.add(href.split("#")[0].rstrip("/"))

    return {
        "url": url,
        "title": title,
        "meta_description": meta_description,
        "h1_count": len(h1_tags),
        "h1_text": h1_text,
        "canonical": canonical,
        "robots": robots,
        "image_count": len(images),
        "image_missing_alt": image_missing_alt,
        "word_count": len(text.split()),
        "title_length": len(title),
        "meta_length": len(meta_description),
        "text": text[:8000],  # cap length, plenty for embeddings + gap analysis
        "internal_links": internal_links,
    }


def crawl_all_pages(sitemap_url: str = None, delay_seconds: float = 0.5, max_pages: int = 400):
    sitemap_url = sitemap_url or config.SITEMAP_URL
    urls = get_sitemap_urls(sitemap_url)[:max_pages]
    site_netloc = urlparse(urls[0]).netloc if urls else ""

    pages = []
    for url in urls:
        pages.append(fetch_page_content(url, site_netloc))
        time.sleep(delay_seconds)  # be polite, avoid hammering the server
    return [p for p in pages if "error" not in p]
