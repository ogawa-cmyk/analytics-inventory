"""クライアント別グループ管理。

data/clients.json にクライアント定義（名前・メモ・所属エンティティのIDリスト）を保存し、
GA4プロパティ / GTMコンテナ / SCサイトをクライアント単位で束ねる。

Schema:
{
  "clients": {
    "cl_ab12cd34": {
      "name": "A社",
      "note": "EC支援。担当: 田中",
      "property_ids": ["300000001"],
      "container_ids": ["GTM-XXXX"],
      "site_hashes": ["cfddd32a0a6a3b42"],
      "created_at": "2026-07-20T...",
    }
  }
}
"""
from __future__ import annotations
import json
import os
import uuid
from datetime import datetime, timezone
from threading import Lock

from config import DATA_DIR

CLIENTS_PATH = DATA_DIR / "clients.json"
_lock = Lock()

_LIST_KEYS = ("property_ids", "container_ids", "site_hashes")


def _load() -> dict:
    if not CLIENTS_PATH.exists():
        return {"clients": {}}
    try:
        d = json.loads(CLIENTS_PATH.read_text(encoding="utf-8"))
        d.setdefault("clients", {})
        return d
    except Exception:
        return {"clients": {}}


def _save(d: dict) -> None:
    tmp = CLIENTS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CLIENTS_PATH)


def list_clients() -> dict:
    """{client_id: client_dict}（name順）。"""
    cs = _load()["clients"]
    return dict(sorted(cs.items(), key=lambda kv: kv[1].get("name", "")))


def get(cid: str) -> dict | None:
    return _load()["clients"].get(cid)


def create(name: str, note: str = "") -> dict:
    with _lock:
        d = _load()
        cid = "cl_" + uuid.uuid4().hex[:8]
        d["clients"][cid] = {
            "name": (name or "").strip() or "無題クライアント",
            "note": (note or "").strip(),
            "property_ids": [], "container_ids": [], "site_hashes": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _save(d)
        return {"client_id": cid, **d["clients"][cid]}


def update(cid: str, **kwargs) -> dict | None:
    """name / note / property_ids / container_ids / site_hashes を更新。"""
    with _lock:
        d = _load()
        c = d["clients"].get(cid)
        if c is None:
            return None
        if "name" in kwargs:
            c["name"] = (kwargs["name"] or "").strip() or c["name"]
        if "note" in kwargs:
            c["note"] = (kwargs["note"] or "").strip()
        for k in _LIST_KEYS:
            if k in kwargs and isinstance(kwargs[k], list):
                c[k] = sorted({str(x) for x in kwargs[k] if x})
        _save(d)
        return c


def delete(cid: str) -> bool:
    with _lock:
        d = _load()
        if cid not in d["clients"]:
            return False
        del d["clients"][cid]
        _save(d)
        return True


# ============================================================
#  集計（enrich済みインベントリと突合）
# ============================================================

def resolve_entities(client: dict, inv: dict) -> dict:
    """クライアントに割り当てられた実エンティティ（enrich済み）を返す。"""
    pid_set = set(client.get("property_ids") or [])
    cid_set = set(client.get("container_ids") or [])
    sh_set = set(client.get("site_hashes") or [])
    props = [p for p in (inv.get("properties") or []) if str(p.get("property_id")) in pid_set]
    conts = [c for c in (inv.get("gtm_containers") or []) if str(c.get("container_id")) in cid_set]
    sites = [s for s in (inv.get("sc_sites") or []) if str(s.get("site_hash")) in sh_set]
    return {"properties": props, "containers": conts, "sc_sites": sites}


def summarize(client: dict, inv: dict) -> dict:
    """クライアントカード用のKPIサマリー。"""
    ent = resolve_entities(client, inv)

    def _stat(items):
        n = len(items)
        avg = round(sum((x.get("health_score") or 0) for x in items) / n, 1) if n else 0
        bad = sum(1 for x in items
                  if not x.get("ann_excluded")
                  and (x.get("health_grade") in ("D", "F") or x.get("has_error_alert")))
        return {"n": n, "avg": avg, "bad": bad}

    ga4 = _stat(ent["properties"])
    gtm = _stat(ent["containers"])
    sc = _stat(ent["sc_sites"])
    return {
        "ga4": ga4, "gtm": gtm, "sc": sc,
        "total_entities": ga4["n"] + gtm["n"] + sc["n"],
        "total_bad": ga4["bad"] + gtm["bad"] + sc["bad"],
    }


def client_change_events(client: dict, days: int = 30, limit: int = 50) -> list[dict]:
    """このクライアントのエンティティに関する変化イベント（新しい順）。"""
    import changes
    pid_set = set(client.get("property_ids") or [])
    cid_set = set(client.get("container_ids") or [])
    sh_set = set(client.get("site_hashes") or [])
    out = []
    for e in changes.recent_events(days=days, limit=changes.MAX_LOG_ENTRIES):
        eid = e.get("entity_id")
        kind = e.get("kind")
        if ((kind == "ga4" and eid in pid_set)
                or (kind == "gtm" and eid in cid_set)
                or (kind == "sc" and eid in sh_set)):
            out.append(e)
            if len(out) >= limit:
                break
    return out
