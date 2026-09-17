"""データ品質チェック — 「設定の棚卸し」に対する「データの形からの不備検出」レイヤー。

health.py が summary の設定値（件数・フラグ）を見るのに対し、ここでは収集済みの
実績データ（イベント・ページ・流入・国別）と v1alpha 設定を検査して、
404流入・PII混入・多重計上・機械的アクセスのような「形から分かる異常」を拾う。

設計原則（3つとも守ること）:

1. **データが無いことを「問題なし」と書かない。** 各チェックは必要データセットを
   宣言し、収集できていなければ判定関数に渡す前に「未確認」として止める。
   「欠損時に ok を返すチェックが1つも無いこと」は tests/test_quality.py で固定する。
2. **指摘IDは内容から決まる安定した符号にする**（通し番号はAPIの返却順で変わる）。
   件数・比率のような実行のたびに変わる数字はIDの素に使わない。
3. **「対象なし」と「見て問題なかった」を区別する。** ルール0件・該当なしでも
   state 文に理由を書く。

判定ロジックの多くは super-access-analytics（MIT License,
Copyright (c) TigerMonday Inc. https://github.com/TigerMonday/super-access-analytics-public）
の計測チェック実装を本ツールのデータ構造へ移植したもの。閾値の根拠は各定数のコメントに残す。
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict

# ──────────────────────────────────────
# しきい値（数字を直書きしない — 根拠を書く場所が無くなるため）
# ──────────────────────────────────────

# session_start はセッション開始時に必ず1回飛ぶ。この比を下回るなら基盤タグが
# 一部ページで動いていない（GA4管理画面の「Googleタグが正しく設定されていない」警告と同じ事象）
SESSION_RATIO_WARN = 0.9
SESSION_RATIO_CRITICAL = 0.7

# user_id に既定値("unknown"等)を送ると全アクセスが同一人物に潰れる
UID_COLLAPSE_MIN_EVENTS = 1_000
UID_COLLAPSE_MAX_USERS = 5

# エラーページタイトルの判定。404ページは複数セクションで同じタイトルになるのが正常な挙動
ERROR_PAGE_TITLE_RE = re.compile(r"404|not\s*found|エラー|ページが見つかりません", re.IGNORECASE)
# エラーページ流入を指摘する最低PV（数PVの打ち間違いまで拾うと信用を失う）
ERROR_PAGE_MIN_PV = 50

# タイトル未設定・タイトル固定（SPA疑い）の閾値。
# 「1ページ読んで離脱する普通のサイト」と区別できる確度の高い兆候だけに絞る
SPA_TITLE_MIN_PV = 10
SPA_TITLE_UNSET_MIN_PV = 100
SPA_TITLE_STUCK_MIN_SECTIONS = 3
SPA_TITLE_STUCK_MIN_PATHS = 10
SPA_TITLE_STUCK_MIN_PV = 200

# URL表記の分裂（/ と /index.html 等）。各表記5セッション以上・組合計50以上に限定
DUP_URL_MIN_SESSIONS = 50
DUP_URL_MIN_MEMBER_SESSIONS = 5

# 申込の手前を数えるイベント（GA4推奨イベント名。独自名でも部分一致で拾う）
MICRO_EVENT_HINTS = (
    "view_item", "select_item", "add_to_cart", "add_to_wishlist", "add_to_compare",
    "view_form", "form_start", "begin_checkout", "view_search_results",
)
MICRO_EVENT_ENOUGH = 4

# 電話タップ。「電話」だけで拾わない（実測で「テレビ電話相談予約」を誤検出した事故があるため、
# クリック・タップの語まで揃って初めて電話タップとみなす）
TEL_EVENT_RE = re.compile(
    r"(?=.*(?:^tel|[_\-]tel|tel[_\-]|phone|電話|TEL))(?=.*(?:click|tap|クリック|タップ))",
    re.IGNORECASE,
)

# サイト内の通知・ポップアップに使われがちな medium（外から来た印ではない疑いが濃い）
INTERNAL_MEDIUM_RE = re.compile(r"^(pop|popup|modal|banner_in|inapp|in-app|notice)", re.IGNORECASE)

# GA4が既定チャネルに分類できなかった流入。1%を超えると月数千セッション規模で読めなくなる
UNASSIGNED_SESSION_RATIO = 0.01
UNASSIGNED_MIN_SESSIONS = 50

# UTM表記ゆれ。同じ意味の source/medium が別表記で混在すると流入が分裂して読めなくなる。
# 大文字小文字違いは確実な表記ゆれ、同義語ファミリは「疑い」に留める（別施策の可能性があるため）
VARIANT_MIN_SESSIONS = 10
MEDIUM_SYNONYM_FAMILIES = (
    frozenset({"email", "mail", "e-mail"}),
    frozenset({"social", "sns"}),
    frozenset({"cpc", "ppc", "paidsearch", "paid_search"}),
    frozenset({"banner", "display"}),
)

# source/medium が (not set) の流入 = アトリビューション情報の欠落（utm未設定・計測不備の兆候）
NOTSET_SESSION_RATIO = 0.01
NOTSET_MIN_SESSIONS = 50

# 海外ノイズ。国名だけでは絶対に判定しない — 集中(5%かつ100S)に加えて
# 行動品質の異常が2信号以上重なった場合だけ「疑い」とする
FOREIGN_MIN_SESSION_RATIO = 0.05
FOREIGN_MIN_SESSIONS = 100
FOREIGN_RATE_DIVISOR = 10
FOREIGN_MAX_AVG_ENGAGEMENT_SECONDS = 3.0
FOREIGN_ENGAGEMENT_SITE_RATIO = 0.10
FOREIGN_MIN_BOUNCE_RATE = 0.95
FOREIGN_MIN_SIGNALS = 2

# イベント名の規約。予約プレフィックスは外部連携・将来の衝突リスク
RESERVED_EVENT_PREFIXES = ("firebase_", "ga_", "google_", "gtag.")
EVENT_NAME_MAX_LEN = 40
JAPANESE_RE = re.compile(r"[ぁ-んァ-ヶ一-龠Ａ-Ｚａ-ｚ０-９　]")

# PII検出。過検出しないことを最優先にする —
# 「文脈（隣接ディレクトリ名）」と「形（長さ・英数字混在）」の両方が揃ったときだけ判定する
PII_TOKEN_MIN_LEN = 20
PII_TOKEN_STRONG_CONTEXT_RE = re.compile(
    r"^(reset[-_]?password|password[-_]?reset|forgot[-_]?password|"
    r"activat(?:e|ion)|confirm(?:ation)?|verif(?:y|ication)|"
    r"invit(?:e|ation)|unsubscribe|magic[-_]?link|login[-_]?link)$",
    re.IGNORECASE,
)
PII_TOKEN_WEAK_CONTEXT_RE = re.compile(r"^(auth|token|session|sid)$", re.IGNORECASE)
_PII_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE,
)
_PII_TOKEN_CHARS_RE = re.compile(r"^[A-Za-z0-9_-]+$")
PII_QUERY_KEY_STRONG_RE = re.compile(
    r"^(email|mail|tel|phone|phone_?number|password|pwd|passwd|"
    r"token|access_?token|refresh_?token|auth_?token)$", re.IGNORECASE,
)
PII_QUERY_KEY_WEAK_RE = re.compile(
    r"^(sid|session_?id|name|fullname|full_?name|last_?name|first_?name)$", re.IGNORECASE,
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+(?:@|%40)[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# 電話番号は区切り文字があるものに限る（区切りの無い数字列はIDと区別が付かない）
PHONE_RE = re.compile(r"(?:\+\d{1,3}[-.\s]?)?(?:\d{2,4}[-.\s]\d{2,4}[-.\s]\d{3,4})")


# ──────────────────────────────────────
# 安定ID
# ──────────────────────────────────────

_SEP = "\x1f"  # 対象名に日本語・URL・記号が入るため、表示に使わない制御文字で区切る
_DIGITS = 8


def make_id(*parts: str) -> str:
    """識別要素（カテゴリ・対象名など。件数・比率は不可）から安定したIDを作る。"""
    basis = _SEP.join(p or "" for p in parts)
    return "q-" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:_DIGITS]


def dedupe_ids(findings: list[dict]) -> list[dict]:
    """識別要素の取りこぼしで同じIDが複数件に付いた場合の最終防衛。

    実行順に依存しない安定ソートで振り直すため、入力順が変わっても結果は変わらない。
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for f in findings:
        groups[f.get("id", "")].append(f)
    for base_id, members in groups.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda f: (f.get("title", ""), f.get("message", "")))
        for i, f in enumerate(members):
            if i > 0:
                f["id"] = f"{base_id}-{i}"
    return findings


_SEVERITY_LEVEL = {"critical": "error", "high": "warn", "medium": "warn", "low": "info"}


def _f(severity: str, category: str, title: str, message: str, fix: str,
       extra: str = "") -> dict:
    """1件の指摘 dict を作る。id は内容（件数以外）から決まる。"""
    return {
        "id": make_id(category, title, extra),
        "severity": severity,
        "level": _SEVERITY_LEVEL.get(severity, "info"),
        "category": category,
        "title": title,
        "message": message,
        "fix": fix,
    }


def _judgement_from(findings: list[dict], ok_state: str) -> tuple[str, str]:
    """findings から判定を丸める。critical/high → ng、medium/low → warn。"""
    if not findings:
        return "ok", ok_state
    sev = {f["severity"] for f in findings}
    if sev & {"critical", "high"}:
        return "ng", findings[0]["message"]
    return "warn", findings[0]["message"]


# ──────────────────────────────────────
# データ取り出しヘルパー
# ──────────────────────────────────────

def _event_rows(detail: dict) -> list[dict]:
    rows = detail.get("events") or []
    return [r for r in rows if isinstance(r, dict) and "_error" not in r]


def _page_rows(detail: dict) -> list[dict]:
    return (detail.get("pages") or {}).get("rows") or []


def _traffic_rows(detail: dict) -> list[dict]:
    return (detail.get("traffic") or {}).get("rows") or []


def _country_rows(detail: dict) -> list[dict]:
    return (detail.get("countries") or {}).get("rows") or []


def _hostname_rows(detail: dict) -> list[dict]:
    return (detail.get("hostnames") or {}).get("rows") or []


def self_referral_suspects(detail: dict) -> list[str]:
    """traffic の source のうち、自プロパティの計測ホスト名と一致するもの（自己参照の疑い）。

    indexer もこれを使い、疑いがある場合だけ発生LPを追加取得する（quality と indexer で
    判定を二重実装しない）。www. 有無の違いは同一ホストとみなす。
    """
    hosts = {str(r.get("hostname", "")).lower().strip() for r in _hostname_rows(detail)}
    hosts.discard("")
    hosts.discard("(not set)")
    variants: set[str] = set()
    for h in hosts:
        variants.add(h)
        variants.add(h[4:] if h.startswith("www.") else "www." + h)
    out = {str(r.get("source", "")) for r in _traffic_rows(detail)
           if str(r.get("source", "")).lower().strip() in variants}
    return sorted(out)


def _top_section(path: str) -> str:
    """パスの先頭セクション（`/blog/a` → `/blog`）。ルート直下は `/`。"""
    parts = [p for p in path.split("?")[0].split("/") if p]
    return "/" + parts[0] if parts else "/"


# ──────────────────────────────────────
# 個別チェック — いずれも fn(detail) -> (judgement, state, findings)
# ──────────────────────────────────────

def check_session_health(detail: dict):
    sessions = int((detail.get("totals_30d") or {}).get("sessions") or 0)
    rows = _event_rows(detail)
    if sessions <= 0:
        return "ok", "セッション0のため対象なし", []
    counts = {r.get("event_name", ""): int(r.get("event_count", 0) or 0) for r in rows}
    n = counts.get("session_start", 0)
    ratio = n / sessions
    if ratio >= SESSION_RATIO_WARN:
        return "ok", f"session_start はセッション数の{ratio:.2f}倍（正常）", []
    if n == 0:
        # 一覧は上位500件。溢れている場合は0件と断定しない
        if "session_start" not in counts and len(rows) >= 500:
            f = _f("high", "計測漏れ", "session_start が上位500件に現れない",
                   f"session_start が直近30日のイベント上位{len(rows)}件に現れない。"
                   "一覧が上限に達しているため0件とは断定できないが、"
                   "セッションごとに1回は飛ぶはずのイベントとしては異常に少ない",
                   "session_start の実績を単独で確認する。0件なら基盤タグ（Googleタグ）が"
                   "「初期化」または「全ページ」で発火しているかを見る")
        else:
            f = _f("critical", "計測漏れ", "session_start が1件も記録されていない",
                   f"{sessions:,}セッションに対して session_start が1件も無い。"
                   "GA4が自動収集するイベントなので、送られていないのは基盤タグが動いていないということ",
                   "GA4の基盤タグ（Googleタグ）がページに存在し「初期化」または「全ページ」で"
                   "発火しているかを最初に確認する。UAタグの「GA4にも送信」経由で届いている場合、"
                   "この経路ではエンゲージメント時間も拡張計測も働かない")
        return "ng", f["message"], [f]
    severity = "critical" if ratio < SESSION_RATIO_CRITICAL else "high"
    f = _f(severity, "計測漏れ", "session_start がセッション数に対して少なすぎる",
           f"session_start がセッション数の{ratio:.2f}倍しかない（{n:,}件 / {sessions:,}セッション）。"
           f"セッションの{(1 - ratio) * 100:.0f}%で発火していない",
           "基盤タグが「初期化」または「全ページ」で発火しているか確認する。"
           "DOM Ready や特定のカスタムイベントに紐づいていると、その前のイベントと離脱が落ちる")
    return "ng", f["message"], [f]


def check_user_id_collapse(detail: dict):
    out = []
    for r in _event_rows(detail):
        name = r.get("event_name", "")
        n = int(r.get("event_count", 0) or 0)
        users = r.get("total_users")
        if users is None or not name:
            continue
        if n >= UID_COLLAPSE_MIN_EVENTS and int(users or 0) <= UID_COLLAPSE_MAX_USERS:
            out.append(_f(
                "critical", "ユーザー識別", f"`{name}` のユーザー数が異常に少ない",
                f"`{name}` は{n:,}件に対してユーザー数が{int(users or 0)}人。"
                "user_id に定数（既定値）が送られている疑いが強い",
                "GTMの user_id 変数の既定値を外し、未ログイン時はパラメータ自体を送らない。"
                "DebugView で uid の実値を確認する", extra=name))
    j, s = _judgement_from(out, "件数とユーザー数の極端な乖離は見つからない")
    return j, s, out


def check_event_names(detail: dict):
    rows = _event_rows(detail)
    if not rows:
        return "ok", "受信イベントなし（対象なし）", []
    high, medium, low = [], [], []
    for r in rows:
        name = r.get("event_name", "")
        if not name or name.startswith("gtm."):
            continue  # gtm.* は別チェック（GTM内部イベント）で扱う
        if JAPANESE_RE.search(name):
            # 日本語はGA4で受信できるため「使用可能文字外」とは分け、外部連携リスクとして medium
            medium.append(name)
        elif (name.startswith(RESERVED_EVENT_PREFIXES) or len(name) > EVENT_NAME_MAX_LEN
                or not re.fullmatch(r"[A-Za-z0-9_-]+", name) or re.match(r"[0-9_]", name)):
            # 予約プレフィックス・40文字超・使用可能文字外・数字/アンダースコア始まり
            high.append(name)
        elif not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            # 大文字・ハイフンのみ。受信は止まらないため参考情報
            low.append(name)
    out = []
    if high:
        names = "・".join(sorted(high)[:5])
        out.append(_f("high", "命名規則", "予約プレフィックス・40文字超のイベント名",
                      f"予約プレフィックス（firebase_/ga_/google_/gtag.）または40文字超のイベント名が"
                      f"{len(high)}種ある（{names}）。将来の衝突・切り捨てのリスクがある",
                      "該当イベントの名称を変更する（既存集計への影響を確認してから）"))
    if medium:
        names = "・".join(sorted(medium)[:5])
        out.append(_f("medium", "命名規則", "日本語・全角のイベント名",
                      f"日本語・全角文字を含むイベント名が{len(medium)}種ある（{names}）。"
                      "GA4で受信できても、BigQueryエクスポート先や外部ツール連携でエラー・非対応になることがある",
                      "英数字とアンダースコアの名称へ変更する。既存の集計・連携への依存を確認してから"))
    if low:
        names = "・".join(sorted(low)[:5])
        out.append(_f("low", "命名規則", "大文字・記号を含むイベント名（参考）",
                      f"大文字・ハイフン等を含むイベント名が{len(low)}種ある（{names}）。"
                      "受信・分析を止める問題ではなく修正必須ではない。同じ意味のイベントを"
                      "別表記で追加すると集計が分かれるため、新規作成時だけ表記を揃える",
                      "対応不要。新しく追加するイベントの命名時に参考にする"))
    j, s = _judgement_from(out, f"受信{len(rows)}種に規約上の問題は見つからない")
    return j, s, out


def check_gtm_internal_events(detail: dict):
    rows = [r for r in _event_rows(detail) if r.get("event_name", "").startswith("gtm.")]
    if not rows:
        return "ok", "GTM内部イベント（gtm.*）のGA4送信は見つからない", []
    total = sum(int(r.get("event_count", 0) or 0) for r in rows)
    names = "・".join(sorted(r.get("event_name", "") for r in rows)[:5])
    f = _f("medium", "計測ノイズ", "GTM内部イベントがGA4へ送信されている",
           f"GTM内部イベント（{names}）が計{total:,}件GA4へ送られている。"
           "イベント名の枠（500種上限）を消費し、集計のノイズになる",
           "GTMのGA4イベントタグのイベント名設定を確認し、gtm.* をそのまま送らない")
    return "warn", f["message"], [f]


def check_micro_events(detail: dict):
    names = [r.get("event_name", "") for r in _event_rows(detail)]
    if not names:
        return "ok", "受信イベントなし（対象なし）", []
    found = [h for h in MICRO_EVENT_HINTS if any(h in n for n in names)]
    form_named = [n for n in names if "form" in n.lower() or "フォーム" in n]
    reason = "申込に至らなかった人がどの段階で離脱したか追えず、CVR改善の打ち手が絞れない"
    if len(found) >= MICRO_EVENT_ENOUGH:
        return "ok", f"申込の手前を{len(found)}種計測している", []
    if form_named:
        return ("warn",
                f"フォーム関連の名前を持つイベントを{len(form_named)}種受信。"
                "各フォームとの対応と中間操作の網羅性は未確認", [])
    if found:
        f = _f("medium", "計測設計", "申込手前の計測が少ない",
               f"申込の手前の計測が{len(found)}種しかない（{'・'.join(found)}）。{reason}",
               "view_item / form_start / begin_checkout など、成果に至る中間行動の計測を追加する")
    else:
        f = _f("medium", "計測設計", "申込手前を計測していない",
               f"申込の手前（閲覧・検索・カート追加・フォーム開始など）を計測していない。{reason}",
               "view_item / form_start / begin_checkout など、成果に至る中間行動の計測を追加する")
    return "warn", f["message"], [f]


def check_tel_key_event(detail: dict):
    key_events = {k.get("event_name", "") for k in (detail.get("key_events") or [])}
    tel = sorted(
        r.get("event_name", "") for r in _event_rows(detail)
        if TEL_EVENT_RE.search(r.get("event_name", ""))
        and r.get("event_name", "") not in key_events
    )
    if not tel:
        return "ok", "未登録の電話タップイベントは見つからない", []
    f = _f("medium", "成果設定", "電話タップがキーイベント未登録",
           f"電話タップに見えるイベント（{'・'.join(tel[:3])}）を受信しているが、"
           "キーイベントに登録されていない。電話経由の成果が集計から漏れる",
           "業務上の成果に当たるなら、GA4管理画面でキーイベントに登録する")
    return "warn", f["message"], [f]


def check_error_pages(detail: dict):
    rows = _page_rows(detail)
    by_title: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for r in rows:
        title = str(r.get("page_title") or "").strip()
        path = r.get("page_path", "")
        if not title or not path or path.startswith("("):
            continue
        if ERROR_PAGE_TITLE_RE.search(title):
            by_title[title].append((path, int(r.get("views", 0) or 0)))
    out = []
    for title, entries in by_title.items():
        total_pv = sum(pv for _, pv in entries)
        if total_pv < ERROR_PAGE_MIN_PV:
            continue
        entries.sort(key=lambda x: -x[1])
        top = ", ".join(f"{p}({pv:,}PV)" for p, pv in entries[:4])
        out.append(_f(
            "medium", "リンク切れ", f"エラーページへの流入（{title[:40]}）",
            f"エラーページのタイトル「{title}」が{len(entries)}件のURL・計{total_pv:,}PVで"
            f"使われている（{top}）。存在しないURLへのアクセス（404）の疑いがある",
            "リンク元（他ページ・外部サイト・広告・過去URLの変更）を確認し、"
            "正しいURLへのリダイレクトまたはリンクの修正を検討する", extra=title))
    j, s = _judgement_from(out, "エラーページタイトルへのまとまった流入は見つからない")
    return j, s, out


def check_title_unset(detail: dict):
    rows = _page_rows(detail)
    unset = [(r.get("page_path", ""), int(r.get("views", 0) or 0))
             for r in rows
             if str(r.get("page_title") or "").strip() in ("", "(not set)")
             and r.get("page_path", "") and not r.get("page_path", "").startswith("(")
             and int(r.get("views", 0) or 0) > 0]
    total = sum(pv for _, pv in unset)
    if total < SPA_TITLE_UNSET_MIN_PV:
        return "ok", "タイトル未設定のまま実績のあるページは見つからない", []
    unset.sort(key=lambda x: -x[1])
    top = ", ".join(f"{p}({pv:,}PV)" for p, pv in unset[:4])
    f = _f("high", "SPA計測ギャップ", "ページタイトルが未設定のまま実績がある",
           f"タイトルが空または (not set) のページが{len(unset)}件・計{total:,}PVある（{top}）。"
           "SPAで画面遷移のたびに document.title を更新していない疑いがある",
           "画面遷移のたびに document.title を更新する（またはGA4の page_title を明示的に上書きする）")
    return "ng", f["message"], [f]


def check_title_stuck(detail: dict):
    rows = _page_rows(detail)
    by_title: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for r in rows:
        title = str(r.get("page_title") or "").strip()
        path = r.get("page_path", "")
        pv = int(r.get("views", 0) or 0)
        if (not title or title == "(not set)" or not path or path.startswith("(")
                or pv < SPA_TITLE_MIN_PV or ERROR_PAGE_TITLE_RE.search(title)):
            continue  # エラーページは check_error_pages が担当（同じ事実から2つの指摘を立てない）
        by_title[title].append((path, pv))
    out = []
    for title, entries in by_title.items():
        sections = {_top_section(p) for p, _ in entries}
        total_pv = sum(pv for _, pv in entries)
        if (len(entries) < SPA_TITLE_STUCK_MIN_PATHS
                or len(sections) < SPA_TITLE_STUCK_MIN_SECTIONS
                or total_pv < SPA_TITLE_STUCK_MIN_PV):
            continue
        entries.sort(key=lambda x: -x[1])
        top = ", ".join(f"{p}({pv:,}PV)" for p, pv in entries[:4])
        out.append(_f(
            "high", "SPA計測ギャップ", f"同一タイトルがセクションを跨いで使い回されている（{title[:40]}）",
            f"タイトル「{title}」が{len(sections)}個の異なるセクションにまたがる{len(entries)}件のURL"
            f"（計{total_pv:,}PV）で同じまま使われている（{top}）。SPAで画面遷移時にタイトルを"
            "更新していない、またはURLと表示内容が対応していない疑いがある",
            "画面遷移のたびに document.title をページ内容に合わせて更新する。"
            "ページネーション等の同一セクション内の共通タイトルは対象外にしている", extra=title))
    j, s = _judgement_from(out, "セクションを跨ぐタイトルの使い回しは見つからない")
    return j, s, out


def _url_variant_key(path: str) -> str:
    """末尾表記・拡張子の違いを正規化した比較キー。拾えるのはこの2種の違いだけ。"""
    p = path.split("?")[0]
    if p.endswith("/index.html"):
        p = p[: -len("index.html")]
    for ext in (".html", ".htm"):
        if p.endswith(ext):
            p = p[: -len(ext)]
    return p.rstrip("/") or "/"


def check_duplicate_page_urls(detail: dict):
    sessions: dict[str, int] = defaultdict(int)
    pv: dict[str, int] = defaultdict(int)
    for r in _page_rows(detail):
        path = r.get("page_path", "")
        if not path or path.startswith("("):
            continue
        sessions[path] += int(r.get("sessions", 0) or 0)
        pv[path] += int(r.get("views", 0) or 0)
    groups: dict[str, list[str]] = defaultdict(list)
    for path in sessions:
        groups[_url_variant_key(path)].append(path)
    hits = []
    for key, members in groups.items():
        members = sorted(m for m in members if sessions.get(m, 0) > 0)
        if len(members) < 2:
            continue
        total = sum(sessions[m] for m in members)
        if min(sessions[m] for m in members) < DUP_URL_MIN_MEMBER_SESSIONS or total < DUP_URL_MIN_SESSIONS:
            continue
        hits.append((total, members))
    if not hits:
        return "ok", "末尾表記・拡張子違いで分裂しているURLは見つからない", []
    hits.sort(key=lambda g: -g[0])
    detail_txt = "、".join(
        " / ".join(f"`{m}`({pv[m]:,}PV)" for m in members) for _, members in hits[:5]
    )
    f = _f("low", "レポートの分裂", f"URL表記の分裂が{len(hits)}組",
           f"末尾表記・拡張子等が異なるURL候補が{len(hits)}組ある: {detail_txt}"
           f"{f'（ほか{len(hits) - 5}組）' if len(hits) > 5 else ''}。"
           "ページ別レポートで数字が割れる。各表記5セッション以上・組合計50セッション以上に限定している",
           "両URLの内容・リダイレクトを確認し、同じページなら正規URLへの誘導や集計時の正規化を検討する")
    return "warn", f["message"], [f]


def _looks_like_pii_token(segment: str) -> bool:
    if _PII_UUID_RE.fullmatch(segment):
        return True
    if len(segment) < PII_TOKEN_MIN_LEN or not _PII_TOKEN_CHARS_RE.fullmatch(segment):
        return False
    return bool(re.search(r"[0-9]", segment) and re.search(r"[A-Za-z]", segment))


def check_pii_urls(detail: dict):
    """URLパス・クエリの個人情報混入。レポートに実値を出さない（マスクして出す）。"""
    rows = _page_rows(detail)
    token_groups: dict[str, dict] = {}
    email_groups: dict[str, dict] = {}
    phone_groups: dict[str, dict] = {}
    query_key_groups: dict[str, dict] = {}

    for r in rows:
        raw = r.get("page_path", "")
        if not raw or raw.startswith("("):
            continue
        pv = int(r.get("views", 0) or 0)
        path_part, _, query_part = raw.partition("?")
        segments = [s for s in path_part.split("/") if s]
        query: dict[str, str] = {}
        for pair in query_part.split("&"):
            k, _, v = pair.partition("=")
            if k:
                query[k] = v

        # 1) 認証系ディレクトリに続くトークンらしい値
        for i in range(len(segments) - 1):
            seg = segments[i]
            is_strong = bool(PII_TOKEN_STRONG_CONTEXT_RE.match(seg))
            is_weak = bool(PII_TOKEN_WEAK_CONTEXT_RE.match(seg))
            if not (is_strong or is_weak) or not _looks_like_pii_token(segments[i + 1]):
                continue
            prefix = "/" + "/".join(segments[: i + 1])
            g = token_groups.setdefault(prefix, {
                "severity": "critical" if is_strong else "high", "count": 0, "pv": 0})
            g["count"] += 1
            g["pv"] += pv
            break

        # 2) メールアドレス（パス・クエリの値）
        if EMAIL_RE.search(raw):
            sec = _top_section(raw)
            g = email_groups.setdefault(sec, {"count": 0, "pv": 0})
            g["count"] += 1
            g["pv"] += pv

        # 3) 電話番号らしき値（区切り文字あり）
        if PHONE_RE.search(raw):
            sec = _top_section(raw)
            g = phone_groups.setdefault(sec, {"count": 0, "pv": 0})
            g["count"] += 1
            g["pv"] += pv

        # 4) クエリのキー名。値自体が(2)(3)で検出できるものは除外（同じ問題の水増し防止）。
        #    値がハッシュ化済みでメール/電話の形に当たらない場合だけ、キー名から拾う
        for qkey, qval in query.items():
            is_strong = bool(PII_QUERY_KEY_STRONG_RE.match(qkey))
            is_weak = bool(PII_QUERY_KEY_WEAK_RE.match(qkey))
            if not (is_strong or is_weak):
                continue
            if EMAIL_RE.search(qval) or PHONE_RE.search(qval):
                continue
            g = query_key_groups.setdefault(qkey.lower(), {
                "severity": "critical" if is_strong else "high", "count": 0, "pv": 0})
            g["count"] += 1
            g["pv"] += pv

    out = []
    delete_note = "あわせてGA4のユーザーデータ削除リクエストで、記録済みの該当データを消す"
    for prefix, g in sorted(token_groups.items()):
        out.append(_f(g["severity"], "個人情報の混入", f"{prefix}/ 配下にトークンらしき値",
                      f"ページパス `{prefix}/` 配下に、20文字以上・英数字混在のトークンらしき値が"
                      f"そのまま記録されているURLが{g['count']:,}件、計{g['pv']:,}PVある。"
                      "GA4の閲覧権限を持つ全員がこの値を読める状態で、"
                      "GA4の利用規約が禁じる「個人を特定できる情報の送信」に抵触するおそれがある",
                      f"トークンをURLパスに含めない設計に変える。{delete_note}", extra=prefix))
    for sec, g in sorted(email_groups.items()):
        out.append(_f("critical", "個人情報の混入", f"{sec} 配下にメールアドレス",
                      f"`{sec}` 配下のURLに、メールアドレスがそのまま記録されている行が"
                      f"{g['count']:,}件、計{g['pv']:,}PVある",
                      f"メールアドレスをURLに含めない設計に変える。{delete_note}", extra=sec))
    for sec, g in sorted(phone_groups.items()):
        out.append(_f("high", "個人情報の混入", f"{sec} 配下に電話番号らしき値",
                      f"`{sec}` 配下のURLに、電話番号らしき値（区切り文字あり）が記録されている行が"
                      f"{g['count']:,}件、計{g['pv']:,}PVある",
                      f"電話番号をURLに含めない設計に変える。{delete_note}", extra=sec))
    for qkey, g in sorted(query_key_groups.items()):
        out.append(_f(g["severity"], "個人情報の混入", f"クエリキー {qkey}=",
                      f"クエリパラメータのキー名 `{qkey}=` を含むURLが{g['count']:,}件、"
                      f"計{g['pv']:,}PVある。このキー名が実測に出ている時点で"
                      "個人情報をURLに載せる設計になっている疑いが強い",
                      f"`{qkey}` の値に個人情報を入れない設計に変える。{delete_note}", extra=qkey))
    j, s = _judgement_from(out, "URLパス・クエリに個人情報らしき値は見つからない")
    return j, s, out


def check_internal_utm(detail: dict):
    rows = [r for r in _traffic_rows(detail)
            if INTERNAL_MEDIUM_RE.match(str(r.get("medium", "")))]
    if not rows:
        return "ok", "サイト内リンクを示唆する medium は見つからない", []
    n = sum(int(r.get("sessions", 0) or 0) for r in rows)
    mediums = "・".join(sorted({str(r.get("medium", "")) for r in rows})[:5])
    f = _f("high", "流入計測", "サイト内の通知・ポップアップにUTMを付けている疑い",
           f"medium={mediums} の流入が月{n:,}セッションある。サイト内リンクにUTMを付けると"
           "セッションが分断され、本来の流入元が上書きされる",
           "サイト内の誘導は UTM ではなくイベントパラメータ等で計測する")
    return "ng", f["message"], [f]


def check_unassigned(detail: dict):
    rows = _traffic_rows(detail)
    total = sum(int(r.get("sessions", 0) or 0) for r in rows)
    if total <= 0:
        return "ok", "流入セッション0のため対象なし", []
    un = [r for r in rows if str(r.get("channel_group", "")).lower() in ("unassigned", "(other)")]
    n = sum(int(r.get("sessions", 0) or 0) for r in un)
    if n < UNASSIGNED_MIN_SESSIONS or n / total < UNASSIGNED_SESSION_RATIO:
        return "ok", f"Unassigned は{n:,}セッション（{n / total:.1%}）で問題ない水準", []
    pairs = sorted(un, key=lambda r: -int(r.get("sessions", 0) or 0))[:3]
    top = "、".join(f"{r.get('source')}/{r.get('medium')}({int(r.get('sessions', 0) or 0):,})" for r in pairs)
    f = _f("high", "流入計測", "チャネル未分類（Unassigned）が多い",
           f"GA4が既定チャネルに分類できなかった流入が{n:,}セッション（全体の{n / total:.1%}）ある"
           f"（{top}）。UTMの綴り・独自mediumが原因のことが多い",
           "該当の source/medium を確認し、UTMの値を既定チャネルの定義に合わせる")
    return "ng", f["message"], [f]


def check_source_medium_variants(detail: dict):
    """同じ意味の source/medium の表記ゆれ（大文字小文字違い＝確実、同義語＝疑い）。"""
    rows = _traffic_rows(detail)
    findings = []

    # 大文字小文字だけが違う表記（Facebook/facebook 等）。各表記に実流入がある場合だけ問題視
    for field, label in (("source", "参照元(source)"), ("medium", "メディア(medium)")):
        groups: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for r in rows:
            raw = str(r.get(field, ""))
            if not raw or raw.startswith("("):  # (direct)/(none)/(not set) は対象外
                continue
            groups[raw.lower()][raw] += int(r.get("sessions", 0) or 0)
        broken = []
        for variants in groups.values():
            live = {k: v for k, v in variants.items() if v >= VARIANT_MIN_SESSIONS}
            if len(live) >= 2:
                broken.append("・".join(f"{k}({v:,})" for k, v in
                                        sorted(live.items(), key=lambda kv: -kv[1])))
        if broken:
            findings.append(_f(
                "medium", "流入計測", f"{label}の表記ゆれ（大文字小文字の混在）",
                f"同じ{label}が別表記で混在し、流入が分裂している: " + "／".join(broken[:3]),
                "UTMの命名規則を小文字に統一し、既存の配信面のパラメータを修正する",
                extra=field))

    # 同義語ファミリ（email/mail 等）。別施策の可能性もあるため「疑い」に留める
    med_sessions: dict[str, int] = defaultdict(int)
    for r in rows:
        med_sessions[str(r.get("medium", "")).lower()] += int(r.get("sessions", 0) or 0)
    for family in MEDIUM_SYNONYM_FAMILIES:
        live = {m: med_sessions[m] for m in family if med_sessions.get(m, 0) >= VARIANT_MIN_SESSIONS}
        if len(live) >= 2:
            pairs = "・".join(f"{m}({v:,})" for m, v in sorted(live.items(), key=lambda kv: -kv[1]))
            findings.append(_f(
                "low", "流入計測", "メディア(medium)に同義語の混在の疑い",
                f"同じ意味と思われる medium が併存している: {pairs}。"
                "別施策の使い分けでなければ流入が分裂している",
                "意図した使い分けか確認し、同じ施策なら medium を1つに統一する",
                extra="・".join(sorted(family))))

    if not findings:
        return "ok", "source/medium の表記ゆれは見つからない", []
    judgement, state = _judgement_from(findings, "")
    return judgement, state, findings


def check_notset_traffic(detail: dict):
    """source/medium が (not set) の流入 = アトリビューション欠落（utm未設定・計測不備の兆候）。"""
    rows = _traffic_rows(detail)
    total = sum(int(r.get("sessions", 0) or 0) for r in rows)
    if total <= 0:
        return "ok", "流入セッション0のため対象なし", []
    ns = [r for r in rows
          if "(not set)" in (str(r.get("source", "")), str(r.get("medium", "")))]
    n = sum(int(r.get("sessions", 0) or 0) for r in ns)
    if n < NOTSET_MIN_SESSIONS or n / total < NOTSET_SESSION_RATIO:
        return "ok", f"(not set) は{n:,}セッションで問題ない水準", []
    pairs = sorted(ns, key=lambda r: -int(r.get("sessions", 0) or 0))[:3]
    top = "、".join(f"{r.get('source')}/{r.get('medium')}({int(r.get('sessions', 0) or 0):,})"
                    for r in pairs)
    f = _f("medium", "流入計測", "参照元情報が欠落した流入（not set）が多い",
           f"source/medium が (not set) の流入が{n:,}セッション（全体の{n / total:.1%}）ある"
           f"（{top}）。リダイレクトによるパラメータ消失・同意モード・計測タグの発火順が原因になり得る",
           "主要な流入経路（広告・メール・QR）のURLにUTMが残っているか実際に踏んで確認し、"
           "リダイレクト時のパラメータ引き継ぎを見直す")
    return "warn", f["message"], [f]


def check_self_referral(detail: dict):
    """自己参照（自サイトのホスト名が参照元になっている）。発生LPが取れていれば併記する。"""
    hosts = _hostname_rows(detail)
    suspects = self_referral_suspects(detail)
    if not suspects:
        return "ok", f"自ホスト名を名乗る参照元は見つからない（棚卸し済みホスト{len(hosts)}件）", []
    rows = [r for r in _traffic_rows(detail) if str(r.get("source", "")) in set(suspects)]
    n = sum(int(r.get("sessions", 0) or 0) for r in rows)
    lp_rows = (detail.get("self_referral_lps") or {}).get("rows") or []
    if lp_rows:
        lps = "、".join(f"{r.get('landing_page')}({int(r.get('sessions', 0) or 0):,})"
                        for r in lp_rows[:3])
        lp_note = f"主な発生ランディングページ: {lps}"
    else:
        lp_note = "発生ページは未取得（GA4探索で landingPage を確認する）"
    f = _f("high", "流入計測", "自己参照（自サイトが参照元になっている）",
           f"source={'・'.join(suspects[:3])} の流入が月{n:,}セッションある。"
           f"セッションが分断され、本来の流入元が失われている。{lp_note}",
           "発生ページで原因（別ドメインへの往復・決済/SSOの戻り・クロスドメイン設定漏れ）を"
           "特定してから対処する。原因を特定せずに参照元除外へ追加しない")
    return "ng", f["message"], [f]


def check_foreign_noise(detail: dict):
    rows = _country_rows(detail)
    total = sum(int(r.get("sessions", 0) or 0) for r in rows)
    if not total:
        return "ok", "国別セッション0のため対象なし", []
    site_key_events = sum(int(r.get("key_events", 0) or 0) for r in rows)
    site_rate = site_key_events / total
    site_engagement = sum(float(r.get("engagement_duration", 0) or 0) for r in rows) / total
    suspicious, names = 0, []
    for row in rows:
        country = str(row.get("country", ""))
        sessions = int(row.get("sessions", 0) or 0)
        if (country in ("Japan", "日本", "(not set)", "")
                or sessions < FOREIGN_MIN_SESSIONS
                or sessions < total * FOREIGN_MIN_SESSION_RATIO):
            continue
        key_events = int(row.get("key_events", 0) or 0)
        rate = key_events / sessions if sessions else 0
        engagement = float(row.get("engagement_duration", 0) or 0) / sessions if sessions else None
        bounce = row.get("bounce_rate")
        try:
            bounce_rate = float(bounce) if bounce not in (None, "") else None
        except (TypeError, ValueError):
            bounce_rate = None
        signals = []
        if (engagement is not None and engagement <= FOREIGN_MAX_AVG_ENGAGEMENT_SECONDS
                and (not site_engagement or engagement <= site_engagement * FOREIGN_ENGAGEMENT_SITE_RATIO)):
            signals.append(f"平均エンゲージメント{engagement:.1f}秒")
        if bounce_rate is not None and bounce_rate >= FOREIGN_MIN_BOUNCE_RATE:
            signals.append(f"直帰率{bounce_rate:.1%}")
        if key_events == 0 or (site_rate and rate <= site_rate / FOREIGN_RATE_DIVISOR):
            signals.append(f"キーイベント{key_events:,}件")
        if len(signals) < FOREIGN_MIN_SIGNALS:
            continue
        suspicious += sessions
        names.append(f"{country}{sessions:,}（{'・'.join(signals)}）")
    if not suspicious:
        return "ok", "特定国への集中と行動品質の異常の重なりは見つからない", []
    f = _f("medium", "アクセス品質", "海外からの機械的アクセスの疑い",
           f"特定地域へ集中し行動品質の異常が重なるセッションが月{suspicious:,}件"
           f"（{suspicious / total:.1%}・{'／'.join(names[:3])}）",
           "国名だけでは除外しない。サーバー・CDN・WAFログで発信元とUser-Agentを確認し、"
           "機械的アクセスと確認できた範囲だけを除外する")
    return "warn", f["message"], [f]


def check_duplicate_event_rules(detail: dict):
    total = 0
    out = []
    for entry in detail.get("event_create_rules") or []:
        rules = entry.get("rules") or []
        total += len(rules)
        groups: dict[tuple, list[str]] = defaultdict(list)
        for rule in rules:
            sig = tuple(sorted(
                (str(c.get("field", "")), str(c.get("comparison_type", "")),
                 str(c.get("value", "")), str(c.get("negated", False)))
                for c in rule.get("event_conditions", [])
            ))
            if sig:
                groups[sig].append(rule.get("destination_event", ""))
        for sig, dests in groups.items():
            names = sorted(d for d in dests if d)
            if len(names) < 2:
                continue
            out.append(_f(
                "high", "多重計上", f"同一条件のイベント作成ルール（{'、'.join(names[:3])}）",
                f"イベント作成ルール{len(names)}件（{'、'.join(names)}）がまったく同じ条件で"
                "作られている。1回のページ表示から同時に生成されるため多重計上になる",
                "1件に統合する。プラン別・店舗別に分けたいなら、その軸を表す値を条件に入れる",
                extra=str(sig)))
    if total == 0:
        return "ok", "イベント作成ルールが0件（対象なし）", []
    j, s = _judgement_from(out, f"{total}件に条件が完全に同じ重複は無い")
    return j, s, out


def check_retention(detail: dict):
    r = detail.get("retention") or {}
    val = r.get("event_data_retention")
    if val == "FOURTEEN_MONTHS":
        return "ok", "イベントデータ保持は14ヶ月（上限）", []
    label = {"TWO_MONTHS": "2ヶ月"}.get(val, str(val))
    f = _f("medium", "設定", "データ保持期間が上限まで延長されていない",
           f"イベントデータの保持期間が {label}。既定の2ヶ月のままだと、"
           "探索レポートで過去データを遡れる範囲が短くなる（費用はかからず延長できる）",
           "GA4管理画面 → データ設定 → データ保持 で「14か月」へ変更する")
    return "warn", f["message"], [f]


def check_enhanced_measurement(detail: dict):
    entries = [e for e in (detail.get("enhanced_measurement") or []) if e.get("ok")]
    if not entries:
        return "ok", "Webストリームなし（対象なし）", []
    feature_labels = (
        ("scrolls_enabled", "スクロール"), ("outbound_clicks_enabled", "離脱クリック"),
        ("site_search_enabled", "サイト内検索"), ("video_engagement_enabled", "動画"),
        ("file_downloads_enabled", "ファイルDL"), ("page_changes_enabled", "履歴によるページ変更"),
        ("form_interactions_enabled", "フォーム操作"),
    )
    all_off = all(not e.get("stream_enabled") for e in entries)
    if all_off:
        f = _f("medium", "設定", "拡張計測がすべて無効",
               f"{len(entries)}本のWebストリームすべてで拡張計測が無効。スクロール・離脱クリック・"
               "フォーム操作などの標準イベントが取れていない（意図した設定かの確認が必要）",
               "意図した無効化でなければ、GA4管理画面 → データストリーム → 拡張計測機能 を有効にする")
        return "warn", f["message"], [f]
    on = [lbl for key, lbl in feature_labels if any(e.get(key) for e in entries)]
    off = [lbl for key, lbl in feature_labels if not any(e.get(key) for e in entries)]
    state = f"有効: {'・'.join(on) or 'なし'}" + (f" ／ 無効: {'・'.join(off)}" if off else "")
    return "ok", state, []


# ──────────────────────────────────────
# レジストリと実行
# ──────────────────────────────────────

DATASET_LABELS = {
    "events": "イベント実績",
    "totals": "セッション総数",
    "pages": "ページ実績",
    "traffic": "流入実績",
    "countries": "国別実績",
    "hostnames": "ホスト名別実績",
    "event_create_rules": "イベント作成ルール",
    "retention": "データ保持設定",
    "enhanced_measurement": "拡張計測設定",
}

# (code, 表示名, 必要データセット, 判定関数)
# 必要データセットが揃わないチェックは判定関数に渡さず「未確認」で止める
# （判定関数の中で欠損を見ると「データが無い」を「問題なし」と返す経路が生まれるため、表側で先に止める）
CHECKS = (
    ("session_health", "基盤タグの発火（session_start比）", ("events", "totals"), check_session_health),
    ("user_id_collapse", "ユーザー識別（user_id）", ("events",), check_user_id_collapse),
    ("event_names", "イベント名の規約", ("events",), check_event_names),
    ("gtm_internal_events", "GTM内部イベントの混入", ("events",), check_gtm_internal_events),
    ("micro_events", "申込手前の計測", ("events",), check_micro_events),
    ("tel_key_event", "電話タップの成果登録", ("events",), check_tel_key_event),
    ("error_pages", "エラーページへの流入", ("pages",), check_error_pages),
    ("title_unset", "ページタイトル未設定", ("pages",), check_title_unset),
    ("title_stuck", "タイトルの使い回し（SPA疑い）", ("pages",), check_title_stuck),
    ("dup_page_urls", "URL表記の分裂", ("pages",), check_duplicate_page_urls),
    ("pii_urls", "個人情報のURL混入", ("pages",), check_pii_urls),
    ("internal_utm", "サイト内UTM", ("traffic",), check_internal_utm),
    ("unassigned", "チャネル未分類（Unassigned）", ("traffic",), check_unassigned),
    ("source_medium_variants", "参照元/メディアの表記ゆれ", ("traffic",), check_source_medium_variants),
    ("notset_traffic", "参照元情報の欠落（not set）", ("traffic",), check_notset_traffic),
    ("self_referral", "自己参照（自サイトが参照元）", ("traffic", "hostnames"), check_self_referral),
    ("foreign_noise", "海外からの機械的アクセス", ("countries",), check_foreign_noise),
    ("dup_event_rules", "イベント作成ルールの重複", ("event_create_rules",), check_duplicate_event_rules),
    ("retention", "データ保持期間", ("retention",), check_retention),
    ("enhanced_measurement", "拡張計測", ("enhanced_measurement",), check_enhanced_measurement),
)

JUDGEMENT_LABELS = {"ok": "○", "warn": "△", "ng": "×", "unverified": "未確認"}


def run_property_checks(detail: dict) -> dict:
    """プロパティ詳細（indexer が保存する detail dict）に全チェックを実行する。

    返り値:
      checks:    全チェックの判定一覧（ok/warn/ng/unverified + 状態文）
      findings:  具体的な指摘（安定ID付き）
      counts:    findings の level 別件数 {"error","warn","info"}
      unverified: データ不足・例外で判定できなかったチェック数
    """
    collected = (detail.get("summary") or {}).get("collected") or {}
    checks: list[dict] = []
    findings_all: list[dict] = []
    unverified = 0
    for code, label, required, fn in CHECKS:
        missing = [DATASET_LABELS.get(k, k) for k in required if not collected.get(k)]
        if missing:
            checks.append({"code": code, "label": label, "judgement": "unverified",
                           "state": "・".join(missing) + "のデータが取れていない", "finding_ids": []})
            unverified += 1
            continue
        try:
            judgement, state, findings = fn(detail)
        except Exception as e:  # 1チェックの失敗で表全体を落とさない
            checks.append({"code": code, "label": label, "judgement": "unverified",
                           "state": f"判定できません（{type(e).__name__}）", "finding_ids": []})
            unverified += 1
            continue
        for f in findings:
            f["check"] = code
        findings_all.extend(findings)
        checks.append({"code": code, "label": label, "judgement": judgement,
                       "state": state, "finding_ids": [f["id"] for f in findings]})
    findings_all = dedupe_ids(findings_all)
    counts = {"error": 0, "warn": 0, "info": 0}
    for f in findings_all:
        counts[f["level"]] = counts.get(f["level"], 0) + 1
    return {"checks": checks, "findings": findings_all,
            "counts": counts, "unverified": unverified}
