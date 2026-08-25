"""
Thin wrapper around GA4 Data API to pull traffic-source breakdown,
matching the Reports_snapshot export the user manually pulled earlier.
"""
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange, Dimension, Metric, RunReportRequest,
)
from google.oauth2 import service_account

from . import config

SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]


def _get_client():
    creds_path = config.get_gsc_credentials_path()  # same SA key works for both APIs
    credentials = service_account.Credentials.from_service_account_file(
        creds_path, scopes=SCOPES
    )
    return BetaAnalyticsDataClient(credentials=credentials)


def fetch_traffic_sources(property_id: str = None, days: int = None):
    """Returns list of dicts: source_medium, sessions, active_users"""
    property_id = property_id or config.GA4_PROPERTY_ID
    days = days or config.GA4_LOOKBACK_DAYS
    if not property_id:
        return []

    client = _get_client()
    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="sessionSourceMedium")],
        metrics=[Metric(name="sessions"), Metric(name="activeUsers")],
        date_ranges=[DateRange(start_date=f"{days}daysAgo", end_date="today")],
    )
    response = client.run_report(request)

    rows = []
    for row in response.rows:
        rows.append({
            "source_medium": row.dimension_values[0].value,
            "sessions": int(row.metric_values[0].value),
            "active_users": int(row.metric_values[1].value),
        })
    return rows


def fetch_landing_pages(property_id: str = None, days: int = None):
    """Returns list of dicts: landing_page, sessions, active_users, avg_engagement_time"""
    property_id = property_id or config.GA4_PROPERTY_ID
    days = days or config.GA4_LOOKBACK_DAYS
    if not property_id:
        return []

    client = _get_client()
    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="landingPagePlusQueryString")],
        metrics=[
            Metric(name="sessions"),
            Metric(name="activeUsers"),
            Metric(name="averageSessionDuration"),
        ],
        date_ranges=[DateRange(start_date=f"{days}daysAgo", end_date="today")],
    )
    response = client.run_report(request)

    rows = []
    for row in response.rows:
        rows.append({
            "landing_page": row.dimension_values[0].value,
            "sessions": int(row.metric_values[0].value),
            "active_users": int(row.metric_values[1].value),
            "avg_session_seconds": float(row.metric_values[2].value),
        })
    return rows
