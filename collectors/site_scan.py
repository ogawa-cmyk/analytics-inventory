"""公開サイトの計測実装スキャン — 権限が無くても分かる範囲の一次診断。

対象サイトの公開HTMLと、そこで配信されている公開 gtm.js を読み、
実際にブラウザへ配信されている計測実装（GTMコンテナ・GA4/UA/広告のID・タグ構成）を
復元する。GTM APIの閲覧権限が無いコンテナでも「何のイベントが・どの条件で・
どの測定IDへ送られるか」まで確認できる。

GTM APIとの違い（重要）:
- タグ名・トリガー名は取得できない（配信時に削除される）
- 公開中バージョンのみ。一時停止タグは個数すら分からない
- 逆に、権限外のコンテナ・直書きのgtagも「実際に配信されている事実」として拾える

読み取り専用。サイトにもGTMにも一切書き込まない。

実装は super-access-analytics（MIT License, Copyright (c) TigerMonday Inc.
https://github.com/TigerMonday/super-access-analytics-public）の gtm_public.py を
本ツール向けに移植・簡約したもの。
"""
from __future__ import annotations

import json
import re
import urllib.request
from collections import defaultdict

UA = "Mozilla/5.0 (compatible; AnalyticsInventory/1.0)"
GTM_JS = "https://www.googletagmanager.com/gtm.js?id={container_id}"

ID_PATTERNS = {
    "gtm_containers": r"GTM-[A-Z0-9]{6,}",
    "ga4_measurement_ids": r"G-[A-Z0-9]{8,}",
    "google_ads_ids": r"AW-\d{9,}",
    "floodlight_ids": r"DC-\d{6,}",
    "universal_analytics_ids": r"UA-\d{4,}-\d+",
}

# 完全一致以外（正規表現・部分一致）で Event を見ているトリガーの擬似名
ANY_DATALAYER_EVENT = "(すべてのカスタムイベント)"

TAG_FUNCTIONS = {
    "__googtag": "Google タグ（基盤設定）",
    "__gaawe": "GA4 イベント",
    "__gaawc": "GA4 設定（旧世代）",
    "__html": "カスタム HTML",
    "__paused": "一時停止中",
    "__awct": "Google 広告 コンバージョン",
    "__awud": "Google 広告 ユーザー提供データ",
    "__gclidw": "コンバージョンリンカー",
    "__baut": "Microsoft 広告（UET）",
    "__tg": "Google Ads リマーケティング",
    "__ua": "Universal Analytics（2024/7 に計測停止済み）",
    "__sp": "Google Ads リマーケティング（旧）",
    "__lcl": "リンククリック リスナー",
}

PREDICATE_OPS = {
    "_eq": "=", "_neq": "≠", "_cn": "含む", "_nc": "含まない",
    "_sw": "前方一致", "_ew": "後方一致", "_re": "正規表現一致", "_nr": "正規表現不一致",
    "_lt": "<", "_le": "≦", "_gt": ">", "_ge": "≧", "_css": "CSSセレクタ一致",
}


def http_get(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        charset = res.headers.get_content_charset() or "utf-8"
        return res.read().decode(charset, errors="replace")


def discover_ids(html: str) -> dict[str, list[str]]:
    return {key: sorted(set(re.findall(pat, html))) for key, pat in ID_PATTERNS.items()}


def fetch_container(container_id: str) -> str:
    if not re.fullmatch(r"GTM-[A-Z0-9]{6,}", container_id):
        raise ValueError(f"GTMコンテナIDの形式ではありません: {container_id}")
    return http_get(GTM_JS.format(container_id=container_id))


def _extract_balanced(text: str, start: int) -> str:
    """text[start] の '{' に対応する '}' までを、文字列リテラルを考慮して切り出す。"""
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start: i + 1]
    raise ValueError("gtm.js の resource が閉じていません（形式が想定外）")


def parse_resource(gtm_js: str) -> dict:
    match = re.search(r'"resource"\s*:\s*\{', gtm_js)
    if not match:
        raise ValueError("gtm.js から resource が見つかりません（形式が変わった可能性）")
    brace = gtm_js.index("{", match.end() - 1)
    return json.loads(_extract_balanced(gtm_js, brace))


class Resolver:
    """macro / predicate 参照を人間が読める文字列に解決する。"""

    def __init__(self, resource: dict):
        self.macros = resource.get("macros", [])
        self.predicates = resource.get("predicates", [])

    def macro(self, index: int, depth: int = 0) -> str:
        if index >= len(self.macros) or depth > 4:
            return f"macro[{index}]"
        m = self.macros[index]
        fn = m.get("function", "?")
        if fn == "__e":
            return "Event"
        if fn == "__v":
            return f"DLV:{m.get('vtp_name', '')}"
        if fn == "__u":
            return f"URL:{m.get('vtp_component', 'URL')}"
        if fn == "__c":
            return self.value(m.get("vtp_value"), depth + 1)
        if fn == "__k":
            return f"Cookie:{m.get('vtp_name', '')}"
        if fn in ("__smm", "__remm"):
            return "ルックアップ"
        return fn.lstrip("_")

    def value(self, v, depth: int = 0) -> str:
        if isinstance(v, list) and v:
            if v[0] == "macro" and len(v) >= 2:
                return self.macro(v[1], depth)
            if v[0] == "template":
                return "".join(self.value(part, depth + 1) for part in v[1:])
            return json.dumps(v, ensure_ascii=False)[:120]
        return "" if v is None else str(v)

    def predicate(self, index: int) -> str:
        if index >= len(self.predicates):
            return f"predicate[{index}]"
        p = self.predicates[index]
        op = PREDICATE_OPS.get(p.get("function", "?"), p.get("function", "?"))
        return f"{self.value(p.get('arg0'))} {op} {self.value(p.get('arg1'))}"

    def datalayer_events_of(self, index: int) -> list[str]:
        """述語が Event を対象にしていれば、対応する dataLayer イベント名の列を返す。"""
        p = self.predicates[index] if index < len(self.predicates) else None
        if not p or p.get("arg0") != ["macro", 0]:
            return []
        fn = p.get("function", "")
        arg1 = p.get("arg1")
        if fn == "_eq":
            return [arg1] if isinstance(arg1, str) and arg1 else []
        if fn in ("_neq", "_nc", "_nr"):
            return []  # 否定形は絞り込みであって対象の指定ではない
        if fn in ("_re", "_cn", "_sw", "_ew", "_css"):
            label = self._broad_or_literals(fn, arg1)
            if not label:
                return []
            if label != ANY_DATALAYER_EVENT and not label.startswith("(条件:"):
                return [x for x in label.split("|") if x]
            return [label]
        return []

    @staticmethod
    def _broad_or_literals(fn: str, pattern) -> str | None:
        """完全一致以外のトリガーの扱い。何にでも当たるものだけ「全イベント」にする
        （`^(login|sign_up)$` のような限定列挙まで全イベント扱いにすると誤検知になる）。"""
        if not isinstance(pattern, str):
            return None
        pat = pattern.strip()
        if pat in {".+", ".*", ".", "^.*$", "^.+$", "^.*", "^.+", ".*$", ".+$", ""}:
            return ANY_DATALAYER_EVENT
        if fn == "_re":
            m = re.fullmatch(r"\^?\(?([A-Za-z0-9_.\-|]+)\)?\$?", pat)
            if m:
                anchored = pat.startswith("^") and pat.endswith("$")
                grouped = "(" in pat and ")" in pat
                parts = [x for x in m.group(1).split("|") if x]
                if parts and (anchored or grouped):
                    return "|".join(parts)
        return f"(条件: Event {PREDICATE_OPS.get(fn, fn)} {pat})"


def normalize(resource: dict, container_id: str) -> dict:
    """gtm.js の resource を「タグ種別・イベント名・送信先・発火条件」の一覧に正規化する。"""
    r = Resolver(resource)
    tags = resource.get("tags", [])
    rules = resource.get("rules", [])

    fire: dict[int, list[str]] = defaultdict(list)
    events: dict[int, list[str]] = defaultdict(list)
    for rule in rules:
        conditions, added, ev_names = [], [], []
        for clause in rule:
            head = clause[0]
            if head == "if":
                for i in clause[1:]:
                    conditions.append(r.predicate(i))
                    ev_names += r.datalayer_events_of(i)
            elif head == "unless":
                conditions += [f"NOT({r.predicate(i)})" for i in clause[1:]]
            elif head == "add":
                added += clause[1:]
        cond_text = " かつ ".join(conditions) if conditions else "(条件なし)"
        for ti in added:
            fire[ti].append(cond_text)
            events[ti] += ev_names

    normalized = []
    type_counter: dict[str, int] = defaultdict(int)
    ga4_destinations: set[str] = set()
    ua_tag_count = 0
    for i, t in enumerate(tags):
        fn = t.get("function", "?")
        type_counter[TAG_FUNCTIONS.get(fn, fn)] += 1
        if fn == "__ua":
            ua_tag_count += 1
        if "vtp_measurementIdOverride" in t:
            dest = r.value(t["vtp_measurementIdOverride"])
        elif "vtp_tagId" in t:
            dest = r.value(t["vtp_tagId"])
        elif "vtp_conversionId" in t:
            dest = f"AW-{r.value(t['vtp_conversionId'])}"
        else:
            dest = ""
        if dest.startswith("G-"):
            ga4_destinations.add(dest)
        normalized.append({
            "index": i,
            "function": fn,
            "type_label": TAG_FUNCTIONS.get(fn, fn),
            "event_name": r.value(t["vtp_eventName"]) if "vtp_eventName" in t else "",
            "destination": dest,
            "datalayer_events": sorted(set(events.get(i, []))),
            "fire_on": fire.get(i, [])[:3],
        })

    # 同一 dataLayer イベントで複数の計測タグが発火する箇所（多重計上の候補）
    by_event: dict[str, list[int]] = defaultdict(list)
    for t in normalized:
        if t["function"] not in ("__gaawe", "__googtag", "__gaawc"):
            continue
        for ev in t["datalayer_events"]:
            by_event[ev].append(t["index"])
    dup_fires = {ev: idxs for ev, idxs in by_event.items() if len(idxs) > 1}

    return {
        "container_id": container_id,
        "version": resource.get("version", ""),
        "tag_total": len(tags),
        "type_counter": dict(sorted(type_counter.items(), key=lambda x: -x[1])),
        "ga4_destinations": sorted(ga4_destinations),
        "ua_tag_count": ua_tag_count,
        "duplicate_fire_events": {ev: len(idxs) for ev, idxs in sorted(dup_fires.items())},
        "tags": normalized,
    }


def scan_site(url: str) -> dict:
    """公開HTMLと公開GTMコンテナを読む。コンテナ単位の失敗は全体を落とさず記録する。"""
    html = http_get(url)
    found = discover_ids(html)
    containers: list[dict] = []
    container_errors: list[dict] = []
    for container_id in found["gtm_containers"]:
        try:
            containers.append(normalize(parse_resource(fetch_container(container_id)), container_id))
        except Exception as exc:
            container_errors.append({"container_id": container_id, "error": type(exc).__name__})
    return {
        "url": url,
        **found,
        "containers": containers,
        "container_errors": container_errors,
    }


def analyze(scan: dict, known_mids: list[str], known_gtm_public_ids: list[str]) -> dict:
    """スキャン結果を、インベントリの既知情報（登録済みMID・権限のあるGTM）と突き合わせる。

    「想定外」は不正の断定ではない（別プロパティの正当な併用・移行中の場合もある）。
    確認候補として提示し、判断は人が行う。
    """
    known_mid_set = {m for m in (known_mids or []) if m}
    known_gtm_set = {g for g in (known_gtm_public_ids or []) if g}

    found_ga4 = set(scan.get("ga4_measurement_ids") or [])
    for c in scan.get("containers") or []:
        found_ga4 |= set(c.get("ga4_destinations") or [])

    return {
        "expected_ga4_found": sorted(found_ga4 & known_mid_set),
        "expected_ga4_missing": sorted(known_mid_set - found_ga4),
        "unexpected_ga4": sorted(found_ga4 - known_mid_set),
        "unknown_gtm_containers": sorted(set(scan.get("gtm_containers") or []) - known_gtm_set),
        "known_gtm_containers": sorted(set(scan.get("gtm_containers") or []) & known_gtm_set),
        "ua_ids_found": scan.get("universal_analytics_ids") or [],
        "ads_ids_found": scan.get("google_ads_ids") or [],
    }
