"""User annotations: tags, notes, favorites for properties and GTM containers.

Stored in data/annotations.json. Schema:
{
  "properties": {
    "<property_id>": {"tags": [...], "note": "...", "favorite": bool}
  },
  "containers": {
    "<container_id>": {"tags": [...], "note": "...", "favorite": bool}
  }
}
"""
from __future__ import annotations
import json
from pathlib import Path
from threading import Lock

from config import DATA_DIR

ANNOTATIONS_PATH = DATA_DIR / "annotations.json"
_lock = Lock()


def _load() -> dict:
    if not ANNOTATIONS_PATH.exists():
        return {"properties": {}, "containers": {}}
    try:
        d = json.loads(ANNOTATIONS_PATH.read_text(encoding="utf-8"))
        d.setdefault("properties", {})
        d.setdefault("containers", {})
        return d
    except Exception:
        return {"properties": {}, "containers": {}}


def _save(d: dict) -> None:
    ANNOTATIONS_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def all_data() -> dict:
    with _lock:
        return _load()


def get(kind: str, key: str) -> dict:
    """kind: 'properties' or 'containers'. Returns {tags, note, favorite}."""
    with _lock:
        d = _load()
        return d.get(kind, {}).get(key, {"tags": [], "note": "", "favorite": False})


def set_annotation(kind: str, key: str, **kwargs) -> dict:
    """Update fields. Allowed kwargs: tags (list), note (str), favorite (bool)."""
    with _lock:
        d = _load()
        d.setdefault(kind, {})
        cur = d[kind].setdefault(key, {"tags": [], "note": "", "favorite": False})
        if "tags" in kwargs:
            tags = kwargs["tags"]
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            cur["tags"] = sorted(set(tags))
        if "note" in kwargs:
            cur["note"] = (kwargs["note"] or "").strip()
        if "favorite" in kwargs:
            cur["favorite"] = bool(kwargs["favorite"])
        _save(d)
        return cur


def enrich_properties(properties: list[dict]) -> list[dict]:
    """Merge annotations into property dicts as ann_tags / ann_note / ann_favorite."""
    data = all_data().get("properties", {})
    for p in properties:
        ann = data.get(str(p.get("property_id")), {})
        p["ann_tags"] = ann.get("tags", [])
        p["ann_note"] = ann.get("note", "")
        p["ann_favorite"] = ann.get("favorite", False)
    return properties


def enrich_containers(containers: list[dict]) -> list[dict]:
    data = all_data().get("containers", {})
    for c in containers:
        ann = data.get(str(c.get("container_id")), {})
        c["ann_tags"] = ann.get("tags", [])
        c["ann_note"] = ann.get("note", "")
        c["ann_favorite"] = ann.get("favorite", False)
    return containers


def all_tags() -> list[str]:
    """Return sorted list of all distinct tags used anywhere."""
    d = all_data()
    tags: set[str] = set()
    for kind in ("properties", "containers"):
        for v in d.get(kind, {}).values():
            for t in v.get("tags", []):
                tags.add(t)
    return sorted(tags)
