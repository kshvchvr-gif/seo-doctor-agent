# SEO Agent Roadmap & Resume Notes

> **Last updated:** 2026-10-16
> **Resume point:** ▶️ **Phase 4 — Dashboard** (see below). Phases 0, 1, 2 & 3 are DONE and live.

This file is the single source of truth for where the project is and what to do next.
When resuming, read this file top-to-bottom, then start at the **Resume point**.

---

## 0. Context (what exists)

Two separate repos:

| Repo | Purpose |
|---|---|
| `kshvchvr-gif/seo-doctor-agent` | The AI website/SEO monitor (all our work lives here) |
| `kshvchvr-gif/krishnbhakti-web` | The actual website (Firebase Hosting, deploys on push to `main`) |

**Monitor architecture (existing, kept intact):**
`src/gsc_client.py` (Search Console), `src/ga4_client.py` (GA4), `src/sitemap_crawler.py`,
`src/embeddings.py` (Gemini embeddings + cosine), `src/scoring.py` (diagnosis),
`src/rag_linking.py`, `src/ai_treatment.py` (Gemini fixes), `src/report_generator.py`, `src/main.py`.

**Workflows**
- `SEO Doctor Weekly` — `.github/workflows/seo-doctor.yml` — Sun 3:00 AM UTC.
- `Website Health` — `.github/workflows/website-health.yml` — daily 2:00 AM UTC.
- `SEO Auto-Fix` — `.github/workflows/auto-fix.yml` — runs right after the weekly report
  (`workflow_run`); verifies merged fixes, then opens/updates a PR against the website repo.

**Rolling issues (update in place, older ones auto-close):**
- `SEO Doctor Report — Latest` (label `seo-report`)
- `Website Health — Latest` (label `site-health`)

**Required secrets (repo → Settings → Secrets → Actions):**
`GSC_SA_KEY`, `GSC_SITE_URL`, `GA4_PROPERTY_ID`, `GEMINI_API_KEY`, `SITEMAP_URL`
(optional: `GEMINI_MODEL`, `AI_TREATMENT_MAX_PAGES`, `SITE_URL`).

**Phase 3 extra secret (required for auto-fix):**
`SITE_REPO_TOKEN` — a fine-grained PAT with `Contents: Read and write` +
`Pull requests: Read and write` on **`krishnbhakti-web`** only. Without it the
auto-fix workflow prints setup instructions and skips (it never fails the run).

## Status at a glance

| Phase | What | Status |
|---|---|---|
| 0 | Notification hygiene + grouped/ranked technical issues | ✅ Done |
| 1 | Daily website health monitor | ✅ Done |
| 2 | Traffic decline diagnosis (evidence-based) | ✅ Done |
| 3 | Safe auto-fix via PR + verify + rollback | ✅ Done |
| 4 | Dashboard | ⬜ **NEXT** |

---

## ✅ Phase 0 — Notification hygiene (DONE)

- `seo-doctor.yml` now updates one rolling issue and closes stale dated issues.
- `report_generator.py` groups technical flags by type, ranked by pages affected.

Verify: Actions → "SEO Doctor Weekly" → Run workflow. Then check Issues — should stay at 1 `seo-report` issue.

## ✅ Phase 1 — Website Health monitor (DONE)

- `src/health.py` + `website-health.yml` (daily).
- Checks: critical-URL availability/HTTP, robots.txt + sitemap.xml, SSL expiry, broken internal
  links, redirect chains (>1 hop). Writes `reports/health/latest.md`, publishes rolling issue,
  fails the run on critical.

Verify: Actions → "Website Health" → Run workflow.

---

## ✅ Phase 2 — Traffic decline diagnosis (DONE)

**Goal:** jab traffic/visibility gire, evidence ke saath batao — *kya badla, kaunse pages/queries,
likely cause, evidence, next action*. Read-only; koi live-site change nahi.

**Built:**
- `src/trends.py` (new): period-over-period comparison for the 28d and 7d windows.
  - Pulls current vs previous period from `gsc_client.fetch_page_data()` / `fetch_query_data()`
    and GA4 `fetch_traffic_sources()` (all now accept explicit `start_date`/`end_date`).
  - Computes per-page and per-query deltas: clicks, impressions, CTR, position.
  - Flags declining and rising pages/queries (absolute + relative thresholds for a tiny site).
  - Deterministic cause heuristic per decline: vanished / ranking drop / demand (seasonality) /
    snippet-CTR loss / mixed — each with evidence and a read-only next action.
- `report_generator.py` — new "📉 Traffic Movement — period-over-period" section plus a
  one-line movement headline in the Executive Diagnosis.
- `main.py` — calls `trends.analyze()` (step 3/8); any failure degrades gracefully to no section.

**Acceptance:** report shows period-over-period deltas + a ranked "declining pages" list with
the 5-question explanation per top item. No live-site edits. ✅

Verify: Actions → "SEO Doctor Weekly" → Run workflow, then read the "Traffic Movement" section
in the rolling issue.

---

## ✅ Phase 3 — Safe auto-fix (PR-based) + verify + rollback (DONE)

**Goal:** monitoring ke baad, low-risk on-page fixes ko **PR ke through** apply karo — kabhi
`krishnbhakti-web` ke `main` pe seedha push nahi. Merge ke baad verify; worse nikle to revert.

**Built:**
- `src/fixes.py` (new): pure planning + applying of fixes. Minimal-diff, **exact-string**
  replacements only (anchor nahi mila to skip — blind rewrite kabhi nahi). Supports:
  - `TITLE` (AI treatment recommended title, validated 30–70 chars) + keeps `og:title` in sync
  - `META_DESCRIPTION` (AI recommended, or deterministically derived when missing) + `og:description`
  - `CANONICAL` (deterministic; insert when absent)
  - `IMAGE_ALT` (deterministic; only content images under `/images/` that lack `alt`)
  - `INTERNAL_LINKS` ("Also read" block; **opt-in**, off by default via `AUTOFIX_INTERNAL_LINKS=1`;
    skips any `.blog-content` with nested `<div>`s to stay safe)
  - `url_to_repo_path()` maps `https://krishnbhakti.com/<path>` → `<path>.html` (Firebase cleanUrls).
  - Wrong/renamed URL (e.g. slugs consolidated to 301) → file not found → **skipped**, never guessed.
- `src/auto_fix.py` (new): clones the website repo (shallow), applies the plan on a stable rolling
  branch `seo-autofix`, commits, pushes, and opens/updates **one** draft PR (label `seo-autofix`).
  Plain push first (fast-forward), `--force-with-lease` only as fallback. Missing `SITE_REPO_TOKEN`
  → prints setup steps and exits 0. Records every change in `fixes_history.json`.
- `src/verify_fixes.py` (new): refreshes each PR's status (open→merged/closed), then for merged
  fixes old enough (default 14d after merge) compares the affected pages' GSC clicks/impressions
  *before* vs *after* (`classify()` = improved / worse / flat, clicks decide). With
  `AUTOFIX_AUTO_REVERT=1` a clearly-worse fix gets a **draft revert PR** (merge commit reverted on a
  fresh branch). Pure classification is unit-tested; GSC fetch is injectable.
- `main.py` — emits `reports/fix_plan.json` after the report (step 9); failure never breaks the report.
- `config.py` — `AUTOFIX_INTERNAL_LINKS` flag.
- `.github/workflows/auto-fix.yml` (new) — triggers on completion of the weekly run
  (`workflow_run`) + manual. Steps: verify merged fixes → apply via PR → commit `fixes_history.json`.
- `fixes_history.json` (new) — the log (starts `[]`).

**Safety model:** PR-only (never `main`), one rolling draft PR, minimal diffs, skips on ambiguity,
verification writes to history, optional revert. Nothing is auto-merged.

**Acceptance:** ✅ code + workflow live; offline tests pass (plan/apply on real site HTML, PR-status
& classification logic). Live PR requires the `SITE_REPO_TOKEN` secret (see above).

Verify locally: `SITE_REPO_TOKEN=<pat> python -m src.auto_fix --dry-run`.
Verify in CI: Actions → "SEO Auto-Fix" → Run workflow.

---

## ⬜ Phase 4 — Dashboard

**Resume here.**

- Static HTML generated from `reports/*.md` + a small JSON index, published via GitHub Pages.
- Show: health status, priority pages, content gaps, technical issue trends, decline list, fix history.

---

## Also recently done on the *site* repo (krishnbhakti-web)

- Consolidated 8 duplicate shloka pages (301) + refreshed internal links.
- Auto hreflang injection wired into `generate-sitemap.py`.
- Deterministic RSS/sitemap/archive ordering (loop-proof builds).
- Deploy workflow: actor guard + blog dirs in generated-file commit.

## One-line resume prompt for next session

> "seo-doctor-agent me ROADMAP.md padho aur **Phase 4 (Dashboard)** se continue karo."
