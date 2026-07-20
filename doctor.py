# -*- coding: utf-8 -*-
"""セットアップ診断: 導入がどこまで完了しているかを順に検査し、次の一手を表示する。

使い方:
    python doctor.py
（または run_doctor.bat をダブルクリック）

検査項目:
  1. Python バージョン
  2. 依存ライブラリ
  3. client_secret.json（Google Cloud の OAuth クライアント）
  4. Google アカウント認証（tokens/）
  5. Google API の疎通（GA4 Admin / GTM / Search Console）
  6. 収集データ（inventory.json）
  7. Anthropic APIキー（任意・AI診断用）
"""
from __future__ import annotations
import io
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning)  # google-api-core のPythonバージョン警告を抑制

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent

_results: list[tuple[str, str, str]] = []  # (status, title, next_step)


def _report(status: str, title: str, detail: str = "", next_step: str = "") -> None:
    mark = {"ok": "[OK]", "ng": "[NG]", "warn": "[注意]", "skip": "[スキップ]"}[status]
    line = f"{mark} {title}"
    if detail:
        line += f" — {detail}"
    print(line)
    if next_step:
        print(f"      → 次にやること: {next_step}")
    _results.append((status, title, next_step))


def check_python() -> bool:
    v = sys.version_info
    if v >= (3, 10):
        _report("ok", "Python バージョン", f"{v.major}.{v.minor}.{v.micro}")
        return True
    _report("ng", "Python バージョン", f"{v.major}.{v.minor}（3.10以上が必要）",
            "python.org から最新版をインストールし直してください（「Add python.exe to PATH」に必ずチェック）")
    return False


def check_dependencies() -> bool:
    missing = []
    for mod, pip_name in [("flask", "flask"), ("google.auth", "google-auth"),
                          ("googleapiclient", "google-api-python-client"),
                          ("google.analytics.admin", "google-analytics-admin"),
                          ("google.analytics.data", "google-analytics-data"),
                          ("requests", "requests")]:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pip_name)
    if not missing:
        _report("ok", "依存ライブラリ", "すべてインストール済み")
        return True
    _report("ng", "依存ライブラリ", f"不足: {', '.join(missing)}",
            "このフォルダで `pip install -r requirements.txt` を実行してください")
    return False


def check_client_secret() -> bool:
    p = ROOT / "client_secret.json"
    if not p.exists():
        _report("ng", "client_secret.json", "見つかりません",
                "Google Cloud Console で OAuth クライアントID（デスクトップアプリ）を作成し、"
                "JSONをダウンロードして client_secret.json という名前でこのフォルダに置いてください（READMEステップ4）")
        return False
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        _report("ng", "client_secret.json", "JSONとして読めません",
                "ダウンロードし直して上書きしてください（メモ帳で編集すると壊れることがあります）")
        return False
    if "installed" not in d and "web" not in d:
        _report("ng", "client_secret.json", "OAuthクライアントの形式ではありません",
                "アプリケーションの種類は「デスクトップアプリ」で作成してください")
        return False
    _report("ok", "client_secret.json", "形式OK")
    return True


def check_accounts() -> list[str]:
    try:
        from auth import list_accounts
        emails = list_accounts()
    except Exception as e:
        _report("ng", "Google アカウント認証", f"確認に失敗: {type(e).__name__}",
                "`python auth.py add` を実行して認証してください")
        return []
    if not emails:
        _report("ng", "Google アカウント認証", "認証済みアカウントがありません",
                "`python auth.py add` を実行し、管理に使うGmailでログインしてください（複数ある場合は繰り返し）")
        return []
    _report("ok", "Google アカウント認証", f"{len(emails)}件: {', '.join(emails)}")
    return emails


def _classify_api_error(e: Exception, api_name: str, enable_url: str) -> tuple[str, str]:
    msg = str(e)
    if "SERVICE_DISABLED" in msg or "has not been used" in msg or "accessNotConfigured" in msg:
        return (f"{api_name} が未有効化です",
                f"ブラウザで {enable_url} を開き「有効にする」を押してください（反映まで1〜2分）")
    if "invalid_grant" in msg or "invalid_scope" in msg or "RefreshError" in type(e).__name__:
        return ("認証トークンが失効しています",
                "`python auth.py add` で同じGmailを再認証してください")
    if "403" in msg:
        return ("権限エラー（403）",
                "そのGmailにGA4/GTM/SCへのアクセス権があるか確認してください")
    return (f"{type(e).__name__}: {msg[:120]}",
            "ネットワーク・プロキシ設定を確認のうえ、再実行してください")


def check_apis(email: str) -> None:
    try:
        from auth import load_credentials
        creds = load_credentials(email)
    except Exception as e:
        _report("ng", "API疎通", f"認証情報の読み込みに失敗: {type(e).__name__}",
                "`python auth.py add` で再認証してください")
        return

    tests = [
        ("GA4 Admin API",
         "https://console.cloud.google.com/apis/library/analyticsadmin.googleapis.com",
         lambda: __import__("collectors.ga4_admin", fromlist=["x"]).list_accounts(creds)),
        ("Tag Manager API",
         "https://console.cloud.google.com/apis/library/tagmanager.googleapis.com",
         lambda: __import__("collectors.gtm", fromlist=["x"]).list_accounts(creds)),
        ("Search Console API",
         "https://console.cloud.google.com/apis/library/searchconsole.googleapis.com",
         lambda: __import__("collectors.sc", fromlist=["x"]).list_sites(creds)),
    ]
    for name, url, fn in tests:
        try:
            result = fn()
            _report("ok", f"API疎通: {name}", f"応答あり（{len(result)}件）")
        except Exception as e:
            detail, step = _classify_api_error(e, name, url)
            _report("ng", f"API疎通: {name}", detail, step)


def check_inventory() -> None:
    p = ROOT / "data" / "inventory.json"
    if not p.exists():
        _report("warn", "収集データ", "まだデータ収集が実行されていません",
                "`python indexer.py` を実行してください（プロパティ数により数十分かかります）")
        return
    try:
        inv = json.loads(p.read_text(encoding="utf-8"))
        gen = inv.get("generated_at") or ""
        n = inv.get("property_count") or len(inv.get("properties") or [])
        age_note = ""
        try:
            dt = datetime.fromisoformat(gen)
            days = (datetime.now(timezone.utc) - dt).days
            if days >= 7:
                age_note = f"（{days}日前と古め。`python indexer.py` で更新を推奨）"
        except Exception:
            pass
        _report("ok", "収集データ", f"プロパティ{n}件 / 最終収集 {gen[:16]} {age_note}")
    except Exception:
        _report("ng", "収集データ", "inventory.json が壊れています",
                "`python indexer.py` を再実行してください")


def check_anthropic() -> None:
    import os
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("ANTHROPIC_API_KEY="):
                key = line.split("=", 1)[1].strip()
                break
    if key.startswith("sk-ant"):
        _report("ok", "Anthropic APIキー（任意）", "設定済み — AI診断が使えます")
    else:
        _report("skip", "Anthropic APIキー（任意）", "未設定 — AI診断以外の全機能は利用できます")


def main() -> int:
    print("=" * 60)
    print("  Analytics Inventory セットアップ診断")
    print("=" * 60)

    ok_py = check_python()
    ok_dep = check_dependencies() if ok_py else False
    if not ok_dep:
        print()
        print("先に上記のNGを解消してから、もう一度 `python doctor.py` を実行してください。")
        return 1

    ok_cs = check_client_secret()
    emails = check_accounts() if ok_cs else []
    if not ok_cs:
        _report("skip", "Google アカウント認証", "client_secret.json の設置後に確認します")
    if emails:
        check_apis(emails[0])
    else:
        _report("skip", "API疎通", "アカウント認証後に確認します")
    check_inventory()
    check_anthropic()

    print()
    ng = [t for s, t, _ in _results if s == "ng"]
    if not ng:
        print("🎉 診断完了: セットアップは正常です。")
        print("   `start_server.bat`（または `python server.py`）で起動し、")
        print("   ブラウザで http://127.0.0.1:8788 を開いてください。")
        return 0
    print(f"診断完了: {len(ng)}件のNGがあります。上の「→ 次にやること」を上から順に対応してください。")
    print("対応後にもう一度 `python doctor.py` を実行すると、続きから確認できます。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
