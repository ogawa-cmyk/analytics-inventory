"""Backfill GTM live version details for all containers in inventory."""
import json
import sys
import time
from datetime import datetime

from auth import load_credentials
from collectors import gtm
from config import GTM_DETAILS_DIR, INVENTORY_PATH


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    if not INVENTORY_PATH.exists():
        print("No inventory.json")
        sys.exit(1)
    inv = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    containers = inv.get("gtm_containers", [])
    _log(f"Backfilling {len(containers)} GTM containers")

    creds_cache: dict = {}
    ok = skipped = failed = 0

    for i, c in enumerate(containers, 1):
        cid = c.get("container_id")
        path = c.get("path")
        email = c.get("auth_email")
        if not (cid and path and email):
            failed += 1
            continue
        out_path = GTM_DETAILS_DIR / f"{cid}.json"
        if out_path.exists():
            skipped += 1
            continue

        if email not in creds_cache:
            try:
                creds_cache[email] = load_credentials(email)
            except Exception as e:
                _log(f"  AUTH FAIL {email}: {e}")
                failed += 1
                continue
        creds = creds_cache[email]

        try:
            live = gtm.get_live_version(creds, path)
            if live:
                out_path.write_text(json.dumps(live, ensure_ascii=False, indent=2), encoding="utf-8")
                ok += 1
                if i % 20 == 0:
                    _log(f"  [{i}/{len(containers)}] ok={ok} skip={skipped} fail={failed}")
            else:
                failed += 1
        except Exception as e:
            _log(f"  FAIL {cid}: {type(e).__name__}: {str(e)[:120]}")
            failed += 1

    _log(f"DONE: ok={ok}, skipped={skipped}, failed={failed}")


if __name__ == "__main__":
    main()
