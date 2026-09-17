"""collectors/site_scan.py のユニットテスト（公開gtm.jsのパースと突き合わせ）。

実行: リポジトリルートで `python -m pytest`
"""
import json

from collectors import site_scan


def _gtm_js(resource: dict) -> str:
    return "var data = {\"resource\":" + json.dumps(resource) + ",\"runtime\":[]};"


RESOURCE = {
    "version": "12",
    "macros": [
        {"function": "__e"},                                   # macro[0] = Event
        {"function": "__c", "vtp_value": "G-PUBLIC01"},        # macro[1] = 定数
    ],
    "predicates": [
        {"function": "_eq", "arg0": ["macro", 0], "arg1": "gtm.js"},      # 0
        {"function": "_eq", "arg0": ["macro", 0], "arg1": "cv_submit"},   # 1
        {"function": "_re", "arg0": ["macro", 0], "arg1": ".*"},          # 2 全イベント
    ],
    "tags": [
        {"function": "__googtag", "vtp_tagId": ["macro", 1]},             # 0
        {"function": "__gaawe", "vtp_eventName": "cv_submit",
         "vtp_measurementIdOverride": ["macro", 1]},                       # 1
        {"function": "__gaawe", "vtp_eventName": "cv_submit_dup",
         "vtp_measurementIdOverride": ["macro", 1]},                       # 2 同じイベントで発火
        {"function": "__ua"},                                              # 3
    ],
    "rules": [
        [["if", 0], ["add", 0]],
        [["if", 1], ["add", 1, 2]],   # cv_submit で2本の計測タグが発火（多重計上候補）
    ],
}


def test_parse_and_normalize():
    resource = site_scan.parse_resource(_gtm_js(RESOURCE))
    data = site_scan.normalize(resource, "GTM-TEST123")
    assert data["tag_total"] == 4
    assert data["ga4_destinations"] == ["G-PUBLIC01"]  # 定数変数を解決して送信先を復元
    assert data["ua_tag_count"] == 1
    # 同一 dataLayer イベントで複数の計測タグが発火する箇所を検出
    assert data["duplicate_fire_events"] == {"cv_submit": 2}


def test_parse_resource_rejects_garbage():
    try:
        site_scan.parse_resource("<html>not gtm.js</html>")
        assert False, "resource が無いのに例外にならなかった"
    except ValueError:
        pass


def test_discover_ids():
    html = ("<script src='https://www.googletagmanager.com/gtm.js?id=GTM-ABC1234'></script>"
            "<script>gtag('config','G-ZZZ99999');ga('create','UA-1234567-1');</script>")
    found = site_scan.discover_ids(html)
    assert found["gtm_containers"] == ["GTM-ABC1234"]
    assert found["ga4_measurement_ids"] == ["G-ZZZ99999"]
    assert found["universal_analytics_ids"] == ["UA-1234567-1"]


def test_analyze_flags_unexpected():
    scan = {
        "url": "https://example.com/",
        "ga4_measurement_ids": ["G-ZZZ99999"],
        "gtm_containers": ["GTM-ABC1234", "GTM-UNKNOWN9"],
        "universal_analytics_ids": ["UA-1234567-1"],
        "google_ads_ids": [],
        "containers": [{"ga4_destinations": ["G-PUBLIC01"]}],
    }
    a = site_scan.analyze(scan, known_mids=["G-PUBLIC01", "G-MISSING1"],
                          known_gtm_public_ids=["GTM-ABC1234"])
    assert a["expected_ga4_found"] == ["G-PUBLIC01"]
    assert a["expected_ga4_missing"] == ["G-MISSING1"]   # 登録済みなのに配信を確認できない
    assert a["unexpected_ga4"] == ["G-ZZZ99999"]          # 登録の無いMIDが配信されている
    assert a["unknown_gtm_containers"] == ["GTM-UNKNOWN9"]
    assert a["ua_ids_found"] == ["UA-1234567-1"]
