"""
Central config. Everything is read from environment variables so the same
code runs locally (via `export ...`) or inside GitHub Actions (via secrets).
"""
import os
import json
import tempfile


def get_gsc_credentials_path() -> str:
    """
    GSC_SA_KEY holds the raw JSON contents of the service account key.
    We write it to a temp file because the google-api-python-client
    credential loaders expect a file path.
    """
    raw = os.environ["GSC_SA_KEY"]
    # Allow the secret to be pasted with or without surrounding whitespace
    data = json.loads(raw)
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    return path


# Domain property in Search Console - must match exactly how the site is registered
GSC_SITE_URL = os.environ.get("GSC_SITE_URL", "sc-domain:krishnbhakti.com")
GA4_PROPERTY_ID = os.environ.get("GA4_PROPERTY_ID", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# Stable, cost-efficient model for structured SEO recommendations. Override in Actions if desired.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
# If the configured model is deprecated/404, we transparently retry with these newer ones.
GEMINI_FALLBACK_MODELS = ["gemini-3.5-flash-lite", "gemini-2.5-flash"]
SITEMAP_URL = os.environ.get("SITEMAP_URL", "https://krishnbhakti.com/sitemap.xml")

# how far back to pull data
GSC_LOOKBACK_DAYS = 90
GA4_LOOKBACK_DAYS = 28

# scoring thresholds - tuned for a small/new site, adjust as authority grows
MIN_IMPRESSIONS_TO_CONSIDER = 15
SNIPPET_LOSS_MAX_CTR = 0.008       # under 0.8% CTR at good position = snippet-loss suspect
GOOD_POSITION_THRESHOLD = 12       # position <=12 counts as "good" for CTR expectations
TOP_N_PRIORITY_PAGES = 15
TOP_N_LINK_SUGGESTIONS_PER_PAGE = 5

AI_TREATMENT_MAX_PAGES = int(os.environ.get("AI_TREATMENT_MAX_PAGES", "8"))
