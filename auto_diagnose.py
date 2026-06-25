"""Run after indexer: detect newly-alerted properties and queue a bulk AI analysis job.

Triggered from run_indexer.bat so it runs automatically every 5 days.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import bulk_analyzer
import diff as diff_mod
import health
from config import DATA_DIR, INVENTORY_PATH

AUTO_STATE_PATH = DATA_DIR / "auto_diagnose_latest.json"
DEFAULT_MAX_PER_RUN = 15
DEFAULT_MODEL = "claude-sonnet-4-6"


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def detect_newly_alerted() -> list[dict]:
    """Compare latest snapshot vs previous to find properties that newly have problems."""
    if not INVENTORY_PATH.exists():
        return []
    inv = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    props = inv.get("properties", [])
    health.enrich_properties(props)

    snaps = diff_mod.list_snapshots()
    prev_map: dict = {}
    if len(snaps) >= 2:
        prev = diff_mod.load_snapshot(snaps[1]["file"]) or {}
        for p in (prev.get("properties") or []):
            prev_map[str(p.get("property_id"))] = p

    flagged = []
    for p in props:
        pid = str(p.get("property_id"))
        prev = prev_map.get(pid)
        reasons = []
        # Tracking became broken
        if prev and prev.get("is_tracked") and not p.get("is_tracked"):
            reasons.append("計測が新規停止")
        # Key event count dropped
        if prev and (prev.get("key_event_count") or 0) > (p.get("key_event_count") or 0):
            reasons.append(f"KE数減少 {prev.get('key_event_count')} → {p.get('key_event_count')}")
        # Sessions plunged > 50%
        if prev and (prev.get("sessions_7d") or 0) > 0:
            cur = p.get("sessions_7d") or 0
            if cur < prev["sessions_7d"] * 0.5:
                reasons.append(f"Sessions急減 {prev['sessions_7d']:,} → {cur:,}")
        # If no prev (first run) we still flag low-grade properties
        if not prev:
            if p.get("has_error_alert"):
                reasons.append("初回検出：重大アラートあり")
        if reasons:
            flagged.append({
                "property_id": pid,
                "display_name": p.get("display_name"),
                "reasons": reasons,
                "health_grade": p.get("health_grade"),
            })
    flagged.sort(key=lambda x: (-len(x["reasons"]), x.get("health_grade") or "Z"))
    return flagged


def run(max_n: int = DEFAULT_MAX_PER_RUN, model: str = DEFAULT_MODEL) -> dict:
    flagged = detect_newly_alerted()
    _log(f"Detected {len(flagged)} newly-alerted properties.")
    if not flagged:
        state = {"generated_at": datetime.now(timezone.utc).isoformat(),
                 "job_id": None, "flagged_count": 0, "flagged": []}
        AUTO_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        _log("No new alerts. Done.")
        return state

    ids = [f["property_id"] for f in flagged[:max_n]]
    label = f"自動診断 {datetime.now().strftime('%Y-%m-%d %H:%M')} - {len(ids)}件"
    job = bulk_analyzer.create_job(ids, model=model, kind="auto", label=label, max_batch=max_n)
    _log(f"Created job {job['job_id']} for {len(ids)} properties.")
    bulk_analyzer.start_job(job["job_id"])

    state = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "job_id": job["job_id"],
        "flagged_count": len(flagged),
        "analyzed_count": len(ids),
        "flagged": flagged,
        "model": model,
    }
    AUTO_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"Auto-diagnose state saved → {AUTO_STATE_PATH}")
    return state


def load_state() -> dict | None:
    if not AUTO_STATE_PATH.exists():
        return None
    return json.loads(AUTO_STATE_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    args = sys.argv[1:]
    max_n = DEFAULT_MAX_PER_RUN
    model = DEFAULT_MODEL
    for a in args:
        if a.startswith("--max="):
            max_n = int(a.split("=", 1)[1])
        elif a.startswith("--model="):
            model = a.split("=", 1)[1]
    run(max_n=max_n, model=model)
