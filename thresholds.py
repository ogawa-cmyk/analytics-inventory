"""監視しきい値のカスタマイズ。

data/thresholds.json に保存。未設定の項目は DEFAULTS を使う。
スコアリング（100点満点の採点基準）は全ユーザー共通のまま変えず、
アラート判定と変化検知にのみ適用する。
"""
from __future__ import annotations
import json
import os
from threading import Lock

from config import DATA_DIR

THRESHOLDS_PATH = DATA_DIR / "thresholds.json"
_lock = Lock()

DEFAULTS = {
    # セッション/検索クリックが「急減」とみなされる減少率（%）。50 = 前回比 -50% 超で警告
    "plunge_pct": 50,
    # 急減判定の最小母数（前回値がこれ未満ならブレとみなして判定しない）
    "plunge_min_base": 50,
    # カスタムディメンション過多の警告閾値（件）
    "cd_warn": 50,
    # レガシーUA系タグ残存の警告件数（件以上で警告）
    "ua_warn": 3,
}

# 入力の許容範囲（設定画面のバリデーション用）
LIMITS = {
    "plunge_pct": (10, 95),
    "plunge_min_base": (0, 100000),
    "cd_warn": (5, 200),
    "ua_warn": (1, 100),
}


def get() -> dict:
    th = dict(DEFAULTS)
    if THRESHOLDS_PATH.exists():
        try:
            saved = json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))
            for k in DEFAULTS:
                if k in saved:
                    th[k] = saved[k]
        except Exception:
            pass
    return th


def save(new: dict) -> dict:
    """許容範囲でクランプして保存。DEFAULTS にないキーは無視。"""
    with _lock:
        cur = get()
        for k in DEFAULTS:
            if k in new:
                try:
                    v = int(new[k])
                except (TypeError, ValueError):
                    continue
                lo, hi = LIMITS[k]
                cur[k] = max(lo, min(hi, v))
        tmp = THRESHOLDS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, THRESHOLDS_PATH)
        return cur


def plunge_factor() -> float:
    """『前回値 × この係数 未満』なら急減。plunge_pct=50 → 0.5"""
    return (100 - get()["plunge_pct"]) / 100.0
