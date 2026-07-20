"""新バージョン通知: GitHub Releases を1日1回チェックし、更新があればUIに知らせる。

- 結果は data/update_check.json に24時間キャッシュ（GitHub API叩きすぎ防止）
- ネットワーク不通・API失敗時は静かに諦める（バナーを出さないだけ）
"""
from __future__ import annotations
import json
import os
import time

from config import DATA_DIR, GITHUB_REPO, VERSION

CACHE_PATH = DATA_DIR / "update_check.json"
CACHE_TTL_SEC = 24 * 3600


def _parse_ver(v: str) -> tuple:
    v = (v or "").lstrip("vV").strip()
    parts = []
    for x in v.split("."):
        try:
            parts.append(int(x))
        except ValueError:
            break
    return tuple(parts) or (0,)


def _fetch_latest() -> str | None:
    """最新リリースのタグ名（例 "v1.2.0"）。リリースが無ければタグから取得。"""
    import requests
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": f"analytics-inventory/{VERSION}"}
    try:
        r = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                         headers=headers, timeout=5)
        if r.status_code == 200:
            return r.json().get("tag_name")
        if r.status_code == 404:  # リリース未作成 → タグの先頭
            r2 = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/tags",
                              headers=headers, timeout=5)
            if r2.status_code == 200 and r2.json():
                tags = sorted((t.get("name") for t in r2.json()), key=_parse_ver, reverse=True)
                return tags[0] if tags else None
    except Exception:
        pass
    return None


def get_update_info() -> dict | None:
    """新しいバージョンがあれば {latest, url, current} を返す。無ければ None。"""
    cache = None
    if CACHE_PATH.exists():
        try:
            cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            cache = None

    if cache is None or (time.time() - (cache.get("checked_at") or 0)) > CACHE_TTL_SEC:
        latest = _fetch_latest()
        cache = {"checked_at": time.time(), "latest": latest}
        try:
            tmp = CACHE_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(cache), encoding="utf-8")
            os.replace(tmp, CACHE_PATH)
        except Exception:
            pass

    latest = cache.get("latest")
    if latest and _parse_ver(latest) > _parse_ver(VERSION):
        return {
            "current": VERSION,
            "latest": latest.lstrip("vV"),
            "url": f"https://github.com/{GITHUB_REPO}/releases",
        }
    return None
