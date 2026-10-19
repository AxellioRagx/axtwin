#!/usr/bin/env python3
"""Drop-accounting harness for the dev45 max-capture-rate run (2026-08-13).

Samples every stage of the mirror path twice, `--seconds` apart, and reports
per-stage rate plus the delta of every drop/error counter. The point is that
"no dropped packets" is a claim about the WHOLE chain, so a number is only
trustworthy if each stage is individually flat:

  [1] source     twin VM tap ifaces on the PVE host  -- what was offered
  [2] mirror     tc ingress filters on the tapped ports -- what tc matched
  [3] gretap tx  the tapg_* netdev on the PVE host   -- what was encapsulated
  [4] sink vhost tap104i1 on the PVE host            -- what vhost delivered
  [5] sink nic   ens19 inside XPress-SW-A            -- what the guest received
  [6] decap      gre-cap inside XPress-SW-A          -- what was decapsulated

Counters come from /proc/net/dev (stable columns, one cheap read per host)
rather than `ip -s link` text. rx `fifo` is the ring-overrun column -- the one
that actually moves first when a capture path is overrun, and the reason a
bytes-only view of "is it keeping up" is not enough.

Read-only. Runs no traffic of its own.
"""
import argparse
import json
import subprocess
import sys
import time

PVE = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
       "root@10.80.76.108"]
SINK = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
        "-o","ControlPath=/tmp/cs-sink-104","fxadmin@10.80.72.43"]

# /proc/net/dev column order after the iface name.
COLS = ("rx_bytes rx_packets rx_errs rx_drop rx_fifo rx_frame rx_compressed "
        "rx_multicast tx_bytes tx_packets tx_errs tx_drop tx_fifo tx_colls "
        "tx_carrier tx_compressed").split()


def ssh(where, cmd, timeout=60):
    try:
        r = subprocess.run(where + [cmd], capture_output=True, text=True,
                           timeout=timeout)
        return r.stdout
    except subprocess.TimeoutExpired:
        return ""


def netdev(where):
    """Parse /proc/net/dev into {iface: {counter: int}}."""
    out = {}
    for line in ssh(where, "cat /proc/net/dev").splitlines():
        if ":" not in line:
            continue
        name, _, rest = line.partition(":")
        vals = rest.split()
        if len(vals) < 16:
            continue
        out[name.strip()] = {c: int(v) for c, v in zip(COLS, vals)}
    return out


def tc_mirror_stats():
    """Per-tapped-port tc ingress filter counters, keyed by device.

    `tc -s filter show ... ingress` prints a `Sent N bytes M pkt (dropped D,
    overlimits O ...)` line per filter. A non-zero dropped/overlimits here is
    the mirror stage itself shedding load -- distinct from the sink being
    unable to keep up, and invisible in /proc/net/dev.
    """
    # Emit raw `tc` output delimited by a marker and parse it HERE. An earlier
    # cut grepped on the remote side; the regex had to survive Python quoting,
    # ssh argv-joining AND the remote shell, and it silently matched nothing --
    # which read exactly like "this stage has no filters", the most dangerous
    # possible failure for a drop-accounting tool. Local parsing has one layer
    # of quoting instead of three.
    script = ('for d in /sys/class/net/tap*; do n=${d##*/}; '
              'echo "##$n"; tc -s filter show dev $n ingress 2>/dev/null; done')
    out, dev = {}, None
    for line in ssh(PVE, script).splitlines():
        if line.startswith("##"):
            dev = line[2:].strip()
            continue
        s = line.strip()
        if dev and s.startswith("Sent ") and "dropped" in s:
            t = s.replace("(", " ").replace(",", " ").split()
            try:
                # Sent <bytes> bytes <pkts> pkt  dropped <d>  overlimits <o> ...
                rec = {"bytes": int(t[1]), "pkts": int(t[3]),
                       "dropped": int(t[t.index("dropped") + 1]),
                       "overlimits": int(t[t.index("overlimits") + 1])}
            except (IndexError, ValueError):
                continue
            # A device can carry several mirred filters; sum them.
            cur = out.setdefault(dev, {"bytes": 0, "pkts": 0,
                                       "dropped": 0, "overlimits": 0})
            for k in cur:
                cur[k] += rec[k]
    return out


def sample():
    """Timestamp EACH host read separately.

    A sample makes three sequential ssh round-trips. Stamping the whole sample
    once and dividing every stage's delta by that single `dt` silently inflates
    rates whenever one read is slow: observed on 2026-08-14 with a stalled sink
    connection, where the tc counters spanned ~80s of real time but were
    divided by a 17s dt, reporting 588k pps against a gretap that had actually
    carried 138k. Per-read stamps make each stage's rate use the interval that
    stage was actually measured over.
    """
    t0 = time.time()
    pve = netdev(PVE)
    t1 = time.time()
    sink = netdev(SINK)
    t2 = time.time()
    tc = tc_mirror_stats()
    t3 = time.time()
    return {"t": t0, "t_pve": (t0 + t1) / 2, "pve": pve,
            "t_sink": (t1 + t2) / 2, "sink": sink,
            "t_tc": (t2 + t3) / 2, "tc": tc}


def rate(d_bytes, d_pkts, dt):
    return d_bytes * 8 / dt / 1e6, d_pkts / dt


def show(a, b, host, iface, direction, label, dt, flag_drops=True):
    dt = b[f"t_{host}"] - a[f"t_{host}"]  # per-host interval, not the sample-wide one
    """Print one stage's rate and drop deltas; return True if anything dropped."""
    ta, tb = a[host].get(iface), b[host].get(iface)
    if ta is None or tb is None:
        print(f"  {label:<22} {iface:<14} -- not present --")
        return False
    p = direction
    mb, pps = rate(tb[f"{p}_bytes"] - ta[f"{p}_bytes"],
                   tb[f"{p}_packets"] - ta[f"{p}_packets"], dt)
    # Check BOTH directions' loss counters regardless of which we rate: a
    # tx-side stage can still be dropping on rx and vice versa.
    losses = {k: tb[k] - ta[k] for k in
              ("rx_errs", "rx_drop", "rx_fifo", "tx_errs", "tx_drop", "tx_fifo")
              if tb[k] - ta[k] != 0}
    bad = bool(losses) and flag_drops
    mark = "  <-- LOSS" if bad else ""
    print(f"  {label:<22} {iface:<14} {mb:>10.1f} Mbit/s {pps:>12,.0f} pps"
          f"  {('loss: ' + json.dumps(losses)) if losses else 'clean':<44}{mark}")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--sink-iface", default="ens19")
    ap.add_argument("--decap-iface", default="gre-cap")
    ap.add_argument("--label", default="")
    # Restrict stage [1] to the hosts actually generating, so "offered" is
    # attributable to the mesh instead of summing every VM on a shared box
    # (XPress-SW, ragx-dev and friends all have tap ifaces too).
    ap.add_argument("--json-out", dest="json_out", default="",
                    help="append a JSON summary line to this file")
    ap.add_argument("--vmids", default="",
                    help="comma-separated vmids to count as the source set")
    a = ap.parse_args()
    want = {v.strip() for v in a.vmids.split(",") if v.strip()}

    s1 = sample()
    time.sleep(a.seconds)
    s2 = sample()
    dt = s2["t"] - s1["t"]
    dt_pve = s2["t_pve"] - s1["t_pve"]
    dt_tc = s2["t_tc"] - s1["t_tc"]

    print(f"\n=== {a.label or 'sample'} :: {dt:.1f}s window ===")
    any_loss = False

    # [1] source: every twin VM's tap iface. rx on the host side = VM -> bridge.
    tw = sorted(k for k in s2["pve"]
                if k.startswith("tap") and k not in ("tap104i1", "tap104i0")
                and (not want or k[3:].split("i")[0] in want))
    tot_b = tot_p = 0
    for d in tw:
        if d in s1["pve"]:
            tot_b += s2["pve"][d]["rx_bytes"] - s1["pve"][d]["rx_bytes"]
            tot_p += s2["pve"][d]["rx_packets"] - s1["pve"][d]["rx_packets"]
    mb, pps = rate(tot_b, tot_p, dt_pve)
    print(f"  {'[1] offered (all VMs)':<22} {len(tw):<14} {mb:>10.1f} Mbit/s"
          f" {pps:>12,.0f} pps")

    # [2] mirror: tc filters.
    tcd = sum(s2["tc"].get(d, {}).get("dropped", 0)
              - s1["tc"].get(d, {}).get("dropped", 0) for d in s2["tc"])
    tco = sum(s2["tc"].get(d, {}).get("overlimits", 0)
              - s1["tc"].get(d, {}).get("overlimits", 0) for d in s2["tc"])
    tcp = sum(s2["tc"].get(d, {}).get("pkts", 0)
              - s1["tc"].get(d, {}).get("pkts", 0) for d in s2["tc"])
    if s2["tc"]:
        # `overlimits` on a mirred action is NOT harmless requeue: when the
        # target device's queue is full the MIRRORED COPY is not delivered,
        # while the original packet continues down the pipe. So it is capture
        # loss that no netdev counter records -- stage [3]'s tx_drop stays at
        # ~0 while thousands of frames never arrive. Measured on dev45: at
        # rung 7 overlimits ran 4.5k/s and the tc->gretap packet deficit ran
        # 5.2k/s; at rung 4 both were ~0. Treat it as loss, and ALSO print the
        # deficit below, which is the direct observation rather than a proxy.
        bad = tcd or tco
        any_loss |= bool(bad)
        print(f"  {'[2] tc mirror':<22} {len(s2['tc']):<14}"
              f" {'':>10}          {tcp / dt_tc:>12,.0f} pps"
              f"  dropped={tcd} overlimits={tco}"
              f"{'  <-- LOSS' if bad else ''}")
    else:
        print(f"  {'[2] tc mirror':<22} {'(no filters)':<14}")

    # [3]/[4] host side of the transport, [5]/[6] guest side.
    gret = 0
    for dev in sorted(d for d in s2["pve"] if d.startswith("tapg")):
        any_loss |= show(s1, s2, "pve", dev, "tx", "[3] gretap tx", dt)
        gret += (s2["pve"][dev]["tx_packets"] - s1["pve"][dev]["tx_packets"])
    # The one loss channel with no counter of its own: frames tc says it
    # mirrored that the gretap never transmitted.
    if tcp and gret:
        deficit = tcp - gret
        pct = deficit / tcp * 100 if tcp else 0
        # ADVISORY ONLY -- do not let this drive the verdict. The tc and
        # netdev counters are read in SEPARATE ssh round-trips, so at ~230k pps
        # even 0.2s of skew manufactures ~46k phantom "missing" frames (~0.2%).
        # Below ~1% this number is indistinguishable from sampling skew;
        # `overlimits` is the countable signal. Kept because a deficit that
        # grows well past 1% AND tracks overlimits is real mirror loss.
        flag = deficit > tcp * 0.01
        any_loss |= flag
        print(f"  {'[2b] mirror->gretap':<22} {'deficit':<14} {'':>10}"
              f"          {deficit/dt_tc:>12,.0f} pps  "
              f"{deficit:,} frames unaccounted ({pct:.3f}%, advisory: skew-prone)"
              f"{'  <-- LOSS' if flag else ''}")
    any_loss |= show(s1, s2, "pve", "tap104i1", "tx", "[4] sink vhost tx", dt)
    any_loss |= show(s1, s2, "sink", a.sink_iface, "rx", "[5] sink nic rx", dt)
    any_loss |= show(s1, s2, "sink", a.decap_iface, "rx", "[6] decap rx", dt)

    print(f"\n  VERDICT: {'LOSS DETECTED' if any_loss else 'clean - no drops'}")

    if a.json_out:
        def dl(host, iface, p):
            x, y = s1[host].get(iface), s2[host].get(iface)
            if not x or not y:
                return None
            return {k: y[k] - x[k] for k in
                    ("rx_bytes", "rx_packets", "tx_bytes", "tx_packets",
                     "rx_drop", "rx_fifo", "rx_errs", "tx_drop", "tx_fifo", "tx_errs")}
        gret = next((d for d in s2["pve"] if d.startswith("tapg")), None)
        rec = {
            "label": a.label, "seconds": dt,
            "offered_mbit": mb, "offered_pps": pps, "dt_pve": dt_pve, "dt_tc": dt_tc,
            "tc_pkts": tcp, "tc_dropped": tcd, "tc_overlimits": tco,
            "gretap": dl("pve", gret, "tx") if gret else None,
            "sink_vhost": dl("pve", "tap104i1", "tx"),
            "sink_nic": dl("sink", a.sink_iface, "rx"),
            "decap": dl("sink", a.decap_iface, "rx"),
            "any_loss": any_loss,
        }
        with open(a.json_out, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    return 1 if any_loss else 0


if __name__ == "__main__":
    sys.exit(main())
