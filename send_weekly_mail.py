"""週次サマリーメールを CLI から送信する（Windowsタスクスケジューラ用）。

サーバー（server.py）を常時起動していなくても、タスクスケジューラから
run_weekly_mail.bat 経由でこのスクリプトを毎日実行しておけば、
通知設定の曜日に一致した日だけメールが送信される。

使い方:
  python send_weekly_mail.py             ガード付き送信（有効・曜日一致・今週未送信のときだけ送る）
  python send_weekly_mail.py --force     ガードを無視して今すぐ送信
  python send_weekly_mail.py --test      [テスト] 付きで今すぐ送信（last_sent は更新しない）
  python send_weekly_mail.py --register  タスクスケジューラに毎日実行タスクを登録/更新
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import notifications

TASK_NAME = "AnalyticsInventory-WeeklyMail"


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def _loader():
    """server.py と同じ enrich 済みインベントリを返す。"""
    import server
    return server._load_inventory()


def run(force: bool = False, test: bool = False) -> int:
    s = notifications.load_settings()

    if not force and not test:
        if not s.get("enabled"):
            _log("SKIP: 通知が無効です（/settings/notifications で有効化してください）")
            return 0
        now = datetime.now()
        if now.weekday() != int(s.get("weekday") or 0):
            names = ["月", "火", "水", "木", "金", "土", "日"]
            _log(f"SKIP: 今日は送信曜日ではありません（設定: {names[int(s.get('weekday') or 0)]}曜）")
            return 0
        week_key = now.strftime("%G-W%V")
        if s.get("last_sent_week") == week_key:
            _log(f"SKIP: 今週は送信済みです（{s.get('last_sent_at')}）")
            return 0

    _log("週次サマリーを構築して送信します...")
    result = notifications.send_weekly(_loader, force=True, test=test)
    if result.get("ok"):
        _log(f"OK: 送信しました → {', '.join(result.get('to') or [])}")
        return 0
    _log(f"ERROR: 送信失敗 — {result.get('error')}")
    return 1


def register() -> int:
    """タスクスケジューラに毎日実行タスクを登録する（設定時刻の5分後に起動）。

    実際に送信するかどうか（曜日・今週未送信）はスクリプト側が判定するため、
    UI で送信曜日を変えてもタスクの再登録は不要。送信時刻を変えた場合のみ
    もう一度 --register を実行する。
    """
    s = notifications.load_settings()
    st = f"{int(s.get('hour') or 9):02d}:05"
    bat = Path(__file__).parent / "run_weekly_mail.bat"
    cmd = ["schtasks", "/Create", "/F",
           "/TN", TASK_NAME,
           "/TR", f'"{bat}"',
           "/SC", "DAILY",
           "/ST", st]
    _log(f"タスクを登録します: {TASK_NAME} — 毎日 {st} に {bat.name} を実行")
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    print(out.strip())
    if r.returncode == 0:
        _log("OK: 登録しました。削除する場合: schtasks /Delete /TN " + TASK_NAME + " /F")
        return 0
    _log("ERROR: 登録に失敗しました")
    return 1


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--register" in args:
        sys.exit(register())
    sys.exit(run(force="--force" in args, test="--test" in args))
