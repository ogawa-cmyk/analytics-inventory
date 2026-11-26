"""収集そのものの健全性判定のテスト。

守りたいのは、**収集が壊れたことが静かに見過ごされない**こと。

2026-10-01 に285件あったプロパティが 11-21 には153件に半減していた。
3アカウントの認証が切れてまるごと取れなくなっていたのに、画面には
ヘッダーの「エラー 6」しか出ず、11-26 まで誰も気づかなかった。

実行: リポジトリルートで `python -m pytest`
"""
import collection_health as ch


def _inv(props=100, gtm=50, sc=20, errors=None, generated_at="2026-11-21T18:43:00Z"):
    return {"generated_at": generated_at,
            "property_count": props, "gtm_container_count": gtm, "sc_site_count": sc,
            "errors": errors or []}


def _auth_err(email, stage="auth"):
    return {"email": email, "stage": stage,
            "error": "('invalid_grant: Bad Request', {'error': 'invalid_grant'})"}


# ============================================================
#  認証の失敗
# ============================================================

def test_auth_failures_groups_by_account():
    """1アカウントで複数段階が失敗しても、アカウント単位にまとめる。"""
    inv = _inv(errors=[_auth_err("a@example.com", "auth"),
                       _auth_err("a@example.com", "sc_list_sites"),
                       _auth_err("b@example.com", "auth")])
    out = ch.auth_failures(inv)
    assert [r["email"] for r in out] == ["a@example.com", "b@example.com"]
    assert out[0]["count"] == 2
    assert set(out[0]["stages"]) == {"auth", "sc_list_sites"}


def test_auth_failures_ignores_other_errors():
    """一時的なAPI障害は認証切れではない（別物として扱う）。"""
    inv = _inv(errors=[{"email": "a@example.com", "stage": "list_properties",
                        "error": "503 The service is currently unavailable."}])
    assert ch.auth_failures(inv) == []


def test_auth_failures_handles_garbage():
    assert ch.auth_failures(None) == []
    assert ch.auth_failures({"errors": "こわれている"}) == []
    assert ch.auth_failures({"errors": [None, "x"]}) == []


# ============================================================
#  件数の急減
# ============================================================

def test_count_drop_detected():
    """285→153 のような半減を拾う（実際に見逃した事例）。"""
    drops = ch.count_drops(_inv(props=153, gtm=70, sc=23),
                           _inv(props=285, gtm=220, sc=44))
    keys = {d["key"] for d in drops}
    assert keys == {"property_count", "gtm_container_count", "sc_site_count"}
    p = next(d for d in drops if d["key"] == "property_count")
    assert (p["before"], p["after"], p["lost"]) == (285, 153, 132)


def test_small_drop_is_not_flagged():
    """数件の増減で毎回警告しない。"""
    assert ch.count_drops(_inv(props=95), _inv(props=100)) == []


def test_increase_is_not_flagged():
    assert ch.count_drops(_inv(props=300), _inv(props=100)) == []


def test_small_base_is_not_flagged():
    """母数が小さいうちは割合で判定しない（5→3 は警告しない）。"""
    assert ch.count_drops(_inv(props=3, gtm=0, sc=0), _inv(props=5, gtm=0, sc=0)) == []


def test_no_previous_record_returns_empty():
    """前回が無いときは「減っていない」とは言わない（判定しないだけ）。"""
    assert ch.count_drops(_inv(), None) == []
    assert ch.count_drops(_inv(), {}) == []


# ============================================================
#  総合判定
# ============================================================

def test_auth_failure_is_critical():
    """認証切れは最も重い。そのアカウントの計測が監視から丸ごと外れるため。"""
    state = ch.assess(_inv(errors=[_auth_err("a@example.com")]), _inv())
    assert state["level"] == "critical"
    assert len(state["auth_failures"]) == 1


def test_drop_without_auth_error_is_warn():
    state = ch.assess(_inv(props=153), _inv(props=285))
    assert state["level"] == "warn"
    assert state["count_drops"]


def test_healthy_collection_is_ok():
    state = ch.assess(_inv(props=285), _inv(props=280))
    assert state["level"] == "ok"
    assert state["auth_failures"] == []
    assert state["count_drops"] == []


def test_assess_keeps_counts_and_timestamp():
    state = ch.assess(_inv(props=153, gtm=70, sc=23), _inv(props=285))
    assert state["counts"]["property_count"] == 153
    assert state["collected_at"] == "2026-11-21T18:43:00Z"
    assert state["generated_at"]


def test_summary_line_reads_naturally():
    crit = ch.assess(_inv(errors=[_auth_err("a@example.com")]), _inv())
    assert "認証が切れて" in ch.summary_line(crit)

    warn = ch.assess(_inv(props=153), _inv(props=285))
    assert "285件 → 153件" in ch.summary_line(warn)

    ok = ch.assess(_inv(), _inv())
    assert "正常" in ch.summary_line(ok)


# ============================================================
#  保存と読み出し
# ============================================================

def test_save_and_load(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "HEALTH_PATH", tmp_path / "collection_health.json")
    state = ch.assess(_inv(props=153), _inv(props=285))
    ch.save(state)
    assert ch.load()["level"] == "warn"


def test_load_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "HEALTH_PATH", tmp_path / "none.json")
    assert ch.load() is None


def test_load_survives_broken_file(tmp_path, monkeypatch):
    p = tmp_path / "broken.json"
    p.write_text("{こわれている", encoding="utf-8")
    monkeypatch.setattr(ch, "HEALTH_PATH", p)
    assert ch.load() is None
