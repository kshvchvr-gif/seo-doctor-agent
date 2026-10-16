# KrishnBhakti SEO Doctor Agent

Fully automated SEO agent for krishnbhakti.com. Runs weekly via GitHub Actions —
no server, no laptop needed. Reads Search Console + GA4 data, diagnoses each
page like a doctor, suggests RAG-based internal links, and posts a
prioritized report as a GitHub Issue.

## What it does every run

1. Pulls last 90 days of Search Console query + page data
2. Pulls last 28 days of GA4 traffic-source data
3. Crawls your sitemap, extracts every blog page's text
4. Builds embeddings (Gemini `gemini-embedding-001`, free tier) for every page
5. **SEO Doctor scoring** — flags each page as one of:
   - `SNIPPET_LOSS` — good position, high impressions, near-zero CTR (Google answers directly, no need to click)
   - `TITLE_WEAK` — high impressions, low CTR, and CTR is below what's normal for that position
   - `CONTENT_GAP` — a query has real impressions but no page targets it well
   - `RANK_LOW` — decent CTR when shown, but position is too low (authority/content-depth issue)
   - `HEALTHY` — performing as expected for its position
6. **RAG internal linking** — for every page, finds the 5 most semantically
   similar other pages on the site and suggests linking them (only if not
   already linked)
7. **AI treatment** — for the highest-priority pages, Gemini produces exact
   title, meta description, H1, primary/secondary keywords, content changes,
   FAQs, schema, image SEO and internal-link treatment. A deterministic fallback
   is used if Gemini is unavailable.
8. **Technical SEO checks** — title/meta length, H1 count, canonical, image alt
   text and thin-content flags.
9. Writes `reports/YYYY-MM-DD.md` and commits it to the repo
10. Updates a **single rolling GitHub Issue** ("SEO Doctor Report — Latest") and auto-closes
    any older weekly issues, so you never get notification spam
11. Technical flags are grouped by type and ranked by pages affected (e.g. "META_LENGTH: 40 pages")
    instead of dumping hundreds of raw per-page flags
12. **Traffic decline diagnosis** — compares the current period against the previous one
    (last 28d vs prior 28d, and last 7d vs prior 7d) and, for every notable drop, answers:
    what changed, which pages/queries, likely cause, evidence, and next action. Read-only.
13. **Safe auto-fix via PR** — low-risk on-page fixes (title, meta description, canonical,
    image alt) are applied on a rolling branch and opened as **one draft PR against the
    website repo** (`krishnbhakti-web`) for human review. It never pushes to the site's
    `main`. Merged fixes are verified against Search Console a couple of weeks later
    (improved / worse / flat) and logged in `fixes_history.json`; a clearly-worse fix can
    be auto-reverted via another draft PR (`AUTOFIX_AUTO_REVERT=1`).

## One-time setup (~20 minutes)

### 1. Google Cloud service account (used for both Search Console + GA4)

1. Go to https://console.cloud.google.com → create a project (or reuse one)
2. Enable **Search Console API** and **Google Analytics Data API**
3. IAM & Admin → Service Accounts → Create service account → create a JSON key,
   download it
4. Copy the service account's email (looks like
   `seo-agent@your-project.iam.gserviceaccount.com`)

### 2. Give the service account access

- **Search Console**: property → Settings → Users and permissions → Add user
  → paste the service account email → Permission: **Full**
- **GA4**: Admin → Property Access Management → Add users → paste the service
  account email → Role: **Viewer**

### 3. Gemini API key (free tier)

Get one from https://aistudio.google.com/apikey

### 4. Create a GitHub repo and add secrets

Repo → Settings → Secrets and variables → Actions → New repository secret:

| Secret name | Value |
|---|---|
| `GSC_SA_KEY` | paste the **entire contents** of the service account JSON file |
| `GSC_SITE_URL` | `sc-domain:krishnbhakti.com` (must match exactly as in Search Console) |
| `GA4_PROPERTY_ID` | your GA4 numeric property ID (Admin → Property Settings) |
| `GEMINI_API_KEY` | your Gemini API key |
| `SITEMAP_URL` | `https://krishnbhakti.com/sitemap.xml` |
| `GEMINI_MODEL` | optional; defaults to `gemini-2.5-flash-lite` |
| `AI_TREATMENT_MAX_PAGES` | optional; defaults to `8` |
| `SITE_REPO_TOKEN` | **Phase 3 (auto-fix).** A fine-grained PAT with `Contents: Read and write` + `Pull requests: Read and write` on the **website** repo only. Without it, auto-fix skips cleanly. |

### 5. Push this code to that repo

```bash
git init
git remote add origin https://github.com/<you>/seo-doctor-agent.git
git add .
git commit -m "seo doctor agent"
git push -u origin main
```

### 6. Enable Actions

Repo → Actions tab → enable workflows. It's set to run **every Sunday 3 AM
UTC** (~8:30 AM IST). You can also trigger it manually anytime: Actions →
"SEO Doctor Weekly" → Run workflow. The **"SEO Auto-Fix"** workflow runs
automatically after each weekly report (and can be run manually), but only does
anything once `SITE_REPO_TOKEN` is set.

## Cost

₹0. Search Console API, GA4 API, GitHub Actions (public repo), and Gemini
free tier embeddings are all free at this site's traffic volume.

## Files

```
src/
  gsc_client.py       # Search Console API wrapper
  ga4_client.py        # GA4 Data API wrapper
  sitemap_crawler.py   # fetches sitemap + page text
  embeddings.py        # Gemini embedding calls + cosine similarity
  scoring.py            # SEO Doctor diagnosis logic
  trends.py             # Phase 2: period-over-period traffic decline diagnosis
  health.py             # Phase 1: daily website health monitor
  rag_linking.py        # internal link suggestions
  report_generator.py   # builds the markdown report
  fixes.py               # Phase 3: plan + apply minimal-diff on-page fixes
  auto_fix.py            # Phase 3: open/update a reviewable PR against the site repo
  verify_fixes.py        # Phase 3: verify merged fixes + optional revert PR
  main.py                # orchestrates everything
fixes_history.json                    # Phase 3: log of applied fixes + outcomes
.github/workflows/seo-doctor.yml     # the weekly cron schedule
.github/workflows/website-health.yml # the daily health cron schedule
.github/workflows/auto-fix.yml       # Phase 3: verify + open the auto-fix PR
```

## Local testing (optional, before automating)

```bash
pip install -r requirements.txt
export GSC_SA_KEY="$(cat path/to/service-account.json)"
export GSC_SITE_URL="sc-domain:krishnbhakti.com"
export GA4_PROPERTY_ID="123456789"
export GEMINI_API_KEY="your-key"
export SITEMAP_URL="https://krishnbhakti.com/sitemap.xml"
python -m src.main
```
Report will appear in `reports/`.
