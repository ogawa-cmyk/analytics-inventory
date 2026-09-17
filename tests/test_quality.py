"""quality.py のユニットテスト。

一番大事なのは test_missing_data_never_ok —
「データが取れていないとき、どのチェックも ok（問題なし）を返さない」ことを固定する。
この保証が崩れると、収集失敗が「健全」に化ける。

実行: リポジトリルートで `python -m pytest`
"""
import quality


def _detail(collected=None, **kwargs):
    d = {"summary": {"collected": collected or {}}}
    d.update(kwargs)
    return d


ALL_COLLECTED = {k: True for k in quality.DATASET_LABELS}


# ──────────────────────────────────────
# 欠損時の保証
# ──────────────────────────────────────

def test_missing_data_never_ok():
    """何も収集できていないとき、全チェックが「未確認」になる（okは1つも無い）。"""
    result = quality.run_property_checks(_detail(collected={}))
    assert len(result["checks"]) == len(quality.CHECKS)
    for c in result["checks"]:
        assert c["judgement"] == "unverified", f"{c['code']} が欠損時に {c['judgement']} を返した"
        assert "取れていない" in c["state"]
    assert result["findings"] == []
    assert result["unverified"] == len(quality.CHECKS)


def test_every_check_declares_required_datasets():
    """必要データセット未宣言のチェックを作らせない（欠損ガードが素通りになるため）。"""
    for code, _label, required, _fn in quality.CHECKS:
        assert required, f"{code} が必要データセットを宣言していない"
        for k in required:
            assert k in quality.DATASET_LABELS, f"{code} の {k} に日本語ラベルが無い"


def test_check_exception_becomes_unverified(monkeypatch):
    """判定関数の例外は未確認として残り、表全体を落とさない。"""
    def boom(detail):
        raise ValueError("boom")
    monkeypatch.setattr(quality, "CHECKS",
                        (("boom_check", "例外を起こすチェック", ("events",), boom),))
    result = quality.run_property_checks(_detail(collected=ALL_COLLECTED, events=[]))
    assert result["checks"][0]["judgement"] == "unverified"
    assert "判定できません" in result["checks"][0]["state"]
    assert result["unverified"] == 1


# ──────────────────────────────────────
# 個別チェック
# ──────────────────────────────────────

def test_session_health_low_ratio():
    d = _detail(
        events=[{"event_name": "session_start", "event_count": 400, "total_users": 300}],
        totals_30d={"ok": True, "sessions": 1000},
    )
    j, s, findings = quality.check_session_health(d)
    assert j == "ng"
    assert findings[0]["severity"] == "critical"  # 0.4 < 0.7


def test_session_health_ok():
    d = _detail(
        events=[{"event_name": "session_start", "event_count": 950, "total_users": 800}],
        totals_30d={"ok": True, "sessions": 1000},
    )
    j, s, findings = quality.check_session_health(d)
    assert j == "ok" and not findings


def test_user_id_collapse():
    d = _detail(events=[{"event_name": "page_view", "event_count": 5000, "total_users": 2}])
    j, s, findings = quality.check_user_id_collapse(d)
    assert j == "ng"
    assert findings[0]["severity"] == "critical"


def test_event_names_japanese_and_reserved():
    d = _detail(events=[
        {"event_name": "お問い合わせ完了", "event_count": 10},
        {"event_name": "firebase_test", "event_count": 10},
        {"event_name": "Detail", "event_count": 10},
        {"event_name": "form_submit", "event_count": 10},
    ])
    j, s, findings = quality.check_event_names(d)
    severities = sorted(f["severity"] for f in findings)
    assert severities == ["high", "low", "medium"]
    assert j == "ng"


def test_gtm_internal_events():
    d = _detail(events=[{"event_name": "gtm.click", "event_count": 500}])
    j, s, findings = quality.check_gtm_internal_events(d)
    assert j == "warn" and findings


def test_micro_events_enough_and_missing():
    enough = _detail(events=[{"event_name": n, "event_count": 1} for n in
                             ("view_item", "add_to_cart", "begin_checkout", "form_start")])
    assert quality.check_micro_events(enough)[0] == "ok"
    missing = _detail(events=[{"event_name": "purchase", "event_count": 1}])
    j, s, findings = quality.check_micro_events(missing)
    assert j == "warn"
    assert "打ち手" in findings[0]["message"]  # 件数だけでなく理由を必ず入れる


def test_tel_event_requires_click_word():
    """「電話」だけでは拾わない（テレビ電話相談予約の誤検出事故の再発防止）。"""
    d = _detail(
        events=[{"event_name": "テレビ電話相談予約", "event_count": 5}],
        key_events=[],
    )
    assert quality.check_tel_key_event(d)[0] == "ok"
    d2 = _detail(
        events=[{"event_name": "電話番号クリック", "event_count": 5}],
        key_events=[],
    )
    j, s, findings = quality.check_tel_key_event(d2)
    assert j == "warn" and findings


def test_error_pages_detected():
    rows = [{"page_path": f"/old-{i}/", "page_title": "ページが見つかりませんでした | サイト",
             "views": 40, "sessions": 35} for i in range(3)]
    d = _detail(pages={"ok": True, "rows": rows})
    j, s, findings = quality.check_error_pages(d)
    assert j == "warn"
    assert findings[0]["category"] == "リンク切れ"


def test_error_pages_below_threshold():
    d = _detail(pages={"ok": True, "rows": [
        {"page_path": "/x/", "page_title": "404 Not Found", "views": 10, "sessions": 8}]})
    assert quality.check_error_pages(d)[0] == "ok"


def test_duplicate_page_urls():
    d = _detail(pages={"ok": True, "rows": [
        {"page_path": "/", "page_title": "Top", "views": 2000, "sessions": 1996},
        {"page_path": "/index.html", "page_title": "Top", "views": 180, "sessions": 176},
    ]})
    j, s, findings = quality.check_duplicate_page_urls(d)
    assert findings and "/index.html" in findings[0]["message"]


def test_pii_masking_and_detection():
    token = "aB3dE5fG7hI9kL1mN3oP5qR7"  # 24文字・英数字混在
    d = _detail(pages={"ok": True, "rows": [
        {"page_path": f"/reset-password/{token}/", "page_title": "reset", "views": 30, "sessions": 30},
        {"page_path": "/thanks?email=taro%40example.com", "page_title": "thanks", "views": 5, "sessions": 5},
        {"page_path": "/verify?token=8f3a9c1b2d", "page_title": "verify", "views": 3, "sessions": 3},
    ]})
    j, s, findings = quality.check_pii_urls(d)
    assert j == "ng"
    cats = [f["title"] for f in findings]
    assert any("reset-password" in t for t in cats)
    assert any("メールアドレス" in t for t in cats)
    assert any("token=" in t for t in cats)
    # レポートは共有される文書なので、実トークン・実メールアドレスを本文に出さない
    all_text = " ".join(f["title"] + f["message"] for f in findings)
    assert token not in all_text
    assert "taro" not in all_text


def test_internal_utm():
    d = _detail(traffic={"ok": True, "rows": [
        {"source": "site", "medium": "popup", "channel_group": "Unassigned", "sessions": 300}]})
    j, s, findings = quality.check_internal_utm(d)
    assert j == "ng" and findings[0]["severity"] == "high"


def test_unassigned_threshold():
    rows = [{"source": "google", "medium": "organic", "channel_group": "Organic Search", "sessions": 9400},
            {"source": "x", "medium": "unknown", "channel_group": "Unassigned", "sessions": 600}]
    d = _detail(traffic={"ok": True, "rows": rows})
    assert quality.check_unassigned(d)[0] == "ng"
    rows[1]["sessions"] = 40  # 1%未満かつ50未満
    assert quality.check_unassigned(d)[0] == "ok"


def test_source_medium_variants_case():
    """大文字小文字だけ違う source は表記ゆれ。実流入が薄い側は問題視しない。"""
    rows = [{"source": "Facebook", "medium": "social", "channel_group": "Organic Social", "sessions": 120},
            {"source": "facebook", "medium": "social", "channel_group": "Organic Social", "sessions": 80},
            {"source": "Yahoo", "medium": "organic", "channel_group": "Organic Search", "sessions": 500},
            {"source": "yahoo", "medium": "organic", "channel_group": "Organic Search", "sessions": 3}]
    d = _detail(traffic={"ok": True, "rows": rows})
    j, s, findings = quality.check_source_medium_variants(d)
    assert j == "warn"
    msgs = " ".join(f["message"] for f in findings)
    assert "Facebook" in msgs and "facebook" in msgs
    assert "yahoo" not in msgs  # 3セッションの表記は閾値未満＝流入分裂の実害なし


def test_source_medium_variants_synonyms_and_parens():
    """medium の同義語（email/mail）は「疑い」。(direct)/(none) は対象外。"""
    rows = [{"source": "newsletter", "medium": "email", "channel_group": "Email", "sessions": 200},
            {"source": "newsletter2", "medium": "mail", "channel_group": "Unassigned", "sessions": 60},
            {"source": "(direct)", "medium": "(none)", "channel_group": "Direct", "sessions": 5000}]
    d = _detail(traffic={"ok": True, "rows": rows})
    j, s, findings = quality.check_source_medium_variants(d)
    assert j == "warn"
    assert any("同義語" in f["title"] for f in findings)
    assert all("(direct)" not in f["message"] for f in findings)


def test_source_medium_variants_clean_is_ok():
    rows = [{"source": "google", "medium": "organic", "channel_group": "Organic Search", "sessions": 900}]
    d = _detail(traffic={"ok": True, "rows": rows})
    assert quality.check_source_medium_variants(d)[0] == "ok"


def test_notset_traffic_threshold():
    rows = [{"source": "google", "medium": "organic", "channel_group": "Organic Search", "sessions": 9000},
            {"source": "(not set)", "medium": "(not set)", "channel_group": "Unassigned", "sessions": 700}]
    d = _detail(traffic={"ok": True, "rows": rows})
    j, s, findings = quality.check_notset_traffic(d)
    assert j == "warn" and "not set" in findings[0]["title"]
    rows[1]["sessions"] = 30  # 50未満
    assert quality.check_notset_traffic(d)[0] == "ok"


def test_self_referral_detected_with_lp():
    """自ホスト名が参照元 → ng。www有無の違いも同一ホスト扱い。LPが取れていれば併記。"""
    d = _detail(
        hostnames={"ok": True, "rows": [{"hostname": "www.example.com", "sessions": 1000}]},
        traffic={"ok": True, "rows": [
            {"source": "example.com", "medium": "referral", "channel_group": "Referral", "sessions": 150},
            {"source": "google", "medium": "organic", "channel_group": "Organic Search", "sessions": 800}]},
        self_referral_lps={"ok": True, "rows": [
            {"source": "example.com", "landing_page": "/payment/done", "sessions": 120}]},
    )
    assert quality.self_referral_suspects(d) == ["example.com"]
    j, s, findings = quality.check_self_referral(d)
    assert j == "ng"
    assert "/payment/done" in findings[0]["message"]
    assert "参照元除外へ追加しない" in findings[0]["fix"]


def test_self_referral_without_lp_says_unfetched():
    d = _detail(
        hostnames={"ok": True, "rows": [{"hostname": "example.com", "sessions": 1000}]},
        traffic={"ok": True, "rows": [
            {"source": "www.example.com", "medium": "referral", "channel_group": "Referral", "sessions": 60}]},
    )
    j, s, findings = quality.check_self_referral(d)
    assert j == "ng" and "未取得" in findings[0]["message"]


def test_self_referral_clean_is_ok():
    d = _detail(
        hostnames={"ok": True, "rows": [{"hostname": "example.com", "sessions": 1000}]},
        traffic={"ok": True, "rows": [
            {"source": "google", "medium": "organic", "channel_group": "Organic Search", "sessions": 800}]},
    )
    assert quality.check_self_referral(d)[0] == "ok"


def test_foreign_noise_needs_two_signals():
    rows = [
        {"country": "Japan", "sessions": 1700, "key_events": 50,
         "engagement_duration": 85000, "bounce_rate": 0.4},
        {"country": "India", "sessions": 300, "key_events": 0,
         "engagement_duration": 150, "bounce_rate": 0.5},  # 0.5秒/セッション + KE0 = 2信号
    ]
    d = _detail(countries={"ok": True, "rows": rows})
    j, s, findings = quality.check_foreign_noise(d)
    assert j == "warn"
    assert "国名だけでは除外しない" in findings[0]["fix"]


def test_duplicate_event_rules():
    conds = [{"field": "page_location", "comparison_type": "CONTAINS", "value": "/thanks", "negated": False}]
    d = _detail(event_create_rules=[{"ok": True, "rules": [
        {"destination_event": "cv_plan_a", "event_conditions": conds},
        {"destination_event": "cv_plan_b", "event_conditions": conds},
    ]}])
    j, s, findings = quality.check_duplicate_event_rules(d)
    assert j == "ng" and "多重計上" in findings[0]["message"]


def test_duplicate_event_rules_none_is_explicit():
    d = _detail(event_create_rules=[{"ok": True, "rules": []}])
    j, s, findings = quality.check_duplicate_event_rules(d)
    assert j == "ok"
    assert "対象なし" in s  # 「対象なし」と「見て問題なかった」を区別する


def test_retention():
    assert quality.check_retention(_detail(retention={"ok": True, "event_data_retention": "FOURTEEN_MONTHS"}))[0] == "ok"
    j, s, findings = quality.check_retention(_detail(retention={"ok": True, "event_data_retention": "TWO_MONTHS"}))
    assert j == "warn" and "2ヶ月" in findings[0]["message"]


def test_enhanced_measurement_all_off():
    d = _detail(enhanced_measurement=[{"ok": True, "stream_enabled": False}])
    assert quality.check_enhanced_measurement(d)[0] == "warn"
    d2 = _detail(enhanced_measurement=[{"ok": True, "stream_enabled": True, "scrolls_enabled": True}])
    assert quality.check_enhanced_measurement(d2)[0] == "ok"


# ──────────────────────────────────────
# ID の安定性
# ──────────────────────────────────────

def _pii_detail(rows):
    return _detail(collected=ALL_COLLECTED,
                   events=[], key_events=[],
                   totals_30d={"ok": True, "sessions": 0},
                   pages={"ok": True, "rows": rows},
                   traffic={"ok": True, "rows": []},
                   countries={"ok": True, "rows": []},
                   event_create_rules=[], retention={"ok": True, "event_data_retention": "FOURTEEN_MONTHS"},
                   enhanced_measurement=[])


def test_finding_ids_stable_across_row_order():
    """入力行の並びが変わっても（APIの返却順は保証されない）同じ指摘には同じIDが付く。"""
    rows = [
        {"page_path": "/a/", "page_title": "404 エラー", "views": 60, "sessions": 50},
        {"page_path": "/b/", "page_title": "404 エラー", "views": 70, "sessions": 60},
    ]
    r1 = quality.run_property_checks(_pii_detail(rows))
    r2 = quality.run_property_checks(_pii_detail(list(reversed(rows))))
    ids1 = sorted(f["id"] for f in r1["findings"])
    ids2 = sorted(f["id"] for f in r2["findings"])
    assert ids1 == ids2 and ids1


def test_counts_and_matrix_shape():
    rows = [{"page_path": "/x/", "page_title": "404", "views": 100, "sessions": 90}]
    r = quality.run_property_checks(_pii_detail(rows))
    assert r["counts"]["warn"] >= 1
    assert len(r["checks"]) == len(quality.CHECKS)
    codes = {c["code"] for c in r["checks"]}
    assert len(codes) == len(quality.CHECKS)  # code の重複なし
