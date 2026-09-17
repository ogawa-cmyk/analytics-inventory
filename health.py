"""Property health scoring + alert detection.

Score is 0-100 based on weighted signals. Alerts are issues that demand attention.
スコアリング基準は固定（全ユーザー共通の比較可能性を保つ）。
アラート判定の一部閾値は thresholds.py でカスタマイズ可能。
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional

import thresholds as _th


def score_property(p: dict) -> dict:
    """Return {score:int, breakdown:dict, grade:str}. p is a property summary dict."""
    pts = 0
    breakdown = {}

    if p.get("is_tracked"):
        pts += 30; breakdown["計測中"] = 30
    elif p.get("data_api_ok") is False:
        breakdown["計測"] = 0
    else:
        breakdown["未計測"] = 0

    ke = p.get("key_event_count") or 0
    if ke >= 3:
        pts += 20; breakdown[f"キーイベント({ke})"] = 20
    elif ke >= 1:
        pts += 12; breakdown[f"キーイベント({ke})"] = 12
    else:
        breakdown["KE未設定"] = 0

    if p.get("is_ecommerce"):
        pts += 10; breakdown["eコマース計測"] = 10

    cd = p.get("custom_dimension_count") or 0
    if 1 <= cd <= 30:
        pts += 15; breakdown[f"CD設定({cd})"] = 15
    elif 30 < cd <= 50:
        pts += 10; breakdown[f"CD多め({cd})"] = 10
    elif cd > 50:
        pts += 5; breakdown[f"CD過多({cd})"] = 5
    else:
        breakdown["CD未設定"] = 0

    if (p.get("stream_count") or 0) >= 1:
        pts += 10; breakdown["ストリーム有"] = 10

    sessions = p.get("sessions_7d") or 0
    if sessions >= 10000:
        pts += 15; breakdown[f"sessions7d {sessions:,}"] = 15
    elif sessions >= 1000:
        pts += 12; breakdown[f"sessions7d {sessions:,}"] = 12
    elif sessions >= 100:
        pts += 8; breakdown[f"sessions7d {sessions:,}"] = 8
    elif sessions > 0:
        pts += 4; breakdown[f"sessions7d {sessions:,}"] = 4

    pts = min(100, pts)
    if pts >= 80:
        grade = "A"
    elif pts >= 60:
        grade = "B"
    elif pts >= 40:
        grade = "C"
    elif pts >= 20:
        grade = "D"
    else:
        grade = "F"
    return {"score": pts, "grade": grade, "breakdown": breakdown}


def detect_alerts(p: dict) -> list[dict]:
    """Return list of alerts {level: 'warn'|'error'|'info', code: str, message: str} for a property.

    code は指摘の種類を表す安定した識別子（件数など実行のたびに変わる値は含めない）。
    message は表示用で、件数を含むため同一性の判定には使わないこと。
    """
    out = []
    if p.get("data_api_ok") is False:
        out.append({"level": "warn", "code": "ga4.api_err",
                    "message": "Data APIエラー: " + (p.get("data_api_error") or "")[:80]})
    elif p.get("is_tracked") is False:
        out.append({"level": "error", "code": "ga4.untracked",
                    "message": "直近7日間データなし（計測停止の可能性）"})

    if (p.get("key_event_count") or 0) == 0:
        out.append({"level": "warn", "code": "ga4.no_ke", "message": "キーイベント未設定"})

    if (p.get("stream_count") or 0) == 0:
        out.append({"level": "error", "code": "ga4.no_streams", "message": "データストリーム未設定"})

    cd = p.get("custom_dimension_count") or 0
    if cd > _th.get()["cd_warn"]:
        out.append({"level": "warn", "code": "ga4.cd_overflow",
                    "message": f"カスタムディメンションが{cd}件と過多"})

    if not p.get("my_roles"):
        out.append({"level": "info", "code": "ga4.roles_unknown",
                    "message": "権限不明（accessBindings取得失敗）"})

    # データ品質チェック（quality.py。収集時に計算され summary に件数が入る）。
    # 「未実施」「実行失敗」は黙って0件扱いにせず、その事実を info で残す。
    if "quality" not in p:
        out.append({"level": "info", "code": "ga4.quality_pending",
                    "message": "データ品質チェック未実施（次回のデータ再収集で実行されます）"})
    elif p.get("quality") is None:
        out.append({"level": "info", "code": "ga4.quality_failed",
                    "message": "データ品質チェックが実行できなかった（indexer.log を確認）"})
    else:
        q = p["quality"]
        if q.get("error"):
            out.append({"level": "error", "code": "ga4.quality_error",
                        "message": f"データ品質の重大な検出が{q['error']}件（詳細ページの品質チェック参照）"})
        if q.get("warn"):
            out.append({"level": "warn", "code": "ga4.quality_warn",
                        "message": f"データ品質の要確認が{q['warn']}件（詳細ページの品質チェック参照）"})
        if q.get("unverified") and p.get("is_tracked"):
            out.append({"level": "info", "code": "ga4.quality_unverified",
                        "message": f"品質チェック{q['unverified']}項目が未確認（データ未取得）"})

    return out


def alert_count_summary(properties: list[dict]) -> dict:
    """Aggregate alert counts across all properties (監視除外 ann_excluded はスキップ)."""
    error_props = 0
    warn_props = 0
    issues = {"untracked": 0, "no_streams": 0, "no_ke": 0, "cd_overflow": 0, "api_err": 0,
              "quality": 0}
    cd_warn = _th.get()["cd_warn"]
    for p in properties:
        if p.get("ann_excluded"):
            continue
        alerts = detect_alerts(p)
        levels = [a["level"] for a in alerts]
        if "error" in levels:
            error_props += 1
        elif "warn" in levels:
            warn_props += 1
        if p.get("is_tracked") is False and p.get("data_api_ok") is not False:
            issues["untracked"] += 1
        if (p.get("stream_count") or 0) == 0:
            issues["no_streams"] += 1
        if (p.get("key_event_count") or 0) == 0:
            issues["no_ke"] += 1
        if (p.get("custom_dimension_count") or 0) > cd_warn:
            issues["cd_overflow"] += 1
        if p.get("data_api_ok") is False:
            issues["api_err"] += 1
        q = p.get("quality") or {}
        if (q.get("error") or 0) + (q.get("warn") or 0) > 0:
            issues["quality"] += 1
    return {"error_props": error_props, "warn_props": warn_props, "issues": issues}


def enrich_properties(properties: list[dict]) -> list[dict]:
    """Add health_score / health_grade / alert_count to each property in-place."""
    for p in properties:
        s = score_property(p)
        p["health_score"] = s["score"]
        p["health_grade"] = s["grade"]
        alerts = detect_alerts(p)
        p["alert_count"] = len(alerts)
        p["has_error_alert"] = any(a["level"] == "error" for a in alerts)
    return properties


# ============================================================
#  GTM container scoring
# ============================================================

# Legacy / deprecated tag types (Universal Analytics etc.)
LEGACY_TAG_TYPES = {
    "ua",       # Universal Analytics
    "uaa",      # legacy UA variant
    "ytm",      # Yahoo! Tag Manager (deprecated)
    "ytrl",     # Yandex remarketing (rarely current)
    "flc",      # Floodlight (legacy form often)
    "fls",      # Floodlight sales (legacy)
}
# GA4 tag types
GA4_TAG_TYPES = {"gaawc", "gaawe"}

# カスタムHTML内の旧GA（Universal Analytics 世代）参照。
# タグの type だけでは拾えない「HTML直書きのUA送信」を検出する。
# "ga.js" のような短い部分一致は誤検出するため、具体的な形に限定する
_UA_IN_HTML_RE = re.compile(
    r"UA-\d{4,}-\d+"                                  # UA測定ID
    r"|google-analytics\.com/(?:analytics|ga)\.js"    # 旧ライブラリの読み込み
    r"|\b_gaq\b"                                      # ga.js世代のグローバル
    r"|ga\(\s*['\"]create['\"]"                       # analytics.js世代の初期化
)
_VAR_REF_RE = re.compile(r"^\{\{(.+)\}\}$")


def _resolve_constant(value, variables_by_name: dict) -> str | None:
    """タグのパラメータ値を定数まで解決する。

    `{{変数名}}` 参照は、その変数が定数（type "c"）のときだけ値を返す。
    ルックアップテーブル等の実行時に変わる変数は「同じ」と決め打ちできないため
    None を返し、呼び出し側は「判定不能」として ○ と混同しない。
    """
    v = str(value or "")
    m = _VAR_REF_RE.fullmatch(v)
    if not m:
        return v or None
    var = variables_by_name.get(m.group(1))
    if not var or (var.get("type") or "").lower() != "c":
        return None
    for p in var.get("parameter", []) or []:
        if p.get("key") == "value":
            val = str(p.get("value") or "")
            return None if _VAR_REF_RE.search(val) else (val or None)
    return None


def _analyze_tag_quality(tags: list, variables: list) -> dict:
    """タグ品質の追加検出（広告CVラベル重複・カスタムHTML内の旧GA参照）。

    判定ロジックは super-access-analytics（MIT License, TigerMonday Inc.）の
    計測チェック実装を GTM API の live version 形式へ移植したもの。
    """
    variables_by_name = {v.get("name"): v for v in variables if v.get("name")}
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    unresolved: list[str] = []
    html_ua: list[str] = []
    for t in tags:
        ttype = (t.get("type") or "").lower()
        if t.get("paused"):
            continue
        params = {p.get("key"): p.get("value") for p in t.get("parameter", []) or []}
        if ttype == "awct":
            raw_id = params.get("conversionId")
            raw_label = params.get("conversionLabel")
            if not raw_id or not raw_label:
                continue  # 片方欠けは組み合わせが作れない（ここでは重複だけを見る）
            cid = _resolve_constant(raw_id, variables_by_name)
            label = _resolve_constant(raw_label, variables_by_name)
            if cid is None or label is None:
                unresolved.append(t.get("name", ""))
                continue
            groups[(cid, label)].append(t.get("name", ""))
        elif ttype == "html":
            if _UA_IN_HTML_RE.search(str(params.get("html") or "")):
                html_ua.append(t.get("name", ""))
    dup_groups = [
        {"conversion_id": cid, "label": label, "tags": sorted(names)}
        for (cid, label), names in groups.items() if len(names) >= 2
    ]
    # APIの返却順に依存しないよう固定してから返す（並びが変わると同じ検出が別物に見える）
    dup_groups.sort(key=lambda g: (g["conversion_id"], g["label"]))
    return {
        "awct_dup_groups": dup_groups,
        "awct_unresolved": sorted(set(unresolved)),
        "html_ua_tags": sorted(set(html_ua)),
    }


def _summarize_live(live: dict | None) -> dict:
    """Aggregate counts from live version JSON for scoring."""
    if not live:
        return {
            "has_live": False, "tag_total": 0, "tag_paused": 0,
            "ua_count": 0, "html_count": 0, "ga4_config_count": 0, "ga4_event_count": 0,
            "trigger_total": 0, "variable_total": 0,
            "type_counter": {},
            "awct_dup_groups": [], "awct_unresolved": [], "html_ua_tags": [],
        }
    tags = live.get("tag") or []
    triggers = live.get("trigger") or []
    variables = live.get("variable") or []
    type_counter: dict = {}
    paused = 0
    ua = 0
    html = 0
    ga4_config = 0
    ga4_event = 0
    for t in tags:
        ttype = (t.get("type") or "").lower()
        type_counter[ttype] = type_counter.get(ttype, 0) + 1
        if t.get("paused"):
            paused += 1
        if ttype in LEGACY_TAG_TYPES:
            ua += 1
        if ttype == "html":
            html += 1
        if ttype == "gaawc":
            ga4_config += 1
        elif ttype == "gaawe":
            ga4_event += 1
    return {
        "has_live": True,
        "tag_total": len(tags),
        "tag_paused": paused,
        "ua_count": ua,
        "html_count": html,
        "ga4_config_count": ga4_config,
        "ga4_event_count": ga4_event,
        "trigger_total": len(triggers),
        "variable_total": len(variables),
        "type_counter": type_counter,
        **_analyze_tag_quality(tags, variables),
    }


def score_container(c: dict, live: dict | None = None) -> dict:
    """Return {score:int, breakdown:dict, grade:str, summary:dict}.

    Uses the live version JSON when provided for fine-grained metrics
    (UA tag count, paused ratio, GA4 config tag presence)."""
    pts = 0
    breakdown: dict = {}
    summary = _summarize_live(live)

    # 1) 公開バージョンあり: 15
    if c.get("version_id") or summary.get("has_live"):
        pts += 15; breakdown["公開バージョンあり"] = 15
    else:
        breakdown["公開バージョンなし"] = 0

    # 2) 稼働中（タグあり）: 15
    tag_total = c.get("tag_count") or summary.get("tag_total") or 0
    if tag_total > 0:
        pts += 15; breakdown[f"タグあり({tag_total})"] = 15
    else:
        breakdown["タグなし"] = 0

    # 3) GA4連携: 15
    mids = c.get("ga4_measurement_ids") or []
    if mids:
        pts += 15; breakdown[f"GA4連携({len(mids)})"] = 15
    else:
        breakdown["GA4連携なし"] = 0

    # 4) GA4 Configurationタグ存在: 10  (only checkable with live data)
    if summary["has_live"]:
        if summary["ga4_config_count"] >= 1:
            pts += 10; breakdown[f"GA4 Configタグあり({summary['ga4_config_count']})"] = 10
        else:
            breakdown["GA4 Configタグなし"] = 0
    else:
        # Without detail data, give partial credit if MID is set
        if mids:
            pts += 5; breakdown["GA4連携あり(詳細未取得)"] = 5

    # 5) モダンタグ（UA系の少なさ）: 15
    if summary["has_live"]:
        ua = summary["ua_count"]
        if ua == 0:
            pts += 15; breakdown["UA系なし"] = 15
        elif ua <= 2:
            pts += 10; breakdown[f"UA系少({ua}件)"] = 10
        elif ua <= 5:
            pts += 5; breakdown[f"UA系あり({ua}件)"] = 5
        else:
            breakdown[f"UA系過多({ua}件)"] = 0
    else:
        pts += 10; breakdown["UA系: 詳細未取得"] = 10

    # 6) 整理度（paused率の低さ）: 10
    if summary["has_live"] and summary["tag_total"] > 0:
        ratio = summary["tag_paused"] / summary["tag_total"]
        if ratio < 0.05:
            pts += 10; breakdown[f"paused少({summary['tag_paused']}/{summary['tag_total']})"] = 10
        elif ratio < 0.15:
            pts += 7; breakdown[f"paused中({summary['tag_paused']}/{summary['tag_total']})"] = 7
        elif ratio < 0.30:
            pts += 3; breakdown[f"paused多({summary['tag_paused']}/{summary['tag_total']})"] = 3
        else:
            breakdown[f"paused過多({summary['tag_paused']}/{summary['tag_total']})"] = 0
    else:
        pts += 5; breakdown["paused: 詳細未取得"] = 5

    # 7) 規模適正: 10
    if 1 <= tag_total <= 200:
        pts += 10; breakdown[f"規模適正({tag_total}tags)"] = 10
    elif 200 < tag_total <= 400:
        pts += 5; breakdown[f"規模大({tag_total}tags)"] = 5
    elif tag_total > 400:
        pts += 2; breakdown[f"規模過大({tag_total}tags)"] = 2
    else:
        breakdown["タグ0"] = 0

    # 8) トリガー設定: 5
    if (c.get("trigger_count") or summary.get("trigger_total") or 0) > 0:
        pts += 5; breakdown["トリガーあり"] = 5

    # 9) 変数設定: 5
    if (c.get("variable_count") or summary.get("variable_total") or 0) > 0:
        pts += 5; breakdown["変数あり"] = 5

    pts = min(100, pts)
    if pts >= 80:
        grade = "A"
    elif pts >= 60:
        grade = "B"
    elif pts >= 40:
        grade = "C"
    elif pts >= 20:
        grade = "D"
    else:
        grade = "F"
    return {"score": pts, "grade": grade, "breakdown": breakdown, "summary": summary}


def detect_container_alerts(c: dict, live: dict | None = None) -> list[dict]:
    out = []
    summary = _summarize_live(live)
    if not (c.get("version_id") or summary.get("has_live")):
        out.append({"level": "error", "code": "gtm.no_version",
                    "message": "公開バージョンが未取得または未公開"})
    if (c.get("tag_count") or summary.get("tag_total") or 0) == 0:
        out.append({"level": "error", "code": "gtm.no_tags",
                    "message": "タグが0件（稼働していない）"})
    if not (c.get("ga4_measurement_ids") or []):
        out.append({"level": "warn", "code": "gtm.no_ga4",
                    "message": "GA4 Measurement IDが紐づいていない"})
    if summary["has_live"]:
        if summary["ga4_config_count"] == 0 and (c.get("ga4_measurement_ids") or []):
            out.append({"level": "warn", "code": "gtm.no_config_tag",
                        "message": "GA4設定タグ(gaawc)が存在しない"})
        if summary["ua_count"] >= _th.get()["ua_warn"]:
            out.append({"level": "warn", "code": "gtm.ua_left",
                        "message": f"レガシーUA系タグが{summary['ua_count']}件残存"})
        if summary["tag_total"] > 0:
            ratio = summary["tag_paused"] / summary["tag_total"]
            if ratio >= 0.30:
                out.append({"level": "warn", "code": "gtm.paused_many",
                            "message": f"pausedタグが{summary['tag_paused']}件（{round(ratio*100)}%）と多い"})
        if summary["tag_total"] > 400:
            out.append({"level": "warn", "code": "gtm.too_many_tags",
                        "message": f"タグが{summary['tag_total']}件と過大"})
        # 広告CVラベルの重複 = 1回の成果が広告側で多重カウントされ、入札の自動調整が
        # 実際より多い成果数を前提に動く
        for g in summary["awct_dup_groups"]:
            out.append({"level": "warn", "code": "gtm.dup_ad_labels",
                        "message": f"広告CVタグ{len(g['tags'])}本（{'、'.join(g['tags'][:3])}）が"
                                   f"同一のコンバージョンID・ラベルで発火（多重計上）"})
        if summary["awct_unresolved"]:
            names = "、".join(summary["awct_unresolved"][:3])
            out.append({"level": "info", "code": "gtm.ad_label_unresolved",
                        "message": f"広告CVタグ{len(summary['awct_unresolved'])}本のID・ラベルが"
                                   f"変数参照で判定不能（{names}）。重複していないか目視確認"})
        if summary["html_ua_tags"]:
            names = "、".join(summary["html_ua_tags"][:3])
            out.append({"level": "warn", "code": "gtm.ua_in_html",
                        "message": f"カスタムHTML内に旧GA（UA/analytics.js）への参照が"
                                   f"{len(summary['html_ua_tags'])}本（{names}）。UAは計測停止済み"})
    if not (c.get("usage_context") or []):
        out.append({"level": "info", "code": "gtm.no_usage_context",
                    "message": "用途(usage_context)が未設定"})
    return out


def container_alert_summary(containers: list[dict]) -> dict:
    error_c = 0
    warn_c = 0
    issues = {"no_tags": 0, "no_ga4": 0, "ua_left": 0, "no_version": 0,
              "dup_ad_labels": 0, "ua_in_html": 0}
    ua_warn = _th.get()["ua_warn"]
    for c in containers:
        if c.get("ann_excluded"):
            continue
        levels = [a["level"] for a in c.get("_alerts", [])]
        if "error" in levels:
            error_c += 1
        elif "warn" in levels:
            warn_c += 1
        if (c.get("tag_count") or 0) == 0:
            issues["no_tags"] += 1
        if not (c.get("ga4_measurement_ids") or []):
            issues["no_ga4"] += 1
        ss = c.get("_score_summary", {})
        if ss.get("ua_count", 0) >= ua_warn:
            issues["ua_left"] += 1
        if not c.get("version_id"):
            issues["no_version"] += 1
        if ss.get("awct_dup_groups"):
            issues["dup_ad_labels"] += 1
        if ss.get("html_ua_tags"):
            issues["ua_in_html"] += 1
    return {"error_count": error_c, "warn_count": warn_c, "issues": issues}


# ============================================================
#  Search Console site scoring
# ============================================================

def score_sc_site(s: dict, has_ga4_link: bool = False) -> dict:
    """Score a Search Console site summary out of 100."""
    pts = 0
    breakdown: dict = {}

    if (s.get("sitemap_count") or 0) > 0:
        pts += 15; breakdown[f"sitemap登録({s['sitemap_count']})"] = 15
    else:
        breakdown["sitemap未登録"] = 0

    clicks = s.get("clicks_28d") or 0
    if clicks >= 1000:
        pts += 15; breakdown[f"流入豊富({clicks:,})"] = 15
    elif clicks >= 100:
        pts += 12; breakdown[f"流入あり({clicks:,})"] = 12
    elif clicks > 0:
        pts += 6; breakdown[f"流入少({clicks})"] = 6
    else:
        breakdown["流入0"] = 0

    imps = s.get("impressions_28d") or 0
    if imps >= 10000:
        pts += 15; breakdown[f"インプ豊富({imps:,})"] = 15
    elif imps >= 1000:
        pts += 12; breakdown[f"インプあり({imps:,})"] = 12
    elif imps >= 100:
        pts += 6; breakdown[f"インプ少({imps})"] = 6
    else:
        breakdown["インプ100未満"] = 0

    ctr = (s.get("ctr_28d") or 0.0) * 100
    if ctr >= 3.0:
        pts += 10; breakdown[f"CTR健全({ctr:.1f}%)"] = 10
    elif ctr >= 1.0:
        pts += 6; breakdown[f"CTR標準({ctr:.1f}%)"] = 6
    elif ctr > 0:
        pts += 2; breakdown[f"CTR低({ctr:.2f}%)"] = 2
    else:
        breakdown["CTR=0"] = 0

    pos = s.get("position_28d") or 999
    if 0 < pos <= 10:
        pts += 10; breakdown[f"順位優秀({pos:.1f})"] = 10
    elif pos <= 20:
        pts += 7; breakdown[f"順位良({pos:.1f})"] = 7
    elif pos <= 30:
        pts += 4; breakdown[f"順位中({pos:.1f})"] = 4
    elif pos < 999:
        pts += 1; breakdown[f"順位低({pos:.1f})"] = 1

    sitemap_err = s.get("sitemap_errors") or 0
    if (s.get("sitemap_count") or 0) > 0 and sitemap_err == 0:
        pts += 5; breakdown["sitemap健全"] = 5
    elif sitemap_err > 0:
        breakdown[f"sitemapエラー{sitemap_err}件"] = 0

    qcount = s.get("top_query_count") or 0
    if qcount >= 20:
        pts += 10; breakdown[f"クエリ多様({qcount})"] = 10
    elif qcount >= 5:
        pts += 6; breakdown[f"クエリ少({qcount})"] = 6
    elif qcount > 0:
        pts += 2; breakdown[f"クエリ極少({qcount})"] = 2

    pcount = s.get("top_page_count") or 0
    if pcount >= 20:
        pts += 10; breakdown[f"ページ多様({pcount})"] = 10
    elif pcount >= 5:
        pts += 6; breakdown[f"ページ少({pcount})"] = 6
    elif pcount > 0:
        pts += 2; breakdown[f"ページ極少({pcount})"] = 2

    if has_ga4_link:
        pts += 10; breakdown["GA4連携あり"] = 10
    else:
        breakdown["GA4連携なし"] = 0

    pts = min(100, pts)
    if pts >= 80:
        grade = "A"
    elif pts >= 60:
        grade = "B"
    elif pts >= 40:
        grade = "C"
    elif pts >= 20:
        grade = "D"
    else:
        grade = "F"
    return {"score": pts, "grade": grade, "breakdown": breakdown}


def detect_sc_alerts(s: dict, has_ga4_link: bool = False) -> list[dict]:
    out = []
    if not s.get("perf_ok"):
        out.append({"level": "warn", "code": "sc.api_err",
                    "message": "Search Analytics取得エラー: " + (s.get("perf_error") or "")[:80]})
    if (s.get("sitemap_count") or 0) == 0:
        out.append({"level": "warn", "code": "sc.no_sitemap", "message": "sitemapが未登録"})
    if (s.get("sitemap_errors") or 0) > 0:
        out.append({"level": "warn", "code": "sc.sitemap_errors",
                    "message": f"sitemapエラー {s['sitemap_errors']}件"})
    if (s.get("clicks_28d") or 0) == 0 and (s.get("impressions_28d") or 0) == 0:
        out.append({"level": "error", "code": "sc.no_traffic",
                    "message": "直近28日で流入・インプレッション共に0"})
    elif (s.get("clicks_28d") or 0) == 0 and (s.get("impressions_28d") or 0) > 100:
        out.append({"level": "warn", "code": "sc.no_clicks",
                    "message": f"Imp {s['impressions_28d']:,}あるがClickが0（CTR=0）"})
    if (s.get("ctr_28d") or 0) > 0 and (s.get("ctr_28d") or 0) < 0.005 and (s.get("impressions_28d") or 0) > 1000:
        out.append({"level": "warn", "code": "sc.low_ctr",
                    "message": "CTRが0.5%未満（メタ・タイトル要改善）"})
    if (s.get("position_28d") or 0) > 30 and (s.get("impressions_28d") or 0) > 0:
        out.append({"level": "info", "code": "sc.low_position",
                    "message": f"平均掲載順位 {s.get('position_28d', 0):.1f} 位"})
    if not has_ga4_link:
        out.append({"level": "info", "code": "sc.no_ga4_link",
                    "message": "GA4プロパティとの自動紐付けなし"})
    return out


def enrich_sc_sites(sites: list[dict], ga4_domains: set) -> list[dict]:
    """Score SC sites; ga4_domains is a set of all GA4 property domains for linkage detection."""
    for s in sites:
        domain = (s.get("domain") or "").lower()
        has_ga4 = any(d.lower() == domain or domain.endswith("." + d.lower()) or d.lower().endswith("." + domain)
                      for d in ga4_domains if d)
        s["has_ga4_link"] = has_ga4
        score = score_sc_site(s, has_ga4_link=has_ga4)
        s["health_score"] = score["score"]
        s["health_grade"] = score["grade"]
        alerts = detect_sc_alerts(s, has_ga4_link=has_ga4)
        s["_alerts"] = alerts
        s["alert_count"] = len(alerts)
        s["has_error_alert"] = any(a["level"] == "error" for a in alerts)
    return sites


def sc_alert_summary(sites: list[dict]) -> dict:
    sites = [s for s in sites if not s.get("ann_excluded")]
    err = sum(1 for s in sites if s.get("has_error_alert"))
    warn = sum(1 for s in sites if not s.get("has_error_alert") and any(a["level"] == "warn" for a in (s.get("_alerts") or [])))
    issues = {
        "no_clicks": sum(1 for s in sites if (s.get("clicks_28d") or 0) == 0),
        "no_sitemap": sum(1 for s in sites if (s.get("sitemap_count") or 0) == 0),
        "low_ctr": sum(1 for s in sites if (s.get("ctr_28d") or 0) > 0 and (s.get("ctr_28d") or 0) < 0.005 and (s.get("impressions_28d") or 0) > 1000),
        "no_ga4_link": sum(1 for s in sites if not s.get("has_ga4_link")),
    }
    return {"error_count": err, "warn_count": warn, "issues": issues}


def enrich_containers_with_score(containers: list[dict], load_live_fn) -> list[dict]:
    """Add health_score / health_grade / alert_count / _alerts / _score_summary.

    load_live_fn(container_id) -> live dict or None.
    """
    for c in containers:
        cid = c.get("container_id")
        live = None
        try:
            live = load_live_fn(cid)
        except Exception:
            live = None
        s = score_container(c, live)
        c["health_score"] = s["score"]
        c["health_grade"] = s["grade"]
        c["_score_summary"] = s["summary"]
        alerts = detect_container_alerts(c, live)
        c["_alerts"] = alerts
        c["alert_count"] = len(alerts)
        c["has_error_alert"] = any(a["level"] == "error" for a in alerts)
    return containers
