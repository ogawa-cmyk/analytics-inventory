"""GA4 Data API collectors — measurement check, ecommerce check, event list."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Filter,
    FilterExpression,
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


def sessions_total(creds, property_id: str, days: int = 30) -> dict:
    """指定期間のセッション総数。イベント実績（30日）と同じ窓で比率検査の分母に使う。"""
    client = data_client(creds)
    try:
        resp = client.run_report(RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[_date_range(days)],
            metrics=[Metric(name="sessions")],
        ))
        row = resp.rows[0] if resp.rows else None
        return {"ok": True, "sessions": int(row.metric_values[0].value) if row else 0}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def page_report(creds, property_id: str, days: int = 30, limit: int = 400) -> dict:
    """ページ実績（pagePath×pageTitle）。404流入・URL分裂・PII・タイトル未設定の検査材料。"""
    client = data_client(creds)
    try:
        resp = client.run_report(RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[_date_range(days)],
            dimensions=[Dimension(name="pagePath"), Dimension(name="pageTitle")],
            metrics=[Metric(name="screenPageViews"), Metric(name="sessions")],
            order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="screenPageViews"), desc=True)],
            limit=limit,
        ))
        rows = []
        for row in resp.rows:
            rows.append({
                "page_path": row.dimension_values[0].value,
                "page_title": row.dimension_values[1].value,
                "views": int(row.metric_values[0].value),
                "sessions": int(row.metric_values[1].value),
            })
        return {"ok": True, "rows": rows, "row_limit": limit, "truncated": len(rows) >= limit}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "rows": []}


def traffic_report(creds, property_id: str, days: int = 30, limit: int = 200) -> dict:
    """流入実績（source×medium×既定チャネル）。サイト内UTM・Unassigned の検査材料。"""
    client = data_client(creds)
    try:
        resp = client.run_report(RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[_date_range(days)],
            dimensions=[
                Dimension(name="sessionSource"),
                Dimension(name="sessionMedium"),
                Dimension(name="sessionDefaultChannelGroup"),
            ],
            metrics=[Metric(name="sessions")],
            order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)],
            limit=limit,
        ))
        rows = []
        for row in resp.rows:
            rows.append({
                "source": row.dimension_values[0].value,
                "medium": row.dimension_values[1].value,
                "channel_group": row.dimension_values[2].value,
                "sessions": int(row.metric_values[0].value),
            })
        return {"ok": True, "rows": rows}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "rows": []}


def hostname_report(creds, property_id: str, days: int = 30, limit: int = 50) -> dict:
    """ホスト名別セッション（計測対象ホストの棚卸し）。

    参照元除外・自己参照の指摘は、まず「どのホスト名で計測されているか」を実データで
    確認してから行う（棚卸し抜きで除外設定を提案しない）。想定外のホスト
    （ステージング・別ドメイン・翻訳プロキシ等）の混入検知にも使う。
    """
    client = data_client(creds)
    try:
        resp = client.run_report(RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[_date_range(days)],
            dimensions=[Dimension(name="hostName")],
            metrics=[Metric(name="sessions")],
            order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)],
            limit=limit,
        ))
        rows = []
        for row in resp.rows:
            rows.append({
                "hostname": row.dimension_values[0].value,
                "sessions": int(row.metric_values[0].value),
            })
        return {"ok": True, "rows": rows}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "rows": []}


def self_referral_landing_pages(creds, property_id: str, sources: list[str],
                                days: int = 30, limit: int = 20) -> dict:
    """自己参照（source=自ホスト名）の発生ランディングページ。

    自己参照を指摘するときは発生ページを併記する（発生ページ不明のまま指摘しない）ための
    追加取得。疑い source が見つかった場合だけ呼ぶ。
    """
    if not sources:
        return {"ok": True, "rows": []}
    client = data_client(creds)
    try:
        resp = client.run_report(RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[_date_range(days)],
            dimensions=[
                Dimension(name="sessionSource"),
                Dimension(name="landingPagePlusQueryString"),
            ],
            metrics=[Metric(name="sessions")],
            dimension_filter=FilterExpression(filter=Filter(
                field_name="sessionSource",
                in_list_filter=Filter.InListFilter(values=sources[:10]),
            )),
            order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)],
            limit=limit,
        ))
        rows = []
        for row in resp.rows:
            rows.append({
                "source": row.dimension_values[0].value,
                "landing_page": row.dimension_values[1].value,
                "sessions": int(row.metric_values[0].value),
            })
        return {"ok": True, "rows": rows}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "rows": []}


def country_report(creds, property_id: str, days: int = 30, limit: int = 30) -> dict:
    """国別の行動品質（機械的アクセス検査の材料）。国名だけでは判定しない前提で複数指標を取る。"""
    client = data_client(creds)
    try:
        resp = client.run_report(RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[_date_range(days)],
            dimensions=[Dimension(name="country")],
            metrics=[
                Metric(name="sessions"),
                Metric(name="keyEvents"),
                Metric(name="userEngagementDuration"),
                Metric(name="bounceRate"),
            ],
            order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)],
            limit=limit,
        ))
        rows = []
        for row in resp.rows:
            rows.append({
                "country": row.dimension_values[0].value,
                "sessions": int(row.metric_values[0].value),
                "key_events": int(float(row.metric_values[1].value or 0)),
                "engagement_duration": float(row.metric_values[2].value or 0),
                "bounce_rate": float(row.metric_values[3].value or 0),
            })
        return {"ok": True, "rows": rows}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "rows": []}


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
