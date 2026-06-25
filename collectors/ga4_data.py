"""GA4 Data API collectors — measurement check, ecommerce check, event list."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    OrderBy,
    RunReportRequest,
)


def data_client(creds):
    return BetaAnalyticsDataClient(credentials=creds)


def _date_range(days: int) -> DateRange:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    return DateRange(start_date=start.isoformat(), end_date=end.isoformat())


def check_measurement(creds, property_id: str, days: int = 7) -> dict:
    client = data_client(creds)
    try:
        resp = client.run_report(RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[_date_range(days)],
            metrics=[Metric(name="sessions"), Metric(name="activeUsers"), Metric(name="eventCount")],
        ))
        row = resp.rows[0] if resp.rows else None
        sessions = int(row.metric_values[0].value) if row else 0
        users = int(row.metric_values[1].value) if row else 0
        events = int(row.metric_values[2].value) if row else 0
        return {
            "ok": True,
            "is_tracked": (sessions + users + events) > 0,
            "sessions_7d": sessions,
            "users_7d": users,
            "events_7d": events,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "is_tracked": None}


def check_ecommerce(creds, property_id: str, ecom_events: list[str], days: int = 30) -> dict:
    client = data_client(creds)
    try:
        resp = client.run_report(RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[_date_range(days)],
            dimensions=[Dimension(name="eventName")],
            metrics=[Metric(name="eventCount")],
        ))
        found = {}
        for row in resp.rows:
            name = row.dimension_values[0].value
            if name in ecom_events:
                found[name] = int(row.metric_values[0].value)
        return {
            "ok": True,
            "is_ecommerce": len(found) > 0,
            "events_found": found,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "is_ecommerce": None}


def list_events(creds, property_id: str, days: int = 30) -> list[dict]:
    client = data_client(creds)
    try:
        resp = client.run_report(RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[_date_range(days)],
            dimensions=[Dimension(name="eventName")],
            metrics=[Metric(name="eventCount"), Metric(name="totalUsers")],
            order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="eventCount"), desc=True)],
            limit=500,
        ))
        out = []
        for row in resp.rows:
            out.append({
                "event_name": row.dimension_values[0].value,
                "event_count": int(row.metric_values[0].value),
                "total_users": int(row.metric_values[1].value),
            })
        return out
    except Exception as e:
        return [{"_error": str(e)[:300]}]
