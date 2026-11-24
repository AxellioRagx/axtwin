#!/usr/bin/env python3
"""The in-VM wrapper and controller of a trafficgen generator (`SPEC.md`, B5).

Pushed into every generator VM at `/opt/axrange-tg/tg.py` by
`provision-generator` and invoked over the guest agent. Imports nothing from
axrange (it runs under the generator's own python3 and TRex's bundled client).

Subcommands (each prints one JSON object):

  provision --legs '{"0": "<mac>", "1": "<mac>"}'
      Render trex_cfg.yaml from the document (legs found by MAC), hand the legs
      to TRex (address flushed, offloads off), raise hugepages, install and
      (re)start the TRex and controller units -- TRex only when its config
      changed -- and wait until the controller has the port namespaces holding
      every emulated address. Converges: a retry starts from whatever is there.
  desire [--state loaded|running] [--line ID --mult M] [--remove ID]
      Change the desired state (atomically, with a new generation) and wait
      until the controller reports it applied. Idempotent.
  status
      The controller's last stats (per line counters).
  controller
      The one long-running owner of the TRex ASTF client (spike A6: two clients
      steal each other's context). Every few seconds: re-assert the namespace
      addresses, reconcile profiles with the desired state, hold each line
      under its max_concurrent and the budget's Mbps by lowering its
      multiplier, and write stats.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

BASE = "/opt/axrange-tg"
STATE = "/var/lib/axrange-tg"
DOC = BASE + "/generator.json"
PROFILE = BASE + "/astf_profile.py"
FILES = BASE + "/files"
CFG = BASE + "/trex_cfg.yaml"
DESIRED = STATE + "/desired.json"
STATS = STATE + "/stats.json"
TREX_HOME = "/opt/trex/current"
TREX_UNIT, CTL_UNIT = "axr-trex.service", "axr-tg-ctl.service"
INTERVAL_S = 2.0


def sh(cmd, check=True, timeout=60):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError("%s -> exit %s: %s" % (cmd, r.returncode, (r.stderr or r.stdout)[:300]))
    return r


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, sort_keys=True)
    os.replace(tmp, path)


def out(obj):
    sys.stdout.write(json.dumps(obj, sort_keys=True) + "\n")


def ifaces_by_mac():
    res = {}
    for name in os.listdir("/sys/class/net"):
        try:
            with open("/sys/class/net/%s/address" % name) as f:
                res[f.read().strip().lower()] = name
        except OSError:
            pass
    return res


def render_cfg(doc, ifnames):
    ports = doc["trex"]["ports"]
    lines = ["# axrange trafficgen generator %s: rendered by tg.py, do not edit" % doc["generator"],
             "- version: 2",
             "  port_limit: %d" % len(ports),
             "  low_end: true",
             "  low_end_core: 0",
             "  stack: %s" % doc["trex"].get("stack", "linux_based"),
             "  interfaces: [%s]" % ", ".join(json.dumps(ifnames[str(p["leg"])]) for p in ports),
             "  port_info:"]
    for p in ports:
        lines += ["    - ip: %s" % p["ip"], "      default_gw: %s" % p["default_gw"]]
    return "\n".join(lines) + "\n"


def unit_text(desc, cmd, cwd):
    return ("[Unit]\nDescription=%s\nAfter=network.target\n\n[Service]\nWorkingDirectory=%s\n"
            "ExecStart=%s\nRestart=on-failure\nRestartSec=3\n\n[Install]\nWantedBy=multi-user.target\n"
            % (desc, cwd, cmd))


def ensure_namespaces(doc):
    """Every emulated address as a /32 on its port's namespace veth, so the
    kernel answers ARP for it (spike A2). Returns (ok, missing_namespaces)."""
    missing = []
    for port, addrs in sorted(doc.get("namespaces", {}).items()):
        ns = "trex-a-%s-0" % port
        if sh("ip netns exec %s true" % ns, check=False).returncode != 0:
            missing.append(ns)
            continue
        have = sh("ip netns exec %s ip -4 -o addr show dev %s-L" % (ns, ns), check=False).stdout
        want = [a for a in addrs if (" %s/32 " % a) not in have]
        if want:
            batch = "\n".join("address add %s/32 dev %s-L" % (a, ns) for a in want)
            subprocess.run("ip -batch - -force -n %s" % ns, shell=True, input=batch, text=True,
                           capture_output=True)
    return not missing, missing


# ---------------------------------------------------------------- provision
def cmd_provision(a):
    doc = read_json(DOC)
    if doc is None:
        raise SystemExit("no generator document at %s" % DOC)
    macs = json.loads(a.legs)
    by_mac = ifaces_by_mac()
    ifnames = {}
    for leg, mac in macs.items():
        name = by_mac.get(mac.lower())
        if name is None:
            raise SystemExit("leg %s: no interface with MAC %s (have %s)" % (leg, mac, sorted(by_mac)))
        ifnames[leg] = name
    for name in ifnames.values():
        sh("ip -4 addr flush dev %s; ip link set %s up" % (name, name))
        sh("ethtool -K %s gro off gso off tso off lro off" % name, check=False)
    want_pages = int(doc["trex"].get("hugepages", 256))
    have = int(open("/proc/sys/vm/nr_hugepages").read().strip() or 0)
    if have < want_pages:
        sh("sysctl -qw vm.nr_hugepages=%d" % want_pages)
    cfg = render_cfg(doc, ifnames)
    old = open(CFG).read() if os.path.exists(CFG) else None
    with open(CFG, "w") as f:
        f.write(cfg)
    units = {
        TREX_UNIT: unit_text("axrange trafficgen TRex ASTF",
                             "%s/t-rex-64 -i --astf --cfg %s --no-termio --iom 0" % (TREX_HOME, CFG), TREX_HOME),
        CTL_UNIT: unit_text("axrange trafficgen controller", "/usr/bin/python3 %s/tg.py controller" % BASE, BASE),
    }
    changed = False
    for name, text in units.items():
        p = "/etc/systemd/system/" + name
        if not os.path.exists(p) or open(p).read() != text:
            with open(p, "w") as f:
                f.write(text)
            changed = True
    if changed:
        sh("systemctl daemon-reload")
    restarted = old != cfg or sh("systemctl is-active --quiet %s" % TREX_UNIT, check=False).returncode != 0
    if restarted:
        sh("systemctl enable %s %s >/dev/null 2>&1; systemctl restart %s" % (TREX_UNIT, CTL_UNIT, TREX_UNIT))
        sh("systemctl restart %s" % CTL_UNIT)
    else:
        sh("systemctl enable --now %s >/dev/null 2>&1" % CTL_UNIT, check=False)
        if sh("systemctl is-active --quiet %s" % CTL_UNIT, check=False).returncode != 0:
            sh("systemctl restart %s" % CTL_UNIT)
    deadline = time.time() + a.timeout
    st = {}
    while time.time() < deadline:
        st = read_json(STATS, {}) or {}
        if st.get("namespaces_ok") and st.get("trex_connected") and st.get("doc_sha") == doc_sha():
            break
        time.sleep(2)
    else:
        raise SystemExit("controller not ready in %ss: %s" % (a.timeout, json.dumps(st)[:400]))
    out({"provisioned": True, "ifaces": ifnames, "trex_restarted": bool(restarted),
         "trex_version": open("/opt/trex/VERSION").read().strip() if os.path.exists("/opt/trex/VERSION") else ""})


def doc_sha():
    try:
        with open(DOC, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


# ------------------------------------------------------------------- desire
def cmd_desire(a):
    doc = read_json(DOC)
    d = read_json(DESIRED, {"generation": 0, "lines": {}}) or {"generation": 0, "lines": {}}
    cur = {k: v for k, v in d.items() if k != "generation"}
    cur.setdefault("lines", {})
    new = json.loads(json.dumps(cur))
    ids = [ln["id"] for ln in doc["lines"]]
    if a.state:
        for lid in ids:
            new["lines"][lid] = {"state": a.state, "mult": new["lines"].get(lid, {}).get("mult", 1.0)}
    if a.line:
        if a.line not in ids:
            raise SystemExit("unknown line %r (lines: %s)" % (a.line, ids))
        if a.mult is None or a.mult <= 0:
            raise SystemExit("--line needs --mult > 0")
        new["lines"][a.line] = {"state": new["lines"].get(a.line, {}).get("state", "running"), "mult": float(a.mult)}
    if a.remove:
        new["lines"].pop(a.remove, None)
    for lid in list(new["lines"]):
        if lid not in ids:
            new["lines"].pop(lid)
    same = cur == new
    gen = d.get("generation", 0) + (0 if same else 1)
    new["generation"] = gen
    if not same:
        write_json(DESIRED, new)
    deadline = time.time() + a.timeout
    st = {}
    while time.time() < deadline:
        st = read_json(STATS, {}) or {}
        if st.get("applied_generation", -1) >= gen and st.get("doc_sha") == doc_sha():
            break
        time.sleep(1)
    else:
        raise SystemExit("controller did not apply generation %s in %ss: %s" % (gen, a.timeout, json.dumps(st)[:400]))
    errs = {lid: v.get("error") for lid, v in st.get("lines", {}).items() if v.get("error")}
    if errs:
        raise SystemExit("line error(s): %s" % json.dumps(errs))
    out({"applied_generation": gen, "changed": not same,
         "lines": {lid: {k: v.get(k) for k in ("state", "mult_desired", "mult_effective")}
                   for lid, v in st.get("lines", {}).items()}})


def cmd_status(a):
    out(read_json(STATS, {}) or {})


# --------------------------------------------------------------- controller
def counters(t):
    c, s = t.get("client", {}), t.get("server", {})
    g = lambda d, k: int(d.get(k, 0) or 0)
    return {
        "opened": g(c, "tcps_connattempt") + g(c, "udps_connects"),
        "established": g(c, "tcps_connects") + g(c, "udps_connects"),
        "accepted": g(s, "tcps_accepts") + g(s, "udps_accepts"),
        "closed": g(c, "tcps_closed"),
        "errors": (g(c, "tcps_conndrops") + g(c, "tcps_drops") + g(s, "tcps_drops") + g(c, "udps_keepdrops")
                   + sum(int(v or 0) for side in (c, s) for k, v in side.items()
                         if k.startswith("err_") and k != "err_no_tcp_udp")),
        "bytes": (g(c, "tcps_sndbyte") + g(c, "tcps_rcvbyte") + g(c, "udps_sndbyte") + g(c, "udps_rcvbyte")),
    }


class Controller(object):
    """One reconcile pass per `step()`; `cmd_controller` loops it. `api` is the
    TRex ASTF client module (`trex.astf.api`; a fake in the engine tests)."""

    def __init__(self, api, ensure_ns=None):
        self.api = api
        self.ensure_ns = ensure_ns or ensure_namespaces
        self.c = None
        self.applied = {}   # line id -> {"sha", "state", "mult"}
        self.eff = {}       # line id -> effective multiplier (caps)
        self.prev = {}

    def _load(self, lid):
        prof = self.api.ASTFProfile.load(PROFILE, tunables=["--doc", DOC, "--line", lid, "--files", FILES])
        self.c.load_profile(prof, pid_input=lid)

    def step(self, now=None):
        st = {"at": now if now is not None else time.time(), "doc_sha": doc_sha(), "lines": {}}
        doc = read_json(DOC)
        try:
            if doc is None:
                raise RuntimeError("no document")
            ok, missing = self.ensure_ns(doc)
            st["namespaces_ok"], st["namespaces_missing"] = ok, missing
            if self.c is None:
                c = self.api.ASTFClient(server="127.0.0.1")
                c.connect()
                c.acquire(force=True)
                for pid in c.get_profiles():
                    if pid != "_":
                        c.stop(pid_input=pid, is_remove=True)
                self.c = c
                self.applied.clear()
            c = self.c
            st["trex_connected"] = True
            desired = read_json(DESIRED, {"generation": 0, "lines": {}}) or {"generation": 0, "lines": {}}
            lines = {ln["id"]: ln for ln in doc["lines"]}
            line_sha = {lid: hashlib.sha256(json.dumps(ln, sort_keys=True).encode()).hexdigest()
                        for lid, ln in lines.items()}
            g = c.get_stats(skip_zero=False)["global"]
            tx_mbps = float(g.get("tx_bps") or 0) / 1e6
            budget = (doc.get("budget") or {}).get("mbps")
            over_budget = bool(budget) and tx_mbps > float(budget)
            for lid in list(self.applied):
                if lid not in desired.get("lines", {}) or lid not in lines:
                    c.stop(pid_input=lid, is_remove=True)
                    self.applied.pop(lid)
                    self.eff.pop(lid, None)
            for lid, want in sorted(desired.get("lines", {}).items()):
                if lid not in lines:
                    continue
                rec = {"state": want["state"], "mult_desired": want["mult"], "rate_cps_planned": lines[lid]["rate_cps"]}
                try:
                    have = self.applied.get(lid)
                    if have is None or have["sha"] != line_sha[lid]:
                        if have is not None:
                            c.stop(pid_input=lid, is_remove=True)
                        self._load(lid)
                        have = self.applied[lid] = {"sha": line_sha[lid], "state": "loaded", "mult": None}
                    # A cap lowers the multiplier below what was asked and remembers it;
                    # a new ask, or recovery up to the ask, forgets it.
                    capst = self.eff.get(lid)
                    if capst is not None and capst["want"] != want["mult"]:
                        capst = None
                        self.eff.pop(lid, None)
                    m_eff = capst["m"] if capst else want["mult"]
                    if want["state"] == "running":
                        cnt = counters(c.get_traffic_stats(skip_zero=False, pid_input=lid)) if have["state"] == "running" else None
                        open_flows = (cnt["established"] - cnt["closed"]) if cnt else 0
                        cap = int(lines[lid].get("max_concurrent") or 0)
                        if have["state"] == "running" and ((cap and open_flows > cap) or over_budget):
                            m_eff = max(0.05, round(m_eff * 0.8, 4))
                            rec["capped"] = "max_concurrent" if (cap and open_flows > cap) else "budget_mbps"
                            self.eff[lid] = {"want": want["mult"], "m": m_eff}
                        elif capst is not None and (not cap or open_flows < cap * 0.7) and not over_budget:
                            m_eff = min(want["mult"], round(m_eff * 1.25, 4))
                            if m_eff >= want["mult"]:
                                self.eff.pop(lid, None)
                            else:
                                self.eff[lid] = {"want": want["mult"], "m": m_eff}
                        if have["state"] != "running":
                            c.start(mult=m_eff, pid_input=lid, client_mask=1 << int(lines[lid]["client_port"]))
                            have.update(state="running", mult=m_eff)
                        elif have["mult"] != m_eff:
                            c.update(mult=m_eff, pid_input=lid)
                            have["mult"] = m_eff
                    elif have["state"] == "running":
                        c.stop(pid_input=lid)            # stays loaded; start resumes it
                        have.update(state="loaded", mult=None)
                    rec["mult_effective"] = have["mult"]
                    cnt = counters(c.get_traffic_stats(skip_zero=False, pid_input=lid))
                    rec.update(cnt)
                    rec["open"] = cnt["established"] - cnt["closed"]
                    p = self.prev.get(lid)
                    if p:
                        dt = st["at"] - p[0]
                        rec["cps_now"] = round((cnt["opened"] - p[1]["opened"]) / dt, 2) if dt > 0 else 0
                    self.prev[lid] = (st["at"], cnt)
                except Exception as e:  # noqa: BLE001 -- reported per line, the loop goes on
                    rec["error"] = str(e)[:300]
                st["lines"][lid] = rec
            st["tx_mbps"] = round(tx_mbps, 3)
            st["applied_generation"] = desired.get("generation", 0)
        except Exception as e:  # noqa: BLE001 -- TRex not up yet / restarted: reconnect next round
            st["trex_connected"] = False
            st["error"] = str(e)[:300]
            try:
                if self.c is not None:
                    self.c.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self.c = None
        write_json(STATS, st)
        return st


def cmd_controller(a):
    sys.path.insert(0, TREX_HOME + "/automation/trex_control_plane/interactive")
    import trex.astf.api as api  # noqa: E402 -- the release's bundled client

    ctl = Controller(api)
    while True:
        ctl.step()
        time.sleep(INTERVAL_S)


def main(argv=None):
    p = argparse.ArgumentParser(prog="tg.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("provision")
    pr.add_argument("--legs", required=True)
    pr.add_argument("--timeout", type=int, default=180)
    de = sub.add_parser("desire")
    de.add_argument("--state", choices=["loaded", "running"])
    de.add_argument("--line")
    de.add_argument("--mult", type=float)
    de.add_argument("--remove")
    de.add_argument("--timeout", type=int, default=90)
    sub.add_parser("status")
    sub.add_parser("controller")
    a = p.parse_args(argv)
    os.makedirs(STATE, exist_ok=True)
    {"provision": cmd_provision, "desire": cmd_desire, "status": cmd_status, "controller": cmd_controller}[a.cmd](a)


if __name__ == "__main__":
    main()
