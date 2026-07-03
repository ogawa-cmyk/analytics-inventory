"""Call Anthropic Claude API to analyze a property and return structured issues + action plan."""
from __future__ import annotations
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from config import DATA_DIR

AI_LOG_DIR = DATA_DIR / "ai_runs"
AI_LOG_DIR.mkdir(parents=True, exist_ok=True)
AI_GTM_LOG_DIR = DATA_DIR / "ai_gtm_runs"
AI_GTM_LOG_DIR.mkdir(parents=True, exist_ok=True)


def _load_env_key() -> str | None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"].strip()
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("ANTHROPIC_API_KEY="):
                v = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    key_path = Path(__file__).parent / "anthropic_key.txt"
    if key_path.exists():
        v = key_path.read_text(encoding="utf-8").strip()
        if v:
            return v
    return None


DEFAULT_MODEL = "claude-sonnet-4-6"
DEEP_MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = """あなたはGA4（Google Analytics 4）とGTM（Google Tag Manager）実装の上級コンサルタントです。
ユーザー（小川卓 / HAPPY ANALYTICS代表）から渡されたプロパティ情報をもとに、課題を洗い出し、具体的なアクションプランを提示してください。

出力は**必ず以下のJSON形式のみ**で返してください。前後の説明文・コードブロック記号は不要です。

```json
{
  "summary": "現状の総合所見（2〜3行）",
  "issues": [
    {
      "severity": "high|medium|low",
      "category": "計測停止 / キーイベント / eコマース / カスタムディメンション / 命名規約 / GTM設定 / 権限 / その他",
      "title": "短いタイトル（〜30字）",
      "description": "課題の詳細と根拠（具体的にどのデータからそう判断したか）"
    }
  ],
  "action_plan": [
    {
      "priority": 1,
      "title": "アクション名（〜30字、動詞で始める）",
      "description": "何をどうするかの説明（3〜5行）",
      "estimated_effort": "小（30分以内） / 中（半日） / 大（1日以上）",
      "delegate_to_ai": true,
      "delegate_label": "外部AIに依頼する作業の一言タイトル（例: GTMタグ設定JSONを生成）",
      "delegate_prompt": "Claude/ChatGPT等の別セッションに貼り付けて作業させるための完全自立型プロンプト。文脈を含めること（プロパティ情報・現状・期待する成果物）。1000〜2000字程度"
    }
  ]
}
```

ルール：
- issues は最大8件、action_plan は最大6件
- priority は 1=最優先 から昇順
- delegate_to_ai が true のアクションは、外部AIに作業を委任できる種類（GTM設定JSON生成、トラッキング計画書作成、GA4管理画面の操作手順書、KE/CD設計案の文書化など）に限る。組織内の合意形成・確認作業など人間の介在が必要なものは false にして delegate_prompt は空文字列にする
- delegate_prompt は単独で読んで意味が通る完結したプロンプト。GA4プロパティ名・URL・現状のKE/CD一覧などの必要情報を含めること
- 日本語で回答
- 推測ではなく、与えられたデータから読み取れることに基づいて書く"""


def _build_user_prompt(detail: dict, linked_containers: list) -> str:
    s = detail.get("summary") or {}
    streams = detail.get("streams") or []
    kes = detail.get("key_events") or []
    cds = detail.get("custom_dimensions") or []
    cms = detail.get("custom_metrics") or []
    events = detail.get("events") or []

    def fmt_streams(ss):
        return "\n".join(
            f"- {x.get('display_name','')} ({x.get('type','')}) MID={x.get('measurement_id','—')} URI={x.get('default_uri') or x.get('package_name') or x.get('bundle_id','—')}"
            for x in ss
        ) or "（なし）"

    def fmt_events(es, n=20):
        if not es:
            return "（なし）"
        out = []
        for e in es[:n]:
            if e.get("_error"):
                return f"（取得エラー: {e['_error'][:80]}）"
            out.append(f"- {e.get('event_name')}: {(e.get('event_count') or 0):,}回 / {(e.get('total_users') or 0):,}人")
        return "\n".join(out)

    def fmt_cds(xs, n=50):
        if not xs:
            return "（なし）"
        out = [f"- {x.get('display_name')} (param: {x.get('parameter_name')}, scope: {x.get('scope')}){' — '+x.get('description') if x.get('description') else ''}" for x in xs[:n]]
        if len(xs) > n:
            out.append(f"（他 {len(xs)-n} 件）")
        return "\n".join(out)

    def fmt_cms(xs):
        return "\n".join(f"- {x.get('display_name')} (param: {x.get('parameter_name')}, unit: {x.get('measurement_unit')})" for x in xs) or "（なし）"

    def fmt_containers(cs):
        return "\n".join(f"- {c.get('account_name')} / {c.get('name')} (Public ID: {c.get('public_id')}, タグ{c.get('tag_count') or 0}件)" for c in cs) or "（紐づくGTMコンテナなし）"

    return f"""次のGA4プロパティを分析し、JSON形式で課題とアクションプランを出してください。

【プロパティ基本情報】
- プロパティ名: {s.get('display_name','(no name)')}
- プロパティID: {s.get('property_id')}
- 認証Gmail: {s.get('auth_email')}
- 業種: {s.get('industry_category') or '不明'}
- タイムゾーン: {s.get('time_zone') or '不明'}
- 通貨: {s.get('currency_code') or '不明'}
- 作成日時: {s.get('create_time') or '不明'}
- 自分のロール: {', '.join(s.get('my_roles') or []) or '不明'}

【ヘルススコア】
{s.get('health_score','—')}/100 (グレード {s.get('health_grade','—')})

【計測状況（直近7日）】
- セッション数: {(s.get('sessions_7d') or 0):,}
- イベント数: {(s.get('events_7d') or 0):,}
- 計測中判定: {s.get('is_tracked')}
- Data API応答: {'正常' if s.get('data_api_ok') else 'エラー: ' + (s.get('data_api_error') or '')}

【データストリーム ({len(streams)} 件)】
{fmt_streams(streams)}

【キーイベント ({len(kes)} 件)】
{', '.join(k.get('event_name','') for k in kes) or '（未設定）'}

【カスタムディメンション ({len(cds)} 件)】
{fmt_cds(cds)}

【カスタム指標 ({len(cms)} 件)】
{fmt_cms(cms)}

【eコマース計測】
{'実装あり' if s.get('is_ecommerce') else '未実装/未検出'}
検出済みイベント: {', '.join((s.get('ecommerce_events_found') or {}).keys()) or 'なし'}

【直近30日イベント上位20】
{fmt_events(events, 20)}

【紐づくGTMコンテナ】
{fmt_containers(linked_containers)}

このプロパティの課題を洗い出し、アクションプランを設計してください。"""


_JSON_BLOCK = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL)


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("AI応答が空でした（content blocks に text が含まれていません）")
    m = _JSON_BLOCK.search(text)
    if m:
        text = m.group(1).strip()
    else:
        first = text.find("{")
        last = text.rfind("}")
        if first >= 0 and last > first:
            text = text[first:last + 1]
    return json.loads(text)


def analyze_property(detail: dict, linked_containers: list, model: str = DEFAULT_MODEL,
                     extra_instructions: str = "") -> dict:
    """Call Claude API and return parsed analysis dict + raw text."""
    from anthropic import Anthropic

    key = _load_env_key()
    if not key:
        return {"error": "ANTHROPIC_API_KEY が見つかりません。ツールフォルダ直下の .env に設定してください。"}

    client = Anthropic(api_key=key)
    user_msg = _build_user_prompt(detail, linked_containers)
    if extra_instructions:
        user_msg += f"\n\n【追加の指示】\n{extra_instructions}"

    last_err: str | None = None
    last_raw: str = ""
    resp = None
    parsed = None
    for attempt in range(2):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=12000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
        except Exception as e:
            last_err = f"API呼び出しエラー(attempt {attempt+1}): {type(e).__name__}: {str(e)[:300]}"
            continue
        raw = "".join((b.text or "") for b in resp.content if hasattr(b, "text"))
        last_raw = raw
        try:
            parsed = _extract_json(raw)
            last_err = None
            break
        except Exception as e:
            stop = getattr(resp, "stop_reason", None)
            last_err = f"JSON解析失敗(attempt {attempt+1}): {e} ・ stop_reason={stop} ・ raw_len={len(raw)}"
            if stop == "max_tokens":
                last_err += " ・ max_tokensに到達"
                break

    if parsed is None:
        pid = detail.get("summary", {}).get("property_id")
        _save_run(pid, {"error": last_err, "raw": last_raw}, user_msg, last_raw, suffix="-error")
        return {"error": last_err or "不明なエラー", "raw": last_raw}

    parsed["_meta"] = {
        "model": resp.model,
        "input_tokens": getattr(resp.usage, "input_tokens", None),
        "output_tokens": getattr(resp.usage, "output_tokens", None),
        "stop_reason": getattr(resp, "stop_reason", None),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_run(detail.get("summary", {}).get("property_id"), parsed, user_msg, raw)
    return parsed


def _save_run(pid: str | None, parsed: dict, user_msg: str, raw: str, suffix: str = "") -> None:
    if not pid:
        return
    pdir = AI_LOG_DIR / str(pid)
    pdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (pdir / f"{stamp}{suffix}.json").write_text(
        json.dumps({"prompt": user_msg, "raw": raw, "parsed": parsed},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _prune_runs(pdir, keep=10)


def _prune_runs(pdir: Path, keep: int) -> None:
    runs = sorted(pdir.glob("*.json"), reverse=True)
    for p in runs[keep:]:
        try:
            p.unlink()
        except Exception:
            pass


def list_runs(pid: str) -> list[dict]:
    pdir = AI_LOG_DIR / str(pid)
    if not pdir.exists():
        return []
    out = []
    for p in sorted(pdir.glob("*.json"), reverse=True):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime).isoformat()
        except Exception:
            mtime = None
        out.append({"file": p.name, "stamp": p.stem, "mtime": mtime})
    return out


def load_run(pid: str, stamp: str) -> dict | None:
    p = AI_LOG_DIR / str(pid) / f"{stamp}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def latest_run(pid: str) -> dict | None:
    runs = list_runs(pid)
    if not runs:
        return None
    return load_run(pid, runs[0]["stamp"])


# ============================================================
#  GTM container analysis
# ============================================================

SYSTEM_PROMPT_GTM = """あなたはGoogle Tag Manager（GTM）の上級コンサルタントです。
ユーザー（小川卓 / HAPPY ANALYTICS代表）から渡されたGTMコンテナの設定情報をもとに、課題を洗い出し、具体的なアクションプランを提示してください。

出力は**必ず以下のJSON形式のみ**で返してください。前後の説明文・コードブロック記号は不要です。

```json
{
  "summary": "現状の総合所見（2〜3行）",
  "issues": [
    {
      "severity": "high|medium|low",
      "category": "古いタグ / 重複 / 未使用 / 命名規約 / セキュリティ / パフォーマンス / GA4連携 / ガバナンス / その他",
      "title": "短いタイトル（〜30字）",
      "description": "課題の詳細と根拠（具体的にどのタグ・トリガー・変数からそう判断したか）"
    }
  ],
  "action_plan": [
    {
      "priority": 1,
      "title": "アクション名（〜30字、動詞で始める）",
      "description": "何をどうするかの説明（3〜5行）",
      "estimated_effort": "小（30分以内） / 中（半日） / 大（1日以上）",
      "delegate_to_ai": true,
      "delegate_label": "外部AIに依頼する作業の一言タイトル（例: 重複タグ統合用のGTMインポートJSONを生成）",
      "delegate_prompt": "Claude/ChatGPT等の別セッションに貼り付けて作業させるための完全自立型プロンプト。文脈を含めること（コンテナ情報・タグ一覧抜粋・期待する成果物）。1000〜2000字程度"
    }
  ]
}
```

GTM特有の観点：
- **古いタグ**: Universal Analytics（gaawc以前のga、gaawe以前のgaawe等じゃないUA系）、deprecated扱いのカスタムテンプレ、Floodlight旧仕様、Yahoo!タグマネージャ移行物など
- **重複・統合候補**: 同じ目的・同じ発火条件で複数存在するタグ、同類のトリガー、同じスニペットの変数
- **未使用**: どのタグからも参照されていないトリガー・変数、長期間 paused のタグ
- **命名規約**: タグ・トリガー・変数の名前にプレフィックス（GA4/UA/Yahoo/Meta/...）と動詞が含まれているか、一貫性、日本語/英語の混在
- **セキュリティ**: カスタムHTMLタグでの innerHTML 注入、eval、外部スクリプト動的読み込み、機密値の埋め込み
- **パフォーマンス**: 全ページ発火タグの数、Window LoadedよりPage Viewで間に合うものが多いか、不要な変数評価
- **GA4連携**: Measurement IDの一貫性、Configurationタグの存在、enhanced measurement、推奨イベント名規約
- **ガバナンス**: 公開未済の変更、過剰なAdmin、命名揺れ、ドキュメント

ルール：
- issues は最大8件、action_plan は最大6件
- priority は 1=最優先 から昇順
- delegate_to_ai が true のアクションは、外部AIに作業を委任できる種類（GTMインポートJSONの生成、テンプレート設計書、新タグの設定手順書、命名規約ドキュメント化など）に限る。組織内の合意形成・承認・確認作業など人間の介在が必要なものは false にして delegate_prompt は空文字列にする
- delegate_prompt は単独で読んで意味が通る完結したプロンプト。コンテナ名・関連タグ抜粋・現状などを含めること
- 日本語で回答
- 推測ではなく、与えられたデータから読み取れることに基づいて書く"""


def _build_gtm_prompt(container: dict, live: dict, linked_props: list) -> str:
    name = container.get("name") or "(no name)"
    cid = container.get("container_id") or "?"
    public_id = container.get("public_id") or "—"
    usage = ", ".join(container.get("usage_context") or []) or "—"
    domains = ", ".join(container.get("domain_name") or []) or "—"
    mids = ", ".join(container.get("ga4_measurement_ids") or []) or "—"

    tags = live.get("tag") or []
    triggers = live.get("trigger") or []
    variables = live.get("variable") or []

    # Tags
    tag_lines = []
    paused = sum(1 for t in tags if t.get("paused"))
    type_counter: dict = {}
    for t in tags:
        type_counter[t.get("type", "?")] = type_counter.get(t.get("type", "?"), 0) + 1
    tag_type_summary = ", ".join(f"{k}={v}" for k, v in sorted(type_counter.items(), key=lambda x: -x[1])[:15])
    for t in tags[:60]:
        flag = "[paused]" if t.get("paused") else ""
        ftc = ",".join((t.get("firingTriggerId") or [])[:3])
        tag_lines.append(f"- {t.get('name')} (type={t.get('type')}){' ' + flag if flag else ''} firing={ftc}")
    if len(tags) > 60:
        tag_lines.append(f"（他 {len(tags)-60} 件）")

    # Triggers
    trig_lines = []
    trig_type_counter: dict = {}
    for t in triggers:
        trig_type_counter[t.get("type", "?")] = trig_type_counter.get(t.get("type", "?"), 0) + 1
    trig_type_summary = ", ".join(f"{k}={v}" for k, v in sorted(trig_type_counter.items(), key=lambda x: -x[1])[:10])
    for t in triggers[:40]:
        trig_lines.append(f"- {t.get('name')} (type={t.get('type')})")
    if len(triggers) > 40:
        trig_lines.append(f"（他 {len(triggers)-40} 件）")

    # Variables
    var_lines = []
    var_type_counter: dict = {}
    for v in variables:
        var_type_counter[v.get("type", "?")] = var_type_counter.get(v.get("type", "?"), 0) + 1
    var_type_summary = ", ".join(f"{k}={v}" for k, v in sorted(var_type_counter.items(), key=lambda x: -x[1])[:10])
    for v in variables[:40]:
        var_lines.append(f"- {v.get('name')} (type={v.get('type')})")
    if len(variables) > 40:
        var_lines.append(f"（他 {len(variables)-40} 件）")

    # Linked GA4 properties
    if linked_props:
        linked_lines = "\n".join(f"- {p.get('display_name')} (ID: {p.get('property_id')})" for p in linked_props)
    else:
        linked_lines = "（紐づくGA4プロパティなし）"

    return f"""次のGTMコンテナを分析し、JSON形式で課題とアクションプランを出してください。

【コンテナ基本情報】
- コンテナ名: {name}
- コンテナID: {cid}
- Public ID: {public_id}
- 用途: {usage}
- ドメイン: {domains}
- 公開バージョン: {live.get('name') or '—'} (id {live.get('containerVersionId') or '—'})
- 公開済み: {live.get('published')}
- 認証Gmail: {container.get('auth_email') or '—'}
- アカウント名: {container.get('account_name') or '—'}

【規模】
- タグ: {len(tags)} 件（paused: {paused}）
- トリガー: {len(triggers)} 件
- 変数: {len(variables)} 件
- 紐づくGA4 MID: {mids}

【タグ種別の集計】
{tag_type_summary}

【トリガー種別の集計】
{trig_type_summary}

【変数種別の集計】
{var_type_summary}

【タグ一覧（最大60件）】
{chr(10).join(tag_lines) if tag_lines else '（なし）'}

【トリガー一覧（最大40件）】
{chr(10).join(trig_lines) if trig_lines else '（なし）'}

【変数一覧（最大40件）】
{chr(10).join(var_lines) if var_lines else '（なし）'}

【紐づくGA4プロパティ】
{linked_lines}

このコンテナの課題を洗い出し、アクションプランを設計してください。"""


def analyze_container(container: dict, live: dict, linked_props: list,
                      model: str = DEFAULT_MODEL, extra_instructions: str = "") -> dict:
    """Call Claude API on a GTM container. Returns parsed analysis dict."""
    from anthropic import Anthropic

    key = _load_env_key()
    if not key:
        return {"error": "ANTHROPIC_API_KEY が見つかりません。ツールフォルダ直下の .env に設定してください。"}

    client = Anthropic(api_key=key)
    user_msg = _build_gtm_prompt(container, live, linked_props)
    if extra_instructions:
        user_msg += f"\n\n【追加の指示】\n{extra_instructions}"

    last_err: str | None = None
    last_raw: str = ""
    resp = None
    parsed = None
    for attempt in range(2):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=12000,
                system=SYSTEM_PROMPT_GTM,
                messages=[{"role": "user", "content": user_msg}],
            )
        except Exception as e:
            last_err = f"API呼び出しエラー(attempt {attempt+1}): {type(e).__name__}: {str(e)[:300]}"
            continue
        raw = "".join((b.text or "") for b in resp.content if hasattr(b, "text"))
        last_raw = raw
        try:
            parsed = _extract_json(raw)
            last_err = None
            break
        except Exception as e:
            stop = getattr(resp, "stop_reason", None)
            last_err = f"JSON解析失敗(attempt {attempt+1}): {e} ・ stop_reason={stop} ・ raw_len={len(raw)}"
            if stop == "max_tokens":
                last_err += " ・ max_tokensに到達"
                break

    cid = container.get("container_id")
    if parsed is None:
        _save_gtm_run(cid, {"error": last_err, "raw": last_raw}, user_msg, last_raw, suffix="-error")
        return {"error": last_err or "不明なエラー", "raw": last_raw}

    parsed["_meta"] = {
        "model": resp.model,
        "input_tokens": getattr(resp.usage, "input_tokens", None),
        "output_tokens": getattr(resp.usage, "output_tokens", None),
        "stop_reason": getattr(resp, "stop_reason", None),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_gtm_run(cid, parsed, user_msg, last_raw)
    return parsed


def _save_gtm_run(cid: str | None, parsed: dict, user_msg: str, raw: str, suffix: str = "") -> None:
    if not cid:
        return
    cdir = AI_GTM_LOG_DIR / str(cid)
    cdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (cdir / f"{stamp}{suffix}.json").write_text(
        json.dumps({"prompt": user_msg, "raw": raw, "parsed": parsed},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _prune_runs(cdir, keep=10)


def list_gtm_runs(cid: str) -> list[dict]:
    cdir = AI_GTM_LOG_DIR / str(cid)
    if not cdir.exists():
        return []
    out = []
    for p in sorted(cdir.glob("*.json"), reverse=True):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime).isoformat()
        except Exception:
            mtime = None
        out.append({"file": p.name, "stamp": p.stem, "mtime": mtime})
    return out


def load_gtm_run(cid: str, stamp: str) -> dict | None:
    p = AI_GTM_LOG_DIR / str(cid) / f"{stamp}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def latest_gtm_run(cid: str) -> dict | None:
    runs = list_gtm_runs(cid)
    if not runs:
        return None
    return load_gtm_run(cid, runs[0]["stamp"])


# ============================================================
#  Aggregations across all AI runs (for the home dashboard)
# ============================================================

def coverage_property() -> int:
    """How many properties have at least one AI run."""
    if not AI_LOG_DIR.exists():
        return 0
    return sum(1 for d in AI_LOG_DIR.iterdir() if d.is_dir() and any(d.glob("*.json")))


def coverage_gtm() -> int:
    if not AI_GTM_LOG_DIR.exists():
        return 0
    return sum(1 for d in AI_GTM_LOG_DIR.iterdir() if d.is_dir() and any(d.glob("*.json")))


def top_issue_categories(top_n: int = 5) -> list[dict]:
    """Aggregate issue categories from latest run of each property+container."""
    from collections import Counter
    cnt: Counter = Counter()
    sev: dict = {}
    for base in (AI_LOG_DIR, AI_GTM_LOG_DIR):
        if not base.exists():
            continue
        for d in base.iterdir():
            if not d.is_dir():
                continue
            runs = sorted([p for p in d.glob("*.json") if "-error" not in p.stem], reverse=True)
            if not runs:
                continue
            try:
                run = json.loads(runs[0].read_text(encoding="utf-8"))
                parsed = run.get("parsed") or {}
                for issue in parsed.get("issues") or []:
                    cat = issue.get("category") or "その他"
                    cnt[cat] += 1
                    if issue.get("severity") == "high":
                        sev.setdefault(cat, 0)
                        sev[cat] += 1
            except Exception:
                continue
    out = []
    for cat, n in cnt.most_common(top_n):
        out.append({"category": cat, "count": n, "high_count": sev.get(cat, 0)})
    return out
