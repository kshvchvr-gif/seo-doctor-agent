"""
Phase 3 — verification & rollback for merged auto-fixes.

Flow (runs a couple of weeks after a fix was merged):
  1. Refresh each history entry's PR status via the GitHub API
     (open -> merged / closed).
  2. For merged fixes old enough to judge, compare the affected pages'
     Search Console performance *before* vs *after* the merge.
  3. Classify improved / worse / flat and write it back to fixes_history.json.
  4. If AUTOFIX_AUTO_REVERT=1 and a fix is clearly worse, open a revert PR
     (the merge commit is reverted on a fresh branch — never on main).

The classification is a pure function so it is unit-testable without GSC.
"""
import argparse
import datetime
import json
import os
from urllib.parse import urlsplit, urlunsplit

import requests

from . import config

SITE_REPO = os.environ.get("SITE_REPO", "kshvchvr-gif/krishnbhakti-web")
API = "https://api.github.com"
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HISTORY_PATH = os.path.join(REPO_ROOT, "fixes_history.json")

WINDOW_DAYS = int(os.environ.get("VERIFY_WINDOW_DAYS", "28"))
MIN_DAYS_AFTER = int(os.environ.get("VERIFY_AFTER_DAYS", "14"))
REL_THRESHOLD = float(os.environ.get("VERIFY_REL_THRESHOLD", "0.15"))
AUTO_REVERT = os.environ.get("AUTOFIX_AUTO_REVERT", "0") not in ("0", "false", "False", "")

# GSC lags ~2 days; leave the merge day itself out of both windows.
_LAG = datetime.timedelta(days=2)


def _norm_url(u: str) -> str:
    """Normalize a URL for matching (drop trailing slash, keep scheme/host/path)."""
    if not u:
        return ""
    p = urlsplit(u)
    path = p.path.rstrip("/") or "/"
    return urlunsplit((p.scheme, p.netloc.lower(), path, "", ""))


def classify(before: dict, after: dict, rel: float = REL_THRESHOLD) -> str:
    """
    before/after = {"clicks": float, "impressions": float} as per-day rates.
    Clicks decide the direction; impressions only break exact ties.
    Returns 'improved' | 'worse' | 'flat'.
    """
    bc, ac = float(before.get("clicks", 0)), float(after.get("clicks", 0))
    bi, ai = float(before.get("impressions", 0)), float(after.get("impressions", 0))
    if ac > bc * (1 + rel) or (ac == bc and ai > bi * (1 + rel)):
        return "improved"
    if ac < bc * (1 - rel) or (ac == bc and ai < bi * (1 - rel)):
        return "worse"
    return "flat"


def _rate(totals: dict, days: int) -> dict:
    days = max(days, 1)
    return {"clicks": totals.get("clicks", 0) / days,
            "impressions": totals.get("impressions", 0) / days}


def summarize_pages(rows: list, urls: list) -> dict:
    """Sum clicks/impressions for the given pages from GSC page rows."""
    want = {_norm_url(u) for u in urls}
    clicks = impressions = 0.0
    for r in rows:
        if _norm_url(r.get("page", "")) in want:
            clicks += r.get("clicks", 0)
            impressions += r.get("impressions", 0)
    return {"clicks": clicks, "impressions": impressions}


# --------------------------------------------------------------------------- #
# GitHub PR status
# --------------------------------------------------------------------------- #
def _headers(token):
    return {"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def _token():
    return os.environ.get("SITE_REPO_TOKEN") or os.environ.get("GH_TOKEN") or ""


def refresh_pr_status(entry: dict, token: str) -> bool:
    """Update entry['status']/['merge_commit_sha'] from the PR. Returns True if changed."""
    num = entry.get("pr")
    if not num or not token:
        return False
    r = requests.get(f"{API}/repos/{SITE_REPO}/pulls/{num}", headers=_headers(token), timeout=30)
    if r.status_code != 200:
        return False
    pr = r.json()
    new_status = "merged" if pr.get("merged_at") else ("closed" if pr.get("state") == "closed" else "open")
    changed = new_status != entry.get("status") or pr.get("merge_commit_sha") != entry.get("merge_commit_sha")
    entry["status"] = new_status
    entry["merged_at"] = pr.get("merged_at")
    entry["merge_commit_sha"] = pr.get("merge_commit_sha")
    return changed


# --------------------------------------------------------------------------- #
# verification
# --------------------------------------------------------------------------- #
def _fetch_page_data(start, end):
    """Indirection so tests can monkeypatch this."""
    from . import gsc_client
    return gsc_client.fetch_page_data(start_date=start.isoformat(), end_date=end.isoformat())


def _parse_date(s):
    try:
        return datetime.date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def evaluate_entry(entry: dict, today: datetime.date) -> dict:
    """Compute before/after metrics for a merged entry. Returns a metrics dict or {}."""
    merged_at = _parse_date(entry.get("merged_at")) or _parse_date(entry.get("date"))
    if not merged_at:
        return {}
    # need enough post-merge days
    if (today - merged_at).days < MIN_DAYS_AFTER + _LAG.days:
        return {"skipped": "not enough days since merge"}

    after_end = today - _LAG
    after_start = merged_at + _LAG
    after_days = (after_end - after_start).days
    before_end = merged_at - _LAG
    before_start = before_end - datetime.timedelta(days=WINDOW_DAYS)

    urls = entry.get("pages") or []
    if not urls:
        return {"skipped": "no pages recorded"}

    before_rows = _fetch_page_data(before_start, before_end)
    after_rows = _fetch_page_data(after_start, after_end)
    before = summarize_pages(before_rows, urls)
    after = summarize_pages(after_rows, urls)

    direction = classify(_rate(before, WINDOW_DAYS), _rate(after, after_days))
    return {
        "direction": direction,
        "before": before, "before_days": WINDOW_DAYS,
        "after": after, "after_days": after_days,
        "checked_on": today.isoformat(),
    }


def open_revert_pr(entry: dict, dry_run: bool = False) -> str:
    """Revert the merged fix commit on a fresh branch and open a PR. Returns URL or ''."""
    import subprocess, tempfile
    token = _token()
    sha = entry.get("merge_commit_sha")
    if not token or not sha:
        return ""
    branch = f"seo-autofix-revert-{entry.get('pr')}"
    if dry_run:
        return f"(dry-run) would revert {sha} on branch {branch}"
    dest = os.path.join(tempfile.mkdtemp(prefix="revert-"), "site")
    url = f"https://x-access-token:{token}@github.com/{SITE_REPO}.git"
    subprocess.run(["git", "clone", url, dest], check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "seo-doctor-bot"], cwd=dest, check=True)
    subprocess.run(["git", "config", "user.email", "actions@github.com"], cwd=dest, check=True)
    subprocess.run(["git", "checkout", "-b", branch], cwd=dest, check=True)
    subprocess.run(["git", "revert", "--no-edit", "-m", "1", sha], cwd=dest, check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "push", "origin", branch], cwd=dest, check=True, capture_output=True, text=True)
    payload = {
        "title": f"Revert SEO auto-fix #{entry.get('pr')} (regression detected)",
        "head": branch, "base": "main", "draft": True,
        "body": f"Automated revert of #{entry.get('pr')}.\n\n"
                f"Verification after merge showed a decline:\n"
                f"- before: {entry.get('verification', {}).get('before')}\n"
                f"- after:  {entry.get('verification', {}).get('after')}\n"
                f"- direction: **worse**\n",
    }
    r = requests.post(f"{API}/repos/{SITE_REPO}/pulls", headers=_headers(token), json=payload, timeout=60)
    return r.json().get("html_url", "") if r.status_code in (200, 201) else ""


def run(check_prs=True, auto_revert=None, dry_run=False) -> int:
    if not os.path.exists(HISTORY_PATH):
        print("No fixes_history.json — nothing to verify.")
        return 0
    with open(HISTORY_PATH, "r", encoding="utf-8") as f:
        history = json.load(f)

    token = _token()
    today = datetime.date.today()
    auto_revert = AUTO_REVERT if auto_revert is None else auto_revert
    refreshed = 0   # PR statuses whose state actually changed
    verified = 0    # entries newly classified against GSC data

    for entry in history:
        if check_prs and refresh_pr_status(entry, token):
            refreshed += 1
        if entry.get("status") != "merged":
            continue
        if entry.get("direction") in ("improved", "worse", "flat"):
            continue
        try:
            metrics = evaluate_entry(entry, today)
        except Exception as e:  # verification must never crash the workflow
            print(f"   verify #{entry.get('pr')} failed: {e}")
            continue
        if not metrics or "skipped" in metrics:
            print(f"   verify #{entry.get('pr')}: {metrics.get('skipped', 'no data')}")
            continue
        entry["verification"] = metrics
        entry["direction"] = metrics["direction"]
        verified += 1
        print(f"   verify #{entry.get('pr')}: {metrics['direction']} "
              f"(before {metrics['before']} / after {metrics['after']})")
        if metrics["direction"] == "worse" and auto_revert:
            url = open_revert_pr(entry, dry_run=dry_run)
            entry["revert_pr"] = url
            print(f"   opened revert PR: {url}")

    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)
    print(f"fixes_history.json: {refreshed} PR status change(s), {verified} fix(es) verified.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-check-prs", action="store_true")
    ap.add_argument("--auto-revert", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    raise SystemExit(run(check_prs=not a.no_check_prs, auto_revert=a.auto_revert or None, dry_run=a.dry_run))
