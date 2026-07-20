"""変化点検知: スナップショット間の差分を重要度付きイベントとして検出・永続化する。

indexer 実行後（save_snapshot 直後）に record_changes() が呼ばれ、
最新スナップショットと直前スナップショットを比較して変化イベントを
data/changes_log.json に追記する。UI は /changes で閲覧できる。

イベント形式:
  {
    "detected_at": ISO8601,
    "snapshot": "20260702T090000Z",      # 比較元（新しい方）
    "prev_snapshot": "20260627T090000Z",
    "severity": "critical" | "warn" | "info",
    "kind": "ga4" | "gtm" | "sc",
    "entity_id": "300000001",
    "entity_name": "example.com",
    "change_type": "tracking_stopped",
    "message": "計測が停止しました（前回: 計測中）",
  }
"""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import diff as diff_mod
from config import DATA_DIR

CHANGES_LOG_PATH = DATA_DIR / "changes_log.json"
MAX_LOG_ENTRIES = 1000


def _plunge_params() -> tuple[float, int]:
    """急減判定の (係数, 最小母数)。thresholds.py の設定を反映。"""
    import thresholds
    return thresholds.plunge_factor(), thresholds.get()["plunge_min_base"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
#  検出ロジック（純粋関数）
# ============================================================

def detect_changes(cur: dict, prev: dict, exclude: dict | None = None) -> list[dict]:
    """2つのスナップショット（diff.save_snapshot 形式）を比較して変化イベントを返す。

    exclude: {"ga4": set, "gtm": set, "sc": set} — 監視除外中のIDは検出対象外。
    """
    ex = exclude or {}
    events: list[dict] = []
    events.extend(_detect_ga4(cur.get("properties") or [], prev.get("properties") or []))
    events.extend(_detect_gtm(cur.get("gtm_containers") or [], prev.get("gtm_containers") or []))
    events.extend(_detect_sc(cur.get("sc_sites") or [], prev.get("sc_sites") or []))
    if ex:
        events = [e for e in events if e.get("entity_id") not in (ex.get(e.get("kind")) or set())]
    return events


def filter_excluded(events: list[dict]) -> list[dict]:
    """現在監視除外中のエンティティのイベントを表示から間引く（過去ログにも適用）。"""
    ex = monitoring_exclusions()
    if not any(ex.values()):
        return events
    return [e for e in events if e.get("entity_id") not in (ex.get(e.get("kind")) or set())]


def monitoring_exclusions() -> dict:
    """annotations の監視除外設定を kind 別 ID セットで返す。"""
    try:
        import annotations as ann
        return {
            "ga4": ann.excluded_ids("properties"),
            "gtm": ann.excluded_ids("containers"),
            "sc": ann.excluded_ids("sc_sites"),
        }
    except Exception:
        return {}


def _ev(severity: str, kind: str, entity_id: str, entity_name: str,
        change_type: str, message: str) -> dict:
    return {
        "severity": severity, "kind": kind,
        "entity_id": str(entity_id or ""), "entity_name": entity_name or "",
        "change_type": change_type, "message": message,
    }


def _detect_ga4(cur_props: list, prev_props: list) -> list[dict]:
    out = []
    prev_map = {str(p.get("property_id")): p for p in prev_props}
    cur_ids = {str(p.get("property_id")) for p in cur_props}

    for p in cur_props:
        pid = str(p.get("property_id"))
        name = p.get("display_name") or pid
        pv = prev_map.get(pid)
        if pv is None:
            out.append(_ev("info", "ga4", pid, name, "property_added",
                           "新しいプロパティが検出されました"))
            continue

        # 計測状態
        if pv.get("is_tracked") and not p.get("is_tracked"):
            out.append(_ev("critical", "ga4", pid, name, "tracking_stopped",
                           "計測が停止しました（直近7日間データなし）"))
        elif pv.get("is_tracked") is False and p.get("is_tracked"):
            out.append(_ev("info", "ga4", pid, name, "tracking_resumed",
                           "計測が再開されました"))

        # キーイベントの増減（名前ベース）
        cur_ke = set(p.get("key_event_names") or [])
        prv_ke = set(pv.get("key_event_names") or [])
        removed = sorted(prv_ke - cur_ke)
        added = sorted(cur_ke - prv_ke)
        if removed:
            out.append(_ev("critical", "ga4", pid, name, "key_events_removed",
                           f"キーイベントが削除されました: {', '.join(removed)}"))
        if added:
            out.append(_ev("info", "ga4", pid, name, "key_events_added",
                           f"キーイベントが追加されました: {', '.join(added)}"))

        # セッション急減
        plunge_factor, plunge_min = _plunge_params()
        prv_s = pv.get("sessions_7d") or 0
        cur_s = p.get("sessions_7d") or 0
        if prv_s >= plunge_min and cur_s < prv_s * plunge_factor:
            pct = round((cur_s - prv_s) / prv_s * 100)
            out.append(_ev("warn", "ga4", pid, name, "sessions_plunge",
                           f"セッションが急減: {prv_s:,} → {cur_s:,}（{pct}%）"))

        # ストリーム削除（stream_count は新形式スナップショットのみ）
        prv_st = pv.get("stream_count")
        cur_st = p.get("stream_count")
        if prv_st is not None and cur_st is not None and cur_st < prv_st:
            out.append(_ev("critical", "ga4", pid, name, "stream_removed",
                           f"データストリームが減少: {prv_st} → {cur_st}"))

        # カスタムディメンション減少
        prv_cd = pv.get("custom_dimension_count") or 0
        cur_cd = p.get("custom_dimension_count") or 0
        if cur_cd < prv_cd:
            out.append(_ev("warn", "ga4", pid, name, "cd_decreased",
                           f"カスタムディメンションが減少: {prv_cd} → {cur_cd}"))

        # eコマース計測の停止
        if pv.get("is_ecommerce") and not p.get("is_ecommerce"):
            out.append(_ev("warn", "ga4", pid, name, "ecommerce_stopped",
                           "eコマースイベントが検出されなくなりました"))

    for pid, pv in prev_map.items():
        if pid not in cur_ids:
            out.append(_ev("warn", "ga4", pid, pv.get("display_name") or pid,
                           "property_removed", "プロパティが一覧から消えました（削除または権限喪失）"))
    return out


def _detect_gtm(cur_conts: list, prev_conts: list) -> list[dict]:
    out = []
    prev_map = {str(c.get("container_id")): c for c in prev_conts}
    cur_ids = {str(c.get("container_id")) for c in cur_conts}

    for c in cur_conts:
        cid = str(c.get("container_id"))
        name = c.get("name") or cid
        pv = prev_map.get(cid)
        if pv is None:
            out.append(_ev("info", "gtm", cid, name, "container_added",
                           "新しいコンテナが検出されました"))
            continue

        # バージョン公開
        if pv.get("version_id") != c.get("version_id"):
            out.append(_ev("info", "gtm", cid, name, "version_published",
                           f"新バージョンが公開されました: v{pv.get('version_id') or '—'} → v{c.get('version_id') or '—'}"))

        # タグ数の増減
        prv_t = pv.get("tag_count") or 0
        cur_t = c.get("tag_count") or 0
        if cur_t == 0 and prv_t > 0:
            out.append(_ev("critical", "gtm", cid, name, "tags_all_removed",
                           f"タグが全て消えました（前回: {prv_t}件）"))
        elif cur_t < prv_t:
            out.append(_ev("warn", "gtm", cid, name, "tags_decreased",
                           f"タグが減少: {prv_t} → {cur_t}件"))
        elif cur_t - prv_t >= 5:
            out.append(_ev("info", "gtm", cid, name, "tags_increased",
                           f"タグが大幅増加: {prv_t} → {cur_t}件"))

        # GA4 Measurement ID の付け外し
        cur_mid = set(c.get("ga4_measurement_ids") or [])
        prv_mid = set(pv.get("ga4_measurement_ids") or [])
        mid_removed = sorted(prv_mid - cur_mid)
        mid_added = sorted(cur_mid - prv_mid)
        if mid_removed:
            out.append(_ev("critical", "gtm", cid, name, "mid_removed",
                           f"GA4 Measurement IDが外れました: {', '.join(mid_removed)}"))
        if mid_added:
            out.append(_ev("info", "gtm", cid, name, "mid_added",
                           f"GA4 Measurement IDが追加されました: {', '.join(mid_added)}"))

    for cid, pv in prev_map.items():
        if cid not in cur_ids:
            out.append(_ev("warn", "gtm", cid, pv.get("name") or cid,
                           "container_removed", "コンテナが一覧から消えました（削除または権限喪失）"))
    return out


def _detect_sc(cur_sites: list, prev_sites: list) -> list[dict]:
    out = []
    prev_map = {str(s.get("site_hash")): s for s in prev_sites}
    for s in cur_sites:
        sh = str(s.get("site_hash"))
        name = s.get("site_url") or sh
        pv = prev_map.get(sh)
        if pv is None:
            continue  # SC サイトの追加はノイズになりやすいので通知しない

        plunge_factor, plunge_min = _plunge_params()
        prv_c = pv.get("clicks_28d") or 0
        cur_c = s.get("clicks_28d") or 0
        if prv_c >= plunge_min and cur_c < prv_c * plunge_factor:
            pct = round((cur_c - prv_c) / prv_c * 100)
            out.append(_ev("warn", "sc", sh, name, "clicks_plunge",
                           f"検索クリックが急減: {prv_c:,} → {cur_c:,}（{pct}%）"))

        prv_e = pv.get("sitemap_errors") or 0
        cur_e = s.get("sitemap_errors") or 0
        if cur_e > prv_e:
            out.append(_ev("warn", "sc", sh, name, "sitemap_errors",
                           f"sitemapエラーが増加: {prv_e} → {cur_e}件"))
    return out


# ============================================================
#  永続化
# ============================================================

def load_log(limit: int = 200) -> list[dict]:
    """変化ログを新しい順で返す。"""
    if not CHANGES_LOG_PATH.exists():
        return []
    try:
        log = json.loads(CHANGES_LOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(log, list):
        return []
    return list(reversed(log))[:limit]


def _append(events: list[dict]) -> None:
    log = []
    if CHANGES_LOG_PATH.exists():
        try:
            log = json.loads(CHANGES_LOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            log = []
    if not isinstance(log, list):
        log = []
    log.extend(events)
    log = log[-MAX_LOG_ENTRIES:]
    tmp = CHANGES_LOG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, CHANGES_LOG_PATH)


def record_changes() -> list[dict]:
    """最新2スナップショットを比較して変化を検出し、ログへ追記して返す。

    indexer の save_snapshot 直後に呼ぶ。スナップショットが2件未満なら何もしない。
    同じスナップショットペアを二重に記録しない（stamp で重複チェック）。
    """
    snaps = diff_mod.list_snapshots()
    if len(snaps) < 2:
        return []
    cur_meta, prev_meta = snaps[0], snaps[1]

    # 二重記録防止: 既に同じ snapshot stamp のイベントが記録済みなら skip
    existing = load_log(limit=1)
    if existing and existing[0].get("snapshot") == cur_meta["stamp"]:
        return []

    cur = diff_mod.load_snapshot(cur_meta["file"]) or {}
    prev = diff_mod.load_snapshot(prev_meta["file"]) or {}
    events = detect_changes(cur, prev, exclude=monitoring_exclusions())
    now = _now()
    for e in events:
        e["detected_at"] = now
        e["snapshot"] = cur_meta["stamp"]
        e["prev_snapshot"] = prev_meta["stamp"]
    if events:
        _append(events)
    return events


def recent_events(days: int = 7, limit: int = 100) -> list[dict]:
    """直近 N 日の変化イベント（新しい順・監視除外を間引き済み）。週次メールで使用。"""
    out = []
    cutoff = None
    try:
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    except Exception:
        pass
    for e in filter_excluded(load_log(limit=MAX_LOG_ENTRIES)):
        if cutoff is not None:
            try:
                dt = datetime.fromisoformat(e.get("detected_at", ""))
                if dt < cutoff:
                    continue
            except Exception:
                pass
        out.append(e)
        if len(out) >= limit:
            break
    return out


def summary_counts(events: list[dict]) -> dict:
    return {
        "critical": sum(1 for e in events if e.get("severity") == "critical"),
        "warn": sum(1 for e in events if e.get("severity") == "warn"),
        "info": sum(1 for e in events if e.get("severity") == "info"),
        "total": len(events),
    }
