"""GTM API v2 collectors — accounts, containers, workspaces, tags."""
from __future__ import annotations

import random
import time

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


_MAX_RETRIES = 6


def gtm_client(creds):
    return build("tagmanager", "v2", credentials=creds, cache_discovery=False)


def _execute_with_retry(request):
    """Exponential backoff for 429/500/503 — handles GTM API rate limits."""
    for attempt in range(_MAX_RETRIES):
        try:
            return request.execute()
        except HttpError as e:
            status = getattr(e.resp, "status", 0)
            if status in (429, 500, 502, 503) and attempt < _MAX_RETRIES - 1:
                wait = (2 ** attempt) + random.uniform(0, 1)
                time.sleep(wait)
                continue
            raise


def list_accounts(creds) -> list[dict]:
    svc = gtm_client(creds)
    resp = _execute_with_retry(svc.accounts().list())
    return resp.get("account", [])


def list_containers(creds, account_path: str) -> list[dict]:
    svc = gtm_client(creds)
    resp = _execute_with_retry(svc.accounts().containers().list(parent=account_path))
    return resp.get("container", [])


def get_live_version(creds, container_path: str) -> dict | None:
    svc = gtm_client(creds)
    try:
        return _execute_with_retry(
            svc.accounts().containers().versions().live(parent=container_path)
        )
    except Exception:
        return None


def summarize_container(creds, container: dict) -> tuple[dict, dict | None]:
    """Returns (summary, live_version_data) — caller persists live version to disk."""
    out = {
        "account_id": container.get("accountId"),
        "container_id": container.get("containerId"),
        "name": container.get("name"),
        "public_id": container.get("publicId"),
        "usage_context": container.get("usageContext"),
        "domain_name": container.get("domainName"),
        "path": container.get("path"),
        "tag_count": 0,
        "trigger_count": 0,
        "variable_count": 0,
        "ga4_measurement_ids": [],
        "version_name": None,
        "version_id": None,
    }
    live = get_live_version(creds, container["path"])
    if live:
        tags = live.get("tag", []) or []
        out["tag_count"] = len(tags)
        out["trigger_count"] = len(live.get("trigger", []) or [])
        out["variable_count"] = len(live.get("variable", []) or [])
        out["version_name"] = live.get("name")
        out["version_id"] = live.get("containerVersionId")
        for t in tags:
            if t.get("type") in ("gaawe", "gaawc"):
                for p in t.get("parameter", []) or []:
                    if p.get("key") in ("measurementId", "measurementIdOverride"):
                        v = p.get("value", "")
                        if v and v not in out["ga4_measurement_ids"]:
                            out["ga4_measurement_ids"].append(v)
    return out, live


def list_tags(creds, container: dict) -> list[dict]:
    live = get_live_version(creds, container["path"])
    if not live:
        return []
    out = []
    for t in live.get("tag", []) or []:
        out.append({
            "tag_id": t.get("tagId"),
            "name": t.get("name"),
            "type": t.get("type"),
            "paused": t.get("paused", False),
            "firing_trigger_id": t.get("firingTriggerId", []),
        })
    return out
