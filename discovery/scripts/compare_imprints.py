#!/usr/bin/env python3
"""Compare two imprints (IR stores) of the same network shape, A = original, B = cast twin.

    compare_imprints.py <irA> <irB> --map 10.72.1=10.73.0,10.72.2=10.73.1

Only LOCAL entities on mapped segments count (A's /24s on the left of the map, B's on the right),
so another twin sharing the sensor does not leak in. Per segment: hosts by (role, listen set).
Per (src role, dst role|external, proto): flows per source host per hour, with B/A ratio.
"""
import json
import sys
from collections import Counter, defaultdict

import duckdb


def load(store, segs):
    c = duckdb.connect(store, read_only=True)
    pit = c.execute("select pit_id, observed_from, observed_to from ir_pit").fetchall()[-1]
    pid, t0, t1 = pit
    hours = (t1 - t0) / 3600
    ents = {r[0]: r[1:] for r in c.execute(
        "select entity_key, locality, segment, role, listen_ports from ir_entity where pit_id=?",
        [pid]).fetchall()}
    local = {k: v for k, v in ents.items() if v[0] == "local" and v[1] in segs}
    hosts = defaultdict(Counter)
    for k, (_l, seg, role, listen) in local.items():
        hosts[segs[seg]][(role or "-", ",".join(sorted(json.loads(listen or "[]"))))] += 1
    members = Counter((segs[v[1]], v[2] or "-") for v in local.values())
    flows = Counter()
    for s, d, proto, n in c.execute(
            "select src_key, dst_key, proto, flows from ir_edge where pit_id=?", [pid]).fetchall():
        if s not in local:
            continue
        sv = local[s]
        if d in local:
            dv = local[d]
            dst = f"{segs[dv[1]]}:{dv[2] or '-'}"
        elif d in ents:
            # outside the mapped segments: the internet, or the cast's synthetic sink
            # (10.200.254.0/24, which the sensor stamps local) standing in for it
            dst = "external"
        else:
            continue
        flows[(f"{segs[sv[1]]}:{sv[2] or '-'}", dst, proto)] += n
    rate = {k: n / members[tuple(k[0].split(":", 1))] / hours for k, n in flows.items()}
    return {"pit": pid, "hours": round(hours, 2), "hosts": hosts, "rate": rate}


def main():
    a, b = sys.argv[1], sys.argv[2]
    m = dict(kv.split("=") for kv in sys.argv[sys.argv.index("--map") + 1].split(","))
    segs_a = {f"{k}.0/24": f"S{i}" for i, k in enumerate(m)}
    segs_b = {f"{v}.0/24": f"S{i}" for i, v in enumerate(m.values())}
    A, B = load(a, segs_a), load(b, segs_b)
    print(f"A {A['pit']} {A['hours']}h   B {B['pit']} {B['hours']}h   segments:",
          {v: k for k, v in segs_a.items()}, "->", {v: k for k, v in segs_b.items()})
    print("\nHOSTS (role, listen) per segment: A vs B")
    for s in sorted(set(A["hosts"]) | set(B["hosts"])):
        keys = sorted(set(A["hosts"][s]) | set(B["hosts"][s]))
        for k in keys:
            na, nb = A["hosts"][s][k], B["hosts"][s][k]
            print(f"  {s} {k[0]:<18} {k[1]:<22} A={na} B={nb} {'OK' if na == nb else 'DIFF'}")
    print("\nFLOWS per source host per hour: A vs B (B/A)")
    keys = sorted(set(A["rate"]) | set(B["rate"]), key=lambda k: -(A["rate"].get(k, 0)))
    for k in keys:
        ra, rb = A["rate"].get(k, 0), B["rate"].get(k, 0)
        ratio = f"{rb / ra:5.2f}" if ra else "  new"
        print(f"  {k[0]:<22} -> {k[1]:<22} {k[2]:<8} A={ra:9.1f} B={rb:9.1f}  {ratio}")


if __name__ == "__main__":
    main()
