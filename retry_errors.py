"""Retry only the errors recorded in inventory.json — much faster than re-indexing all."""
import json
import sys
import time
import traceback
from datetime import datetime, timezone

from auth import load_credentials
from collectors import ga4_admin, ga4_data, gtm
from config import (
    DETAILS_DIR,
    ECOMMERCE_EVENTS,
    ECOMMERCE_LOOKBACK_DAYS,
    EVENTS_LOOKBACK_DAYS,
    DATA_FRESHNESS_DAYS,
    GTM_DETAILS_DIR,
    INVENTORY_PATH,
)
from indexer import collect_property


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def retry_gtm_account(creds, email: str, account_id: str, existing_containers: list) -> tuple[list[dict], str | None]:
    """Retry listing containers for a GTM account. Returns (new_containers, error_or_none)."""
    account_path = f"accounts/{account_id}"
    try:
        accounts = gtm.list_accounts(creds)
        account = next((a for a in accounts if a.get("accountId") == account_id), None)
        if not account:
            return [], f"account {account_id} not found in current GTM accounts"
        containers = gtm.list_containers(creds, account_path)
        results = []
        for c in containers:
            s, live = gtm.summarize_container(creds, c)
            s["auth_email"] = email
            s["account_name"] = account.get("name")
            results.append(s)
            if live:
                (GTM_DETAILS_DIR / f"{s['container_id']}.json").write_text(
                    json.dumps(live, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        return results, None
    except Exception as e:
        return [], f"{type(e).__name__}: {str(e)[:200]}"


def retry_property(creds, email: str, property_id: str, inv: dict) -> tuple[dict | None, str | None]:
    """Re-collect a single property. Need to find its account info."""
    try:
        accounts = ga4_admin.list_accounts(creds)
        for acc in accounts:
            props = ga4_admin.list_properties(creds, acc["name"])
            for prop in props:
                if prop["property_id"] == property_id:
                    summary = collect_property(creds, email, acc, prop)
                    return summary, None
        return None, f"property {property_id} not found"
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"


def main() -> None:
    if not INVENTORY_PATH.exists():
        print("No inventory.json — run indexer.py first")
        sys.exit(1)

    inv = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    errors = inv.get("errors", [])
    if not errors:
        print("No errors to retry.")
        return

    _log(f"Errors to retry: {len(errors)}")
    creds_cache: dict = {}

    new_errors = []
    added_gtm = 0
    updated_props = 0
    retried_gtm_accounts: set = set()

    for err in errors:
        email = err.get("email")
        stage = err.get("stage")
        if email not in creds_cache:
            try:
                creds_cache[email] = load_credentials(email)
            except Exception as e:
                new_errors.append({**err, "retry_error": f"auth: {e}"})
                continue
        creds = creds_cache[email]

        if stage == "gtm_list_containers":
            account_id = err.get("account")
            if not account_id:
                new_errors.append({**err, "retry_error": "no account id"})
                continue
            key = (email, account_id)
            if key in retried_gtm_accounts:
                continue
            retried_gtm_accounts.add(key)
            _log(f"  GTM retry: {email} acct={account_id}")
            new_containers, retry_err = retry_gtm_account(creds, email, account_id, inv.get("gtm_containers", []))
            if retry_err:
                new_errors.append({**err, "retry_error": retry_err})
                _log(f"    FAIL: {retry_err}")
            else:
                inv["gtm_containers"].extend(new_containers)
                added_gtm += len(new_containers)
                _log(f"    OK: +{len(new_containers)} containers")
            time.sleep(1)

        elif stage == "property":
            pid = err.get("property_id")
            _log(f"  Property retry: {email} pid={pid}")
            summary, retry_err = retry_property(creds, email, pid, inv)
            if retry_err or not summary:
                new_errors.append({**err, "retry_error": retry_err or "no summary"})
                _log(f"    FAIL: {retry_err}")
            else:
                replaced = False
                for i, p in enumerate(inv["properties"]):
                    if p["property_id"] == pid:
                        inv["properties"][i] = summary
                        replaced = True
                        break
                if not replaced:
                    inv["properties"].append(summary)
                updated_props += 1
                _log(f"    OK")

        else:
            new_errors.append(err)

    inv["errors"] = new_errors
    inv["error_count"] = len(new_errors)
    inv["property_count"] = len(inv["properties"])
    inv["gtm_container_count"] = len(inv["gtm_containers"])
    inv["retry_at"] = _now()

    INVENTORY_PATH.write_text(json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"DONE: +{added_gtm} GTM containers, {updated_props} properties updated, "
         f"{len(new_errors)} errors remaining")


if __name__ == "__main__":
    main()
