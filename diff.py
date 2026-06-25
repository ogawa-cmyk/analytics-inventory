"""Snapshot history + diff calculation."""
from __future__ import annotations
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from config import DATA_DIR

SNAPSHOTS_DIR = DATA_DIR / "snapshots"
SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)


def save_snapshot(inventory: dict) -> Path:
    """Save a slim inventory snapshot for time-series comparison."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slim = {
        "generated_at": inventory.get("generated_at"),
        "properties": [
            {
                "property_id": p.get("property_id"),
                "display_name": p.get("display_name"),
                "is_tracked": p.get("is_tracked"),
                "sessions_7d": p.get("sessions_7d"),
                "events_7d": p.get("events_7d"),
                "key_event_count": p.get("key_event_count"),
                "key_event_names": p.get("key_event_names"),
                "custom_dimension_count": p.get("custom_dimension_count"),
                "custom_metric_count": p.get("custom_metric_count"),
                "is_ecommerce": p.get("is_ecommerce"),
            }
            for p in inventory.get("properties", [])
        ],
        "gtm_containers": [
            {
                "container_id": c.get("container_id"),
                "name": c.get("name"),
                "tag_count": c.get("tag_count"),
                "trigger_count": c.get("trigger_count"),
                "variable_count": c.get("variable_count"),
                "version_id": c.get("version_id"),
                "ga4_measurement_ids": c.get("ga4_measurement_ids"),
            }
            for c in inventory.get("gtm_containers", [])
        ],
    }
    path = SNAPSHOTS_DIR / f"{ts}.json"
    path.write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")
    _prune(keep=20)
    return path


def _prune(keep: int = 20) -> None:
    snaps = sorted(SNAPSHOTS_DIR.glob("*.json"), reverse=True)
    for p in snaps[keep:]:
        try:
            p.unlink()
        except Exception:
            pass


def list_snapshots() -> list[dict]:
    """Newest first."""
    out = []
    for p in sorted(SNAPSHOTS_DIR.glob("*.json"), reverse=True):
        m = re.match(r"(\d{8}T\d{6}Z)", p.stem)
        out.append({"file": p.name, "stamp": m.group(1) if m else p.stem, "path": str(p)})
    return out


def load_snapshot(name: str) -> dict | None:
    p = SNAPSHOTS_DIR / name
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def previous_snapshot_for(property_id: str) -> dict | None:
    """Return the property record from the most recent prior snapshot."""
    snaps = sorted(SNAPSHOTS_DIR.glob("*.json"), reverse=True)
    if len(snaps) < 2:
        return None
    prev = json.loads(snaps[1].read_text(encoding="utf-8"))
    for p in prev.get("properties", []):
        if str(p.get("property_id")) == str(property_id):
            return p
    return None


def diff_property(current: dict, previous: dict | None) -> dict:
    """Return a dict of changes vs previous snapshot."""
    if not previous:
        return {"first_seen": True}
    d = {}
    for field in ("key_event_count", "custom_dimension_count", "custom_metric_count",
                  "sessions_7d", "events_7d"):
        cur = current.get(field)
        prv = previous.get(field)
        if cur is None or prv is None:
            continue
        try:
            delta = cur - prv
            if delta != 0:
                d[field] = {"current": cur, "previous": prv, "delta": delta,
                            "pct": (delta / prv * 100.0) if prv else None}
        except Exception:
            continue
    cur_ke = set(current.get("key_event_names") or [])
    prv_ke = set(previous.get("key_event_names") or [])
    if cur_ke != prv_ke:
        d["key_events_added"] = sorted(cur_ke - prv_ke)
        d["key_events_removed"] = sorted(prv_ke - cur_ke)
    if current.get("is_tracked") != previous.get("is_tracked"):
        d["tracking_changed"] = {"current": current.get("is_tracked"), "previous": previous.get("is_tracked")}
    if current.get("is_ecommerce") != previous.get("is_ecommerce"):
        d["ecommerce_changed"] = {"current": current.get("is_ecommerce"), "previous": previous.get("is_ecommerce")}
    return d
