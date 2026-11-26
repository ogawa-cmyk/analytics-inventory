"""収集そのものが健全だったかを判定する。

変化ログ（changes.py）は**個々のプロパティ**の変化を見るが、
「収集できた件数が全体として減った」ことは見ていない。

2026-10-01 に285件だったプロパティが、11-21 には153件に半減していた。
3アカウントの認証が切れてまるごと取れなくなっていたのが原因で、
11-21 に起きたものが 11-26 まで誰にも気づかれなかった。ヘッダーに
「エラー 6」と小さく出るだけで、画面上はいつもどおりに見えていたため。

ここで見るのは次の2つ。

- **認証の失敗**: そのアカウントのプロパティが丸ごと消えるので最も重い
- **件数の急減**: 認証以外の理由（権限剥奪・API障害）でも起きる

判定結果は data/collection_health.json に保存し、ホーム画面が読む。
収集のたびに1回だけ計算する（画面表示のたびに重いJSONを読まないため）。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

from config import DATA_DIR

HEALTH_PATH = DATA_DIR / "collection_health.json"

# 件数がこの割合を超えて減ったら警告する（0.20 = 2割減）。
# 正当な整理で減ることもあるので、エラーではなく「確認して」の位置づけ。
DROP_WARN_RATIO = 0.20

# 母数がこれ未満のときは割合を見ない（数件の増減で毎回警告しないため）
DROP_MIN_BASE = 10

# 認証の失敗とみなす文字列。Googleのトークン失効はこの形で返る。
_AUTH_ERROR_MARKERS = ("invalid_grant", "invalid_client", "unauthorized",
                       "Token has been expired", "credentials")

_KINDS = (
    ("property_count", "GA4プロパティ"),
    ("gtm_container_count", "GTMコンテナ"),
    ("sc_site_count", "Search Consoleサイト"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def auth_failures(inv: Any) -> list[dict]:
    """認証に失敗しているアカウントの一覧（アカウント単位にまとめる）。

    1アカウントにつき auth と sc_list_sites の2件が出るなど、同じ失効が
    複数のエラーになるので、メールアドレスで束ねて返す。
    """
    errors = _as_dict(inv).get("errors")
    errors = errors if isinstance(errors, list) else []
    by_email: dict[str, dict] = {}
    for e in errors:
        if not isinstance(e, dict):
            continue
        text = str(e.get("error") or "")
        if not any(m.lower() in text.lower() for m in _AUTH_ERROR_MARKERS):
            continue
        email = str(e.get("email") or "").strip() or "(不明)"
        row = by_email.setdefault(email, {"email": email, "stages": [], "count": 0})
        stage = str(e.get("stage") or "")
        if stage and stage not in row["stages"]:
            row["stages"].append(stage)
        row["count"] += 1
    return sorted(by_email.values(), key=lambda r: r["email"])


def count_drops(cur: Any, prev: Any,
                warn_ratio: float = DROP_WARN_RATIO,
                min_base: int = DROP_MIN_BASE) -> list[dict]:
    """前回より大きく減った種別を返す。

    増えた場合・母数が小さい場合は何も返さない。判定できないとき
    （前回の記録が無い）も空を返す＝**減っていないとは言っていない**。
    """
    cur_d, prev_d = _as_dict(cur), _as_dict(prev)
    if not prev_d:
        return []
    out = []
    for key, label in _KINDS:
        try:
            now_n = int(cur_d.get(key) or 0)
            was_n = int(prev_d.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if was_n < min_base or now_n >= was_n:
            continue
        ratio = (was_n - now_n) / was_n
        if ratio >= warn_ratio:
            out.append({"key": key, "label": label, "before": was_n, "after": now_n,
                        "lost": was_n - now_n, "ratio": round(ratio, 3)})
    return out


def assess(cur: Any, prev: Any = None) -> dict:
    """収集結果を評価する。ホーム画面に出す内容そのもの。

    level は "ok" / "warn" / "critical"。認証の失敗があれば critical
    （そのアカウントの計測が丸ごと監視対象から外れるため）。
    """
    auths = auth_failures(cur)
    drops = count_drops(cur, prev)
    if auths:
        level = "critical"
    elif drops:
        level = "warn"
    else:
        level = "ok"
    return {
        "generated_at": _now(),
        "level": level,
        "auth_failures": auths,
        "count_drops": drops,
        "counts": {key: _as_dict(cur).get(key) for key, _ in _KINDS},
        "collected_at": _as_dict(cur).get("generated_at"),
    }


def save(state: dict) -> None:
    tmp = HEALTH_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, HEALTH_PATH)


def load() -> Optional[dict]:
    if not HEALTH_PATH.exists():
        return None
    try:
        d = json.loads(HEALTH_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def summary_line(state: Any) -> str:
    """1行の説明文。通知やログに使う。"""
    s = _as_dict(state)
    if s.get("level") == "critical":
        n = len(s.get("auth_failures") or [])
        return f"{n}アカウントの認証が切れています。そのアカウントのデータは収集できていません。"
    if s.get("level") == "warn":
        d = (s.get("count_drops") or [{}])[0]
        return (f"{d.get('label')}が {d.get('before')}件 → {d.get('after')}件 に減りました"
                f"（{d.get('lost')}件）。")
    return "収集は正常です。"
