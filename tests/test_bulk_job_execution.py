"""一括AI分析ジョブの実行方式のテスト。

守りたいのは1点だけ。**バッチから起動したジョブが、分析されないまま
「作っただけ」で終わらない**こと。

2026-06〜11 に実際に起きた事故がこれで、auto_diagnose.py が
start_job()（デーモンスレッド）を呼んだ直後にプロセスを終えていたため、
スレッドが仕事を始める前に殺され、ジョブが queued のまま38件たまった。
画面には「分析した」と出ていたので5か月気づけなかった。

実行: リポジトリルートで `python -m pytest`
"""
import json

import pytest

import ai_executor
import bulk_analyzer


@pytest.fixture
def jobs_dir(tmp_path, monkeypatch):
    """ジョブの保存先を一時ディレクトリに逃がす（本物のdata/を汚さない）。"""
    d = tmp_path / "bulk_jobs"
    d.mkdir()
    monkeypatch.setattr(bulk_analyzer, "JOBS_DIR", d)
    return d


@pytest.fixture
def fake_detail(tmp_path, monkeypatch):
    """detail JSON と inventory を用意し、AI呼び出しは差し替える（課金させない）。"""
    details = tmp_path / "details"
    details.mkdir()
    for pid in ("111", "222"):
        (details / f"{pid}.json").write_text(
            json.dumps({"summary": {"display_name": f"サイト{pid}",
                                    "auth_email": "x@example.com"}}),
            encoding="utf-8")
    inv = tmp_path / "inventory.json"
    inv.write_text(json.dumps({"properties": [], "gtm_containers": []}), encoding="utf-8")
    monkeypatch.setattr(bulk_analyzer, "DETAILS_DIR", details)
    monkeypatch.setattr(bulk_analyzer, "INVENTORY_PATH", inv)

    calls = []

    def fake_analyze(detail, linked, model="", extra_instructions=""):
        calls.append(model)
        return {"summary": "テスト要約", "issues": [], "action_plan": [],
                "_meta": {"input_tokens": 100, "output_tokens": 200}}

    monkeypatch.setattr(ai_executor, "analyze_property", fake_analyze)
    return calls


# ============================================================
#  run_job — バッチ用の同期実行
# ============================================================

def test_run_job_completes_synchronously(jobs_dir, fake_detail):
    """run_job は戻ってきた時点で分析が終わっている（これが修正の本体）。"""
    job = bulk_analyzer.create_job(["111", "222"], model="claude-sonnet-4-6")
    assert job["status"] == "queued"

    done = bulk_analyzer.run_job(job["job_id"])

    assert done["status"] == "done"
    assert len(done["results"]) == 2
    assert all(not r.get("error") for r in done["results"])
    assert len(fake_detail) == 2  # 2プロパティ分、実際にAIを呼んでいる


def test_run_job_records_tokens_and_cost(jobs_dir, fake_detail):
    job = bulk_analyzer.create_job(["111"], model="claude-sonnet-4-6")
    done = bulk_analyzer.run_job(job["job_id"])
    assert done["actual_tokens"]["in"] == 100
    assert done["actual_tokens"]["out"] == 200
    assert done["actual_usd"] > 0


def test_run_job_survives_analysis_error(jobs_dir, fake_detail, monkeypatch):
    """1件が失敗しても残りは続け、ジョブは done で終わる。"""
    def boom(detail, linked, model="", extra_instructions=""):
        raise RuntimeError("APIが落ちた")

    monkeypatch.setattr(ai_executor, "analyze_property", boom)
    job = bulk_analyzer.create_job(["111", "222"])
    done = bulk_analyzer.run_job(job["job_id"])
    assert done["status"] == "done"
    assert len(done["results"]) == 2
    assert all(r.get("error") for r in done["results"])


def test_run_job_skips_missing_detail(jobs_dir, fake_detail):
    job = bulk_analyzer.create_job(["999"])
    done = bulk_analyzer.run_job(job["job_id"])
    assert done["status"] == "done"
    assert "not found" in done["results"][0]["error"]


def test_run_job_resumes_without_redoing_finished_properties(jobs_dir, fake_detail):
    """途中で死んだジョブを再実行しても、終わった分は二度叩かない（課金を増やさない）。"""
    job = bulk_analyzer.create_job(["111", "222"])
    # 1件だけ終わった状態を作る
    job["results"] = [{"property_id": "111", "summary": "済"}]
    job["status"] = "running"
    bulk_analyzer._save(job)

    fake_detail.clear()
    done = bulk_analyzer.run_job(job["job_id"])
    assert done["status"] == "done"
    assert len(fake_detail) == 1  # 残り1件だけを分析した


# ============================================================
#  滞留ジョブの検出と掃除
# ============================================================

def test_stale_jobs_lists_unfinished_only(jobs_dir, fake_detail):
    a = bulk_analyzer.create_job(["111"])              # queued のまま
    b = bulk_analyzer.create_job(["222"])
    bulk_analyzer.run_job(b["job_id"])                  # これは done になる

    stale = bulk_analyzer.stale_jobs()
    assert [j["job_id"] for j in stale] == [a["job_id"]]


def test_stale_jobs_includes_running(jobs_dir):
    job = bulk_analyzer.create_job(["111"])
    job["status"] = "running"
    bulk_analyzer._save(job)
    assert len(bulk_analyzer.stale_jobs()) == 1


def test_stale_jobs_ignores_broken_files(jobs_dir):
    (jobs_dir / "job_broken.json").write_text("{壊れている", encoding="utf-8")
    assert bulk_analyzer.stale_jobs() == []


def test_discard_job(jobs_dir):
    job = bulk_analyzer.create_job(["111"])
    assert bulk_analyzer.discard_job(job["job_id"]) is True
    assert bulk_analyzer.stale_jobs() == []
    assert bulk_analyzer.discard_job(job["job_id"]) is False


# ============================================================
#  auto_diagnose が同期実行を使っていること
# ============================================================

def test_auto_diagnose_waits_for_analysis(tmp_path, monkeypatch):
    """バッチ経路は分析の完了を待つ。デーモンスレッドに投げっぱなしにしない。

    ここが戻ると、また「作っただけ」のジョブが静かに溜まる。
    """
    import auto_diagnose

    monkeypatch.setattr(auto_diagnose, "AUTO_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(auto_diagnose, "detect_newly_alerted",
                        lambda: [{"property_id": "111", "display_name": "サイト",
                                  "reasons": ["テスト"], "health_grade": "F"}])
    monkeypatch.setattr(bulk_analyzer, "create_job",
                        lambda ids, **kw: {"job_id": "job_test", "property_ids": list(ids)})
    monkeypatch.setattr(bulk_analyzer, "start_job",
                        lambda jid: pytest.fail("バッチからデーモンスレッドで起動してはいけない"))

    ran = []

    def fake_run_job(job_id):
        ran.append(job_id)
        return {"status": "done", "actual_usd": 0.01,
                "results": [{"property_id": "111", "summary": "済"}]}

    monkeypatch.setattr(bulk_analyzer, "run_job", fake_run_job)

    state = auto_diagnose.run()

    assert ran == ["job_test"], "run_job で同期実行していない"
    assert state["job_status"] == "done"
    assert state["analyzed_ok"] == 1
    assert state["analyzed_error"] == 0


def test_auto_diagnose_records_failure_in_state(tmp_path, monkeypatch):
    """分析が失敗したら state にそう残る（「分析した」と嘘をつかない）。"""
    import auto_diagnose

    monkeypatch.setattr(auto_diagnose, "AUTO_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(auto_diagnose, "detect_newly_alerted",
                        lambda: [{"property_id": "111", "display_name": "サイト",
                                  "reasons": ["テスト"], "health_grade": "F"}])
    monkeypatch.setattr(bulk_analyzer, "create_job",
                        lambda ids, **kw: {"job_id": "job_test", "property_ids": list(ids)})
    monkeypatch.setattr(bulk_analyzer, "run_job",
                        lambda jid: {"status": "done", "actual_usd": 0,
                                     "results": [{"property_id": "111", "error": "APIが落ちた"}]})

    state = auto_diagnose.run()
    assert state["analyzed_ok"] == 0
    assert state["analyzed_error"] == 1


# ============================================================
#  見積もり金額
# ============================================================

def test_pricing_matches_official_rates():
    """料金表が公式価格とずれていないこと。

    ずれていると見積もり画面が誤った金額を表示する。実際、opus-4-8 が
    $15/$75（正しくは $5/$25）と3倍に設定されていた。
    """
    assert bulk_analyzer._PRICING["claude-opus-4-8"] == {"in": 5.0, "out": 25.0}
    assert bulk_analyzer._PRICING["claude-sonnet-4-6"] == {"in": 3.0, "out": 15.0}
    assert bulk_analyzer._PRICING["claude-opus-5-5"] == {"in": 4.0, "out": 20.0}
    assert bulk_analyzer._PRICING["claude-sonnet-5-5"] == {"in": 2.0, "out": 10.0}


def test_estimate_uses_known_model_rate():
    """料金表とトークン概算から金額を出していること（値は定数から引く）。"""
    n = 10
    est = bulk_analyzer.estimate_cost("claude-opus-4-8", n)
    tok = bulk_analyzer._TOKEN_EST
    expected = ((tok["in"] * n) / 1_000_000) * 5.0 + ((tok["out"] * n) / 1_000_000) * 25.0
    assert abs(est["estimated_usd"] - round(expected, 4)) < 1e-6


def test_estimate_falls_back_for_unknown_model():
    """未知のモデルでも落ちない（既定の料金で概算する）。"""
    est = bulk_analyzer.estimate_cost("claude-未知-9", 1)
    assert est["estimated_usd"] > 0


def test_token_estimate_is_not_below_observed_output():
    """見積もりの出力トークンが実測を下回らないこと。

    現行世代は thinking が既定で有効で、その分も出力に計上される。
    2026-11-26 に claude-sonnet-5-5 で実測した出力は 9,941 トークン。
    旧世代想定の 5,500 のままだと、見積もりが実費の6割しか出ない。
    """
    assert bulk_analyzer._TOKEN_EST["out"] >= 9941


def test_list_jobs_exposes_cost(jobs_dir, fake_detail):
    """一覧がコストを返すこと（画面の「推定/実コスト」列が空のままだった）。"""
    job = bulk_analyzer.create_job(["111"], model="claude-sonnet-5-5")
    rows = bulk_analyzer.list_jobs()
    assert rows[0]["estimated_usd"] > 0
    assert rows[0]["actual_usd"] is None  # 未実行なので実費はまだ無い

    bulk_analyzer.run_job(job["job_id"])
    rows = bulk_analyzer.list_jobs()
    assert rows[0]["actual_usd"] > 0
