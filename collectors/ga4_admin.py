"""GA4 Admin API collectors — accounts, properties, key events, custom defs, access bindings."""
from __future__ import annotations

from google.analytics.admin_v1beta import AnalyticsAdminServiceClient
from google.analytics.admin_v1alpha import AnalyticsAdminServiceClient as AdminAlphaClient


def admin_client(creds):
    return AnalyticsAdminServiceClient(credentials=creds)


def admin_alpha_client(creds):
    return AdminAlphaClient(credentials=creds)


def list_accounts(creds):
    client = admin_client(creds)
    out = []
    for acc in client.list_accounts():
        out.append({
            "name": acc.name,
            "account_id": acc.name.split("/")[-1],
            "display_name": acc.display_name,
            "region_code": acc.region_code,
            "create_time": acc.create_time.isoformat() if acc.create_time else None,
        })
    return out


def list_properties(creds, account_resource_name: str):
    client = admin_client(creds)
    out = []
    for prop in client.list_properties(request={"filter": f"parent:{account_resource_name}"}):
        out.append({
            "name": prop.name,
            "property_id": prop.name.split("/")[-1],
            "display_name": prop.display_name,
            "time_zone": prop.time_zone,
            "currency_code": prop.currency_code,
            "industry_category": prop.industry_category.name if prop.industry_category else None,
            "property_type": prop.property_type.name if prop.property_type else None,
            "create_time": prop.create_time.isoformat() if prop.create_time else None,
            "parent": prop.parent,
        })
    return out


def list_data_streams(creds, property_resource_name: str):
    client = admin_client(creds)
    out = []
    for stream in client.list_data_streams(parent=property_resource_name):
        s = {
            "name": stream.name,
            "type": stream.type_.name if stream.type_ else None,
            "display_name": stream.display_name,
            "create_time": stream.create_time.isoformat() if stream.create_time else None,
        }
        if stream.web_stream_data:
            s["measurement_id"] = stream.web_stream_data.measurement_id
            s["default_uri"] = stream.web_stream_data.default_uri
        elif stream.android_app_stream_data:
            s["package_name"] = stream.android_app_stream_data.package_name
        elif stream.ios_app_stream_data:
            s["bundle_id"] = stream.ios_app_stream_data.bundle_id
        out.append(s)
    return out


def list_key_events(creds, property_resource_name: str):
    client = admin_client(creds)
    out = []
    try:
        for ke in client.list_key_events(parent=property_resource_name):
            out.append({
                "name": ke.name,
                "event_name": ke.event_name,
                "counting_method": ke.counting_method.name if ke.counting_method else None,
                "create_time": ke.create_time.isoformat() if ke.create_time else None,
            })
    except Exception:
        try:
            for ce in client.list_conversion_events(parent=property_resource_name):
                out.append({
                    "name": ce.name,
                    "event_name": ce.event_name,
                    "counting_method": None,
                    "create_time": ce.create_time.isoformat() if ce.create_time else None,
                })
        except Exception:
            pass
    return out


def list_custom_dimensions(creds, property_resource_name: str):
    client = admin_client(creds)
    out = []
    for cd in client.list_custom_dimensions(parent=property_resource_name):
        out.append({
            "name": cd.name,
            "parameter_name": cd.parameter_name,
            "display_name": cd.display_name,
            "description": cd.description,
            "scope": cd.scope.name if cd.scope else None,
        })
    return out


def list_custom_metrics(creds, property_resource_name: str):
    client = admin_client(creds)
    out = []
    for cm in client.list_custom_metrics(parent=property_resource_name):
        out.append({
            "name": cm.name,
            "parameter_name": cm.parameter_name,
            "display_name": cm.display_name,
            "description": cm.description,
            "measurement_unit": cm.measurement_unit.name if cm.measurement_unit else None,
            "scope": cm.scope.name if cm.scope else None,
        })
    return out


def get_data_retention(creds, property_resource_name: str) -> dict:
    """データ保持期間（v1beta）。取得失敗は ok=False で返し、呼び出し側は「未確認」として扱う。"""
    client = admin_client(creds)
    try:
        s = client.get_data_retention_settings(
            name=f"{property_resource_name}/dataRetentionSettings"
        )
        return {
            "ok": True,
            "event_data_retention": s.event_data_retention.name if s.event_data_retention else None,
            "reset_user_data_on_new_activity": bool(s.reset_user_data_on_new_activity),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def list_google_ads_links(creds, property_resource_name: str) -> dict:
    """Google広告リンク一覧（v1beta）。"""
    client = admin_client(creds)
    try:
        out = []
        for link in client.list_google_ads_links(parent=property_resource_name):
            out.append({
                "name": link.name,
                "customer_id": link.customer_id,
                "ads_personalization_enabled": bool(link.ads_personalization_enabled),
            })
        return {"ok": True, "links": out}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "links": []}


def get_enhanced_measurement(creds, stream_resource_name: str) -> dict:
    """拡張計測設定（v1alpha・webストリームのみ）。parent はデータストリームのリソース名。"""
    client = admin_alpha_client(creds)
    try:
        s = client.get_enhanced_measurement_settings(
            name=f"{stream_resource_name}/enhancedMeasurementSettings"
        )
        return {
            "ok": True,
            "stream_enabled": bool(s.stream_enabled),
            "scrolls_enabled": bool(s.scrolls_enabled),
            "outbound_clicks_enabled": bool(s.outbound_clicks_enabled),
            "site_search_enabled": bool(s.site_search_enabled),
            "video_engagement_enabled": bool(s.video_engagement_enabled),
            "file_downloads_enabled": bool(s.file_downloads_enabled),
            "page_changes_enabled": bool(s.page_changes_enabled),
            "form_interactions_enabled": bool(s.form_interactions_enabled),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def list_event_create_rules(creds, stream_resource_name: str) -> dict:
    """イベント作成ルール一覧（v1alpha・データストリーム単位）。

    GTM にも gtag にも現れない定義のため、ここを取らないとカスタムイベントの
    定義元や、同一条件による多重生成（CV水増し）を見落とす。
    """
    client = admin_alpha_client(creds)
    try:
        out = []
        for r in client.list_event_create_rules(parent=stream_resource_name):
            out.append({
                "name": r.name,
                "destination_event": r.destination_event,
                "source_copy_parameters": bool(r.source_copy_parameters),
                "event_conditions": [
                    {
                        "field": c.field,
                        "comparison_type": c.comparison_type.name if c.comparison_type else None,
                        "value": c.value,
                        "negated": bool(getattr(c, "negated", False)),
                    }
                    for c in (r.event_conditions or [])
                ],
            })
        return {"ok": True, "rules": out}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "rules": []}


def list_access_bindings(creds, property_resource_name: str, my_email: str):
    """Returns my own roles on the property. Requires admin on the property to list all bindings;
    falls back to empty list if forbidden."""
    client = admin_alpha_client(creds)
    out = []
    try:
        for ab in client.list_access_bindings(parent=property_resource_name):
            user = getattr(ab, "user", "") or ""
            if user.lower() == my_email.lower():
                out.append({
                    "name": ab.name,
                    "user": user,
                    "roles": list(ab.roles),
                })
    except Exception:
        pass
    return out
