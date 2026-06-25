"""Flask UI for the GA4/GTM inventory."""
import csv
import io
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, abort, jsonify, render_template, request, send_file

import ai_executor
import ai_prompts
import annotations as ann
import bulk_analyzer
import crossref
import diff as diff_mod
import health
import search_index
from config import DETAILS_DIR, GTM_DETAILS_DIR, SC_DETAILS_DIR, INVENTORY_PATH, SERVER_PORT

app = Flask(__name__, template_folder="templates", static_folder="static")


_GTM_LIVE_CACHE: dict = {}


def _load_gtm_live(cid: str) -> dict | None:
    if cid in _GTM_LIVE_CACHE:
        return _GTM_LIVE_CACHE[cid]
    path = GTM_DETAILS_DIR / f"{cid}.json"
    if not path.exists():
        _GTM_LIVE_CACHE[cid] = None
        return None
    try:
        live = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        live = None
    _GTM_LIVE_CACHE[cid] = live
    return live


def _extract_domain(url: str | None) -> str:
    if not url:
        return ""
    s = url
    if s.startswith("https://"):
        s = s[8:]
    elif s.startswith("http://"):
        s = s[7:]
    s = s.split("/", 1)[0]
    if s.startswith("www."):
        s = s[4:]
    return s.lower()


def _load_inventory() -> dict:
    if not INVENTORY_PATH.exists():
        return {"generated_at": None, "properties": [], "gtm_containers": [], "sc_sites": [], "errors": []}
    inv = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    health.enrich_properties(inv.get("properties", []))
    ann.enrich_properties(inv.get("properties", []))
    ann.enrich_containers(inv.get("gtm_containers", []))
    health.enrich_containers_with_score(inv.get("gtm_containers", []), _load_gtm_live)
    # SC enrichment uses GA4 domains for linkage detection
    ga4_domains: set = set()
    for p in inv.get("properties", []):
        for stream in (p.get("measurement_ids") or []):
            pass
    # Use stream URIs from details if available; fall back to property display URLs
    for p in inv.get("properties", []):
        pid = p.get("property_id")
        path = DETAILS_DIR / f"{pid}.json"
        if not path.exists():
            continue
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            for s in d.get("streams", []) or []:
                dom = _extract_domain(s.get("default_uri"))
                if dom:
                    ga4_domains.add(dom)
        except Exception:
            continue
    health.enrich_sc_sites(inv.get("sc_sites", []) or [], ga4_domains)
    return inv


@app.context_processor
def inject_globals():
    return {"all_tags": ann.all_tags()}


@app.route("/")
def home():
    inv = _load_inventory()
    props = inv.get("properties", [])
    conts = inv.get("gtm_containers", [])
    alerts = health.alert_count_summary(props)
    gtm_alerts = health.container_alert_summary(conts)

    # === LAYER 1 — Top KPI ===
    grade_dist = Counter(p.get("health_grade") for p in props)
    gtm_grade_dist = Counter(c.get("health_grade") for c in conts)
    avg_score = round(sum((p.get("health_score") or 0) for p in props) / max(len(props), 1), 1)
    gtm_avg_score = round(sum((c.get("health_score") or 0) for c in conts) / max(len(conts), 1), 1)
    by_email = Counter(p.get("auth_email") for p in props)
    n_tracked = sum(1 for p in props if p.get("is_tracked"))
    n_ecom = sum(1 for p in props if p.get("is_ecommerce"))
    n_gtm_active = sum(1 for c in conts if (c.get("tag_count") or 0) > 0)
    n_gtm_ga4 = sum(1 for c in conts if c.get("ga4_measurement_ids"))
    n_gtm_ua = sum(1 for c in conts if c.get("_score_summary", {}).get("ua_count", 0) >= 1)
    snapshots_n = len(diff_mod.list_snapshots())

    # === LAYER 3 — AI panel ===
    import auto_diagnose
    auto_state = auto_diagnose.load_state()
    recent_jobs = bulk_analyzer.list_jobs(limit=5)
    ai_cov_prop = ai_executor.coverage_property()
    ai_cov_gtm = ai_executor.coverage_gtm()
    top_categories = ai_executor.top_issue_categories(top_n=5)

    # === LAYER 4 — Trends & highlights ===
    recent_changes = []
    try:
        snaps = diff_mod.list_snapshots()
        if len(snaps) >= 2:
            prev = diff_mod.load_snapshot(snaps[1]["file"])
            prev_map = {str(p.get("property_id")): p for p in (prev.get("properties") or [])}
            for p in props:
                d = diff_mod.diff_property(p, prev_map.get(str(p.get("property_id"))))
                if d and not d.get("first_seen"):
                    recent_changes.append({"prop": p, "diff": d})
            recent_changes.sort(
                key=lambda x: abs(x["diff"].get("sessions_7d", {}).get("delta", 0))
                if isinstance(x["diff"].get("sessions_7d"), dict) else 0,
                reverse=True,
            )
            recent_changes = recent_changes[:5]
    except Exception:
        recent_changes = []

    error_props = sorted(
        [p for p in props if p.get("has_error_alert")],
        key=lambda p: p.get("health_score") or 0,
    )[:10]
    top_health = sorted(props, key=lambda p: -(p.get("health_score") or 0))[:5]

    gtm_error_conts = sorted(
        [c for c in conts if c.get("has_error_alert") or c.get("health_grade") in ("D", "F")
         or c.get("_score_summary", {}).get("ua_count", 0) >= 3
         or not (c.get("ga4_measurement_ids") or [])],
        key=lambda c: c.get("health_score") or 0,
    )[:10]
    gtm_top_health = sorted(conts, key=lambda c: -(c.get("health_score") or 0))[:5]

    cross = crossref.build(props, conts)
    duplicate_mids = cross["duplicate_mids_in_containers"][:10]

    # Mismatch: GA4 properties whose MID is not received by any GTM container
    mismatch_props = []
    mid_to_cont = cross["mid_to_containers"]
    for p in props:
        mids = p.get("measurement_ids") or []
        unrelated = [mid for mid in mids if mid and not mid_to_cont.get(mid)]
        if unrelated and p.get("is_tracked"):
            mismatch_props.append({"prop": p, "unmatched_mids": unrelated})
        if len(mismatch_props) >= 5:
            break

    # === SC stats ===
    sc_sites = inv.get("sc_sites") or []
    sc_grade_dist = Counter(s.get("health_grade") for s in sc_sites)
    sc_avg_score = round(sum((s.get("health_score") or 0) for s in sc_sites) / max(len(sc_sites), 1), 1) if sc_sites else 0
    sc_total_clicks = sum((s.get("clicks_28d") or 0) for s in sc_sites)
    sc_total_imps = sum((s.get("impressions_28d") or 0) for s in sc_sites)
    sc_alerts_summary = health.sc_alert_summary(sc_sites) if sc_sites else {"error_count": 0, "warn_count": 0, "issues": {"no_clicks": 0, "no_sitemap": 0, "low_ctr": 0, "no_ga4_link": 0}}
    sc_top_trouble = sorted(
        [s for s in sc_sites if s.get("has_error_alert") or s.get("health_grade") in ("D", "F")],
        key=lambda s: s.get("health_score") or 0,
    )[:10]
    sc_top_health = sorted(sc_sites, key=lambda s: -(s.get("health_score") or 0))[:5]

    # === LAYER 5 — Fixed assets ===
    fav_props = [p for p in props if p.get("ann_favorite")]
    fav_conts = [c for c in conts if c.get("ann_favorite")]
    tag_counter: Counter = Counter()
    for p in props:
        for t in (p.get("ann_tags") or []):
            tag_counter[t] += 1
    for c in conts:
        for t in (c.get("ann_tags") or []):
            tag_counter[t] += 1
    tag_dist = tag_counter.most_common(15)

    return render_template(
        "home.html",
        inv=inv,
        properties=props,
        containers=conts,
        # layer 1
        n_tracked=n_tracked, n_ecom=n_ecom,
        n_gtm_active=n_gtm_active, n_gtm_ga4=n_gtm_ga4, n_gtm_ua=n_gtm_ua,
        avg_score=avg_score, gtm_avg_score=gtm_avg_score,
        grade_dist=grade_dist, gtm_grade_dist=gtm_grade_dist,
        by_email=by_email, snapshot_count=snapshots_n,
        sc_sites=sc_sites, sc_grade_dist=sc_grade_dist, sc_avg_score=sc_avg_score,
        sc_total_clicks=sc_total_clicks, sc_total_imps=sc_total_imps,
        sc_alerts_summary=sc_alerts_summary,
        sc_top_trouble=sc_top_trouble, sc_top_health=sc_top_health,
        # layer 2
        alerts=alerts, gtm_alerts=gtm_alerts,
        duplicate_mids=duplicate_mids,
        # layer 3
        auto_state=auto_state, recent_jobs=recent_jobs,
        ai_cov_prop=ai_cov_prop, ai_cov_gtm=ai_cov_gtm,
        top_categories=top_categories,
        # layer 4
        recent_changes=recent_changes,
        error_props=error_props, top_health=top_health,
        gtm_error_conts=gtm_error_conts, gtm_top_health=gtm_top_health,
        mismatch_props=mismatch_props,
        # layer 5
        fav_props=fav_props, fav_conts=fav_conts,
        tag_dist=tag_dist,
    )


@app.route("/properties")
def properties_view():
    inv = _load_inventory()
    return render_template("index.html", inv=inv, properties=inv.get("properties", []))


@app.route("/gtm")
def gtm_view():
    inv = _load_inventory()
    cross = crossref.build(inv.get("properties", []), inv.get("gtm_containers", []))
    cont_to_props = cross["container_to_properties"]
    names_by_cid: dict[str, dict] = {}
    has_detail: dict[str, bool] = {}
    for c in inv.get("gtm_containers", []):
        cid = c.get("container_id")
        if not cid:
            continue
        path = GTM_DETAILS_DIR / f"{cid}.json"
        has_detail[cid] = path.exists()
        if not path.exists():
            names_by_cid[cid] = {"tags": [], "triggers": [], "variables": []}
            continue
        try:
            live = json.loads(path.read_text(encoding="utf-8"))
            names_by_cid[cid] = {
                "tags": [t.get("name", "") for t in (live.get("tag") or [])],
                "triggers": [t.get("name", "") for t in (live.get("trigger") or [])],
                "variables": [v.get("name", "") for v in (live.get("variable") or [])],
            }
        except Exception:
            names_by_cid[cid] = {"tags": [], "triggers": [], "variables": []}
    return render_template("gtm.html", inv=inv, containers=inv.get("gtm_containers", []),
                           names_by_cid=names_by_cid, has_detail=has_detail,
                           cont_to_props=cont_to_props)


@app.route("/property/<pid>")
def property_detail(pid: str):
    path = DETAILS_DIR / f"{pid}.json"
    if not path.exists():
        abort(404)
    detail = json.loads(path.read_text(encoding="utf-8"))
    inv = _load_inventory()
    summary = detail.get("summary") or {}
    # Re-enrich the summary for the detail page
    health.enrich_properties([summary])
    ann.enrich_properties([summary])
    detail["summary"] = summary
    detail["score"] = health.score_property(summary)
    detail["alerts"] = health.detect_alerts(summary)
    prev = diff_mod.previous_snapshot_for(pid)
    detail["diff"] = diff_mod.diff_property(summary, prev)
    detail["annotation"] = ann.get("properties", str(pid))
    cross = crossref.build(inv.get("properties", []), inv.get("gtm_containers", []))
    detail["linked_containers"] = cross["property_to_containers"].get(pid, [])
    detail["ai_templates"] = ai_prompts.list_for_property(detail)
    detail["latest_ai_run"] = ai_executor.latest_run(pid)
    detail["ai_runs"] = ai_executor.list_runs(pid)
    return render_template("property.html", d=detail, pid=pid, inv=inv,
                           ai_services=ai_prompts.AI_SERVICES)


@app.route("/api/property/<pid>/ai_analyze", methods=["POST"])
def api_property_ai_analyze(pid: str):
    path = DETAILS_DIR / f"{pid}.json"
    if not path.exists():
        abort(404)
    detail = json.loads(path.read_text(encoding="utf-8"))
    body = request.get_json(force=True, silent=True) or {}
    model = body.get("model") or ai_executor.DEFAULT_MODEL
    extra = body.get("extra", "")
    inv = _load_inventory()
    cross = crossref.build(inv.get("properties", []), inv.get("gtm_containers", []))
    linked = cross["property_to_containers"].get(pid, [])
    result = ai_executor.analyze_property(detail, linked, model=model, extra_instructions=extra)
    return jsonify(result)


@app.route("/api/property/<pid>/ai_run/<stamp>")
def api_property_ai_run(pid: str, stamp: str):
    run = ai_executor.load_run(pid, stamp)
    if not run:
        abort(404)
    return jsonify(run)


@app.route("/api/bulk/estimate")
def api_bulk_estimate():
    n = int(request.args.get("n") or 0)
    model = request.args.get("model") or ai_executor.DEFAULT_MODEL
    return jsonify(bulk_analyzer.estimate_cost(model, n))


@app.route("/api/bulk/select_alerted")
def api_bulk_select_alerted():
    inv = _load_inventory()
    max_n = int(request.args.get("max") or bulk_analyzer.DEFAULT_MAX_BATCH)
    ids = bulk_analyzer.select_properties_alerted(inv, max_n=max_n)
    return jsonify({"property_ids": ids, "n": len(ids)})


@app.route("/api/alerts/<kind>")
def api_alerts(kind: str):
    inv = _load_inventory()
    props = inv.get("properties", []) or []
    conts = inv.get("gtm_containers", []) or []
    items: list = []

    if kind == "untracked":
        for p in props:
            if p.get("is_tracked") is False:
                items.append({
                    "kind": "property", "id": p.get("property_id"),
                    "name": p.get("display_name"), "subtitle": p.get("auth_email"),
                    "grade": p.get("health_grade"),
                    "link": f"/property/{p.get('property_id')}",
                    "extra": f"sessions7d={(p.get('sessions_7d') or 0):,}",
                })
    elif kind == "no_ke":
        for p in props:
            if (p.get("key_event_count") or 0) == 0:
                items.append({
                    "kind": "property", "id": p.get("property_id"),
                    "name": p.get("display_name"), "subtitle": p.get("auth_email"),
                    "grade": p.get("health_grade"),
                    "link": f"/property/{p.get('property_id')}",
                    "extra": f"events7d={(p.get('events_7d') or 0):,}",
                })
    elif kind == "no_streams":
        for p in props:
            if (p.get("stream_count") or 0) == 0:
                items.append({
                    "kind": "property", "id": p.get("property_id"),
                    "name": p.get("display_name"), "subtitle": p.get("auth_email"),
                    "grade": p.get("health_grade"),
                    "link": f"/property/{p.get('property_id')}",
                })
    elif kind == "api_err":
        for p in props:
            if p.get("data_api_ok") is False:
                items.append({
                    "kind": "property", "id": p.get("property_id"),
                    "name": p.get("display_name"), "subtitle": p.get("auth_email"),
                    "grade": p.get("health_grade"),
                    "link": f"/property/{p.get('property_id')}",
                    "extra": (p.get("data_api_error") or "")[:80],
                })
    elif kind == "cd_overflow":
        for p in props:
            if (p.get("custom_dimension_count") or 0) > 50:
                items.append({
                    "kind": "property", "id": p.get("property_id"),
                    "name": p.get("display_name"), "subtitle": p.get("auth_email"),
                    "grade": p.get("health_grade"),
                    "link": f"/property/{p.get('property_id')}",
                    "extra": f"CD {p.get('custom_dimension_count')}件",
                })
    elif kind == "ua_left":
        for c in conts:
            if (c.get("_score_summary") or {}).get("ua_count", 0) >= 3:
                items.append({
                    "kind": "container", "id": c.get("container_id"),
                    "name": c.get("name"), "subtitle": f"{c.get('account_name')} / {c.get('auth_email')}",
                    "grade": c.get("health_grade"),
                    "link": f"/gtm/{c.get('container_id')}/tag",
                    "extra": f"UA系{c['_score_summary']['ua_count']}件 / 全{c.get('tag_count', 0)}タグ",
                })
    elif kind == "no_ga4":
        for c in conts:
            if not (c.get("ga4_measurement_ids") or []):
                items.append({
                    "kind": "container", "id": c.get("container_id"),
                    "name": c.get("name"), "subtitle": f"{c.get('account_name')} / {c.get('auth_email')}",
                    "grade": c.get("health_grade"),
                    "link": f"/gtm/{c.get('container_id')}/tag",
                    "extra": f"タグ{c.get('tag_count', 0)}件",
                })
    elif kind == "no_tags":
        for c in conts:
            if (c.get("tag_count") or 0) == 0:
                items.append({
                    "kind": "container", "id": c.get("container_id"),
                    "name": c.get("name"), "subtitle": f"{c.get('account_name')} / {c.get('auth_email')}",
                    "grade": c.get("health_grade"),
                    "link": f"/gtm/{c.get('container_id')}/tag",
                })
    elif kind == "duplicate_mids":
        cross = crossref.build(props, conts)
        for d in cross["duplicate_mids_in_containers"]:
            items.append({
                "kind": "duplicate_mid",
                "name": d.get("mid"),
                "subtitle": f"{len(d.get('containers') or [])} コンテナで使用",
                "containers": d.get("containers"),
            })
    elif kind == "sc_no_clicks":
        for s in (inv.get("sc_sites") or []):
            if (s.get("clicks_28d") or 0) == 0:
                items.append({
                    "kind": "sc_site", "id": s.get("site_hash"),
                    "name": s.get("site_url"), "subtitle": s.get("auth_email"),
                    "grade": s.get("health_grade"),
                    "link": f"/sc/{s.get('site_hash')}",
                    "extra": f"imp={s.get('impressions_28d') or 0}",
                })
    elif kind == "sc_no_sitemap":
        for s in (inv.get("sc_sites") or []):
            if (s.get("sitemap_count") or 0) == 0:
                items.append({
                    "kind": "sc_site", "id": s.get("site_hash"),
                    "name": s.get("site_url"), "subtitle": s.get("auth_email"),
                    "grade": s.get("health_grade"),
                    "link": f"/sc/{s.get('site_hash')}",
                })
    elif kind == "sc_sitemap_errors":
        for s in (inv.get("sc_sites") or []):
            if (s.get("sitemap_errors") or 0) > 0:
                items.append({
                    "kind": "sc_site", "id": s.get("site_hash"),
                    "name": s.get("site_url"), "subtitle": s.get("auth_email"),
                    "grade": s.get("health_grade"),
                    "link": f"/sc/{s.get('site_hash')}",
                    "extra": f"errors={s.get('sitemap_errors')}",
                })
    elif kind == "sc_low_ctr":
        for s in (inv.get("sc_sites") or []):
            ctr = s.get("ctr_28d") or 0
            if 0 < ctr < 0.005 and (s.get("impressions_28d") or 0) > 1000:
                items.append({
                    "kind": "sc_site", "id": s.get("site_hash"),
                    "name": s.get("site_url"), "subtitle": s.get("auth_email"),
                    "grade": s.get("health_grade"),
                    "link": f"/sc/{s.get('site_hash')}",
                    "extra": f"CTR={ctr*100:.2f}% / imp={s.get('impressions_28d') or 0:,}",
                })
    elif kind == "sc_no_ga4":
        for s in (inv.get("sc_sites") or []):
            if not s.get("has_ga4_link"):
                items.append({
                    "kind": "sc_site", "id": s.get("site_hash"),
                    "name": s.get("site_url"), "subtitle": s.get("auth_email"),
                    "grade": s.get("health_grade"),
                    "link": f"/sc/{s.get('site_hash')}",
                })
    elif kind == "mismatch":
        cross = crossref.build(props, conts)
        mid_to_cont = cross["mid_to_containers"]
        for p in props:
            if not p.get("is_tracked"):
                continue
            mids = p.get("measurement_ids") or []
            unrelated = [mid for mid in mids if mid and not mid_to_cont.get(mid)]
            if unrelated:
                items.append({
                    "kind": "mismatch",
                    "id": p.get("property_id"),
                    "name": p.get("display_name"),
                    "subtitle": p.get("auth_email"),
                    "grade": p.get("health_grade"),
                    "link": f"/property/{p.get('property_id')}",
                    "unmatched_mids": unrelated,
                })

    return jsonify({"kind": kind, "count": len(items), "items": items})


@app.route("/api/auto_diagnose/run", methods=["POST"])
def api_auto_diagnose_run():
    import auto_diagnose
    state = auto_diagnose.run()
    return jsonify(state)


@app.route("/api/bulk/start", methods=["POST"])
def api_bulk_start():
    body = request.get_json(force=True, silent=True) or {}
    ids = body.get("property_ids") or []
    model = body.get("model") or ai_executor.DEFAULT_MODEL
    extra = body.get("extra", "")
    label = body.get("label", "")
    if not ids:
        return jsonify({"error": "property_ids が空です"}), 400
    job = bulk_analyzer.create_job(ids, model=model, extra=extra, label=label)
    bulk_analyzer.start_job(job["job_id"])
    return jsonify({"job_id": job["job_id"], "status": job["status"], "estimate": job["estimate"]})


@app.route("/api/bulk/<job_id>/status")
def api_bulk_status(job_id: str):
    job = bulk_analyzer.get_job(job_id)
    if not job:
        abort(404)
    return jsonify({
        "job_id": job_id,
        "status": job["status"],
        "n_total": len(job["property_ids"]),
        "n_done": len(job["results"]),
        "n_error": sum(1 for r in job["results"] if r.get("error")),
        "current_property": job["property_ids"][len(job["results"])] if job["status"] == "running" and len(job["results"]) < len(job["property_ids"]) else None,
        "estimate": job["estimate"],
        "actual_tokens": job.get("actual_tokens"),
        "actual_usd": job.get("actual_usd"),
        "finished_at": job.get("finished_at"),
    })


@app.route("/bulk")
def bulk_list():
    jobs = bulk_analyzer.list_jobs()
    inv = _load_inventory()
    import auto_diagnose
    auto_state = auto_diagnose.load_state()
    return render_template("bulk_list.html", inv=inv, jobs=jobs, auto_state=auto_state)


@app.route("/bulk/<job_id>")
def bulk_view(job_id: str):
    job = bulk_analyzer.get_job(job_id)
    if not job:
        abort(404)
    inv = _load_inventory()
    top_issues = bulk_analyzer.aggregate_top_issues(job, top_n=20)
    return render_template("bulk_view.html", inv=inv, job=job, top_issues=top_issues)


@app.route("/api/property/<pid>/ai_prompt")
def api_property_ai_prompt(pid: str):
    tpl = request.args.get("template")
    if not tpl:
        abort(400)
    path = DETAILS_DIR / f"{pid}.json"
    if not path.exists():
        abort(404)
    detail = json.loads(path.read_text(encoding="utf-8"))
    inv = _load_inventory()
    cross = crossref.build(inv.get("properties", []), inv.get("gtm_containers", []))
    linked = cross["property_to_containers"].get(pid, [])
    prompt = ai_prompts.render(tpl, detail, linked_containers=linked)
    return jsonify({"prompt": prompt})


@app.route("/gtm/<cid>/download")
def gtm_download(cid: str):
    path = GTM_DETAILS_DIR / f"{cid}.json"
    if not path.exists():
        abort(404)
    inv = _load_inventory()
    container = next((c for c in inv.get("gtm_containers", []) if c.get("container_id") == cid), {})
    safe_name = (container.get("name") or cid).replace("/", "_").replace("\\", "_").replace(" ", "_")
    return send_file(
        path,
        as_attachment=True,
        download_name=f"gtm_{safe_name}_{cid}.json",
        mimetype="application/json",
    )


@app.route("/gtm/<cid>/<kind>")
def gtm_detail(cid: str, kind: str):
    if kind not in ("tag", "trigger", "variable"):
        abort(404)
    path = GTM_DETAILS_DIR / f"{cid}.json"
    if not path.exists():
        abort(404)
    live = json.loads(path.read_text(encoding="utf-8"))
    inv = _load_inventory()
    container = next((c for c in inv.get("gtm_containers", []) if c.get("container_id") == cid), {})
    triggers_by_id = {t.get("triggerId"): t for t in (live.get("trigger") or [])}
    variables_by_name = {v.get("name"): v for v in (live.get("variable") or [])}
    cross = crossref.build(inv.get("properties", []), inv.get("gtm_containers", []))
    linked_props = cross["container_to_properties"].get(cid, [])
    return render_template(
        "gtm_detail.html",
        cid=cid,
        kind=kind,
        live=live,
        container=container,
        items=live.get(kind, []) or [],
        triggers_by_id=triggers_by_id,
        variables_by_name=variables_by_name,
        inv=inv,
        linked_props=linked_props,
        latest_ai_run=ai_executor.latest_gtm_run(cid),
        ai_runs=ai_executor.list_gtm_runs(cid),
        ai_services=ai_prompts.AI_SERVICES,
    )


@app.route("/api/gtm/<cid>/ai_analyze", methods=["POST"])
def api_gtm_ai_analyze(cid: str):
    path = GTM_DETAILS_DIR / f"{cid}.json"
    if not path.exists():
        abort(404)
    live = json.loads(path.read_text(encoding="utf-8"))
    inv = _load_inventory()
    container = next((c for c in inv.get("gtm_containers", []) if c.get("container_id") == cid), {})
    body = request.get_json(force=True, silent=True) or {}
    model = body.get("model") or ai_executor.DEFAULT_MODEL
    extra = body.get("extra", "")
    cross = crossref.build(inv.get("properties", []), inv.get("gtm_containers", []))
    linked_props = cross["container_to_properties"].get(cid, [])
    result = ai_executor.analyze_container(container, live, linked_props, model=model, extra_instructions=extra)
    return jsonify(result)


@app.route("/api/gtm/<cid>/ai_run/<stamp>")
def api_gtm_ai_run(cid: str, stamp: str):
    run = ai_executor.load_gtm_run(cid, stamp)
    if not run:
        abort(404)
    return jsonify(run)


@app.route("/search")
def search_view():
    inv = _load_inventory()
    q = (request.args.get("q") or "").strip()
    kinds = request.args.getlist("kind") or ["event", "ke", "cd", "cm", "tag", "trigger", "variable"]
    hits = search_index.search(q, inv, kinds=kinds) if q else []
    by_kind = Counter(h["kind"] for h in hits)
    return render_template("search.html", inv=inv, q=q, kinds=kinds, hits=hits, by_kind=by_kind)


@app.route("/api/annotate", methods=["POST"])
def api_annotate():
    data = request.get_json(force=True, silent=True) or request.form
    kind = data.get("kind")
    key = str(data.get("key") or "")
    if kind not in ("properties", "containers") or not key:
        return jsonify({"ok": False, "error": "kind/key invalid"}), 400
    payload = {}
    if "tags" in data:
        payload["tags"] = data["tags"]
    if "note" in data:
        payload["note"] = data["note"]
    if "favorite" in data:
        fav = data["favorite"]
        if isinstance(fav, str):
            fav = fav.lower() in ("1", "true", "yes", "on")
        payload["favorite"] = fav
    cur = ann.set_annotation(kind, key, **payload)
    return jsonify({"ok": True, "current": cur, "all_tags": ann.all_tags()})


def _csv_response(rows: list[list], filename: str) -> Response:
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf)
    for r in rows:
        w.writerow(r)
    return Response(buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


@app.route("/export/properties.csv")
def export_properties_csv():
    inv = _load_inventory()
    rows = [["auth_email", "account_id", "account_name", "property_id", "display_name",
             "is_tracked", "sessions_7d", "events_7d", "key_event_count", "key_events",
             "is_ecommerce", "custom_dimension_count", "custom_metric_count", "stream_count",
             "measurement_ids", "my_roles", "health_score", "health_grade", "alerts",
             "ann_favorite", "ann_tags", "ann_note"]]
    for p in inv.get("properties", []):
        alerts = health.detect_alerts(p)
        rows.append([
            p.get("auth_email"), p.get("account_id"), p.get("account_display_name"),
            p.get("property_id"), p.get("display_name"),
            p.get("is_tracked"), p.get("sessions_7d"), p.get("events_7d"),
            p.get("key_event_count"), ", ".join(p.get("key_event_names") or []),
            p.get("is_ecommerce"), p.get("custom_dimension_count"), p.get("custom_metric_count"),
            p.get("stream_count"), ", ".join(p.get("measurement_ids") or []),
            ", ".join(p.get("my_roles") or []),
            p.get("health_score"), p.get("health_grade"),
            " | ".join(f"[{a['level']}] {a['message']}" for a in alerts),
            p.get("ann_favorite"), ", ".join(p.get("ann_tags") or []), p.get("ann_note"),
        ])
    return _csv_response(rows, f"ga4_properties_{datetime.now().strftime('%Y%m%d')}.csv")


@app.route("/export/gtm.csv")
def export_gtm_csv():
    inv = _load_inventory()
    rows = [["auth_email", "account_name", "container_id", "container_name", "public_id",
             "usage_context", "domain_name", "tag_count", "trigger_count", "variable_count",
             "ga4_measurement_ids", "version_name", "ann_favorite", "ann_tags", "ann_note"]]
    for c in inv.get("gtm_containers", []):
        rows.append([
            c.get("auth_email"), c.get("account_name"), c.get("container_id"), c.get("name"),
            c.get("public_id"),
            ", ".join(c.get("usage_context") or []),
            ", ".join(c.get("domain_name") or []),
            c.get("tag_count"), c.get("trigger_count"), c.get("variable_count"),
            ", ".join(c.get("ga4_measurement_ids") or []),
            c.get("version_name"),
            c.get("ann_favorite"), ", ".join(c.get("ann_tags") or []), c.get("ann_note"),
        ])
    return _csv_response(rows, f"gtm_containers_{datetime.now().strftime('%Y%m%d')}.csv")


@app.route("/api/inventory")
def api_inventory():
    return jsonify(_load_inventory())


@app.route("/api/property/<pid>")
def api_property(pid: str):
    path = DETAILS_DIR / f"{pid}.json"
    if not path.exists():
        abort(404)
    return jsonify(json.loads(path.read_text(encoding="utf-8")))


_INDEXER_LOG = Path(__file__).parent / "indexer.log"


@app.route("/search-console")
def sc_list():
    inv = _load_inventory()
    sites = inv.get("sc_sites") or []
    # Build GA4 lookup for linked properties
    ga4_props_by_domain: dict = {}
    for p in inv.get("properties", []):
        pid = p.get("property_id")
        path = DETAILS_DIR / f"{pid}.json"
        if not path.exists():
            continue
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            for stream in d.get("streams", []) or []:
                dom = _extract_domain(stream.get("default_uri"))
                if dom:
                    ga4_props_by_domain.setdefault(dom, []).append(p)
        except Exception:
            continue
    site_to_ga4: dict = {}
    for s in sites:
        dom = (s.get("domain") or "").lower()
        if dom.startswith("www."):
            dom = dom[4:]
        site_to_ga4[s.get("site_hash")] = ga4_props_by_domain.get(dom, [])
    return render_template("sc.html", inv=inv, sites=sites, site_to_ga4=site_to_ga4)


@app.route("/sc/<site_hash>")
def sc_detail(site_hash: str):
    path = SC_DETAILS_DIR / f"{site_hash}.json"
    if not path.exists():
        abort(404)
    detail = json.loads(path.read_text(encoding="utf-8"))
    inv = _load_inventory()
    # Enrich summary inline
    summary = detail.get("summary") or {}
    ga4_domains: set = set()
    for p in inv.get("properties", []):
        pid = p.get("property_id")
        dpath = DETAILS_DIR / f"{pid}.json"
        if not dpath.exists():
            continue
        try:
            d = json.loads(dpath.read_text(encoding="utf-8"))
            for stream in d.get("streams", []) or []:
                dom = _extract_domain(stream.get("default_uri"))
                if dom:
                    ga4_domains.add(dom)
        except Exception:
            continue
    health.enrich_sc_sites([summary], ga4_domains)
    detail["summary"] = summary

    # Linked GA4 properties
    sdom = (summary.get("domain") or "").lower()
    if sdom.startswith("www."):
        sdom = sdom[4:]
    linked_ga4 = []
    for p in inv.get("properties", []):
        pid = p.get("property_id")
        dpath = DETAILS_DIR / f"{pid}.json"
        if not dpath.exists():
            continue
        try:
            d = json.loads(dpath.read_text(encoding="utf-8"))
            for stream in d.get("streams", []) or []:
                dom = _extract_domain(stream.get("default_uri"))
                if dom == sdom or (dom and (dom.endswith("." + sdom) or sdom.endswith("." + dom))):
                    linked_ga4.append(p)
                    break
        except Exception:
            continue

    return render_template("sc_detail.html", inv=inv, d=detail, summary=summary, linked_ga4=linked_ga4)


@app.route("/errors")
def errors_page():
    inv = _load_inventory()
    errors = inv.get("errors", []) or []
    from collections import Counter as _C
    by_stage = _C(e.get("stage") for e in errors)
    by_email = _C(e.get("email") for e in errors)
    return render_template("errors.html", inv=inv, errors=errors, by_stage=by_stage, by_email=by_email)


@app.route("/help")
def help_page():
    inv = _load_inventory()
    return render_template("help.html", inv=inv)


@app.route("/usage")
def usage_page():
    inv = _load_inventory()
    return render_template("usage.html", inv=inv)


@app.route("/refresh", methods=["POST"])
def refresh():
    emails = request.form.getlist("email")
    if _INDEXER_LOG.exists():
        _INDEXER_LOG.write_text("", encoding="utf-8")
    args = [sys.executable, "indexer.py"] + emails
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    with open(_INDEXER_LOG, "ab") as log:
        subprocess.Popen(args, cwd=str(Path(__file__).parent),
                         stdout=log, stderr=subprocess.STDOUT, env=env)
    return jsonify({"status": "started", "emails": emails or "all"})


@app.route("/refresh/status")
def refresh_status():
    import re
    from datetime import datetime as _dt
    inv = _load_inventory()
    log_lines = []
    log_tail = ""
    log_first_ts = None
    log_last_ts = None
    done_props = 0
    total_props_estimate = inv.get("property_count") or 0
    expected_from_log = 0
    current_property = None
    current_email = None

    if _INDEXER_LOG.exists():
        try:
            log_lines = _INDEXER_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
            log_tail = "\n".join(log_lines[-20:])
        except Exception as e:
            log_tail = f"(log read error: {e})"

    # Parse progress from log
    ts_re = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\]")
    prop_re = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s+property\s+(\d+)\s+'(.*?)'")
    account_props_re = re.compile(r"account\s+\d+\s+.+:\s*(\d+)\s+props")
    email_re = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s+===\s+(\S+)\s+===")
    for ln in log_lines:
        m = ts_re.match(ln)
        if m:
            log_last_ts = m.groups()
            if log_first_ts is None:
                log_first_ts = m.groups()
        pm = prop_re.search(ln)
        if pm:
            done_props += 1
            current_property = f"{pm.group(1)} '{pm.group(2)}'"
        ap = account_props_re.search(ln)
        if ap:
            expected_from_log += int(ap.group(1))
        em = email_re.match(ln)
        if em:
            current_email = em.group(1)

    if expected_from_log > total_props_estimate:
        total_props_estimate = expected_from_log

    elapsed_sec = None
    eta_sec = None
    rate = None
    if log_first_ts and log_last_ts:
        f = int(log_first_ts[0]) * 3600 + int(log_first_ts[1]) * 60 + int(log_first_ts[2])
        l = int(log_last_ts[0]) * 3600 + int(log_last_ts[1]) * 60 + int(log_last_ts[2])
        elapsed_sec = (l - f) % 86400
        if done_props > 5 and elapsed_sec > 0:
            rate = done_props / elapsed_sec
            remaining = max(0, total_props_estimate - done_props)
            if rate > 0:
                eta_sec = int(remaining / rate)

    running = False
    try:
        import psutil
        for p in psutil.process_iter(["name", "cmdline"]):
            cmd = p.info.get("cmdline") or []
            if any("indexer.py" in str(c) for c in cmd):
                running = True
                break
    except Exception:
        if _INDEXER_LOG.exists() and inv.get("generated_at"):
            log_mtime = _INDEXER_LOG.stat().st_mtime
            inv_dt = _dt.fromisoformat(inv["generated_at"].replace("Z", "+00:00"))
            running = log_mtime > inv_dt.timestamp() + 5

    pct = round(done_props / total_props_estimate * 100, 1) if total_props_estimate else 0
    return jsonify({
        "running": running,
        "last_generated_at": inv.get("generated_at"),
        "property_count": inv.get("property_count"),
        "error_count": inv.get("error_count"),
        "done_properties": done_props,
        "total_estimate": total_props_estimate,
        "progress_pct": pct,
        "elapsed_sec": elapsed_sec,
        "eta_sec": eta_sec,
        "rate_per_min": round(rate * 60, 1) if rate else None,
        "current_property": current_property,
        "current_email": current_email,
        "log_tail": log_tail,
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=SERVER_PORT, debug=False)
