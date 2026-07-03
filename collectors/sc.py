"""Google Search Console (Webmasters API v3) collectors."""
from __future__ import annotations

import hashlib
import random
import time
from datetime import datetime, timedelta, timezone

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


_MAX_RETRIES = 5
PERFORMANCE_DAYS = 28
TOP_QUERIES = 100
TOP_PAGES = 100


def sc_client(creds):
    return build("webmasters", "v3", credentials=creds, cache_discovery=False)


def site_hash(site_url: str) -> str:
    return hashlib.sha1((site_url or "").encode("utf-8")).hexdigest()[:16]


def site_type_of(site_url: str) -> str:
    if site_url.startswith("sc-domain:"):
        return "DOMAIN"
    return "URL_PREFIX"


def domain_of(site_url: str) -> str:
    if site_url.startswith("sc-domain:"):
        return site_url.replace("sc-domain:", "")
    s = site_url
    if s.startswith("https://"):
        s = s[8:]
    elif s.startswith("http://"):
        s = s[7:]
    if s.endswith("/"):
        s = s[:-1]
    return s


def _exec(req):
    """Run a Webmasters API request with exponential backoff for 429/500/503."""
    for attempt in range(_MAX_RETRIES):
        try:
            return req.execute()
        except HttpError as e:
            status = getattr(e.resp, "status", 0)
            if status in (429, 500, 502, 503) and attempt < _MAX_RETRIES - 1:
                time.sleep((2 ** attempt) + random.uniform(0, 0.5))
                continue
            raise


def list_sites(creds) -> list[dict]:
    svc = sc_client(creds)
    resp = _exec(svc.sites().list())
    out = []
    for s in resp.get("siteEntry", []) or []:
        url = s.get("siteUrl")
        if not url:
            continue
        # Skip unverified
        plvl = s.get("permissionLevel") or ""
        out.append({
            "site_url": url,
            "site_type": site_type_of(url),
            "domain": domain_of(url),
            "permission_level": plvl,
        })
    return out


def _date_range(days: int = PERFORMANCE_DAYS, end_lag_days: int = 3) -> tuple[str, str]:
    end = datetime.now(timezone.utc).date() - timedelta(days=end_lag_days)
    start = end - timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def query_search_analytics(creds, site_url: str, dimensions: list[str] | None = None,
                           row_limit: int = 100, days: int = PERFORMANCE_DAYS) -> dict:
    svc = sc_client(creds)
    start, end = _date_range(days)
    body = {
        "startDate": start,
        "endDate": end,
        "rowLimit": row_limit,
        "dataState": "final",
    }
    if dimensions:
        body["dimensions"] = dimensions
    return _exec(svc.searchanalytics().query(siteUrl=site_url, body=body)) or {}


def performance_summary(creds, site_url: str) -> dict:
    """Aggregate performance metrics (no dimensions)."""
    try:
        resp = query_search_analytics(creds, site_url, dimensions=None, row_limit=1)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
    rows = resp.get("rows", []) or []
    if not rows:
        return {"ok": True, "clicks": 0, "impressions": 0, "ctr": 0.0, "position": 0.0}
    r = rows[0]
    return {
        "ok": True,
        "clicks": int(r.get("clicks", 0)),
        "impressions": int(r.get("impressions", 0)),
        "ctr": float(r.get("ctr", 0.0)),
        "position": float(r.get("position", 0.0)),
    }


def top_queries(creds, site_url: str, limit: int = TOP_QUERIES) -> list[dict]:
    try:
        resp = query_search_analytics(creds, site_url, dimensions=["query"], row_limit=limit)
    except Exception as e:
        return [{"_error": f"{type(e).__name__}: {str(e)[:200]}"}]
    out = []
    for r in resp.get("rows", []) or []:
        out.append({
            "query": (r.get("keys") or [""])[0],
            "clicks": int(r.get("clicks", 0)),
            "impressions": int(r.get("impressions", 0)),
            "ctr": float(r.get("ctr", 0.0)),
            "position": float(r.get("position", 0.0)),
        })
    return out


def top_pages(creds, site_url: str, limit: int = TOP_PAGES) -> list[dict]:
    try:
        resp = query_search_analytics(creds, site_url, dimensions=["page"], row_limit=limit)
    except Exception as e:
        return [{"_error": f"{type(e).__name__}: {str(e)[:200]}"}]
    out = []
    for r in resp.get("rows", []) or []:
        out.append({
            "page": (r.get("keys") or [""])[0],
            "clicks": int(r.get("clicks", 0)),
            "impressions": int(r.get("impressions", 0)),
            "ctr": float(r.get("ctr", 0.0)),
            "position": float(r.get("position", 0.0)),
        })
    return out


def device_breakdown(creds, site_url: str) -> list[dict]:
    try:
        resp = query_search_analytics(creds, site_url, dimensions=["device"], row_limit=10)
    except Exception:
        return []
    return [
        {
            "device": (r.get("keys") or [""])[0],
            "clicks": int(r.get("clicks", 0)),
            "impressions": int(r.get("impressions", 0)),
            "ctr": float(r.get("ctr", 0.0)),
            "position": float(r.get("position", 0.0)),
        }
        for r in (resp.get("rows", []) or [])
    ]


def country_breakdown(creds, site_url: str, top_n: int = 15) -> list[dict]:
    try:
        resp = query_search_analytics(creds, site_url, dimensions=["country"], row_limit=top_n)
    except Exception:
        return []
    return [
        {
            "country": (r.get("keys") or [""])[0],
            "clicks": int(r.get("clicks", 0)),
            "impressions": int(r.get("impressions", 0)),
            "ctr": float(r.get("ctr", 0.0)),
            "position": float(r.get("position", 0.0)),
        }
        for r in (resp.get("rows", []) or [])
    ]


def list_sitemaps(creds, site_url: str) -> list[dict]:
    svc = sc_client(creds)
    try:
        resp = _exec(svc.sitemaps().list(siteUrl=site_url))
    except HttpError as e:
        if getattr(e.resp, "status", 0) in (403, 404):
            return []
        raise
    out = []
    for s in resp.get("sitemap", []) or []:
        out.append({
            "path": s.get("path"),
            "last_submitted": s.get("lastSubmitted"),
            "last_downloaded": s.get("lastDownloaded"),
            "is_pending": s.get("isPending"),
            "is_sitemaps_index": s.get("isSitemapsIndex"),
            "type": s.get("type"),
            "errors": int(s.get("errors", 0)),
            "warnings": int(s.get("warnings", 0)),
            "contents": s.get("contents", []),
        })
    return out


def collect_site(creds, email: str, site: dict) -> dict:
    """Collect everything for one site. Returns dict with summary + detail."""
    perf = performance_summary(creds, site["site_url"])
    queries = top_queries(creds, site["site_url"], TOP_QUERIES)
    pages = top_pages(creds, site["site_url"], TOP_PAGES)
    devices = device_breakdown(creds, site["site_url"])
    countries = country_breakdown(creds, site["site_url"], 15)
    sitemaps = list_sitemaps(creds, site["site_url"])

    query_with_clicks = sum(1 for q in queries if isinstance(q, dict) and q.get("clicks", 0) > 0)
    page_with_clicks = sum(1 for p in pages if isinstance(p, dict) and p.get("clicks", 0) > 0)
    sitemap_errors = sum((s.get("errors") or 0) for s in sitemaps)

    summary = {
        "auth_email": email,
        "site_url": site["site_url"],
        "site_hash": site_hash(site["site_url"]),
        "site_type": site["site_type"],
        "domain": site["domain"],
        "permission_level": site["permission_level"],
        "perf_ok": perf.get("ok"),
        "perf_error": perf.get("error"),
        "clicks_28d": perf.get("clicks", 0),
        "impressions_28d": perf.get("impressions", 0),
        "ctr_28d": perf.get("ctr", 0.0),
        "position_28d": perf.get("position", 0.0),
        "top_query_count": query_with_clicks,
        "top_page_count": page_with_clicks,
        "sitemap_count": len(sitemaps),
        "sitemap_errors": sitemap_errors,
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }
    detail = {
        "summary": summary,
        "queries": queries,
        "pages": pages,
        "devices": devices,
        "countries": countries,
        "sitemaps": sitemaps,
    }
    return {"summary": summary, "detail": detail}
