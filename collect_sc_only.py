"""Collect Search Console data only and merge into inventory.json.

Use after enabling Search Console API to avoid full re-collection.
Run: python collect_sc_only.py
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from auth import list_accounts as list_oauth_accounts, load_credentials
from collectors import sc as sc_collector
from config import INVENTORY_PATH, SC_DETAILS_DIR


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def main(target_emails: list[str] | None = None) -> None:
    if not INVENTORY_PATH.exists():
        print("No inventory.json — run indexer.py first")
        sys.exit(1)

    emails = target_emails or list_oauth_accounts()
    if not emails:
        print("No accounts. Run: python auth.py add")
        sys.exit(1)

    inv = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    sc_sites: list = []
    new_errors: list = []
    # Strip prior SC errors, keep others
    prior_errors = [e for e in (inv.get("errors") or []) if not (e.get("stage") or "").startswith("sc_")]

    started = time.time()
    for email in emails:
        _log(f"=== {email} ===")
        try:
            creds = load_credentials(email)
        except Exception as e:
            new_errors.append({"email": email, "stage": "sc_auth", "error": str(e)})
            _log(f"  AUTH ERROR: {e}")
            continue

        try:
            sites = sc_collector.list_sites(creds)
            _log(f"  SC sites: {len(sites)}")
            for site in sites:
                try:
                    r = sc_collector.collect_site(creds, email, site)
                    sc_sites.append(r["summary"])
                    (SC_DETAILS_DIR / f"{r['summary']['site_hash']}.json").write_text(
                        json.dumps(r["detail"], ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    _log(f"    {site['site_url']!r}: clicks={r['summary']['clicks_28d']:,} imps={r['summary']['impressions_28d']:,}")
                except Exception as e:
                    new_errors.append({"email": email, "stage": "sc_site",
                                       "site_url": site.get("site_url"), "error": str(e)})
                    _log(f"    ERROR {site.get('site_url')}: {e}")
        except Exception as e:
            new_errors.append({"email": email, "stage": "sc_list_sites", "error": str(e)})
            _log(f"  LIST ERROR: {e}")

    inv["sc_sites"] = sc_sites
    inv["sc_site_count"] = len(sc_sites)
    inv["errors"] = prior_errors + new_errors
    inv["error_count"] = len(inv["errors"])
    inv["sc_collected_at"] = datetime.now(timezone.utc).isoformat()
    INVENTORY_PATH.write_text(json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"DONE: {len(sc_sites)} SC sites, {len(new_errors)} errors. duration={round(time.time()-started,1)}s")


if __name__ == "__main__":
    args = sys.argv[1:]
    main(args if args else None)
