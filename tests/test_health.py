"""health.py のユニットテスト（アラートの code 付与・GTMタグ品質検出）。

実行: リポジトリルートで `python -m pytest`
"""
import health


# ──────────────────────────────────────
# プロパティのアラート
# ──────────────────────────────────────

def test_every_alert_has_code():
    p = {"data_api_ok": True, "is_tracked": False, "key_event_count": 0,
         "stream_count": 0, "custom_dimension_count": 99, "my_roles": []}
    for a in health.detect_alerts(p):
        assert a.get("code"), f"code の無いアラート: {a}"


def test_quality_pending_is_visible():
    """品質チェック未実施（旧データ）は黙って0件扱いにせず info で見えるようにする。"""
    p = {"is_tracked": True, "key_event_count": 1, "stream_count": 1,
         "custom_dimension_count": 1, "my_roles": ["viewer"]}
    codes = [a["code"] for a in health.detect_alerts(p)]
    assert "ga4.quality_pending" in codes


def test_quality_counts_become_alerts():
    p = {"is_tracked": True, "key_event_count": 1, "stream_count": 1,
         "custom_dimension_count": 1, "my_roles": ["viewer"],
         "quality": {"error": 1, "warn": 2, "info": 0, "unverified": 3}}
    alerts = {a["code"]: a for a in health.detect_alerts(p)}
    assert alerts["ga4.quality_error"]["level"] == "error"
    assert alerts["ga4.quality_warn"]["level"] == "warn"
    assert alerts["ga4.quality_unverified"]["level"] == "info"


def test_alert_summary_counts_quality():
    props = [{"is_tracked": True, "key_event_count": 1, "stream_count": 1,
              "custom_dimension_count": 1, "my_roles": ["v"],
              "quality": {"error": 0, "warn": 1, "info": 0, "unverified": 0}}]
    s = health.alert_count_summary(props)
    assert s["issues"]["quality"] == 1


# ──────────────────────────────────────
# GTM タグ品質（広告CVラベル重複・カスタムHTML内の旧GA）
# ──────────────────────────────────────

def _tag(ttype, name, params=None, paused=False):
    return {"type": ttype, "name": name, "paused": paused,
            "parameter": [{"key": k, "value": v} for k, v in (params or {}).items()]}


def test_awct_duplicate_labels_direct_values():
    tags = [
        _tag("awct", "CV_A", {"conversionId": "123456789", "conversionLabel": "abcDEF"}),
        _tag("awct", "CV_B", {"conversionId": "123456789", "conversionLabel": "abcDEF"}),
        _tag("awct", "CV_C", {"conversionId": "123456789", "conversionLabel": "other"}),
    ]
    q = health._analyze_tag_quality(tags, [])
    assert len(q["awct_dup_groups"]) == 1
    assert q["awct_dup_groups"][0]["tags"] == ["CV_A", "CV_B"]


def test_awct_variable_constant_resolution():
    """{{定数変数}} は解決して比較し、ルックアップテーブルは判定不能として別枠に残す。"""
    variables = [
        {"name": "convId", "type": "c", "parameter": [{"key": "value", "value": "999888777"}]},
        {"name": "lookup", "type": "smm", "parameter": []},
    ]
    tags = [
        _tag("awct", "CV_X", {"conversionId": "{{convId}}", "conversionLabel": "lbl"}),
        _tag("awct", "CV_Y", {"conversionId": "999888777", "conversionLabel": "lbl"}),
        _tag("awct", "CV_Z", {"conversionId": "{{lookup}}", "conversionLabel": "lbl"}),
    ]
    q = health._analyze_tag_quality(tags, variables)
    assert q["awct_dup_groups"] and set(q["awct_dup_groups"][0]["tags"]) == {"CV_X", "CV_Y"}
    assert q["awct_unresolved"] == ["CV_Z"]  # 変数の中身は実行時に変わり得るため ○ と混同しない


def test_paused_awct_ignored():
    tags = [
        _tag("awct", "CV_A", {"conversionId": "1", "conversionLabel": "x"}),
        _tag("awct", "CV_B", {"conversionId": "1", "conversionLabel": "x"}, paused=True),
    ]
    q = health._analyze_tag_quality(tags, [])
    assert q["awct_dup_groups"] == []


def test_html_ua_remnants():
    tags = [
        _tag("html", "旧GA計測", {"html": "<script>ga('create','UA-1234567-1');</script>"}),
        _tag("html", "チャットツール", {"html": "<script src='https://example.com/chat.js'></script>"}),
    ]
    q = health._analyze_tag_quality(tags, [])
    assert q["html_ua_tags"] == ["旧GA計測"]


def test_container_alert_codes():
    live = {"tag": [
        _tag("awct", "CV_A", {"conversionId": "1", "conversionLabel": "x"}),
        _tag("awct", "CV_B", {"conversionId": "1", "conversionLabel": "x"}),
        _tag("html", "旧GA", {"html": "_gaq.push(['_trackPageview'])"}),
    ], "trigger": [], "variable": []}
    c = {"version_id": "5", "tag_count": 3, "ga4_measurement_ids": ["G-XXXX"],
         "usage_context": ["web"]}
    codes = [a["code"] for a in health.detect_container_alerts(c, live)]
    assert "gtm.dup_ad_labels" in codes
    assert "gtm.ua_in_html" in codes
