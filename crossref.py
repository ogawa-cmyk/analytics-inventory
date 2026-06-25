"""Cross-references between GA4 properties and GTM containers via Measurement IDs."""
from __future__ import annotations
from collections import defaultdict


def build(properties: list[dict], containers: list[dict]) -> dict:
    """Build forward and reverse indexes.

    Returns:
      {
        "mid_to_properties": {mid: [property_dict, ...]},
        "mid_to_containers": {mid: [container_dict, ...]},
        "property_to_containers": {property_id: [container_dict, ...]},
        "container_to_properties": {container_id: [property_dict, ...]},
        "duplicate_mids_in_containers": [{mid, containers:[...]}, ...]
      }
    """
    mid_to_props: dict[str, list] = defaultdict(list)
    mid_to_conts: dict[str, list] = defaultdict(list)
    for p in properties:
        for mid in p.get("measurement_ids") or []:
            if mid:
                mid_to_props[mid].append(p)
    for c in containers:
        for mid in c.get("ga4_measurement_ids") or []:
            if mid:
                mid_to_conts[mid].append(c)

    prop_to_conts: dict[str, list] = defaultdict(list)
    cont_to_props: dict[str, list] = defaultdict(list)
    for p in properties:
        pid = p.get("property_id")
        seen_cids = set()
        for mid in p.get("measurement_ids") or []:
            for c in mid_to_conts.get(mid, []):
                cid = c.get("container_id")
                if cid in seen_cids:
                    continue
                seen_cids.add(cid)
                prop_to_conts[pid].append(c)
    for c in containers:
        cid = c.get("container_id")
        seen_pids = set()
        for mid in c.get("ga4_measurement_ids") or []:
            for p in mid_to_props.get(mid, []):
                pid = p.get("property_id")
                if pid in seen_pids:
                    continue
                seen_pids.add(pid)
                cont_to_props[cid].append(p)

    duplicates = []
    for mid, conts in mid_to_conts.items():
        if len(conts) > 1:
            duplicates.append({
                "mid": mid,
                "containers": [{"container_id": c.get("container_id"), "name": c.get("name"),
                                "account_name": c.get("account_name")} for c in conts]
            })

    return {
        "mid_to_properties": dict(mid_to_props),
        "mid_to_containers": dict(mid_to_conts),
        "property_to_containers": dict(prop_to_conts),
        "container_to_properties": dict(cont_to_props),
        "duplicate_mids_in_containers": duplicates,
    }
