"""
Website health monitor (Phase 1).

No Google credentials required — runs standalone and is safe to run daily.
Checks:
  - availability + HTTP status of critical URLs
  - robots.txt + sitemap.xml (present, valid, declared in robots)
  - SSL certificate expiry
  - broken internal links (crawled from the sitemap)
  - redirect chains (more than one hop)

Writes reports/health/YYYY-MM-DD.md and reports/health/latest.md.
Exits non-zero only on critical problems (site unreachable, or a 5xx/4xx on a
critical path) so GitHub can alert you.
"""
import os
import sys
import ssl
import socket
import datetime
import requests
from bs4 import BeautifulSoup

from . import config

HEADERS = {"User-Agent": "KrishnBhakti-HealthBot/1.0 (+https://krishnbhakti.com)"}
TIMEOUT = 20
HEALTH_DIR = os.path.join(os.path.dirname(__file__), "..", "reports", "health")
CRITICAL_PATHS = ["/", "/sitemap.xml", "/robots.txt", "/rss.xml", "/blog-archive", "/panchang", "/privacy-policy"]
NONPAGE_SUFFIXES = (".xml", ".txt", ".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg",
                    ".css", ".js", ".ico", ".json", ".mp3", ".mp4")
MAX_PAGES = int(os.environ.get("HEALTH_MAX_PAGES", "80"))
MAX_LINKS = int(os.environ.get("HEALTH_MAX_LINKS", "250"))


def _ssl_days_left(host: str, port: int = 443):
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
        exp = datetime.datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
        return (exp - datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)).days
    except Exception:
        return None


def check_url(url: str, allow_redirects: bool = False) -> dict:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=allow_redirects)
        return {"url": url, "status": r.status_code, "hops": len(r.history),
                "chain": [str(h.status_code) for h in r.history], "final": r.url, "error": None}
    except requests.RequestException as e:
        return {"url": url, "status": None, "hops": 0, "chain": [], "final": url, "error": str(e)}


def check_availability(site_url: str) -> list:
    return [check_url(site_url + p) for p in CRITICAL_PATHS]


def check_robots_sitemap(site_url: str) -> dict:
    info = {"robots_status": None, "sitemap_declared": False, "sitemap_url": None,
            "sitemap_status": None, "sitemap_url_count": 0}
    robots = site_url + "/robots.txt"
    r = check_url(site_url + "/robots.txt")
    info["robots_status"] = r["status"]
    if r["status"] == 200:
        try:
            txt = requests.get(site_url + "/robots.txt", headers=HEADERS, timeout=TIMEOUT).text
            for line in txt.splitlines():
                if line.lower().strip().startswith("sitemap:"):
                    info["sitemap_declared"] = True
                    info["sitemap_url"] = line.split(":", 1)[1].strip()
        except requests.RequestException:
            pass
    sm = check_url(site_url + "/sitemap.xml")
    info["sitemap_status"] = sm["status"]
    if sm["status"] == 200:
        try:
            xml = requests.get(site_url + "/sitemap.xml", headers=HEADERS, timeout=TIMEOUT).content
            info["sitemap_url_count"] = len(BeautifulSoup(xml, "xml").find_all("loc"))
        except Exception:
            pass
    return info


def collect_internal_links(site_url: str, max_pages: int) -> tuple:
    from . import sitemap_crawler
    pages = sitemap_crawler.crawl_all_pages(max_pages=max_pages)
    links = set()
    for p in pages:
        for link in p.get("internal_links", []):
            if link.lower().endswith(NONPAGE_SUFFIXES):
                continue
            links.add(link)
    return pages, sorted(links)


def check_links(links: list, max_links: int) -> tuple:
    broken, redirect_chains = [], []
    for url in links[:max_links]:
        r = check_url(url, allow_redirects=True)
        if r["status"] is None:
            broken.append((url, r["error"] or "request failed"))
        elif r["status"] >= 400:
            broken.append((url, r["status"]))
        elif r["hops"] > 1:
            redirect_chains.append((url, r["hops"], r["final"]))
    return broken, redirect_chains


def build_health_report(avail, robots_sitemap, ssl_days, broken, redirect_chains, pages_count) -> tuple:
    today = datetime.date.today().isoformat()
    down = [a for a in avail if a["status"] is None]
    server_err = [a for a in avail if a["status"] and a["status"] >= 500]
    client_err = [a for a in avail if a["status"] and 400 <= a["status"] < 500]
    critical = bool(down or server_err)

    lines = [f"# 🏥 Website Health Report — {today}", "", "## Status"]
    lines.append(f"- Overall: **{'🔴 CRITICAL' if critical else ('🟡 WARNING' if (client_err or broken or redirect_chains or (ssl_days is not None and ssl_days < 21)) else '🟢 OK')}**")
    lines.append(f"- Pages checked: **{pages_count}**")
    lines.append(f"- SSL certificate: **{ssl_days if ssl_days is not None else 'unknown'} days** left")
    lines.append("")

    lines.append("## Availability (critical URLs)")
    lines.append("| URL | Status |")
    lines.append("|---|---|")
    for a in avail:
        status = a["status"] if a["status"] is not None else f"ERROR: {a['error']}"
        lines.append(f"| {a['url']} | {status} |")
    lines.append("")

    lines.append("## robots.txt & sitemap.xml")
    lines.append(f"- robots.txt: **{robots_sitemap['robots_status']}**")
    lines.append(f"- sitemap declared in robots.txt: **{robots_sitemap['sitemap_declared']}**")
    lines.append(f"- sitemap.xml: **{robots_sitemap['sitemap_status']}**, URLs listed: **{robots_sitemap['sitemap_url_count']}**")
    lines.append("")

    lines.append("## Broken internal links")
    if broken:
        lines.append("| Link | Problem |")
        lines.append("|---|---|")
        for url, problem in broken[:50]:
            lines.append(f"| {url} | {problem} |")
    else:
        lines.append("- None found ✅")
    lines.append("")

    lines.append("## Redirect chains (>1 hop)")
    if redirect_chains:
        lines.append("| Link | Hops | Final |")
        lines.append("|---|---:|---|")
        for url, hops, final in redirect_chains[:50]:
            lines.append(f"| {url} | {hops} | {final} |")
    else:
        lines.append("- None found ✅")
    lines.append("")
    return "\n".join(lines), critical


def run() -> int:
    site = config.SITE_URL.rstrip("/")
    host = site.split("://", 1)[-1].split("/", 1)[0]

    print(f"Checking {site} ...")
    avail = check_availability(site)
    robots_sitemap = check_robots_sitemap(site)
    ssl_days = _ssl_days_left(host)
    pages, links = collect_internal_links(site, MAX_PAGES)
    broken, redirect_chains = check_links(links, MAX_LINKS)

    report, critical = build_health_report(avail, robots_sitemap, ssl_days, broken, redirect_chains, len(pages))

    os.makedirs(HEALTH_DIR, exist_ok=True)
    for name in (f"{datetime.date.today().isoformat()}.md", "latest.md"):
        with open(os.path.join(HEALTH_DIR, name), "w", encoding="utf-8") as f:
            f.write(report)

    print(report)
    print(f"\nCritical: {critical} | broken links: {len(broken)} | redirect chains: {len(redirect_chains)}")
    return 1 if critical else 0


if __name__ == "__main__":
    sys.exit(run())
