#!/usr/bin/env python3
"""Compare a cast twin bundle against the ground-truth twin it was discovered from.

    compare_cast_to_twin.py <original bundle dir> <cast bundle dir> [--json]

Axes (each scored separately, never averaged):
  segments   CIDRs present in both / only in one
  hosts      per segment: expanded host count (router excluded)
  services   per segment: the multiset of listen sets (what each server serves)
  recipes    per served port: real daemon vs echo responder in the cast's serve script
  router     a router joining every segment
  conduits   segment pairs allowed to talk
  traffic    per client group: (proto, port, peer segment) emits, cadence, volume, drive
"""
import ipaddress
import json
import re
import sys
from collections import Counter
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]
VOL = {"low": (2, 0.5), "med": (8, 0.2), "high": (24, 0.05), "bulk": (64, 0.0)}


def load(bundle: Path):
    te = yaml.safe_load((bundle / "te.yaml").read_text())["twin_config"]
    tfc = {}
    p = bundle / "tfc" / "default.yaml"
    if p.exists():
        tfc = yaml.safe_load(p.read_text())["traffic-profile"]
    return te, tfc


def persona(bundle: Path, pid: str):
    for d in (bundle / "personas", REPO / "catalog" / "personas"):
        for f in d.glob("*.yaml"):
            doc = yaml.safe_load(f.read_text()).get("persona", {})
            if doc.get("id") == pid:
                return doc
    return None


def legs(node):
    if node.get("nets"):
        return [n["segment"] for n in node["nets"]]
    return [node["net"]] if node.get("net") else []


def listen_set(node):
    return tuple(sorted(f"{l['port']}/{l.get('proto','tcp')}" for l in node.get("listen") or []))


def cadence_s(c):
    m = re.match(r"(?:cyclic|burst):(\d+(?:\.\d+)?)(ms|s|m|h)", str(c or ""))
    if not m:
        return None
    v, u = float(m.group(1)), m.group(2)
    return v * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[u]


def summarize(bundle: Path):
    te, tfc = load(bundle)
    segs = {s["name"]: s["cidr"] for s in te.get("segments", [])}
    hosts, services, roles_seg, recipes = Counter(), {}, {}, {}
    routers = []
    for n in te.get("nodes", []):
        cnt = int(n.get("count", 1))
        if n.get("kind") == "router":
            routers.append(sorted(segs[s] for s in legs(n)))
            continue
        for s in legs(n):
            cidr = segs[s]
            hosts[cidr] += cnt
            roles_seg[n["role"]] = cidr
            ls = listen_set(n)
            if ls:
                services.setdefault(cidr, Counter())[ls] += cnt
        for inst in n.get("install") or []:
            sc = inst.get("script")
            if sc and (bundle / sc).exists():
                body = (bundle / sc).read_text()
                for port in listen_set(n):
                    kind = "echo" if re.search(rf"echo responder[^\n]*\n# serves:[^\n]*{port}", body) else "real"
                    recipes[f"{n['role']}@{segs[legs(n)[0]]}:{port}"] = kind
    conduits = sorted(tuple(sorted((segs[c["from"]], segs[c["to"]]))) for c in te.get("conduits", []))
    traffic = {}
    for a in tfc.get("assignments", []):
        p = persona(bundle, a["persona"])
        if not p:
            traffic[a["role"]] = {"error": f"persona {a['persona']} not found"}
            continue
        emits = []
        for e in p.get("emits", []):
            n, gap = VOL.get(e.get("volume", "low"), VOL["low"])
            iv = cadence_s(e.get("cadence"))
            emits.append({
                "proto": e.get("proto"), "ports": e.get("ports"),
                "peer_seg": roles_seg.get(e.get("peer_role"), e.get("peer_role")),
                "interval_s": iv, "msgs": n,
                "rate_per_min": round(60 * n / iv, 1) if iv else None,
            })
        traffic[roles_seg.get(a["role"], a["role"])] = {
            "drive": p.get("drive"), "persona": p["id"],
            "intensity": a.get("intensity"), "emits": emits,
            "hosts": sum(int(n.get("count", 1)) for n in te["nodes"] if n.get("role") == a["role"]),
        }
    return {"segments": sorted(segs.values(), key=lambda c: ipaddress.ip_network(c)),
            "hosts": dict(hosts), "services": {k: dict(v) for k, v in services.items()},
            "routers": routers, "conduits": conduits, "recipes": recipes, "traffic": traffic}


def compare(a, b):
    out = {}
    sa, sb = set(a["segments"]), set(b["segments"])
    out["segments"] = {"both": sorted(sa & sb), "only_original": sorted(sa - sb), "only_cast": sorted(sb - sa)}
    out["hosts"] = {c: [a["hosts"].get(c, 0), b["hosts"].get(c, 0)] for c in sorted(sa | sb)}
    out["services"] = {c: {"original": {",".join(k): v for k, v in a["services"].get(c, {}).items()},
                           "cast": {",".join(k): v for k, v in b["services"].get(c, {}).items()}}
                       for c in sorted(set(a["services"]) | set(b["services"]))}
    out["routers"] = {"original": a["routers"], "cast": b["routers"]}
    out["conduits"] = {"original": a["conduits"], "cast": b["conduits"]}
    out["cast_recipes"] = b["recipes"]
    out["traffic"] = {"original": a["traffic"], "cast": b["traffic"]}
    return out


def main():
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    a, b = summarize(Path(args[0])), summarize(Path(args[1]))
    print(json.dumps(compare(a, b), indent=1, default=str))


if __name__ == "__main__":
    main()
