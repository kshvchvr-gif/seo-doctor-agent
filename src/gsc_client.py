"""
Thin wrapper around the Search Console API (searchanalytics.query).
Docs: https://developers.google.com/webmaster-tools/v1/searchanalytics/query
"""
import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build

from . import config

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]


def _get_service():
    creds_path = config.get_gsc_credentials_path()
    credentials = service_account.Credentials.from_service_account_file(
        creds_path, scopes=SCOPES
    )
    return build("searchconsole", "v1", credentials=credentials)


def _date_range(days: int):
    end = datetime.date.today() - datetime.timedelta(days=2)  # GSC data lags ~2 days
    start = end - datetime.timedelta(days=days)
    return start.isoformat(), end.isoformat()


def fetch_query_data(site_url: str = None, days: int = None, row_limit: int = 5000,
                     start_date: str = None, end_date: str = None):
    """Returns list of dicts: query, page, clicks, impressions, ctr, position.
    Pass explicit start_date/end_date (ISO) to fetch an arbitrary period,
    or days to use the default lagged lookback window."""
    site_url = site_url or config.GSC_SITE_URL
    if start_date and end_date:
        start, end = start_date, end_date
    else:
        days = days or config.GSC_LOOKBACK_DAYS
        start, end = _date_range(days)
    service = _get_service()

    rows = []
    start_row = 0
    while True:
        body = {
            "startDate": start,
            "endDate": end,
            "dimensions": ["query", "page"],
            "rowLimit": row_limit,
            "startRow": start_row,
        }
        resp = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
        batch = resp.get("rows", [])
        if not batch:
            break
        for r in batch:
            rows.append({
                "query": r["keys"][0],
                "page": r["keys"][1],
                "clicks": r["clicks"],
                "impressions": r["impressions"],
                "ctr": r["ctr"],
                "position": r["position"],
            })
        if len(batch) < row_limit:
            break
        start_row += row_limit
    return rows


def fetch_page_data(site_url: str = None, days: int = None, row_limit: int = 5000,
                    start_date: str = None, end_date: str = None):
    """Returns list of dicts: page, clicks, impressions, ctr, position (aggregated across queries).
    Pass explicit start_date/end_date (ISO) to fetch an arbitrary period."""
    site_url = site_url or config.GSC_SITE_URL
    if start_date and end_date:
        start, end = start_date, end_date
    else:
        days = days or config.GSC_LOOKBACK_DAYS
        start, end = _date_range(days)
    service = _get_service()

    body = {
        "startDate": start,
        "endDate": end,
        "dimensions": ["page"],
        "rowLimit": row_limit,
    }
    resp = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
    rows = []
    for r in resp.get("rows", []):
        rows.append({
            "page": r["keys"][0],
            "clicks": r["clicks"],
            "impressions": r["impressions"],
            "ctr": r["ctr"],
            "position": r["position"],
        })
    return rows


def fetch_sitemaps_status(site_url: str = None):
    """Returns indexing-relevant info: which sitemaps are submitted + their status."""
    site_url = site_url or config.GSC_SITE_URL
    service = _get_service()
    resp = service.sitemaps().list(siteUrl=site_url).execute()
    return resp.get("sitemap", [])
