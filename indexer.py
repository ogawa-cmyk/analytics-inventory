"""Crawl every connected Gmail account → GA4 + GTM → write inventory.json + details/*.json."""
import json
import sys
import time
import traceback
from datetime import datetime, timezone

from auth import list_accounts as list_oauth_accounts, load_credentials
from collectors import ga4_admin, ga4_data, gtm
from collectors import sc as sc_collector
from config import (
    DETAILS_DIR,
    ECOMMERCE_EVENTS,
    ECOMMERCE_LOOKBACK_DAYS,
    EVENTS_LOOKBACK_DAYS,
    DATA_FRESHNESS_DAYS,
    GTM_DETAILS_DIR,
    INDEXER_LOCK_PATH,
    INVENTORY_PATH,
    SC_DETAILS_DIR,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def collect_property(creds, email: str, account: dict, prop: dict) -> dict:
    pid = prop["property_id"]
    pname = prop["name"]
    _log(f"    property {pid} {prop['display_name']!r}")
    streams = ga4_admin.list_data_streams(creds, pname)
    key_events = ga4_admin.list_key_events(creds, pname)
    custom_dims = ga4_admin.list_custom_dimensions(creds, pname)
    custom_metrics = ga4_admin.list_custom_metrics(creds, pname)
    bindings = ga4_admin.list_access_bindings(creds, pname, email)
    measurement = ga4_data.check_measurement(creds, pid, days=DATA_FRESHNESS_DAYS)
    ecom = ga4_data.check_ecommerce(creds, pid, ECOMMERCE_EVENTS, days=ECOMMERCE_LOOKBACK_DAYS)
    events = ga4_data.list_events(creds, pid, days=EVENTS_LOOKBACK_DAYS)

    # ── 追加設定（Admin API）。取得失敗は ok=False のまま残し「未確認」として扱う ──
    retention = ga4_admin.get_data_retention(creds, pname)
    ads_links = ga4_admin.list_google_ads_links(creds, pname)
    web_streams = [s for s in streams if s.get("measurement_id")]
    enhanced = []
    event_rules = []
    for s in web_streams:
        em = ga4_admin.get_enhanced_measurement(creds, s["name"])
        em["stream"] = s["name"]
        em["stream_display_name"] = s.get("display_name")
        enhanced.append(em)
        ecr = ga4_admin.list_event_create_rules(creds, s["name"])
        ecr["stream"] = s["name"]
        event_rules.append(ecr)

    # ── 実績データ（Data API）。未計測プロパティはAPI呼び出しを省略する ──
    is_tracked = bool(measurement.get("is_tracked"))
    if is_tracked:
        totals30 = ga4_data.sessions_total(creds, pid, days=EVENTS_LOOKBACK_DAYS)
        pages = ga4_data.page_report(creds, pid, days=EVENTS_LOOKBACK_DAYS)
        traffic = ga4_data.traffic_report(creds, pid, days=EVENTS_LOOKBACK_DAYS)
        countries = ga4_data.country_report(creds, pid, days=EVENTS_LOOKBACK_DAYS)
    else:
        skipped = {"ok": False, "skipped": True, "error": "未計測のため取得を省略", "rows": []}
        totals30, pages, traffic, countries = dict(skipped), dict(skipped), dict(skipped), dict(skipped)

    # 品質チェックの前提: どのデータセットが実際に取れたか。
    # 「取れていない」を「問題なし」と混同しないため、判定側はこのフラグを見て未確認に倒す
    events_ok = not (events and isinstance(events[0], dict) and "_error" in events[0])
    collected = {
        "events": events_ok,
        "totals": bool(totals30.get("ok")),
        "pages": bool(pages.get("ok")),
        "traffic": bool(traffic.get("ok")),
        "countries": bool(countries.get("ok")),
        "retention": bool(retention.get("ok")),
        "ads_links": bool(ads_links.get("ok")),
        # Webストリームが無い場合（空リスト＝all()はTrue）は「対象なし」であって「未取得」ではない
        "enhanced_measurement": all(e.get("ok") for e in enhanced),
        "event_create_rules": all(e.get("ok") for e in event_rules),
    }

    roles = []
    for b in bindings:
        roles.extend(b.get("roles", []))
    roles = sorted(set(roles))

    summary = {
        "auth_email": email,
        "account_id": account["account_id"],
        "account_display_name": account["display_name"],
        "property_id": pid,
        "property_name": pname,
        "display_name": prop["display_name"],
        "time_zone": prop.get("time_zone"),
        "currency_code": prop.get("currency_code"),
        "property_type": prop.get("property_type"),
        "create_time": prop.get("create_time"),
        "stream_count": len(streams),
        "measurement_ids": [s.get("measurement_id") for s in streams if s.get("measurement_id")],
        "key_event_count": len(key_events),
        "key_event_names": [k["event_name"] for k in key_events],
        "custom_dimension_count": len(custom_dims),
        "custom_metric_count": len(custom_metrics),
        "my_roles": roles,
        "is_tracked": measurement.get("is_tracked"),
        "sessions_7d": measurement.get("sessions_7d"),
        "events_7d": measurement.get("events_7d"),
        "is_ecommerce": ecom.get("is_ecommerce"),
        "ecommerce_events_found": ecom.get("events_found", {}),
        "data_api_ok": measurement.get("ok"),
        "data_api_error": measurement.get("error"),
        "collected": collected,
        "retention_event_data": retention.get("event_data_retention"),
        "ads_link_count": len(ads_links.get("links") or []),
        "event_create_rule_count": sum(len(e.get("rules") or []) for e in event_rules),
        "collected_at": _now(),
    }
    detail = {
        "summary": summary,
        "streams": streams,
        "key_events": key_events,
        "custom_dimensions": custom_dims,
        "custom_metrics": custom_metrics,
        "access_bindings": bindings,
        "events": events,
        "retention": retention,
        "ads_links": ads_links.get("links") or [],
        "enhanced_measurement": enhanced,
        "event_create_rules": event_rules,
        "totals_30d": totals30,
        "pages": pages,
        "traffic": traffic,
        "countries": countries,
    }

    # データ品質チェック（表示時ではなく収集時に1回だけ計算し、summary へ件数を焼き込む）
    try:
        import quality
        q = quality.run_property_checks(detail)
        detail["quality"] = q
        summary["quality"] = {**q["counts"], "unverified": q["unverified"]}
    except Exception as e:
        _log(f"    QUALITY CHECK ERROR {pid}: {e}")
        summary["quality"] = None

    (DETAILS_DIR / f"{pid}.json").write_text(
        json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def collect_sc_for_email(email: str, sc_out: list, errors: list) -> None:
    _log(f"  --- Search Console ---")
    try:
        creds = load_credentials(email)
        sites = sc_collector.list_sites(creds)
        _log(f"  SC sites: {len(sites)}")
        for site in sites:
            try:
                r = sc_collector.collect_site(creds, email, site)
                sc_out.append(r["summary"])
                (SC_DETAILS_DIR / f"{r['summary']['site_hash']}.json").write_text(
                    json.dumps(r["detail"], ensure_ascii=False, indent=2), encoding="utf-8"
                )
                _log(f"    site {site['site_url']!r}: clicks={r['summary']['clicks_28d']:,} imps={r['summary']['impressions_28d']:,}")
            except Exception as e:
                errors.append({"email": email, "stage": "sc_site",
                               "site_url": site.get("site_url"), "error": str(e)})
                _log(f"    SC ERROR {site.get('site_url')}: {e}")
    except Exception as e:
        errors.append({"email": email, "stage": "sc_list_sites", "error": str(e)})
        _log(f"  SC LIST ERROR: {e}")


def collect_for_email(email: str, props_out: list, gtm_out: list, errors: list) -> None:
    _log(f"=== {email} ===")
    try:
        creds = load_credentials(email)
    except Exception as e:
        errors.append({"email": email, "stage": "auth", "error": str(e)})
        _log(f"  AUTH ERROR: {e}")
        return

    try:
        accounts = ga4_admin.list_accounts(creds)
        _log(f"  GA4 accounts: {len(accounts)}")
        for acc in accounts:
            try:
                props = ga4_admin.list_properties(creds, acc["name"])
                _log(f"  account {acc['account_id']} {acc['display_name']!r}: {len(props)} props")
                for prop in props:
                    try:
                        props_out.append(collect_property(creds, email, acc, prop))
                    except Exception as e:
                        errors.append({"email": email, "stage": "property",
                                       "property_id": prop["property_id"], "error": str(e)})
                        _log(f"    PROPERTY ERROR {prop['property_id']}: {e}")
            except Exception as e:
                errors.append({"email": email, "stage": "list_properties",
                               "account_id": acc["account_id"], "error": str(e)})
    except Exception as e:
        errors.append({"email": email, "stage": "list_accounts", "error": str(e)})
        _log(f"  ACCOUNTS ERROR: {e}\n{traceback.format_exc()}")

    try:
        gtm_accounts = gtm.list_accounts(creds)
        _log(f"  GTM accounts: {len(gtm_accounts)}")
        for ga in gtm_accounts:
            try:
                containers = gtm.list_containers(creds, ga["path"])
                _log(f"    GTM account {ga.get('accountId')} {ga.get('name')!r}: {len(containers)} containers")
                for c in containers:
                    try:
                        s, live = gtm.summarize_container(creds, c)
                        s["auth_email"] = email
                        s["account_name"] = ga.get("name")
                        gtm_out.append(s)
                        if live:
                            (GTM_DETAILS_DIR / f"{s['container_id']}.json").write_text(
                                json.dumps(live, ensure_ascii=False, indent=2), encoding="utf-8"
                            )
                    except Exception as e:
                        errors.append({"email": email, "stage": "gtm_container",
                                       "container": c.get("containerId"), "error": str(e)})
            except Exception as e:
                errors.append({"email": email, "stage": "gtm_list_containers",
                               "account": ga.get("accountId"), "error": str(e)})
    except Exception as e:
        errors.append({"email": email, "stage": "gtm_list_accounts", "error": str(e)})
        _log(f"  GTM ERROR: {e}")


def main(target_emails: list[str] | None = None) -> None:
    emails = target_emails or list_oauth_accounts()
    if not emails:
        print("No accounts. Run: python auth.py add")
        sys.exit(1)

    # Lock file: prevents concurrent indexer runs (which would corrupt the
    # shared inventory.json / details files via interleaved writes).
    try:
        INDEXER_LOCK_PATH.write_text(_now(), encoding="utf-8")
    except Exception:
        pass

    try:
        _run(emails)
    finally:
        try:
            INDEXER_LOCK_PATH.unlink(missing_ok=True)
        except Exception:
            pass


def _run(emails: list[str]) -> None:
    properties: list = []
    gtm_containers: list = []
    sc_sites: list = []
    errors: list = []
    started = time.time()

    for email in emails:
        collect_for_email(email, properties, gtm_containers, errors)
        collect_sc_for_email(email, sc_sites, errors)

    inventory = {
        "generated_at": _now(),
        "duration_seconds": round(time.time() - started, 1),
        "accounts_scanned": emails,
        "property_count": len(properties),
        "gtm_container_count": len(gtm_containers),
        "sc_site_count": len(sc_sites),
        "error_count": len(errors),
        "properties": properties,
        "gtm_containers": gtm_containers,
        "sc_sites": sc_sites,
        "errors": errors,
    }
    INVENTORY_PATH.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        from diff import save_snapshot
        snap = save_snapshot(inventory)
        _log(f"Snapshot saved: {snap.name}")
    except Exception as e:
        _log(f"Snapshot save failed: {e}")
    try:
        import changes
        events = changes.record_changes()
        _log(f"Change detection: {len(events)} events recorded")
    except Exception as e:
        _log(f"Change detection failed: {e}")
    _log(f"DONE: {len(properties)} properties, {len(gtm_containers)} GTM containers, "
         f"{len(sc_sites)} SC sites, {len(errors)} errors → {INVENTORY_PATH}")


if __name__ == "__main__":
    args = sys.argv[1:]
    main(args if args else None)
