"""User annotations: tags, notes, favorites, monitoring exclusion.

Stored in data/annotations.json. Schema:
{
  "properties": {
    "<property_id>": {"tags": [...], "note": "...", "favorite": bool, "excluded": bool}
  },
  "containers": {
    "<container_id>": {"tags": [...], "note": "...", "favorite": bool, "excluded": bool}
  },
  "sc_sites": {
    "<site_hash>": {"tags": [...], "note": "...", "favorite": bool, "excluded": bool}
  }
}

excluded=True の項目は「監視から除外」: アラート集計・今日の要対応・変化ログ・
週次メール・自動診断の対象外になる（一覧・詳細ページには表示され続ける）。
snooze_until（"YYYY-MM-DD"）が未来日の間は期限付きで同じ扱い（スヌーズ）。
"""
from __future__ import annotations
import json
from datetime import date
from pathlib import Path
from threading import Lock

from config import DATA_DIR

ANNOTATIONS_PATH = DATA_DIR / "annotations.json"
_lock = Lock()

KINDS = ("properties", "containers", "sc_sites")
_DEFAULT = {"tags": [], "note": "", "favorite": False, "excluded": False, "snooze_until": None}


def _snooze_active(ann: dict) -> bool:
    su = ann.get("snooze_until")
    if not su:
        return False
    try:
        return date.today().isoformat() <= str(su)
    except Exception:
        return False


def is_effectively_excluded(ann: dict) -> bool:
    """恒久除外 or スヌーズ期間中なら True。"""
    return bool(ann.get("excluded")) or _snooze_active(ann)


def _load() -> dict:
    if not ANNOTATIONS_PATH.exists():
        return {k: {} for k in KINDS}
    try:
        d = json.loads(ANNOTATIONS_PATH.read_text(encoding="utf-8"))
        for k in KINDS:
            d.setdefault(k, {})
        return d
    except Exception:
        return {k: {} for k in KINDS}


def _save(d: dict) -> None:
    ANNOTATIONS_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def all_data() -> dict:
    with _lock:
        return _load()


def get(kind: str, key: str) -> dict:
    """kind: 'properties' | 'containers' | 'sc_sites'."""
    with _lock:
        d = _load()
        return {**_DEFAULT, **d.get(kind, {}).get(key, {})}


def set_annotation(kind: str, key: str, **kwargs) -> dict:
    """Update fields. Allowed kwargs: tags (list), note (str), favorite (bool), excluded (bool)."""
    with _lock:
        d = _load()
        d.setdefault(kind, {})
        cur = d[kind].setdefault(key, dict(_DEFAULT, tags=[]))
        if "tags" in kwargs:
            tags = kwargs["tags"]
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            cur["tags"] = sorted(set(tags))
        if "note" in kwargs:
            cur["note"] = (kwargs["note"] or "").strip()
        if "favorite" in kwargs:
            cur["favorite"] = bool(kwargs["favorite"])
        if "excluded" in kwargs:
            cur["excluded"] = bool(kwargs["excluded"])
        if "snooze_until" in kwargs:
            cur["snooze_until"] = kwargs["snooze_until"] or None
        _save(d)
        return cur


def excluded_ids(kind: str) -> set[str]:
    """監視除外中（恒久＋スヌーズ期間中）のIDセット。"""
    data = all_data().get(kind, {})
    return {k for k, v in data.items() if is_effectively_excluded(v)}


def _enrich(items: list[dict], kind: str, id_key: str) -> list[dict]:
    data = all_data().get(kind, {})
    for x in items:
        ann = data.get(str(x.get(id_key)), {})
        x["ann_tags"] = ann.get("tags", [])
        x["ann_note"] = ann.get("note", "")
        x["ann_favorite"] = ann.get("favorite", False)
        # ann_excluded は「実効値」（恒久 or スヌーズ中）。下流はこれだけ見ればよい
        x["ann_excluded"] = is_effectively_excluded(ann)
        x["ann_excluded_permanent"] = bool(ann.get("excluded"))
        x["ann_snooze_until"] = ann.get("snooze_until") if _snooze_active(ann) else None
    return items


def enrich_properties(properties: list[dict]) -> list[dict]:
    """Merge annotations into property dicts as ann_tags / ann_note / ann_favorite / ann_excluded."""
    return _enrich(properties, "properties", "property_id")


def enrich_containers(containers: list[dict]) -> list[dict]:
    return _enrich(containers, "containers", "container_id")


def enrich_sc_sites(sites: list[dict]) -> list[dict]:
    return _enrich(sites, "sc_sites", "site_hash")


def all_tags() -> list[str]:
    """Return sorted list of all distinct tags used anywhere."""
    d = all_data()
    tags: set[str] = set()
    for kind in KINDS:
        for v in d.get(kind, {}).values():
            for t in v.get("tags", []):
                tags.add(t)
    return sorted(tags)
