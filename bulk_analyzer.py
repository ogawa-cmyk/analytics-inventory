"""Bulk AI analysis: queue multiple properties, run sequentially, persist progress.

Job state lives in data/bulk_jobs/{job_id}.json so it survives restarts.
"""
from __future__ import annotations
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import ai_executor
import crossref
from config import DATA_DIR, DETAILS_DIR, INVENTORY_PATH

JOBS_DIR = DATA_DIR / "bulk_jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_MAX_BATCH = 20

# Pricing (per million tokens). Approximate; update if Anthropic pricing changes.
_PRICING = {
    "claude-sonnet-4-6": {"in": 3.0, "out": 15.0},
    "claude-opus-4-8":   {"in": 15.0, "out": 75.0},
    "claude-haiku-4-5-20251001": {"in": 1.0, "out": 5.0},
}

# Per-property token estimates (averaged from observed runs)
_TOKEN_EST = {"in": 3000, "out": 5500}


def estimate_cost(model: str, n_properties: int) -> dict:
    p = _PRICING.get(model, _PRICING["claude-sonnet-4-6"])
    total_in = _TOKEN_EST["in"] * n_properties
    total_out = _TOKEN_EST["out"] * n_properties
    usd = (total_in / 1_000_000) * p["in"] + (total_out / 1_000_000) * p["out"]
    return {
        "n": n_properties,
        "model": model,
        "estimated_input_tokens": total_in,
        "estimated_output_tokens": total_out,
        "estimated_usd": round(usd, 4),
        "estimated_jpy": round(usd * 155, 0),
    }


def _job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def _load(job_id: str) -> dict | None:
    p = _job_path(job_id)
    if not p.exists():
        return None
    return _safe_load(p)


def _save(job: dict) -> None:
    # Atomic write: write to temp then replace, so status-polling never reads a
    # half-written file (which would crash json.loads).
    path = _job_path(job["job_id"])
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    import os as _os
    _os.replace(tmp, path)


def _safe_load(p: Path) -> dict | None:
    """Read a job file, tolerating a transient half-written state."""
    for _ in range(3):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            time.sleep(0.05)
    return None


def list_jobs(limit: int = 50) -> list[dict]:
    out = []
    for p in sorted(JOBS_DIR.glob("*.json"), reverse=True)[:limit]:
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
            out.append({
                "job_id": j["job_id"],
                "started_at": j.get("started_at"),
                "finished_at": j.get("finished_at"),
                "model": j.get("model"),
                "status": j.get("status"),
                "n_total": len(j.get("property_ids") or []),
                "n_done": len(j.get("results") or []),
                "n_error": sum(1 for r in (j.get("results") or []) if r.get("error")),
                "kind": j.get("kind", "manual"),
                "label": j.get("label", ""),
            })
        except Exception:
            continue
    return out


def get_job(job_id: str) -> dict | None:
    return _load(job_id)


def create_job(property_ids: list[str], model: str = ai_executor.DEFAULT_MODEL,
               extra: str = "", kind: str = "manual", label: str = "",
               max_batch: int = DEFAULT_MAX_BATCH) -> dict:
    ids = list(dict.fromkeys(str(p) for p in property_ids))[:max_batch]
    job_id = "job_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:6]
    job = {
        "job_id": job_id,
        "kind": kind,
        "label": label,
        "model": model,
        "extra": extra,
        "property_ids": ids,
        "results": [],
        "status": "queued",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "estimate": estimate_cost(model, len(ids)),
        "actual_tokens": {"in": 0, "out": 0},
    }
    _save(job)
    return job


def _process(job_id: str) -> None:
    job = _load(job_id)
    if not job:
        return
    job["status"] = "running"
    _save(job)

    inv = json.loads(INVENTORY_PATH.read_text(encoding="utf-8")) if INVENTORY_PATH.exists() else {"properties": [], "gtm_containers": []}
    cross = crossref.build(inv.get("properties", []), inv.get("gtm_containers", []))

    for pid in job["property_ids"]:
        if any(r["property_id"] == pid for r in job["results"]):
            continue
        detail_path = DETAILS_DIR / f"{pid}.json"
        if not detail_path.exists():
            job["results"].append({"property_id": pid, "error": "detail JSON not found"})
            _save(job)
            continue
        detail = json.loads(detail_path.read_text(encoding="utf-8"))
        linked = cross["property_to_containers"].get(pid, [])
        try:
            res = ai_executor.analyze_property(detail, linked, model=job["model"], extra_instructions=job.get("extra", ""))
        except Exception as e:
            res = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
        entry = {
            "property_id": pid,
            "display_name": (detail.get("summary") or {}).get("display_name"),
            "auth_email": (detail.get("summary") or {}).get("auth_email"),
            "health_grade": (detail.get("summary") or {}).get("health_grade"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        if "error" in res:
            entry["error"] = res["error"]
        else:
            entry["summary"] = res.get("summary")
            entry["issues"] = res.get("issues", [])
            entry["action_plan"] = res.get("action_plan", [])
            entry["meta"] = res.get("_meta", {})
            meta = res.get("_meta", {})
            job["actual_tokens"]["in"] += meta.get("input_tokens") or 0
            job["actual_tokens"]["out"] += meta.get("output_tokens") or 0
        job["results"].append(entry)
        _save(job)

    job["status"] = "done"
    job["finished_at"] = datetime.now(timezone.utc).isoformat()
    p = _PRICING.get(job["model"], _PRICING["claude-sonnet-4-6"])
    usd = (job["actual_tokens"]["in"] / 1_000_000) * p["in"] + (job["actual_tokens"]["out"] / 1_000_000) * p["out"]
    job["actual_usd"] = round(usd, 4)
    _save(job)


def start_job(job_id: str) -> None:
    threading.Thread(target=_process, args=(job_id,), daemon=True).start()


def select_properties_alerted(inv: dict, max_n: int = DEFAULT_MAX_BATCH) -> list[str]:
    """Return property_ids that have alerts (used by auto-diagnose and 'select alerted' helper)."""
    import health
    props = inv.get("properties", [])
    health.enrich_properties(props)
    out = []
    for p in props:
        if p.get("has_error_alert") or (p.get("alert_count") or 0) >= 2 or (p.get("health_grade") in ("D", "F")):
            out.append(str(p.get("property_id")))
        if len(out) >= max_n:
            break
    return out


def aggregate_top_issues(job: dict, top_n: int = 20) -> list[dict]:
    """Cross-property issue ranking from a bulk job."""
    bucket = []
    for r in job.get("results", []):
        for i in r.get("issues", []):
            bucket.append({
                "property_id": r.get("property_id"),
                "display_name": r.get("display_name"),
                "severity": i.get("severity"),
                "category": i.get("category"),
                "title": i.get("title"),
                "description": i.get("description"),
            })
    weight = {"high": 3, "medium": 2, "low": 1}
    bucket.sort(key=lambda x: -weight.get(x["severity"], 0))
    return bucket[:top_n]
