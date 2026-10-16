# SEO Agent Roadmap & Resume Notes

> **Last updated:** 2026-10-16
> **Resume point:** ✅ **All planned phases (0–4) are DONE and live.** See
> "Ideas / backlog" at the bottom for optional next work.

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
It is **set** as of 2026-10-16; it is currently the account's `gh` OAuth token
(broad `repo`+`workflow` scope) — replace it with a scoped fine-grained PAT when
convenient (no code change needed; CI just reads the secret).

## Status at a glance

| Phase | What | Status |
|---|---|---|
| 0 | Notification hygiene + grouped/ranked technical issues | ✅ Done |
| 1 | Daily website health monitor | ✅ Done |
| 2 | Traffic decline diagnosis (evidence-based) | ✅ Done |
| 3 | Safe auto-fix via PR + verify + rollback | ✅ Done |
| 4 | Dashboard | ✅ Done |

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
  - `META_DESCRIPTION` (AI recommended, or deterministically derived when missing) + `og:description`.
    Also **repairs a malformed leftover line**: a bulk edit once dropped the `<meta name="description" `
    opening, leaving `content="…">` rendered as visible text. When present it is replaced with a proper
    tag (no valid description yet) or deleted (a valid description already exists) — never a duplicate,
    and idempotent so a reused auto-fix branch converges cleanly.
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
- `config.py` — `AUTOFIX_INTERNAL_LINKS` flag; `GEMINI_FALLBACK_MODELS` refreshed to live models
  (`gemini-2.5-flash` now returns HTTP 404, which had turned transient free-tier rate-limits into hard
  failures and forced deterministic fallbacks).
- `.github/workflows/auto-fix.yml` (new) — triggers on completion of the weekly run
  (`workflow_run`) + manual. Steps: verify merged fixes → apply via PR → commit `fixes_history.json`.
- `fixes_history.json` (new) — the log (starts `[]`). Holds **one cumulative row per
  rolling PR**: later runs merge into the existing row (union of pages/files, changes
  deduped by file+type) instead of appending duplicates, and a reused PR's description is
  refreshed from that row so it always matches the diff. Empty/corrupt history is tolerated.

**Safety model:** PR-only (never `main`), one rolling draft PR, minimal diffs, skips on ambiguity,
verification writes to history, optional revert. Nothing is auto-merged.

**Acceptance:** ✅ code + workflow live; offline tests pass (plan/apply on real site HTML, PR-status
& classification logic). Live PR requires the `SITE_REPO_TOKEN` secret (see above).

Verify locally: `SITE_REPO_TOKEN=<pat> python -m src.auto_fix --dry-run`.
Verify in CI: Actions → "SEO Auto-Fix" → Run workflow.

---

## ✅ Phase 4 — Dashboard (DONE)

**Goal:** ek jagah sab kuch — health, priority pages, content gaps, technical trends,
decline list, fix history — bina kisi server ke, GitHub Pages pe.

**Built:**
- `src/dashboard.py` (new): pure-stdlib parser + static-site generator. Reads
  `reports/YYYY-MM-DD.md`, `reports/health/*.md`, `reports/fix_plan.json` and
  `fixes_history.json`, and writes:
  - `docs/index.html` — the dashboard: KPI cards (with deltas), status, trend charts
    (inline SVG), technical issues + per-type sparkline trends, priority pages,
    content gaps, declining/rising pages, and the full fix history (with diffs).
  - `docs/data/index.json` — the small JSON index (every parsed run, health, plan, history).
  - `docs/reports/<date>.html` + `docs/reports/health/<date>.html` — the full markdown
    reports, rendered (via the `markdown` package); `docs/reports/index.html` lists them.
  - Charts are **adaptive**: they use the 28-day window once Phase 2 has ≥3 runs, and
    fall back to the rolling 90-day totals so the dashboard is never empty.
  - Parsing is defensive (missing section → `None`) and all report text is HTML-escaped
    (an explicit `_Safe` marker distinguishes trusted HTML from data — a value like
    `<h1>` is escaped, never injected).
- `.github/workflows/dashboard.yml` (new): builds `docs/` and deploys it to GitHub Pages
  via `actions/configure-pages` + `upload-pages-artifact` + `deploy-pages`. Triggers on
  `workflow_run` completion of the weekly + health workflows (so it catches their
  report commits, which a plain `on: push` would miss), plus push to `src/dashboard.py`
  and manual dispatch. Publishes even when a health run *fails* (that's when you most
  want to see the critical status).
- `requirements.txt` — added `markdown==3.10.2` (only used to render the full reports).
- `.gitignore` — ignores the generated `docs/` (built in CI, not committed).
- GitHub Pages enabled (Source: GitHub Actions). Live at
  **https://kshvchvr-gif.github.io/seo-doctor-agent/**

**Acceptance:** ✅ generator + workflow live; offline tests pass (74 checks: real
report/health parsing, index build, HTML structure + tag balance, XSS escaping,
empty/malformed input, full build output). Pages deploys green.

Verify locally: `python -m src.dashboard --out docs` then open `docs/index.html`.
Verify in CI: Actions → "SEO Dashboard" → Run workflow.

---

## Ideas / backlog (optional, not started)

- **Swap `SITE_REPO_TOKEN` for a fine-grained PAT** (security hygiene, no urgency —
  everything works today). It is currently a `gho_` OAuth token with `repo` + `workflow`
  scope, i.e. a master key for *every* repo. Replace with a fine-grained PAT scoped to
  **`krishnbhakti-web` only**, permissions **Contents: R/W + Pull requests: R/W**
  (optionally Issues: R/W so the `seo-autofix` label applies; it is best-effort, so it is
  safe to omit). Steps: <https://github.com/settings/personal-access-tokens/new> →
  Only select repositories → `krishnbhakti-web` → generate, copy the `github_pat_…` string
  once, then update the `SITE_REPO_TOKEN` secret in the agent repo. Verify with an "SEO
  Auto-Fix" run (an invalid/expired token makes that workflow red, never silently). Why it
  matters: the token is only exposed to the two steps that need it (not `pip install`), but a
  compromised PyPI dependency could still read it at Python startup — with the narrow PAT
  that blast radius shrinks from "all repos + workflows" to "one repo, two actions".
- Private-vs-public dashboard: Pages on a public repo is public (already-public
  `reports/*.md` data), publish elsewhere if that should change.
- Per-page drill-down + historical GSC charts; Slack/email digest; more fix types.

---

## Also recently done on the *site* repo (krishnbhakti-web)

- Consolidated 8 duplicate shloka pages (301) + refreshed internal links.
- Auto hreflang injection wired into `generate-sitemap.py`.
- Deterministic RSS/sitemap/archive ordering (loop-proof builds).
- Deploy workflow: actor guard + blog dirs in generated-file commit.

## One-line resume prompt for next session

> "seo-doctor-agent me ROADMAP.md padho — sab phases done hain; backlog dekho aur jo chuno woh karo."
