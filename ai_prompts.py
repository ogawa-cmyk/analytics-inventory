"""AI consultation prompt templates.

Each template renders a complete, context-rich prompt for GA4 properties
or GTM containers, ready to send to ChatGPT / Claude / Perplexity / Gemini / Grok.
"""
from __future__ import annotations


# (id, title, description, applies_when_callable_takes_summary)
TEMPLATES_PROPERTY = [
    {
        "id": "diagnose_tracking",
        "title": "計測停止診断",
        "description": "直近7日間データが0の原因と確認手順",
        "applies_when": lambda s: s.get("is_tracked") is False
                                  or (s.get("sessions_7d") or 0) == 0,
    },
    {
        "id": "suggest_key_events",
        "title": "必須キーイベント提案",
        "description": "業種・サイト構成から推奨KEを提案",
        "applies_when": lambda s: (s.get("key_event_count") or 0) < 3,
    },
    {
        "id": "diagnose_ecommerce",
        "title": "eコマース実装診断",
        "description": "ECサイトの場合に必要なイベント・パラメータを整理",
        "applies_when": lambda s: not s.get("is_ecommerce"),
    },
    {
        "id": "audit_cd",
        "title": "カスタムディメンション棚卸し",
        "description": "命名規約・スコープ・重複・不要候補をレビュー",
        "applies_when": lambda s: (s.get("custom_dimension_count") or 0) >= 10,
    },
    {
        "id": "review_key_events",
        "title": "キーイベント妥当性レビュー",
        "description": "ビジネスKPIとの整合・ファネル充足度",
        "applies_when": lambda s: (s.get("key_event_count") or 0) >= 1,
    },
]


def _fmt_streams(streams: list) -> str:
    out = []
    for s in streams or []:
        bits = [s.get("display_name") or "", s.get("type") or ""]
        if s.get("measurement_id"):
            bits.append(f"MID={s['measurement_id']}")
        if s.get("default_uri"):
            bits.append(f"URI={s['default_uri']}")
        out.append(" / ".join(b for b in bits if b))
    return "\n".join(f"- {o}" for o in out) or "（情報なし）"


def _fmt_kes(kes: list) -> str:
    if not kes:
        return "（未設定）"
    return ", ".join(k.get("event_name", "") for k in kes)


def _fmt_events(events: list, top: int = 15) -> str:
    if not events:
        return "（取得失敗）"
    rows = []
    for e in events[:top]:
        if e.get("_error"):
            return f"（取得エラー: {e['_error'][:80]}）"
        rows.append(f"- {e.get('event_name')}: {(e.get('event_count') or 0):,}回 / {(e.get('total_users') or 0):,}人")
    return "\n".join(rows)


def _fmt_cds(cds: list, limit: int = 60) -> str:
    if not cds:
        return "（なし）"
    rows = []
    for c in cds[:limit]:
        rows.append(f"- {c.get('display_name')} (param: {c.get('parameter_name')}, scope: {c.get('scope')}){' — '+c.get('description') if c.get('description') else ''}")
    extra = f"\n他 {len(cds)-limit} 件" if len(cds) > limit else ""
    return "\n".join(rows) + extra


def _fmt_cms(cms: list) -> str:
    if not cms:
        return "（なし）"
    return "\n".join(f"- {c.get('display_name')} (param: {c.get('parameter_name')}, unit: {c.get('measurement_unit')})" for c in cms)


def _fmt_containers(containers: list) -> str:
    if not containers:
        return "（紐づくGTMコンテナなし）"
    return "\n".join(f"- {c.get('account_name')} / {c.get('name')} (Public ID: {c.get('public_id')}, タグ{c.get('tag_count') or 0}件)" for c in containers)


def _site_url(streams: list) -> str:
    for s in streams or []:
        if s.get("default_uri"):
            return s["default_uri"]
    return "（不明）"


def _mids(streams: list) -> str:
    mids = [s.get("measurement_id") for s in (streams or []) if s.get("measurement_id")]
    return ", ".join(mids) or "（なし）"


def render(template_id: str, detail: dict, linked_containers: list = None) -> str:
    """Render a complete prompt string. detail is the property detail dict."""
    s = detail.get("summary") or {}
    streams = detail.get("streams") or []
    kes = detail.get("key_events") or []
    cds = detail.get("custom_dimensions") or []
    cms = detail.get("custom_metrics") or []
    events = detail.get("events") or []
    linked_containers = linked_containers or []

    pname = s.get("display_name") or "(no name)"
    pid = s.get("property_id") or "?"
    url = _site_url(streams)
    mids = _mids(streams)
    industry = s.get("industry_category") or "不明"
    tz = s.get("time_zone") or "不明"

    if template_id == "diagnose_tracking":
        return f"""あなたはGA4実装の専門家です。以下のGA4プロパティで計測が停止しているか、極端にデータが少ない可能性があります。原因を切り分けたいので診断ください。

【プロパティ情報】
- プロパティ名: {pname}
- プロパティID: {pid}
- サイトURL: {url}
- Measurement ID: {mids}
- タイムゾーン: {tz}
- 業種: {industry}

【計測状況（直近7日）】
- セッション数: {(s.get('sessions_7d') or 0):,}
- イベント数: {(s.get('events_7d') or 0):,}
- データAPI応答: {'正常' if s.get('data_api_ok') else 'エラー'}

【データストリーム】
{_fmt_streams(streams)}

【紐づくGTMコンテナ】
{_fmt_containers(linked_containers)}

【直近30日のイベント上位】
{_fmt_events(events, top=15)}

以下の観点で診断と対処手順をください：
1. 計測停止/激減の原因として考えられる仮説（実装ミス、タグ削除、ドメイン変更、CMP/同意管理、ボット過多、フィルタ過剰、テストプロパティ等）を可能性の高い順にリストアップ
2. 各原因について、確認すべき項目と確認手順（GA4管理画面、GTM、デベロッパーツール、サーバーログ）
3. 優先して着手すべき調査の順番
4. それでも判明しない場合のエスカレーション先

日本語で、具体的なクリック先や検証コマンドを含めて回答してください。"""

    if template_id == "suggest_key_events":
        return f"""あなたはGA4設計の専門家です。以下のGA4プロパティに対して、ビジネスKPIから逆算した推奨キーイベントを提案してください。

【プロパティ情報】
- プロパティ名: {pname}
- サイトURL: {url}
- 業種: {industry}

【現在のキーイベント設定】
- 件数: {s.get('key_event_count') or 0} 件
- イベント名: {_fmt_kes(kes)}

【直近30日で観測されているイベント上位】
{_fmt_events(events, top=20)}

【eコマース計測の有無】
{'あり' if s.get('is_ecommerce') else 'なし'}

このサイトの想定ビジネスゴール（業種・URLから推測）に対して、追加すべきキーイベントを最大5つ提案してください。各提案について以下を含めてください：

1. **イベント名**（snake_case、GA4予約イベント名と衝突しないこと）
2. **計測する理由**（どのKPIに紐づくか）
3. **発火タイミング**（具体的なユーザー行動・要素）
4. **送信パラメータ**（カスタムディメンション化の必要性も含む）
5. **GTMでの実装方法**（タグ・トリガー・変数の構成）
6. **既存KEとの重複可能性チェック**

最後に、既存のキーイベントの中で削除・統合を検討すべきものがあれば指摘してください。"""

    if template_id == "diagnose_ecommerce":
        ecom_events = list((s.get("ecommerce_events_found") or {}).keys())
        return f"""あなたはGA4 eコマース計測の専門家です。以下のサイトがECサイトと想定される場合、適切なeコマース計測ができているか確認したいです。

【プロパティ情報】
- プロパティ名: {pname}
- サイトURL: {url}
- 業種: {industry}

【現在のeコマース関連イベント検出状況（直近30日）】
- 検出されたeコマースイベント: {', '.join(ecom_events) if ecom_events else 'なし'}

【直近30日のイベント上位】
{_fmt_events(events, top=15)}

【現状のキーイベント】
{_fmt_kes(kes)}

以下を作成してください：

1. **このサイトがECかどうかの判定**（URL/イベント名から推測）と判定根拠
2. ECサイトの場合、計測すべき推奨イベント一覧（GA4 eコマース推奨イベント、item_array、購入動線のステップ）
3. 各イベントに必要な**ecommerceパラメータ・itemsパラメータ**の構造
4. **GTMでの実装手順**（dataLayer設計、変数、トリガー、タグ）
5. **テスト方法**（GTMプレビュー、DebugView、Real-time、検証チェックリスト）
6. 現状の検出状況から判断した**今すぐ着手すべき次の3ステップ**

なお、サイトがECではない場合は、その判定理由と「このプロパティはeコマース計測対象外」とご回答ください。"""

    if template_id == "audit_cd":
        return f"""あなたはGA4設計の専門家です。以下のGA4プロパティのカスタムディメンション設定を棚卸しレビューしてください。件数が多めで整理が必要です。

【プロパティ情報】
- プロパティ名: {pname}
- サイトURL: {url}
- 業種: {industry}
- カスタムディメンション件数: {s.get('custom_dimension_count') or 0} 件 / 上限50件
- カスタム指標件数: {s.get('custom_metric_count') or 0} 件

【設定済みカスタムディメンション一覧】
{_fmt_cds(cds)}

【設定済みカスタム指標一覧】
{_fmt_cms(cms)}

以下の観点でレビューと整理案をお願いします：

1. **命名規約の一貫性**: snake_case徹底、語順、略語の統一などの逸脱
2. **スコープの妥当性**: event/user/item/session 各スコープが適切か（誤ったスコープを使うとレポートで使えない）
3. **重複・統合候補**: 役割が被っているCD
4. **不要候補**: 説明やparam名から、現在使われていない可能性が高いもの
5. **不足候補**: 業種・サイト構成から見て、定番なのに未設定なもの
6. **優先削除リスト**: 上限50に近い場合、削除すべきトップ5
7. **改名・スコープ変更が必要なものの優先リスト**

各指摘には具体的な「現状 → あるべき姿 → 影響と移行手順」をセットで示してください。"""

    if template_id == "review_key_events":
        return f"""あなたはGA4設計の専門家です。以下のGA4プロパティのキーイベント設定の妥当性をレビューしてください。

【プロパティ情報】
- プロパティ名: {pname}
- サイトURL: {url}
- 業種: {industry}

【設定済みキーイベント】
件数: {s.get('key_event_count') or 0} 件
{_fmt_kes(kes)}

【直近30日のイベント上位】
{_fmt_events(events, top=20)}

【eコマース計測の有無】
{'あり' if s.get('is_ecommerce') else 'なし'}

以下の観点で評価と改善案を出してください：

1. **ビジネスKPI整合**: 現在のKEがサイトの目的・収益モデルと合っているか
2. **ファネル充足度**: 認知 → 興味 → 検討 → コンバージョン → リテンション の各段階にKEがあるか
3. **過剰・不足**: KEが多すぎる/少なすぎる兆候
4. **誤った設定**: 全イベントをKE化していないか、Sessionスコープ等が必要なものを見落としていないか
5. **追加すべきKE**: top eventsから昇格すべき候補
6. **削除すべきKE**: 価値が低い、または別の指標で代替可能なもの

回答の最後に「優先度順アクションリスト（最大5件）」を箇条書きで示してください。"""

    return "(未知のテンプレートID)"


def list_for_property(detail: dict) -> list[dict]:
    """Return template metadata + applicability flag for a property detail."""
    s = detail.get("summary") or {}
    out = []
    for t in TEMPLATES_PROPERTY:
        try:
            applies = bool(t["applies_when"](s))
        except Exception:
            applies = False
        out.append({
            "id": t["id"],
            "title": t["title"],
            "description": t["description"],
            "applies": applies,
        })
    return out


# AI service URL templates. {q} will be URL-encoded query.
AI_SERVICES = [
    {"id": "claude",     "label": "Claude",     "url": "https://claude.ai/new?q={q}"},
    {"id": "chatgpt",    "label": "ChatGPT",    "url": "https://chatgpt.com/?q={q}"},
    {"id": "perplexity", "label": "Perplexity", "url": "https://www.perplexity.ai/search/new?q={q}"},
    {"id": "gemini",     "label": "Gemini",     "url": "https://gemini.google.com/app?q={q}"},
    {"id": "grok",       "label": "Grok",       "url": "https://grok.com/?q={q}"},
]
