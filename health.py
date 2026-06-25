"""Property health scoring + alert detection.

Score is 0-100 based on weighted signals. Alerts are issues that demand attention.
"""
from __future__ import annotations
from typing import Optional


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
    """Return list of alerts {level: 'warn'|'error'|'info', message: str} for a property."""
    out = []
    if p.get("data_api_ok") is False:
        out.append({"level": "warn", "message": "Data APIエラー: " + (p.get("data_api_error") or "")[:80]})
    elif p.get("is_tracked") is False:
        out.append({"level": "error", "message": "直近7日間データなし（計測停止の可能性）"})

    if (p.get("key_event_count") or 0) == 0:
        out.append({"level": "warn", "message": "キーイベント未設定"})

    if (p.get("stream_count") or 0) == 0:
        out.append({"level": "error", "message": "データストリーム未設定"})

    cd = p.get("custom_dimension_count") or 0
    if cd > 50:
        out.append({"level": "warn", "message": f"カスタムディメンションが{cd}件と過多"})

    if not p.get("my_roles"):
        out.append({"level": "info", "message": "権限不明（accessBindings取得失敗）"})

    return out


def alert_count_summary(properties: list[dict]) -> dict:
    """Aggregate alert counts across all properties."""
    error_props = 0
    warn_props = 0
    issues = {"untracked": 0, "no_streams": 0, "no_ke": 0, "cd_overflow": 0, "api_err": 0}
    for p in properties:
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
        if (p.get("custom_dimension_count") or 0) > 50:
            issues["cd_overflow"] += 1
        if p.get("data_api_ok") is False:
            issues["api_err"] += 1
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


def _summarize_live(live: dict | None) -> dict:
    """Aggregate counts from live version JSON for scoring."""
    if not live:
        return {
            "has_live": False, "tag_total": 0, "tag_paused": 0,
            "ua_count": 0, "html_count": 0, "ga4_config_count": 0, "ga4_event_count": 0,
            "trigger_total": 0, "variable_total": 0,
            "type_counter": {},
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
        out.append({"level": "error", "message": "公開バージョンが未取得または未公開"})
    if (c.get("tag_count") or summary.get("tag_total") or 0) == 0:
        out.append({"level": "error", "message": "タグが0件（稼働していない）"})
    if not (c.get("ga4_measurement_ids") or []):
        out.append({"level": "warn", "message": "GA4 Measurement IDが紐づいていない"})
    if summary["has_live"]:
        if summary["ga4_config_count"] == 0 and (c.get("ga4_measurement_ids") or []):
            out.append({"level": "warn", "message": "GA4設定タグ(gaawc)が存在しない"})
        if summary["ua_count"] >= 3:
            out.append({"level": "warn", "message": f"レガシーUA系タグが{summary['ua_count']}件残存"})
        if summary["tag_total"] > 0:
            ratio = summary["tag_paused"] / summary["tag_total"]
            if ratio >= 0.30:
                out.append({"level": "warn", "message": f"pausedタグが{summary['tag_paused']}件（{round(ratio*100)}%）と多い"})
        if summary["tag_total"] > 400:
            out.append({"level": "warn", "message": f"タグが{summary['tag_total']}件と過大"})
    if not (c.get("usage_context") or []):
        out.append({"level": "info", "message": "用途(usage_context)が未設定"})
    return out


def container_alert_summary(containers: list[dict]) -> dict:
    error_c = 0
    warn_c = 0
    issues = {"no_tags": 0, "no_ga4": 0, "ua_left": 0, "no_version": 0}
    for c in containers:
        levels = [a["level"] for a in c.get("_alerts", [])]
        if "error" in levels:
            error_c += 1
        elif "warn" in levels:
            warn_c += 1
        if (c.get("tag_count") or 0) == 0:
            issues["no_tags"] += 1
        if not (c.get("ga4_measurement_ids") or []):
            issues["no_ga4"] += 1
        if c.get("_score_summary", {}).get("ua_count", 0) >= 3:
            issues["ua_left"] += 1
        if not c.get("version_id"):
            issues["no_version"] += 1
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
        out.append({"level": "warn", "message": "Search Analytics取得エラー: " + (s.get("perf_error") or "")[:80]})
    if (s.get("sitemap_count") or 0) == 0:
        out.append({"level": "warn", "message": "sitemapが未登録"})
    if (s.get("sitemap_errors") or 0) > 0:
        out.append({"level": "warn", "message": f"sitemapエラー {s['sitemap_errors']}件"})
    if (s.get("clicks_28d") or 0) == 0 and (s.get("impressions_28d") or 0) == 0:
        out.append({"level": "error", "message": "直近28日で流入・インプレッション共に0"})
    elif (s.get("clicks_28d") or 0) == 0 and (s.get("impressions_28d") or 0) > 100:
        out.append({"level": "warn", "message": f"Imp {s['impressions_28d']:,}あるがClickが0（CTR=0）"})
    if (s.get("ctr_28d") or 0) > 0 and (s.get("ctr_28d") or 0) < 0.005 and (s.get("impressions_28d") or 0) > 1000:
        out.append({"level": "warn", "message": "CTRが0.5%未満（メタ・タイトル要改善）"})
    if (s.get("position_28d") or 0) > 30 and (s.get("impressions_28d") or 0) > 0:
        out.append({"level": "info", "message": f"平均掲載順位 {s.get('position_28d', 0):.1f} 位"})
    if not has_ga4_link:
        out.append({"level": "info", "message": "GA4プロパティとの自動紐付けなし"})
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
