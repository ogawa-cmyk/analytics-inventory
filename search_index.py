"""Cross-asset full-text search: events / CDs / CMs / GTM tags / triggers / variables."""
from __future__ import annotations
import json
from pathlib import Path

from config import DETAILS_DIR, GTM_DETAILS_DIR


def search(query: str, inventory: dict, kinds: list[str] | None = None, limit: int = 500) -> list[dict]:
    """Return a flat list of hits matching query (case-insensitive substring)."""
    q = (query or "").strip().lower()
    if not q:
        return []
    kinds = kinds or ["event", "ke", "cd", "cm", "tag", "trigger", "variable"]
    hits = []
    for p in inventory.get("properties", []):
        pid = p.get("property_id")
        path = DETAILS_DIR / f"{pid}.json"
        if not path.exists():
            continue
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if "ke" in kinds:
            for ke in d.get("key_events", []):
                if q in (ke.get("event_name") or "").lower():
                    hits.append({"kind": "ke", "label": ke.get("event_name"),
                                 "asset": "GA4", "owner": p.get("display_name"),
                                 "owner_id": pid, "link": f"/property/{pid}",
                                 "auth_email": p.get("auth_email"), "extra": "キーイベント"})
        if "event" in kinds:
            for ev in d.get("events", []):
                name = ev.get("event_name", "")
                if name and q in name.lower():
                    hits.append({"kind": "event", "label": name,
                                 "asset": "GA4", "owner": p.get("display_name"),
                                 "owner_id": pid, "link": f"/property/{pid}",
                                 "auth_email": p.get("auth_email"),
                                 "extra": f"{ev.get('event_count',0):,} 回 / 30日"})
        if "cd" in kinds:
            for cd in d.get("custom_dimensions", []):
                hay = ((cd.get("display_name") or "") + " " + (cd.get("parameter_name") or "")).lower()
                if q in hay:
                    hits.append({"kind": "cd",
                                 "label": f"{cd.get('display_name')} ({cd.get('parameter_name')})",
                                 "asset": "GA4", "owner": p.get("display_name"),
                                 "owner_id": pid, "link": f"/property/{pid}",
                                 "auth_email": p.get("auth_email"),
                                 "extra": cd.get("scope") or ""})
        if "cm" in kinds:
            for cm in d.get("custom_metrics", []):
                hay = ((cm.get("display_name") or "") + " " + (cm.get("parameter_name") or "")).lower()
                if q in hay:
                    hits.append({"kind": "cm",
                                 "label": f"{cm.get('display_name')} ({cm.get('parameter_name')})",
                                 "asset": "GA4", "owner": p.get("display_name"),
                                 "owner_id": pid, "link": f"/property/{pid}",
                                 "auth_email": p.get("auth_email"),
                                 "extra": cm.get("measurement_unit") or ""})
        if len(hits) >= limit:
            break

    if any(k in kinds for k in ("tag", "trigger", "variable")):
        for c in inventory.get("gtm_containers", []):
            cid = c.get("container_id")
            path = GTM_DETAILS_DIR / f"{cid}.json"
            if not path.exists():
                continue
            try:
                live = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            owner = f"{c.get('account_name')} / {c.get('name')}"
            if "tag" in kinds:
                for t in live.get("tag") or []:
                    if q in (t.get("name") or "").lower():
                        hits.append({"kind": "tag", "label": t.get("name"),
                                     "asset": "GTM", "owner": owner,
                                     "owner_id": cid, "link": f"/gtm/{cid}/tag",
                                     "auth_email": c.get("auth_email"),
                                     "extra": t.get("type") or ""})
            if "trigger" in kinds:
                for t in live.get("trigger") or []:
                    if q in (t.get("name") or "").lower():
                        hits.append({"kind": "trigger", "label": t.get("name"),
                                     "asset": "GTM", "owner": owner,
                                     "owner_id": cid, "link": f"/gtm/{cid}/trigger",
                                     "auth_email": c.get("auth_email"),
                                     "extra": t.get("type") or ""})
            if "variable" in kinds:
                for t in live.get("variable") or []:
                    if q in (t.get("name") or "").lower():
                        hits.append({"kind": "variable", "label": t.get("name"),
                                     "asset": "GTM", "owner": owner,
                                     "owner_id": cid, "link": f"/gtm/{cid}/variable",
                                     "auth_email": c.get("auth_email"),
                                     "extra": t.get("type") or ""})
            if len(hits) >= limit:
                break

    return hits[:limit]
