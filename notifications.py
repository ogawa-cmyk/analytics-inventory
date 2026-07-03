"""週次ヘルスサマリーメール通知。

- 設定は data/notify_settings.json（data/ は .gitignore 済み）に保存。
- SMTP パスワードは設定ファイルに保存するが、環境変数 SMTP_PASSWORD があれば優先。
- server.py 起動時に start_scheduler(loader) を呼ぶと、バックグラウンドスレッドが
  10分おきに送信条件（曜日・時刻・今週未送信）をチェックして自動送信する。
"""
from __future__ import annotations
import json
import os
import smtplib
import threading
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path

import changes
import health
from config import DATA_DIR

SETTINGS_PATH = DATA_DIR / "notify_settings.json"

DEFAULT_SETTINGS = {
    "enabled": False,
    "to": [],                # 送信先メールアドレスのリスト
    "weekday": 0,            # 0=月曜 〜 6=日曜
    "hour": 9,               # 送信時刻（この時刻以降の最初のチェックで送信）
    "smtp_host": "",
    "smtp_port": 587,
    "smtp_security": "tls",  # "tls" | "ssl" | "none"
    "smtp_user": "",
    "smtp_password": "",
    "from_addr": "",
    "from_name": "Analytics Inventory",
    "base_url": "http://127.0.0.1:8788",
    "last_sent_week": None,  # ISO週キー "2026-W27"
    "last_sent_at": None,
}

_lock = threading.Lock()
_scheduler_started = False


# ============================================================
#  設定の読み書き
# ============================================================

def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    if SETTINGS_PATH.exists():
        try:
            s.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return s


def save_settings(new: dict) -> dict:
    """UI からの更新。smtp_password が空文字なら既存値を維持する。"""
    with _lock:
        cur = load_settings()
        for k in ("enabled", "to", "weekday", "hour", "smtp_host", "smtp_port",
                  "smtp_security", "smtp_user", "from_addr", "from_name", "base_url"):
            if k in new:
                cur[k] = new[k]
        pw = new.get("smtp_password")
        if pw:  # 空欄は「変更なし」
            cur["smtp_password"] = pw
        _write(cur)
        return cur


def _write(s: dict) -> None:
    tmp = SETTINGS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, SETTINGS_PATH)


def public_settings() -> dict:
    """パスワードを伏せた設定（UI 表示用）。"""
    s = load_settings()
    s["smtp_password"] = ""
    s["smtp_password_set"] = bool(load_settings().get("smtp_password") or os.environ.get("SMTP_PASSWORD"))
    return s


# ============================================================
#  サマリー構築
# ============================================================

def build_summary(inv: dict) -> dict:
    """enrich 済みインベントリから週次サマリーの素材を組み立てる。"""
    props = inv.get("properties", []) or []
    conts = inv.get("gtm_containers", []) or []
    sc_sites = inv.get("sc_sites", []) or []

    def _bad(items, name_key):
        rows = []
        for x in items:
            if x.get("health_grade") in ("D", "F") or x.get("has_error_alert"):
                rows.append({
                    "name": x.get(name_key) or "",
                    "grade": x.get("health_grade"),
                    "score": x.get("health_score"),
                    "alerts": x.get("alert_count") or 0,
                    "id": x.get("property_id") or x.get("container_id") or x.get("site_hash"),
                })
        rows.sort(key=lambda r: (r["score"] or 0))
        return rows

    ga4_bad = _bad(props, "display_name")
    gtm_bad = _bad(conts, "name")
    sc_bad = _bad(sc_sites, "site_url")

    recent = changes.recent_events(days=7, limit=50)
    counts = changes.summary_counts(recent)

    avg = lambda xs: round(sum((x.get("health_score") or 0) for x in xs) / max(len(xs), 1), 1)
    return {
        "generated_at": inv.get("generated_at"),
        "stats": {
            "n_props": len(props), "n_conts": len(conts), "n_sc": len(sc_sites),
            "avg_prop": avg(props), "avg_cont": avg(conts), "avg_sc": avg(sc_sites),
            "n_ga4_bad": len(ga4_bad), "n_gtm_bad": len(gtm_bad), "n_sc_bad": len(sc_bad),
        },
        "ga4_bad": ga4_bad[:15],
        "gtm_bad": gtm_bad[:15],
        "sc_bad": sc_bad[:15],
        "changes": recent[:30],
        "change_counts": counts,
    }


_SEV_LABEL = {"critical": ("重大", "#c62828"), "warn": ("注意", "#ef6c00"), "info": ("情報", "#546e7a")}
_KIND_LABEL = {"ga4": "GA4", "gtm": "GTM", "sc": "SC"}


def render_html(summary: dict, base_url: str = "") -> str:
    """週次サマリーの HTML メール本文を生成。"""
    st = summary["stats"]
    esc = _esc

    def _table(rows, kind):
        if not rows:
            return '<p style="color:#5a6a5a;font-size:13px;margin:4px 0 16px">問題のある項目はありません 🎉</p>'
        trs = []
        for r in rows:
            trs.append(
                f'<tr>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #dde7dd">{esc(r["name"])}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #dde7dd;text-align:center">'
                f'<span style="background:{"#e53935" if r["grade"]=="F" else "#fb8c00"};color:#fff;'
                f'padding:1px 8px;border-radius:4px;font-weight:700;font-size:11px">{esc(r["grade"])}</span></td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #dde7dd;text-align:right">{r["score"]}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #dde7dd;text-align:right">{r["alerts"]}</td>'
                f'</tr>')
        return (
            '<table style="border-collapse:collapse;width:100%;font-size:13px;margin:4px 0 16px">'
            '<tr style="background:#e8f5e9;color:#1b5e20">'
            '<th style="padding:6px 10px;text-align:left">名前</th>'
            '<th style="padding:6px 10px">グレード</th>'
            '<th style="padding:6px 10px;text-align:right">スコア</th>'
            '<th style="padding:6px 10px;text-align:right">アラート</th></tr>'
            + "".join(trs) + "</table>")

    change_rows = []
    for e in summary.get("changes") or []:
        label, color = _SEV_LABEL.get(e.get("severity"), ("情報", "#546e7a"))
        change_rows.append(
            f'<tr>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #dde7dd;white-space:nowrap">'
            f'<span style="background:{color};color:#fff;padding:1px 7px;border-radius:4px;font-size:11px;font-weight:600">{label}</span></td>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #dde7dd;white-space:nowrap;font-size:11px;color:#5a6a5a">{_KIND_LABEL.get(e.get("kind"), "")}</td>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #dde7dd;font-size:13px">{esc(e.get("entity_name"))}<br>'
            f'<span style="font-size:12px;color:#5a6a5a">{esc(e.get("message"))}</span></td>'
            f'</tr>')
    changes_html = (
        '<table style="border-collapse:collapse;width:100%;margin:4px 0 16px">' + "".join(change_rows) + "</table>"
        if change_rows else
        '<p style="color:#5a6a5a;font-size:13px;margin:4px 0 16px">直近7日間に検出された変化はありません。</p>')

    cc = summary.get("change_counts") or {}
    kpi_cells = (
        _kpi_cell("GA4プロパティ", st["n_props"],
                  "平均 {}点 / 要対応 {}件".format(st["avg_prop"], st["n_ga4_bad"]))
        + _kpi_cell("GTMコンテナ", st["n_conts"],
                    "平均 {}点 / 要対応 {}件".format(st["avg_cont"], st["n_gtm_bad"]))
        + _kpi_cell("SCサイト", st["n_sc"],
                    "平均 {}点 / 要対応 {}件".format(st["avg_sc"], st["n_sc_bad"]))
        + _kpi_cell("変化検出(7日)", cc.get("total", 0),
                    "重大 {} / 注意 {}".format(cc.get("critical", 0), cc.get("warn", 0)))
    )
    kpi = ('<table style="border-collapse:collapse;width:100%;margin:8px 0 20px"><tr>'
           + kpi_cells + '</tr></table>')

    gen = (summary.get("generated_at") or "")[:16].replace("T", " ")
    link = (f'<p style="margin:20px 0 0"><a href="{esc(base_url)}" '
            f'style="color:#2e7d32">→ ダッシュボードを開く</a></p>') if base_url else ""

    return f"""<!doctype html><html><body style="font-family:'Segoe UI',Meiryo,sans-serif;background:#f6f9f6;padding:20px;color:#1f2a1f">
<div style="max-width:720px;margin:0 auto;background:#fff;border:1px solid #dde7dd;border-radius:8px;padding:24px">
<h1 style="font-size:18px;color:#1b5e20;margin:0 0 4px">📊 Analytics Inventory 週次サマリー</h1>
<p style="font-size:12px;color:#5a6a5a;margin:0 0 16px">最終データ収集: {esc(gen)}</p>
{kpi}
<h2 style="font-size:15px;color:#1b5e20;border-bottom:2px solid #e8f5e9;padding-bottom:6px">🔔 直近7日間の変化</h2>
{changes_html}
<h2 style="font-size:15px;color:#1b5e20;border-bottom:2px solid #e8f5e9;padding-bottom:6px">⚠ 要対応 GA4プロパティ（D/F・エラー）</h2>
{_table(summary.get("ga4_bad"), "ga4")}
<h2 style="font-size:15px;color:#1b5e20;border-bottom:2px solid #e8f5e9;padding-bottom:6px">⚠ 要対応 GTMコンテナ</h2>
{_table(summary.get("gtm_bad"), "gtm")}
<h2 style="font-size:15px;color:#1b5e20;border-bottom:2px solid #e8f5e9;padding-bottom:6px">⚠ 要対応 Search Consoleサイト</h2>
{_table(summary.get("sc_bad"), "sc")}
{link}
<p style="font-size:11px;color:#8aa08a;margin-top:24px">このメールは Analytics Inventory の週次通知設定により自動送信されています。</p>
</div></body></html>"""


def _kpi_cell(label: str, value, sub: str) -> str:
    return (f'<td style="background:#f4f9f4;border:1px solid #dde7dd;border-radius:6px;'
            f'padding:10px 12px;text-align:center;width:25%">'
            f'<div style="font-size:22px;font-weight:700;color:#2e7d32">{value}</div>'
            f'<div style="font-size:11px;color:#5a6a5a">{_esc(label)}</div>'
            f'<div style="font-size:10px;color:#8aa08a">{_esc(sub)}</div></td>')


def _esc(s) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ============================================================
#  送信
# ============================================================

def send_email(subject: str, html: str, settings: dict | None = None) -> dict:
    """設定済み SMTP でメール送信。成功時 {ok: True}、失敗時 {ok: False, error}。"""
    s = settings or load_settings()
    to = s.get("to") or []
    if isinstance(to, str):
        to = [x.strip() for x in to.replace(";", ",").split(",") if x.strip()]
    if not to:
        return {"ok": False, "error": "送信先(to)が未設定です"}
    host = s.get("smtp_host")
    if not host:
        return {"ok": False, "error": "SMTPホストが未設定です"}
    port = int(s.get("smtp_port") or 587)
    user = s.get("smtp_user") or ""
    password = os.environ.get("SMTP_PASSWORD") or s.get("smtp_password") or ""
    from_addr = s.get("from_addr") or user
    if not from_addr:
        return {"ok": False, "error": "送信元(from)が未設定です"}

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((s.get("from_name") or "Analytics Inventory", from_addr))
    msg["To"] = ", ".join(to)
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        security = (s.get("smtp_security") or "tls").lower()
        if security == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
        try:
            server.ehlo()
            if security == "tls":
                server.starttls()
                server.ehlo()
            if user and password:
                server.login(user, password)
            server.sendmail(from_addr, to, msg.as_string())
        finally:
            server.quit()
        return {"ok": True, "to": to}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}


def send_weekly(loader, force: bool = False, test: bool = False) -> dict:
    """週次サマリーを構築して送信。loader() は enrich 済みインベントリを返す関数。"""
    s = load_settings()
    if not force and not s.get("enabled"):
        return {"ok": False, "error": "通知が無効です"}
    inv = loader()
    summary = build_summary(inv)
    html = render_html(summary, base_url=s.get("base_url") or "")
    today = datetime.now().strftime("%Y-%m-%d")
    prefix = "[テスト] " if test else ""
    n_bad = summary["stats"]["n_ga4_bad"] + summary["stats"]["n_gtm_bad"] + summary["stats"]["n_sc_bad"]
    subject = f"{prefix}📊 週次サマリー {today} — 要対応 {n_bad}件 / 変化 {summary['change_counts']['total']}件"
    result = send_email(subject, html, settings=s)
    if result.get("ok") and not test:
        with _lock:
            cur = load_settings()
            cur["last_sent_week"] = datetime.now().strftime("%G-W%V")
            cur["last_sent_at"] = datetime.now(timezone.utc).isoformat()
            _write(cur)
    return result


# ============================================================
#  スケジューラ
# ============================================================

def start_scheduler(loader, interval_sec: int = 600) -> None:
    """デーモンスレッドで送信条件を定期チェックする。多重起動は無視。"""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def _loop():
        import time as _time
        while True:
            try:
                _tick(loader)
            except Exception:
                pass
            _time.sleep(interval_sec)

    threading.Thread(target=_loop, daemon=True, name="notify-scheduler").start()


def _tick(loader) -> None:
    s = load_settings()
    if not s.get("enabled"):
        return
    now = datetime.now()
    if now.weekday() != int(s.get("weekday") or 0):
        return
    if now.hour < int(s.get("hour") or 9):
        return
    week_key = now.strftime("%G-W%V")
    if s.get("last_sent_week") == week_key:
        return
    send_weekly(loader)
