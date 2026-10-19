#!/usr/bin/env python3
"""verify_determinism — assert aximprint's determinism invariants.

All three are stated as house rules but nothing else enforces them:

  1. **Idempotent window close** (DESIGN_BRIEF §5.4, CONVENTIONS §3) — a window's
     outputs are a pure function of (events, resolver state, code build). Replay
     the same events through the same build and the features must be *identical*.
     This is what makes backfill, reprocessing, and the round-trip self-test
     honest; if it silently breaks, every downstream comparison is meaningless.
  2. **Deterministic compiler** (DESIGN_BRIEF §9) — the same belief snapshot must
     compile to byte-identical twin YAML, or the twin's fidelity claim is
     untestable.
  3. **Byte-stable feature archive** — invariant 1 observed where it is actually
     consumed: the files `aximprint features` writes. This is where a wall-clock
     read or a dict-iteration order would land, and the in-memory check above
     cannot see either. The archive is byte-identical run to run except for the
     manifest's one wall-clock field.

Runs the whole pipeline twice, in two separate stores and two separate feature
directories, and compares canonical JSON of the window artifacts, the raw bytes
of the synthesized YAML, and the raw bytes of the feature files. Non-mutating
apart from files under a temp dir, so it is safe to run unattended — it is the
aximprint analogue of axRange's cluster-free preflight checks.

`--plugin` (default `zeek`) selects which registered ingest plugin the three checks run
against; unqualified, "the Zeek path" below means whichever plugin was selected. Track B
(`docs/WORK_ORDER_axrange_integration.md`) widened this from a Zeek-only script so the new
Suricata plugin gets the identical three-invariant coverage, not a lesser one — this script
proves EACH plugin agrees with itself; `scripts/verify_suricata_equivalence.py` is the
separate, additional check that the two plugins agree with EACH OTHER on equivalent input.

Usage:
  python scripts/verify_determinism.py                              # synthetic zeek capture
  python scripts/verify_determinism.py --plugin suricata             # synthetic suricata capture
  python scripts/verify_determinism.py --source <dir>                # a real Zeek log dir
  python scripts/verify_determinism.py --plugin suricata --source <dir>  # a real eve.json source
  python scripts/verify_determinism.py --keep             # leave the temp dir for inspection

Exit 0 = all three checks hold. Exit 1 = a determinism break (with the first diff).
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import hashlib
import json
import random
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

# Imported from the producer rather than re-typed as a literal: a gate that re-declares the
# constant it is checking passes when both copies drift together, which is the duplicated-fact
# hazard `LES-105` names for exactly this kind of carve-out.
from aximprint.synth.compiler import PROVENANCE_SCHEMA

# Fixed base epoch — never `time.time()`, or the fixture stops being reproducible.
T0 = 1_754_000_000
SEED = 20260803


@dataclasses.dataclass
class _SyntheticFlow:
    """One logical flow, SENSOR-NEUTRAL — the shared unit `_synthetic_cast` produces and
    every per-sensor renderer below (`build_synthetic_capture` for Zeek,
    `build_synthetic_suricata_capture` for Suricata, `docs/WORK_ORDER_axrange_integration.md`
    Track B) turns into its own log shape.

    `outcome` is deliberately abstract (`"accepted" | "no_reply" | "refused"`) rather than a
    Zeek `conn_state` literal or a Suricata tcp-flags byte: it names what happened ON THE WIRE,
    which each renderer translates into ITS OWN sensor's vocabulary, honestly — including at
    the one point the two vocabularies cannot agree (see `build_synthetic_suricata_capture`'s
    own docstring on the "refused" case).
    """
    ts: int
    src: str
    sport: int
    dst: str
    dport: int
    proto: str            # "tcp" | "udp"
    app: str               # "krb" | "smb" | "dns" | "snmp" | "scan" — a label, not a wire fact
    duration: float
    bytes_out: int
    bytes_in: int
    pkts_out: int
    pkts_in: int
    outcome: str           # "accepted" | "no_reply" | "refused"


@dataclasses.dataclass
class _SyntheticCast:
    """The whole seeded plan, SENSOR-NEUTRAL — flows AND the identity facts on the wire.

    The identity lists were hoisted out of the two renderers on 2026-08-25 and the reason is
    the same one `_synthetic_cast`'s own docstring gives for the flows: until then each
    renderer spelled the mac, the host name and the Kerberos principal for itself, so
    "both sensors carry the SAME identity evidence" rested on two hand-copied format
    strings staying in step. They did not — the `@`-vs-`/` principal bug was exactly that
    drift, copied identically into both halves, and `scripts/verify_suricata_equivalence
    .py` compared two broken sides and passed. Declaring the facts ONCE gives that gate a
    plan to compare each sensor's output AGAINST, rather than only to the other sensor.
    """
    hosts: list          # all 80 addresses, in cast order
    dc: str
    fileserver: str
    collector: str
    scanner: str
    workstations: list
    flows: list           # list[_SyntheticFlow], in construction (pre-sort) order
    #: (ip, mac, host_name) — one DHCP lease per workstation. Both renderers write these
    #: verbatim into their own sensor's field spellings; the equivalence gate asserts the
    #: resulting bindings ARE these, per address, not merely equal to each other.
    dhcp_leases: list
    #: (ip, cname, realm) — an Active Directory MACHINE account (`cname` ends in `$`), the
    #: one identity shape in this plan that only anchors a host if the principal grammar is
    #: parsed correctly. Split into its parts, not pre-joined, because the two sensors
    #: legitimately carry it differently: Zeek's `kerberos.log` logs the joined principal,
    #: Suricata's `krb5` record logs `cname`/`realm` as separate fields.
    kerberos_principals: list
    #: (ip, mac, opcode) — ARP the SENDER asserts about itself, and a wire fact NO Zeek
    #: log carries (Zeek ships no `arp.log`), so this is the mirror image of `ntlm.log`:
    #: evidence one sensor can see and the other structurally cannot. Rendered into the
    #: eve.json fixture only.
    arp_announcements: list
    #: (ip, mac, opcode, why) — ARP records that MUST mint nothing, each with the positive
    #: gate in `ingest/suricata/plugin.py::_arp` that must refuse it. A parser's declined
    #: cases are as much a checkable claim as its accepted ones, and a fixture with only
    #: the happy path cannot tell "refused it" from "never saw it".
    arp_nonbinding: list


def _synthetic_cast(seed: int = SEED, t0: int = T0) -> _SyntheticCast:
    """The ONE traffic plan both sensor renderers draw from — same cast, same flows, same
    seed (`docs/WORK_ORDER_axrange_integration.md` Track B's "Done when"), by CONSTRUCTION
    rather than by two independently-hand-copied `random.Random(SEED)` call sequences that
    could silently drift apart the moment either renderer is next edited. This is the exact
    risk `ingest/suricata/sources.py`'s own module docstring names for a reorder buffer one
    layer over ("a second, independent implementation is never written alongside it",
    `Q2`) — applied here to a test fixture instead of production code, because a determinism
    fixture that quietly stopped exercising the SAME plan on both sensors would still run
    green while proving nothing.

    Returns the RAW plan (host addresses, flows in construction order, not yet sorted or
    rendered into any sensor's own field names) — every rendering decision (which Zeek
    `conn_state` string, which Suricata `tcp`/`flow` fields) belongs to the caller, not here.
    """
    rng = random.Random(seed)
    hosts = [f"10.62.{i // 254}.{i % 254 + 1}" for i in range(80)]
    dc, fileserver, collector, scanner = hosts[0], hosts[1], hosts[2], hosts[3]
    workstations = hosts[4:]
    flows: list[_SyntheticFlow] = []

    def add(ts, src, sport, dst, dport, proto, app, dur, bo, bi, po, pi, outcome):
        flows.append(_SyntheticFlow(ts, src, sport, dst, dport, proto, app, dur, bo, bi,
                                    po, pi, outcome))

    # Workstations: kerberos to the DC + SMB to the file server, over 3 hours.
    for i, ws in enumerate(workstations):
        for k in range(rng.randint(4, 14)):
            ts = t0 + rng.randint(0, 3 * 3600 - 1)
            add(ts, ws, 40000 + k, dc, 88, "tcp", "krb", rng.uniform(0.05, 0.9),
                rng.randint(300, 900), rng.randint(800, 2400), 5, 5, "accepted")
        for k in range(rng.randint(1, 5)):
            ts = t0 + rng.randint(0, 3 * 3600 - 1)
            add(ts, ws, 41000 + k, fileserver, 445, "tcp", "smb", rng.uniform(1.0, 30.0),
                rng.randint(2_000, 40_000), rng.randint(50_000, 2_000_000), 40, 700, "accepted")
        # DNS to the DC
        for k in range(rng.randint(2, 8)):
            ts = t0 + rng.randint(0, 3 * 3600 - 1)
            add(ts, ws, 42000 + k, dc, 53, "udp", "dns", 0.01,
                rng.randint(40, 120), rng.randint(60, 400), 1, 1, "accepted")

    # Collector: talks to EVERY host -> forces top-K peer eviction + longtail spill.
    for i, h in enumerate(hosts):
        if h == collector:
            continue
        ts = t0 + (i * 37) % 3600
        add(ts, collector, 50000 + i, h, 161, "udp", "snmp", 0.05,
            rng.randint(100, 400), rng.randint(200, 900), 2, 2, "accepted")

    # Scanner: unanswered + refused across the space. These 60 rows are the ONLY thing in
    # this plan that exercises the `attempted` and `refused` arms of `state_mix` — every
    # other flow above is `accepted`. Until 2026-08-26 this comment said they fed the "S0 /
    # REJ hygiene counters"; those two scalars are gone (`Q34` deleted them rather than
    # re-keying, since `state_mix` already carries both classes under a CLOSED vocabulary),
    # but the rows are not vestigial — without them `_flow_state_class` and
    # `_CONN_STATE_CLASS` are handed one class each and no gate can tell a working
    # classifier from a constant (`verify_suricata_equivalence.py` asserts exactly that,
    # and fails loudly if this plan ever spans fewer than two outcome classes).
    for i, h in enumerate(hosts[:60]):
        ts = t0 + 1800 + i
        outcome = "no_reply" if i % 3 else "refused"
        add(ts, scanner, 33000 + i, h, 23, "tcp", "scan", 0.0, 0, 0, 1, 0, outcome)

    # Boundary-STRADDLING flows: each starts strictly inside one window and closes
    # strictly inside the next. Added 2026-08-25 with no `rng` draw of its own, at the
    # very end, so every seeded value above is byte-unchanged.
    #
    # These exist for one reason: **`ingest/suricata/plugin.py::_flow`'s `M4` fix — key
    # `FlowEvent.ts` on `flow.start`, not on the record's own emit `timestamp` — is
    # UNOBSERVABLE without them.** Measured on this plan before they were added: 1,429
    # flows, of which exactly **0** had a close time in a different 1 h window than their
    # start, so keying on either field placed every single row in the same window and a
    # build that keyed on the wrong one produced identical output. `t0 % 3600 == 800`, so
    # the window edges inside this plan's 3 h span sit at `t0+2800`, `t0+6400`, `t0+10000`;
    # each flow below starts 5 s before an edge and runs 20 s, landing its close 15 s past
    # it. Zeek is unaffected either way (`conn.log` is keyed on `ts` and its renderer never
    # consults `duration`), which is exactly what makes this a one-sided null test: the two
    # sensors must still agree, and they can only do so if Suricata keys on start.
    #
    # Deliberately re-using an existing (workstation -> fileserver, 445) conversation so
    # this adds no node and no edge — only rows on an edge the top-K/longtail comparison
    # already ranks.
    for edge in (2800, 6400, 10000):
        add(t0 + edge - 5, workstations[0], 43000 + edge, fileserver, 445, "tcp", "smb",
            20.0, 5_000, 90_000, 30, 300, "accepted")

    # -- identity facts on the wire ------------------------------------------------
    # No `rng` draw anywhere below (nor above, in the straddler loop), on purpose: every
    # seeded flow value stays byte-identical to what this plan produced before these
    # lists existed. The formats are the ones both renderers already used, moved here
    # rather than rewritten.
    dhcp_leases = [(ws, f"aa:bb:cc:00:{i // 256:02x}:{i % 256:02x}", f"ws{i:03d}")
                   for i, ws in enumerate(workstations)]
    # `$`-suffixed: an AD COMPUTER account, i.e. host-scoped evidence
    # (`windowing/entities.py::kerberos_identifier`). A principal without the suffix
    # names a PERSON and anchors nothing, which is the distinction the `@`-vs-`/` bug
    # accidentally erased on both sensors at once.
    kerberos_principals = [(ws, f"ws{i:03d}$", "CORP.AXR.LAB")
                           for i, ws in enumerate(workstations[:20])]
    # ARP, Suricata-side only (see `_SyntheticCast.arp_announcements`). The macs are the
    # SAME ones the DHCP leases above carry for these addresses — a host asserting its own
    # hardware address over ARP is asserting the fact its DHCP lease already recorded, and
    # rendering a DIFFERENT mac here would inject a contradiction this plan does not mean
    # to make (the resolver would be right to treat it as one).
    arp_announcements = [(ip, mac, opcode) for (ip, mac, _hn), opcode in
                         zip(dhcp_leases[:4], ("reply", "reply", "request", "request"),
                             strict=False)]
    arp_nonbinding = [
        # RFC 5227 §2.1.1: a host ASKING whether an address is free claims nothing.
        ("0.0.0.0", dhcp_leases[4][1], "request", "rfc5227 probe: unspecified sender"),
        # An opcode outside `_ARP_SENDER_OPCODES` is a shape this build has not ruled on:
        # counted by name in `SuricataPlugin.parse_notes`, never guessed at.
        (dhcp_leases[5][0], dhcp_leases[5][1], "reverse_request", "unruled opcode"),
    ]
    return _SyntheticCast(hosts, dc, fileserver, collector, scanner, workstations, flows,
                          dhcp_leases, kerberos_principals, arp_announcements,
                          arp_nonbinding)


def build_synthetic_capture(dest: Path) -> Path:
    """Write a seeded synthetic Zeek log set. Same seed -> byte-identical logs.

    Deliberately shaped to exercise the paths a 3-row fixture misses: several
    windows, sub-bucket spread, quantile spread, all three wire outcomes (so
    `state_mix`'s `established`/`attempted`/`refused` arms are each populated —
    this used to read "S0/REJ hygiene counters", two scalars `Q34` deleted on
    2026-08-26; the ROWS still matter, the counters they fed no longer exist),
    alert taint, capture loss, and a collector host with enough peers to force the
    top-K edge eviction + `__longtail__` spill (windows.TOP_PEERS).

    The flow traffic (`conn.log`) is `_synthetic_cast()`'s output rendered into Zeek's own
    `conn_state` vocabulary — factored out (not hand-duplicated) so
    `build_synthetic_suricata_capture` renders the identical plan rather than a second,
    independently-drawn one (that function's own docstring, `docs/WORK_ORDER_axrange_
    integration.md` Track B).
    """
    dest.mkdir(parents=True, exist_ok=True)
    cast = _synthetic_cast()
    dc, fileserver, collector, scanner = cast.dc, cast.fileserver, cast.collector, cast.scanner
    workstations = cast.workstations

    _ZEEK_STATE = {"accepted": "SF", "no_reply": "S0", "refused": "REJ"}
    conn = [
        f"{f.ts:.6f}\t{f.src}\t{f.sport}\t{f.dst}\t{f.dport}\t{f.proto}\t"
        f"{'-' if f.app == 'scan' else f.app}"
        f"\t{f.duration:.6f}\t{f.bytes_out}\t{f.bytes_in}\t{_ZEEK_STATE[f.outcome]}"
        f"\t{f.pkts_out}\t{f.pkts_in}"
        for f in sorted(cast.flows, key=lambda f: f.ts)
    ]
    header = "\n".join([
        "#separator \\x09",
        "#fields\tts\tid.orig_h\tid.orig_p\tid.resp_h\tid.resp_p\tproto\tservice"
        "\tduration\torig_bytes\tresp_bytes\tconn_state\torig_pkts\tresp_pkts",
    ])
    (dest / "conn.log").write_text(header + "\n" + "\n".join(conn) + "\n")

    # DHCP (JSON-lines) — bindings, so the identity-delta path is exercised.
    dhcp = [json.dumps({"ts": T0 + 5 + i * 3, "mac": mac,
                        "assigned_addr": ws, "host_name": host_name,
                        "lease_time": 86400})
            for i, (ws, mac, host_name) in enumerate(cast.dhcp_leases)]
    (dest / "dhcp.log").write_text("\n".join(dhcp) + "\n")

    # Kerberos principals, SSL certs, an alert (taint), and capture loss.
    #
    # **`client` joins the principal to its realm with a SLASH, not an `@`** — Zeek's own
    # `kerberos.log` spelling, and what `windowing/entities.py::kerberos_identifier`'s own
    # docstring states outright ("Zeek's `client` is the principal joined to its realm with
    # `/`"). This fixture wrote `ws000$@CORP.AXR.LAB` from its first landing until
    # 2026-08-25, and that one character silently disarmed the strongest identity axis this
    # whole fixture has: `kerberos_identifier` splits on `/`, saw ONE component whose primary
    # (`ws000$@CORP.AXR.LAB`) does not end in `$`, and classified all 20 machine accounts as
    # USER principals — `USER` scope anchors no host (D1), so every one of them was recorded
    # and discarded. Worse, the Suricata renderer below composed its `principal` the same
    # wrong way, so `scripts/verify_suricata_equivalence.py` compared two IDENTICALLY BROKEN
    # sides on the raw `bound_to` string and reported them equivalent. That is exactly the
    # failure mode that gate exists to catch, and agreement-in-brokenness passed it — which
    # is why `aggregate_identity` there now carries `scope`/`namespace` in its comparison key
    # and not just the raw string.
    (dest / "kerberos.log").write_text("\n".join(
        json.dumps({"ts": T0 + 60 + i, "client": f"{cname}/{realm}", "id.orig_h": ws})
        for i, (ws, cname, realm) in enumerate(cast.kerberos_principals)) + "\n")
    (dest / "ssl.log").write_text("\n".join(
        json.dumps({"ts": T0 + 120 + i * 7, "id.resp_h": fileserver, "id.resp_p": 443,
                    "server_name": f"files{i}.corp.axr.lab", "version": "TLSv13",
                    "ja3": f"{i:032x}"})
        for i in range(12)) + "\n")
    (dest / "notice.log").write_text(json.dumps(
        {"ts": T0 + 1900, "id.orig_h": scanner, "id.resp_h": dc,
         "note": "Scan::Port_Scan", "msg": "scanned 60 hosts"}) + "\n")
    (dest / "capture_loss.log").write_text(json.dumps(
        {"ts": T0 + 3600, "percent_lost": 2.5, "peer": "zeek-worker-1"}) + "\n")
    # Two `stats.log` rows, exercising BOTH `SENSOR_HEALTH_AGGREGATION` reducers on values
    # that would disagree under the wrong one: `pkts_dropped` (`max`) is smaller on the
    # second row, `pkts_proc` (`sum`) grows — so a fixture that only ever wrote one row per
    # kind could not tell `max` from `sum` from `last`. `pkts_dropped`/`pkt_lag` unset on
    # the second row exercises the same per-report presence gate `_num` always enforces
    # (`code review, docs/WORK_ORDER_zeek_stem_coverage.md step 3 addendum` —
    # `pkts_proc`/`bytes_recv` joined `_stats`'s emitted kinds this round).
    (dest / "stats.log").write_text("\n".join([
        json.dumps({"ts": T0 + 3610, "peer": "zeek-worker-1", "pkts_dropped": 40,
                    "pkt_lag": 0.3, "pkts_proc": 1, "bytes_recv": 82}),
        json.dumps({"ts": T0 + 3620, "peer": "zeek-worker-1", "pkts_dropped": 12,
                    "pkts_proc": 400_053, "bytes_recv": 115_723_828}),
    ]) + "\n")

    # docs/WORK_ORDER_zeek_stem_coverage.md step 3's ten stems (`Q24`) — `dc`/`fileserver`
    # pick up a second role each (a small network's DC and file server routinely also run
    # HTTP/SSH/LDAP/SMTP), and `device` is a dedicated edge host so `snmp`/`tunnel` land on
    # an entity the krb/smb/dns loops above never touch. Real values throughout, including
    # `smtp`/`sip`'s `date` and `snmp`'s `duration` — RESTORED (code review `S1`) after this
    # stem's first landing excluded them; present on these rows so the transport-equivalence
    # gate exercises the actual (stem, column)-keyed typing / `hive_partitioning=false` fix
    # end to end, not just the unit tests.
    device = workstations[-1]
    (dest / "http.log").write_text(json.dumps(
        {"ts": T0 + 2000, "id.orig_h": workstations[0], "id.orig_p": 52000,
         "id.resp_h": dc, "id.resp_p": 80, "host": "intranet.corp.axr.lab", "uri": "/",
         "method": "GET", "user_agent": "Mozilla/5.0", "username": "alice",
         "password": "hunter2"}) + "\n")
    (dest / "ssh.log").write_text(json.dumps(
        {"ts": T0 + 2001, "id.orig_h": workstations[0], "id.orig_p": 52001,
         "id.resp_h": dc, "id.resp_p": 22, "server": "SSH-2.0-OpenSSH_9.0",
         "host_key": "SHA256:abc123", "auth_success": True}) + "\n")
    (dest / "quic.log").write_text(json.dumps(
        {"ts": T0 + 2002, "id.orig_h": workstations[0], "id.orig_p": 52002,
         "id.resp_h": dc, "id.resp_p": 443, "version": "1",
         "server_name": "intranet.corp.axr.lab"}) + "\n")
    (dest / "snmp.log").write_text(json.dumps(
        {"ts": T0 + 2003, "id.orig_h": collector, "id.orig_p": 52003,
         "id.resp_h": device, "id.resp_p": 161, "duration": 0.5, "version": "2c",
         "community": "public", "display_string": "Cisco IOS Software"}) + "\n")
    (dest / "tunnel.log").write_text(json.dumps(
        {"ts": T0 + 2004, "id.orig_h": workstations[1], "id.orig_p": 52004,
         "id.resp_h": device, "id.resp_p": 4789, "tunnel_type": "Tunnel::VXLAN",
         "action": "Tunnel::DISCOVER"}) + "\n")
    (dest / "smtp.log").write_text(json.dumps(
        {"ts": T0 + 2005, "id.orig_h": workstations[2], "id.orig_p": 52005,
         "id.resp_h": fileserver, "id.resp_p": 25, "date": "Wed, 06 Aug 2026 10:15:00 -0700",
         "helo": "mail.corp.axr.lab", "path": [fileserver, "198.51.100.7"],
         "x_originating_ip": "198.51.100.7"}) + "\n")
    (dest / "sip.log").write_text(json.dumps(
        {"ts": T0 + 2006, "id.orig_h": workstations[3], "id.orig_p": 52006,
         "id.resp_h": fileserver, "id.resp_p": 5060, "date": "Wed, 06 Aug 2026 10:15:00 -0700",
         "user_agent": "Zeek-SIP-Test/1.0"}) + "\n")
    (dest / "ldap.log").write_text(json.dumps(
        {"ts": T0 + 2007, "id.orig_h": workstations[4], "id.orig_p": 52007,
         "id.resp_h": dc, "id.resp_p": 389, "opcode": "bindRequest",
         "argument": "cn=alice,dc=corp,dc=axr,dc=lab"}) + "\n")
    # Two rows: the DNS-name-wins case, and the NetBIOS-only fallback case.
    (dest / "ntlm.log").write_text("\n".join([
        json.dumps({"ts": T0 + 2008, "id.orig_h": workstations[5], "id.orig_p": 52008,
                    "id.resp_h": dc, "id.resp_p": 445,
                    "server_dns_computer_name": "dc01.corp.axr.lab",
                    "server_nb_computer_name": "DC01"}),
        json.dumps({"ts": T0 + 2009, "id.orig_h": workstations[6], "id.orig_p": 52009,
                    "id.resp_h": fileserver, "id.resp_p": 445,
                    "server_nb_computer_name": "FILESRV1"}),
    ]) + "\n")
    # x509 carries no connection tuple at all (verified against the real baseline) — file
    # -keyed only, structurally inert for node/edge features today.
    (dest / "x509.log").write_text(json.dumps(
        {"ts": T0 + 2010, "fingerprint": "aa:bb:cc:dd", "certificate.subject":
         "CN=intranet.corp.axr.lab", "san.dns": ["intranet.corp.axr.lab",
                                                  "alt.corp.axr.lab"]}) + "\n")
    # ocsp carries no connection tuple either (`Q29`) — ProfileEvent, not ServiceEvent, and the
    # one stem in this fixture with no subject at all. Two rows, two distinct cache-lifetime
    # deltas (`nextUpdate - thisUpdate`), matching the two dominant values measured on the real
    # baseline (604800s/7d and 601200s/~6.96d, `/tmp/aximprint-zeek-test/`) so the quantile
    # summary this exercises is not a single repeated point.
    (dest / "ocsp.log").write_text("\n".join([
        json.dumps({"ts": T0 + 2011, "id": "ocsp-resp-1", "certStatus": "good",
                    "thisUpdate": T0, "nextUpdate": T0 + 604800}),
        json.dumps({"ts": T0 + 2012, "id": "ocsp-resp-2", "certStatus": "good",
                    "thisUpdate": T0 + 100, "nextUpdate": T0 + 100 + 601200}),
    ]) + "\n")
    # `docs/WORK_ORDER_zeek_stem_coverage.md` D5, amended by code review `S2`: this
    # fixture DOES carry stems outside `_PARSERS` — a declared `_SKIP` member
    # (`reporter`) and a genuinely unrecognized one (`_UNKNOWN_STEM`-shaped, never
    # a real Zeek stem name, `LES-086`) — so `unparsed_log_types`/
    # `unrecognized_log_types` exercise their real, POPULATED shape through this
    # script's own two-pass determinism comparison and through tier 2's
    # transport-equivalence gate, not only the trivial `[]`/`None` case.
    #
    # An EARLIER version of this comment argued the opposite — that adding such a
    # stem here would make the archive transport's `unparsed_log_types` genuinely,
    # correctly DIVERGE from the flat transport's (the archive's own converter,
    # `make_zeek_archive.py`, never writes a skipped/unrecognized stem into its
    # manifest at all — `ingest/zeek/sources.py::ParquetArchiveSource
    # .inventory_is_complete`), and that `scripts/verify_pipeline_gates.sh`'s
    # transport-equivalence gate would fail on that real, expected divergence, so
    # it "was tried and reverted". That reasoning was correct **at the time it was
    # written** and stopped being true the moment the gate grew a DECLARED
    # carve-out for exactly this divergence, in the SAME round (code review `S1`,
    # `scripts/verify_pipeline_gates.sh`'s own `windowing.windows
    # .TRANSPORT_DEPENDENT_HEALTH_FIELDS`-driven comparison) — leaving the old
    # comment in place afterward would have quietly discouraged the next reader
    # from ever re-trying what had, by then, already been fixed.
    (dest / "reporter.log").write_text(json.dumps(
        {"ts": T0 + 2013, "level": "REPORTER_INFO",
         "message": "loaded_scripts.log will end at this pcap's own last packet",
         "location": ""}) + "\n")
    (dest / "__not_a_zeek_stem__.log").write_text(json.dumps(
        {"ts": T0 + 2014, "note": "a stem no build has ever routed into _PARSERS or _SKIP"}
    ) + "\n")
    return dest


def _suricata_ts(ts: float) -> str:
    """Epoch seconds -> Suricata's own `timestamp` shape, the exact inverse of
    `ingest.suricata.sources.parse_suricata_timestamp` (offset with no colon)."""
    return _dt.datetime.fromtimestamp(ts, tz=_dt.UTC).strftime(
        "%Y-%m-%dT%H:%M:%S.%f") + "+0000"


def _suricata_tcp_flags(outcome: str) -> tuple[str, str]:
    """(tcp_flags_ts, tcp_flags_tc) — the accumulated per-direction hex flag bytes
    `ingest/suricata/plugin.py::_flow_state_class` reads, chosen to be the bytes a REAL
    capture of this outcome would actually carry (RFC 793 bit positions: FIN=0x01,
    SYN=0x02, RST=0x04, PSH=0x08, ACK=0x10):

      accepted   both sides completed a normal handshake and a graceful close
                 (SYN+ACK+PSH+FIN, no RST either way) -> `_flow_state_class` reads
                 pkts_toclient>0 and no RST -> "established". Matches Zeek's "SF".
      no_reply   the originator's lone SYN, nothing back at all -> pkts_toclient<=0
                 -> "attempted". Matches Zeek's "S0" exactly: both sensors see zero
                 packets from the responder, so both classifications agree. The Zeek
                 tokens named here and in `build_synthetic_capture`'s `_ZEEK_STATE` are
                 RENDERING targets — the word a Zeek `conn.log` would carry — never a
                 field the pipeline publishes; `Q34` (2026-08-26) deleted the two node
                 scalars that keyed on them, and that agreement is now also asserted one
                 layer up, on the normalized class rather than the sensor's word, by
                 `tests/test_windowing_states.py::
                 test_hygiene_counts_are_keyed_on_the_normalized_class_not_the_sensor_word`.
      refused    the responder answered the SYN with a single RST/ACK — a REAL
                 REJ-shaped exchange, physically. `tc` carries RST and **no SYN**, which
                 is the whole discrimination: an established-then-reset flow necessarily
                 carries the responder's own SYN in its accumulated `tcp_flags_tc` byte
                 and a refused one cannot, so `_flow_state_class` returns `"refused"` —
                 matching Zeek's `REJ`, and OUTSIDE `events.FLOW_ACCEPTED` on both
                 sensors. Still the one outcome this fixture renders with genuinely
                 different pkts/bytes than the Zeek side uses (see
                 `build_synthetic_suricata_capture`), because feeding it Zeek's own
                 S0-shaped zero counters would send it down the `"attempted"` branch
                 (`pkts_toclient <= 0` is checked first, and rightly) and this flag pair
                 would never be read at all.

    **An earlier version of this docstring said the opposite** — that a flow record
    cannot tell a refusal from a later reset, "both read as RST from the responder with
    nothing else to go on" — and `scripts/verify_suricata_equivalence.py` carried the
    resulting `listens_on 23/tcp` on 20 non-listening hosts as its named exception #1.
    Code review `M2` disproved the premise (the responder's SYN is right there in the
    accumulated byte) and this fixture's flags were already correct for the fixed
    classifier, so the exception is gone rather than reworded. Measured after the fix:
    `state_mix` run totals are identical on both sensors, `refused` included.
    """
    if outcome == "refused":
        return "02", "14"    # ts: SYN only.  tc: RST(0x04)|ACK(0x10) = 0x14
    if outcome == "no_reply":
        return "02", "00"    # ts: SYN only.  tc: nothing back
    return "1b", "1b"        # accepted: SYN|PSH|FIN|ACK (0x1b) both directions


def build_synthetic_suricata_capture(dest: Path) -> Path:
    """Write a seeded synthetic Suricata `eve.json` capture — same cast, same flows, same
    identity bindings, same services, same seed as `build_synthetic_capture`'s Zeek logs
    (`docs/WORK_ORDER_axrange_integration.md` Track B's "Done when"), rendered through
    `ingest/suricata/plugin.py`'s own assumed eve.json field shapes instead of Zeek's.

    Every event_type this fixture emits is one `_PARSERS`/`_SKIP` already names (that
    module's own confidence disclosure applies here unchanged: HIGH for `flow`/`alert`/
    `stats`/`dns`-shaped kinds, lower for the nested field spellings of `smb`/`dcerpc`/
    `snmp`/`sip`/`ssh`). This fixture does NOT defer to that module's chosen paths as
    ground truth — it is built independently against Suricata's publicly documented
    eve.json schema, so a real spelling mistake in the plugin has somewhere to show up
    (a parser that silently produces nothing) rather than being invisible because the
    fixture and the parser share one author's guess (see `docs/WORK_ORDER_axrange_
    integration.md` Track B and this build's own task brief on why that self-consistency
    trap is exactly what a unit-test-only fixture cannot catch).

    **What this fixture cannot make equivalent, named rather than smoothed over** —
    `scripts/verify_suricata_equivalence.py` is where each of these is actually asserted
    or explicitly excluded, not just mentioned here:

    * **Window PLACEMENT is equivalent, and this fixture is what makes that checkable.**
      Records are stamped at flow CLOSE (`timestamp = flow.start + duration`, what a real
      Suricata emits) while `_flow` keys `FlowEvent.ts` on `flow.start` (code review
      `M4`) — so the two differ, the rows arrive sorted by close and therefore OUT OF
      ORDER in their own key (measured: 151 of 1,432 flow rows, max regression 29.0 s,
      well inside `REORDER_HORIZON_SECONDS`'s 600 s), and `_synthetic_cast`'s three
      deliberately boundary-straddling flows start in one 1 h window and close in the
      next. Before 2026-08-25 this fixture set `timestamp == flow.start` and the plan had
      0 straddlers, so `M4` was unobservable in both halves at once: either keying choice
      placed every row in the same window. NOT a named exception — an equality assertion
      (`verify_suricata_equivalence.py` section 2), listed here because the fixture, not
      the gate, is what makes it non-vacuous.
    * **ARP identity evidence — the asymmetry running the OTHER way.** Suricata logs `arp`
      (verified in its own `etc/schema.json`) and Zeek ships no `arp.log` at all, so this
      fixture renders ARP on the eve.json side ONLY. It is not a `capture_profile`-shaped
      "one sensor has no code path" gap either: `ingest/suricata/plugin.py::_arp` parses it
      into `IdentityEvent(kind="arp")` today, and `windowing.entities.IDENTITY_SCOPES` has
      no `arp` row, so that evidence is COUNTED (`unscoped_identity_kinds`) and anchors
      nothing on either sensor. That is the honest current state, asserted as such by
      `verify_suricata_equivalence.py` — including a trip-wire that FAILS the day the scope
      row lands, because on that day this stops being a divergence and starts being a real
      identity axis that has to be compared, not excused.
    * **NTLM identity evidence.** No `ntlm` event_type exists in Suricata's documented
      eve.json vocabulary (Windows name resolution surfaces inside `smb`, not as its own
      top-level record) — `ingest/suricata/plugin.py` has no parser for it and none is
      added here. The two ntlm-derived hostname bindings the Zeek fixture's `ntlm.log`
      contributes (naming the DC and the file server) have no Suricata-side counterpart,
      so those two entities resolve HOST-scoped on Zeek (`ent-NNNN`) and un-anchored on
      Suricata (their own address, D4) — a real difference in entity-KEY kind, not
      value, for exactly those two hosts.
    * **`capture_profile` (the `ProfileEvent`/`Q29` channel).** `ingest/suricata/plugin.py`
      does not import `ProfileEvent` at all — there is no code path from any eve.json
      record to this channel, so a Suricata-sourced run's `capture_profile` is `{}` in
      EVERY window, structurally, not merely unexercised by this fixture. No OCSP-shaped
      event_type exists in Suricata's eve.json either, so there is nothing to emit
      regardless.
    * **`tunnel`, `x509` (as a distinct log), sensor-side `capture_loss`.** None of these
      are real Suricata eve.json event types (overlay-tunnel detection, and certificate
      fields, are not separately logged the way Zeek's dedicated stems are; Suricata's
      loss signal is `stats.capture.kernel_drops` only, already narrower per that
      module's own docstring) — not rendered here since there is no honest shape to
      render. `x509` costs nothing on the Zeek side either: `_SERVICE_NOT_PROJECTED
      ["x509"]` already makes it inert for node/edge/service comparison (no connection
      tuple), so omitting it from this fixture drops no comparison this gate could
      otherwise make.
    * **`stats.capture.kernel_drops` is rendered and DELIBERATELY consumed by nothing.**
      The same two readings (40, then 12) `stats.log` carries are written here, `stats` is
      in `_PARSERS`, and `_stats` reads them and mints no `SensorHealthEvent` at all
      (code review `S5`): Suricata's counters are cumulative-since-start unless the
      deployment set `deltas: yes`, and one record cannot prove which. So a Suricata run's
      `sensor_pkts_dropped_max` is `None` — "not observed", not "no drops" — and the
      equivalence gate carries that as its named exception #1. They stay in this fixture
      on purpose: the day that decline is reopened by a measurement, the rows the parser
      needs are already here.
    * **Raw byte/packet counts on `refused` flows.** Deliberately DIFFERENT from the Zeek
      fixture's own `orig_bytes`/`resp_bytes` for those 20 rows (which are `0`, since
      Zeek's classifier never derives state from them) — see `_suricata_tcp_flags`.
      Elsewhere (the `accepted` flows) this fixture reuses the exact same byte/packet
      values on both sensors, which is a synthetic-fixture simplification, not a general
      claim: a real capture would very likely show Zeek's `orig_bytes`/`resp_bytes`
      (payload bytes) and Suricata's `flow.bytes_toserver`/`bytes_toclient` (full wire
      bytes, headers included) disagree on the SAME real flow. This fixture is
      hand-authored, not replayed from packets, so using one number on both sides is a
      choice, not a measurement — flagged here so nobody reads the equivalence gate's
      byte-count check as a general property of the two sensors.
    """
    dest.mkdir(parents=True, exist_ok=True)
    cast = _synthetic_cast()
    dc, fileserver, collector, scanner = cast.dc, cast.fileserver, cast.collector, cast.scanner
    workstations = cast.workstations
    device = workstations[-1]

    rows: list[dict] = []

    _APP_PROTO = {"krb": "krb5", "smb": "smb", "dns": "dns", "snmp": "snmp", "scan": "failed"}
    for f in cast.flows:
        pkts_toclient, bytes_toclient = f.pkts_in, f.bytes_in
        if f.outcome == "refused":
            # Physically honest for a real REJ-shaped exchange (a lone RST/ACK back),
            # NOT the Zeek fixture's own `resp_pkts=0` for this row — see this
            # function's own docstring and `_suricata_tcp_flags`.
            pkts_toclient, bytes_toclient = 1, 40
        rec = {
            # **Stamped at flow CLOSE, not at flow start** (code review `M4`). A real
            # Suricata emits a `flow` record when the flow ends or times out, so the
            # record's own `timestamp` is the END of the span while `flow.start` is the
            # beginning — which is why `_flow` keys `FlowEvent.ts` on `flow.start` and
            # falls back to `timestamp` only when it cannot. Until 2026-08-25 this fixture
            # set the two equal, which made `M4` INVISIBLE here: with `timestamp ==
            # flow.start` the two keys pick the same window for every row, so a build that
            # keyed on the wrong one would still have produced identical output and passed
            # the equivalence gate. Stamping the honest close time is what puts the
            # `smb` flows (up to 30 s) genuinely across window boundaries and, because the
            # rows below are sorted by `timestamp`, feeds the events to `ingest/suricata/
            # sources.py`'s reorder buffer OUT OF ORDER in their own key by up to that
            # span — bounded well inside `REORDER_HORIZON_SECONDS` (600 s), so the repair
            # is expected to be complete and the window placement identical to Zeek's.
            "timestamp": _suricata_ts(f.ts + f.duration), "event_type": "flow",
            "src_ip": f.src, "src_port": f.sport, "dest_ip": f.dst, "dest_port": f.dport,
            "proto": f.proto.upper(), "app_proto": _APP_PROTO[f.app],
            "flow": {
                "pkts_toserver": f.pkts_out, "pkts_toclient": pkts_toclient,
                "bytes_toserver": f.bytes_out, "bytes_toclient": bytes_toclient,
                "start": _suricata_ts(f.ts), "end": _suricata_ts(f.ts + f.duration),
                "age": round(f.duration),
                "state": {"accepted": "closed", "no_reply": "new",
                         "refused": "closed"}[f.outcome],
            },
        }
        if f.proto == "tcp":
            ts_flags, tc_flags = _suricata_tcp_flags(f.outcome)
            rec["tcp"] = {"tcp_flags_ts": ts_flags, "tcp_flags_tc": tc_flags,
                         "state": rec["flow"]["state"]}
        rows.append(rec)

    # DHCP — the same 76 leases `build_synthetic_capture`'s `dhcp.log` carries, from the
    # one plan that declares them (`_SyntheticCast.dhcp_leases`), not a second hand-copy
    # of the mac/hostname formats.
    for i, (ws, mac, host_name) in enumerate(cast.dhcp_leases):
        rows.append({
            "timestamp": _suricata_ts(T0 + 5 + i * 3), "event_type": "dhcp",
            "src_ip": ws,
            "dhcp": {"client_mac": mac, "assigned_ip": ws, "hostname": host_name,
                     "lease_time": 86400},
        })

    # Kerberos — the same 20 machine principals `kerberos.log` carries. `_krb5` joins
    # `cname`+`realm` with a SLASH (code review `M1`), producing the identical
    # `ws{i:03d}$/CORP.AXR.LAB` string `_kerberos`'s Zeek-side parser receives pre-joined
    # from `kerberos.log`'s own `client` field — so both sensors hand
    # `windowing/entities.py::kerberos_identifier` a principal in the grammar it parses,
    # and all 20 resolve at HOST scope in namespace `hostname`. This fixture carried an
    # `@` on BOTH sides until 2026-08-25 and the two agreed while both were wrong; see
    # `build_synthetic_capture`'s own note on why the equivalence gate could not see it.
    for i, (ws, cname, realm) in enumerate(cast.kerberos_principals):
        rows.append({
            "timestamp": _suricata_ts(T0 + 60 + i), "event_type": "krb5",
            "src_ip": ws, "krb5": {"cname": cname, "realm": realm},
        })

    # ARP — SURICATA-ONLY identity evidence, the mirror image of `ntlm.log` (Zeek ships no
    # `arp.log` at all, so there is nothing to render on that side and no equality to claim).
    # `_arp` (`ingest/suricata/plugin.py`) is the highest-value parser this plugin gained and
    # was, until 2026-08-25, exercised ONLY by the unit suite: tier 2 never saw a single ARP
    # record, so a regression in it — including it going back to `_SKIP` on the false premise
    # it started from — could not fail any gate.
    #
    # Rendered as BOTH halves of the parser's contract, from `_SyntheticCast`'s own two
    # lists: records that must bind (a declared opcode, a real sender address and hardware
    # address) and records that must NOT (an RFC 5227 probe, an opcode nothing has ruled on).
    # `dest_*` is filled the way a real exchange would carry it — the placeholder target mac
    # on a request is precisely the field `_arp`'s sender-only rule exists to refuse.
    for i, (ip, mac, opcode) in enumerate(cast.arp_announcements):
        rows.append({
            "timestamp": _suricata_ts(T0 + 300 + i * 2), "event_type": "arp",
            "arp": {"opcode": opcode, "src_ip": ip, "src_mac": mac,
                    "dest_ip": dc,
                    "dest_mac": ("00:00:00:00:00:00" if opcode == "request"
                                 else "aa:bb:cc:00:ff:01"),
                    "hw_type": "ethernet", "proto_type": "ipv4"},
        })
    for i, (ip, mac, opcode, _why) in enumerate(cast.arp_nonbinding):
        rows.append({
            "timestamp": _suricata_ts(T0 + 320 + i * 2), "event_type": "arp",
            "arp": {"opcode": opcode, "src_ip": ip, "src_mac": mac,
                    "dest_ip": dc, "dest_mac": "00:00:00:00:00:00",
                    "hw_type": "ethernet", "proto_type": "ipv4"},
        })

    # TLS — the same 12 SNI/ja3 facts `ssl.log` carries.
    for i in range(12):
        rows.append({
            "timestamp": _suricata_ts(T0 + 120 + i * 7), "event_type": "tls",
            "src_ip": workstations[0], "src_port": 51000 + i,
            "dest_ip": fileserver, "dest_port": 443,
            "tls": {"sni": f"files{i}.corp.axr.lab", "version": "TLSv13",
                    "ja3": {"hash": f"{i:032x}"}},
        })

    # Alert — the direct analogue of `notice.log`'s port-scan finding (only `tainted`,
    # a bool, is asserted downstream; category/signature text is not compared).
    rows.append({
        "timestamp": _suricata_ts(T0 + 1900), "event_type": "alert",
        "src_ip": scanner, "dest_ip": dc,
        "alert": {"category": "Scan::Port_Scan", "signature": "scanned 60 hosts"},
    })

    # Stats — the SAME `pkts_dropped` sequence (40, then 12) `stats.log` carries. These
    # rows are read by `_stats` and reduce to NOTHING on this sensor, on purpose (code
    # review `S5`; this function's own docstring): cumulative-vs-delta is unprovable from
    # one record, so no `SensorHealthEvent` is minted and `sensor_pkts_dropped_max` stays
    # `None`. An earlier version of this comment claimed the opposite — that this was "the
    # one sensor-health figure the gate can assert equal across sensors" — which stopped
    # being true when that decline landed. Kept in the fixture so the reopen (one
    # `stats.uptime` measurement) has its input already here.
    rows.append({"timestamp": _suricata_ts(T0 + 3610), "event_type": "stats",
                "stats": {"capture": {"kernel_drops": 40}}})
    rows.append({"timestamp": _suricata_ts(T0 + 3620), "event_type": "stats",
                "stats": {"capture": {"kernel_drops": 12}}})

    # http / ssh / quic / ldap — the same four DC-side service facts the Zeek fixture's
    # own dedicated stems carry, so `SERVICE_PROJECTIONS["http"|"ssh"|"quic"|"ldap_op"]`
    # has the identical projected fact on both sensors.
    rows.append({
        "timestamp": _suricata_ts(T0 + 2000), "event_type": "http",
        "src_ip": workstations[0], "src_port": 52000, "dest_ip": dc, "dest_port": 80,
        "http": {"hostname": "intranet.corp.axr.lab", "url": "/", "http_method": "GET",
                "protocol": "HTTP/1.1", "http_user_agent": "Mozilla/5.0"},
    })
    rows.append({
        "timestamp": _suricata_ts(T0 + 2001), "event_type": "ssh",
        "src_ip": workstations[0], "src_port": 52001, "dest_ip": dc, "dest_port": 22,
        "ssh": {"server": {"software_version": "SSH-2.0-OpenSSH_9.0", "proto_version": "2.0"}},
    })
    rows.append({
        "timestamp": _suricata_ts(T0 + 2002), "event_type": "quic",
        "src_ip": workstations[0], "src_port": 52002, "dest_ip": dc, "dest_port": 443,
        "quic": {"sni": "intranet.corp.axr.lab", "version": "1"},
    })
    rows.append({
        "timestamp": _suricata_ts(T0 + 2007), "event_type": "ldap",
        "src_ip": workstations[4], "src_port": 52007, "dest_ip": dc, "dest_port": 389,
        "ldap": {"request": {"operation": "bindRequest", "message_id": 1}},
    })

    # snmp — the same collector -> device fact `snmp.log` carries.
    rows.append({
        "timestamp": _suricata_ts(T0 + 2003), "event_type": "snmp",
        "src_ip": collector, "src_port": 52003, "dest_ip": device, "dest_port": 161,
        "snmp": {"version": "2c", "community": "public"},
    })

    # smtp / sip — parsed on both sensors but PROJECTED on neither (`R1`,
    # `_SERVICE_NOT_PROJECTED`), so these exercise parser coverage only; no assertion in
    # the equivalence gate depends on their exact nested shape.
    rows.append({
        "timestamp": _suricata_ts(T0 + 2005), "event_type": "smtp",
        "src_ip": workstations[2], "src_port": 52005, "dest_ip": fileserver, "dest_port": 25,
        "smtp": {"helo": "mail.corp.axr.lab"},
    })
    rows.append({
        "timestamp": _suricata_ts(T0 + 2006), "event_type": "sip",
        "src_ip": workstations[3], "src_port": 52006, "dest_ip": fileserver, "dest_port": 5060,
        "sip": {"method": "INVITE", "uri": "sip:1000@corp.axr.lab"},
    })

    # A declared-`_SKIP` event_type and a genuinely unrecognized one — the Suricata-side
    # analogue of `reporter.log`/`__not_a_zeek_stem__.log`, exercising
    # `unparsed_log_types`/`unrecognized_log_types` on this transport too (module
    # docstring's "the census contract is weaker than Zeek's": both stay `None` until
    # every discovered file is exhausted, unlike Zeek's pre-first-yield guarantee).
    rows.append({"timestamp": _suricata_ts(T0 + 2013), "event_type": "drop"})
    rows.append({"timestamp": _suricata_ts(T0 + 2014),
                "event_type": "__not_a_suricata_event_type__"})

    rows.sort(key=lambda r: r["timestamp"])
    (dest / "eve.json").write_text(
        "\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
    return dest


def canonical_windows(source: str, window_seconds: int, subbuckets: int,
                      plugin_name: str = "zeek") -> str:
    """Run ingest -> windowing and return a canonical JSON rendering of the outputs."""
    from aximprint.ingest import get_plugin
    from aximprint.learners import extract_assertions
    from aximprint.windowing import WindowManager

    # `plugin` kept as its own local (not inlined into the `wm.process(...)` call
    # below, the way this used to read) so both providers have something to close
    # over — the SAME shape `cli._pipeline` uses, and for the same reason
    # (`windowing.windows.WindowManager.__init__`'s own docstring, which points at
    # `ingest/zeek/plugin.py::events` as the one canonical explanation of why a
    # callable read fresh per `_close()` is correct, `docs/WORK_ORDER_zeek_stem_
    # coverage.md` D5, additive second field per code review `S4`). Without this,
    # `health["unparsed_log_types"]`/`["unrecognized_log_types"]` would flow into
    # the payload below either way (`"health": w.health` is already wholesale,
    # `LES-098`'s fix does not need a second key added there) but would trivially
    # read `None` (no provider ever wired in) on every window of every pass,
    # instead of the real, populated lists this fixture's `reporter`/
    # `_UNKNOWN_STEM`-shaped log files now produce (`build_synthetic_capture`'s
    # own comment on why they are there) — a real, checked distinction
    # (`LES-006`'s "no evidence" vs "evidence of none"), not the same value
    # reached two different ways. `plugin_name` widened to cover `"suricata"`
    # (`docs/WORK_ORDER_axrange_integration.md` Track B) — the SAME provider wiring
    # applies unchanged: `ingest/suricata/plugin.py`'s own module docstring discloses
    # its weaker, post-exhaustion-only timing for these two fields, which is exactly
    # why this callable-per-`_close()` shape (not a value read once) is what surfaces
    # that difference honestly instead of masking it.
    plugin = get_plugin(plugin_name)
    wm = WindowManager(window_seconds=window_seconds, subbuckets=subbuckets,
                       unparsed_log_types_provider=lambda: plugin.unparsed_stems,
                       unrecognized_log_types_provider=lambda: plugin.unrecognized_stems)
    wins = list(wm.process(plugin.events(source)))
    wins += list(wm.finalize())
    # DERIVED from `WindowResult`'s own field list, not a hand-written key literal — which is
    # `LES-098`'s own prescribed fix, recorded when `capture_profile` (`Q29`) grew the dataclass
    # and this function was not touched, so `belief_store.py` could claim the new channel was
    # byte-deterministic while nothing checked it. `Q1` then added a FIFTH channel
    # (`entity_merges`) and the hand-written list would have silently narrowed the gate for the
    # second time in two rounds. Deriving it means a new channel is compared by construction: the
    # only way to omit one now is to delete it from the dataclass.
    #
    # `assertions` is the one key that is NOT a `WindowResult` field — it is computed FROM the
    # window by a different layer (`learners`), and its determinism is a separate claim from the
    # window's own, so it is added explicitly rather than folded in.
    payload = [{**{f.name: getattr(w, f.name) for f in dataclasses.fields(w)},
                "assertions": extract_assertions(w)} for w in wins]
    return json.dumps(payload, sort_keys=True, default=str, indent=1)


def run_once(source: str, workdir: Path, tag: str, window_seconds: int,
             subbuckets: int,
             plugin_name: str = "zeek") -> tuple[str, str, dict[str, str], str]:
    """One full pipeline pass — store + synth + cast + feature sink off a single ingest.

    Returns (canonical window JSON, synthesized YAML, {feature file: text}, cast sidecar JSON).

    **The fourth channel was added 2026-09-04** (`docs/WORK_ORDER_reference_ingest.md` S0/S1),
    and its absence is `LES-098`'s failure one layer up rather than inside a rendering function:
    check [2] compiles through `synth/synthesizer.py`, the **v0 draft path**, so the artifact
    every cast actually ships — `synth/compiler.py`'s twin config and its provenance sidecar —
    had never been covered by a determinism gate at all. The sidecar is the half worth the
    round trip: it is where a `dict` iteration order, an unsorted `set` or a float formatting
    difference would land, and it is read by another repo.

    Drives the store + jsonl sink through `cli._pipeline` — the SAME driver
    `aximprint ingest`/`aximprint features` run — rather than a hand-rolled
    ingest/window/write loop (`S6`). The hand-rolled version is exactly what
    drifted once already (only `cmd_features` used to stamp a run into its
    output; `cmd_ingest` stamped nothing at all) before the two verbs were
    unified onto one driver — so a determinism check built on a SECOND,
    independent loop would silently stop exercising the code path it exists to
    guard the moment the two next diverge.

    `plugin_name` widened (`docs/WORK_ORDER_axrange_integration.md` Track B) from a
    hardcoded `"zeek"` so this SAME function, unchanged otherwise, is what proves the
    Suricata plugin's own window close and synth compiler are just as idempotent — the
    determinism gate is a property of `cli._pipeline`'s driver, not of either plugin, so
    there is no reason for a second copy of this function to exist per plugin.
    """
    import aximprint.features  # noqa: F401  (registers "jsonl" into the sinks registry)
    from aximprint.cli import _pipeline
    from aximprint.ir.materialize import materialize
    from aximprint.store import BeliefStore
    from aximprint.synth.compiler import compile_twin
    from aximprint.synth.synthesizer import synthesize, to_yaml

    windows_json = canonical_windows(source, window_seconds, subbuckets, plugin_name)

    store_path = workdir / f"{tag}.duckdb"
    if store_path.exists():
        store_path.unlink()
    features_dir = workdir / f"{tag}.features"
    specs = [("store", str(store_path)), ("jsonl", str(features_dir))]
    _pipeline(plugin_name, source, ts_from=None, ts_to=None,
             window_seconds=window_seconds, subbuckets=subbuckets, specs=specs)

    store = BeliefStore(str(store_path))
    yaml_text = to_yaml(synthesize(store.snapshot()))
    # The CAST path, end to end: belief store -> PIT -> twin config + sidecar. `--relocate` is
    # passed deliberately rather than for coverage's sake — it is the one live producer of a
    # `transformation` record, so without it the gate would not compare the shape that carries
    # the pre-transformation value.
    pit = materialize(store)
    pit.pop("_stats", None)
    # AND WITH AN ASSERTIONS FILE (D16, `docs/WORK_ORDER_reference_ingest.md` S2). The file is
    # synthesized here from the PIT's own first placed entity rather than checked in, for the
    # reason every fixture in this script is: the gate has to run against whatever capture it was
    # pointed at, and a checked-in file keyed on one capture's addresses would silently resolve to
    # nothing on another and test an empty code path (`LES-103`). What it pins is the mechanism —
    # an override, an add and a suppress all resolving — not one network's content.
    assertions = _assertions_for(pit)
    # Serialized EXACTLY as `cli.py::cmd_cast` writes it — `indent=2`, no `sort_keys`, trailing
    # newline. The first version passed `sort_keys=True`, which normalizes away dict key order,
    # and dict key order is precisely what the shipped file preserves: the gate would have been
    # green on bytes nobody ships. That is `LES-098`'s trap one notch further in — naming the
    # right producer is not enough if you then compare a different rendering of its output.
    sidecar = json.dumps(compile_twin(pit, relocate="10.200.0.0/16")["provenance"],
                         indent=2) + "\n"
    asserted = json.dumps(
        compile_twin(pit, relocate="10.200.0.0/16", assertions=assertions)["provenance"],
        indent=2) + "\n"
    store.close()
    features = {p.name: p.read_text() for p in sorted(features_dir.iterdir())}
    return windows_json, yaml_text, features, sidecar + asserted


def _assertions_for(pit: dict):
    """A synthetic `axr-assertions/1` set derived from `pit`'s own placed entities.

    Derived rather than checked in, on `LES-103`'s rule: this script runs against whatever capture
    it was pointed at (`--source`, or its own seeded synthetic one), and a file keyed on one
    network's addresses would resolve to nothing on another — leaving the gate green while
    comparing two runs of a code path that did nothing. Every record kind that CAN be exercised
    without knowing the network is exercised: an override of the first placed host's role, a
    segment add carrying a zone type, and a suppression of the last placed host.

    Returns `None` when the PIT places fewer than two hosts, in which case the asserted cast is
    the plain one and `check_cast_sidecar` says so rather than pretending.
    """
    from aximprint.operator_assertions import ASSERTIONS_SCHEMA, parse_assertions

    placed = sorted((e for e in pit["ir_entity"]
                     if e["locality"] == "local" and e["segment"]),
                    key=lambda e: e["entity_key"])
    if len(placed) < 2:
        return None
    first, last = placed[0], placed[-1]
    return parse_assertions({
        # Imported, never re-typed: a gate that re-declares the constant it is checking passes
        # when both copies drift together (`LES-105`), and `tests/test_ir_schema.py`'s
        # declared-once guard enforces exactly that for this string.
        "schema": ASSERTIONS_SCHEMA,
        "imprint": "determinism-fixture",
        "as_of": "2026-09-04T18:00:00Z",
        "source": "operator:verify-determinism",
        "add": [{"kind": "segment", "key": first["segment"], "zone_type": "vlan",
                 "note": "fixture: a zone type no capture can observe"}],
        "override": [{"key": first["entity_key"], "field": "role",
                      "value": "domain-controller", "note": "fixture: a role override"}],
        "suppress": [{"key": last["entity_key"], "note": "fixture: out of scope"}],
    }, path="<verify_determinism fixture>")


def check_cast_sidecar(p1: str, p2: str, workdir: Path) -> int:
    """[3/4] — the cast provenance sidecar is byte-stable, and says what it is.

    Two claims, and the second is why this is more than a third `==`:

      * **Byte-stable.** The sidecar is a dict of dicts built by appending across several
        loops; a `set` reaching an output or an unordered DuckDB result consumed as-is lands
        here first (`LES-002`, `LES-009`).
      * **It still declares its own schema, and no row has reverted to `defaulted`.** The
        2026-09-04 rename is exactly the kind of change a determinism gate is blind to — two
        passes of a WRONG build agree with each other perfectly — so the invariant the artifact
        is supposed to hold is asserted here as well, which is `LES-105`'s rule about carving
        checks into an equality gate applied in the other direction.
    """
    print(f"\n[3/4] byte-stable cast sidecar ({len(p1)} bytes, "
          "`docs/WORK_ORDER_reference_ingest.md` S0/S1)")
    if p1 != p2:
        print("  ✗ THE CAST SIDECAR IS NOT DETERMINISTIC — same PIT, different provenance.")
        print(_first_diff(p1, p2, "run1", "run2"))
        (workdir / "run1.provenance.json").write_text(p1)
        (workdir / "run2.provenance.json").write_text(p2)
        print(f"    dumped: {workdir}/run{{1,2}}.provenance.json")
        return 1

    # Two documents concatenated — the plain cast and the asserted one — so the checks below run
    # over both. `raw_decode` rather than a split on the separator: the separator is a newline and
    # so is a great deal of the content.
    docs, decoder, offset = [], json.JSONDecoder(), 0
    while offset < len(p1):
        doc, end = decoder.raw_decode(p1, offset)
        docs.append(doc)
        offset = end + 1
    doc = docs[0]
    fields = {k: v for d in docs for k, v in (d.get("fields") or {}).items()}
    problems = []
    for d in docs:
        if d.get("schema") != PROVENANCE_SCHEMA:
            problems.append(f"schema={d.get('schema')!r}, expected {PROVENANCE_SCHEMA!r}")
    # Scoped to the CLASS VALUES, not a substring of the whole document: a future `reason=`
    # or `note=` string that legitimately mentions the old name by way of explaining the rename
    # must not fire this. What is being checked is that no ROW still carries it.
    stale = sorted(k for k, v in fields.items() if v.get("evidence_class") == "defaulted")
    if stale:
        problems.append(f"{len(stale)} row(s) still say 'defaulted' — the rename to "
                        f"'compiler' regressed, e.g. {stale[:3]}")
    missing = sorted(k for k, v in fields.items() if not v.get("signal"))
    if missing:
        problems.append(f"{len(missing)} field(s) carry no `signal`, e.g. {missing[:3]}")
    nulls = sorted(k for k, v in fields.items()
                   if "confidence" in v and v["confidence"] is None)
    if nulls:
        problems.append(f"{len(nulls)} field(s) carry a NULL confidence, e.g. {nulls[:3]}")
    # `.get` throughout: a malformed `population` block must produce a NAMED failure here, not
    # a `KeyError` traceback from a gate whose whole job is to report legibly.
    # And the ASSERTED cast's own claims: an assertions file that resolved to nothing would leave
    # this gate green while testing an empty path, which is `LES-103`'s failure exactly.
    asserted_rows = [k for k, v in fields.items() if v.get("evidence_class") == "asserted"]
    if len(docs) > 1 and not asserted_rows:
        problems.append(
            "the asserted cast carries NO `asserted` field: the synthesized assertions file "
            "resolved to nothing, so this gate compared two runs of an empty code path")
    for d in docs[1:]:
        block = (d.get("population") or {}).get("assertions") or {}
        if not block.get("counts"):
            problems.append("the asserted cast carries no `population.assertions` block")

    pop = doc.get("population") or {}
    groups = pop.get("node_groups")
    if pop and not isinstance(groups, list):
        problems.append("population block carries no `node_groups` list")
    elif pop:
        # `placed_hosts == sum(observed) + suppressed(placed)` since S2 (D18): a suppressed host
        # leaves `sum(observed)` and reappears in `population.suppressed`, so the identity is
        # still an equality and still reconstructs the original population from the sidecar
        # alone — which is the property, not the particular arithmetic.
        suppressed = len([r for r in (pop.get("suppressed") or []) if r.get("placed")])
        if pop.get("placed_hosts") != sum(g.get("observed", 0) for g in groups) + suppressed:
            problems.append(
                f"population identity broken: placed_hosts ({pop.get('placed_hosts')}) != "
                f"sum(observed) ({sum(g.get('observed', 0) for g in groups)}) + "
                f"suppressed ({suppressed})")
    if problems:
        for problem in problems:
            print(f"  ✗ {problem}")
        return 1

    n_transformed = sum(1 for v in fields.values() if "transformation" in v)
    print(f"  ✓ identical  sha256={hashlib.sha256(p1.encode()).hexdigest()[:16]}   "
          f"({len(fields)} field(s) over {len(docs)} cast(s), {n_transformed} declared "
          f"transformation(s), {len(asserted_rows)} asserted, schema={doc.get('schema')})")
    return 0


def _first_diff(a: str, b: str, label_a: str, label_b: str) -> str:
    la, lb = a.splitlines(), b.splitlines()
    for i in range(max(len(la), len(lb))):
        x = la[i] if i < len(la) else "<missing>"
        y = lb[i] if i < len(lb) else "<missing>"
        if x != y:
            return (f"    first difference at line {i + 1}:\n"
                    f"      {label_a}: {x[:200]}\n"
                    f"      {label_b}: {y[:200]}")
    return "    (no line differs — length or trailing-whitespace mismatch)"


def check_features(f1: dict[str, str], f2: dict[str, str], workdir: Path) -> int:
    """Invariant 3: the feature archive is byte-stable except for wall-clock.

    The four JSONL artifacts must match byte for byte. `manifest.json` is allowed
    to differ in exactly one key — the sink's wall-clock group — and the check is
    that the *set* of differing keys is that key and nothing else, so a new
    non-reproducible field cannot slip in behind the exemption. Returns the
    number of failures.
    """
    from aximprint.features.jsonl import MANIFEST, WALLCLOCK_FIELD

    rows = sum(len(text.splitlines()) for name, text in f1.items() if name != MANIFEST)
    print(f"\n[4/4] byte-stable feature archive ({len(f1)} file(s), {rows} row(s), "
          "WORK_ORDER_feature_vector_sink)")
    failures = 0
    for name in sorted(f1):
        if name == MANIFEST or f1[name] == f2[name]:
            continue
        failures = 1
        print(f"  ✗ FEATURE FILE NOT BYTE-STABLE: {name}")
        print(_first_diff(f1[name], f2[name], "run1", "run2"))

    m1, m2 = json.loads(f1[MANIFEST]), json.loads(f2[MANIFEST])
    differing = sorted(k for k in set(m1) | set(m2) if m1.get(k) != m2.get(k))
    if differing not in ([], [WALLCLOCK_FIELD]):
        failures = 1
        print(f"  ✗ MANIFEST DIFFERS OUTSIDE {WALLCLOCK_FIELD}: {differing}")
    if failures:
        for tag, files in (("run1", f1), ("run2", f2)):
            (workdir / f"{tag}.manifest.json").write_text(files[MANIFEST])
        print(f"    dumped: {workdir}/run{{1,2}}.manifest.json")
        return failures
    stable = "".join(f1[n] for n in sorted(f1) if n != MANIFEST)
    print(f"  ✓ identical  sha256={hashlib.sha256(stable.encode()).hexdigest()[:16]}"
          f"   (manifest differs only in: {differing or 'nothing'})")
    return 0


#: plugin name -> its own seeded synthetic-capture builder. One dict, not an
#: if/elif chain, so a THIRD plugin (`docs/WORK_ORDER_axrange_integration.md`'s own
#: closing note: "Track B is independent... it belongs in aximprint's own `/dev`" reads
#: as a standing invitation for a next one) adds one line here rather than a new branch
#: at every call site below.
_FIXTURE_BUILDERS = {
    "zeek": build_synthetic_capture,
    "suricata": build_synthetic_suricata_capture,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plugin", default="zeek", choices=sorted(_FIXTURE_BUILDERS),
                    help="ingest plugin to run the three invariants against "
                         "(default: zeek). Only affects the DEFAULT synthetic fixture "
                         "below — a caller-supplied --source is read by whichever "
                         "plugin --plugin names regardless of its own shape.")
    ap.add_argument("--source", default="", help="log dir (default: seeded synthetic, "
                    "shaped for --plugin)")
    ap.add_argument("--window", type=int, default=3600, help="window seconds (default 3600)")
    ap.add_argument("--subbuckets", type=int, default=12)
    ap.add_argument("--keep", action="store_true", help="keep the temp workdir")
    ap.add_argument("--emit-fixture", metavar="DIR", default="",
                    help="write the seeded synthetic capture (--plugin-shaped) to DIR "
                         "and exit (used by scripts/verify_pipeline_gates.sh)")
    args = ap.parse_args()
    build_fixture = _FIXTURE_BUILDERS[args.plugin]

    if args.emit_fixture:
        dest = build_fixture(Path(args.emit_fixture))
        print(dest)
        return 0

    workdir = Path(tempfile.mkdtemp(prefix="aximprint-determinism-"))
    failures = 0
    try:
        if args.source:
            source = args.source
            if not Path(source).exists():
                print(f"✗ source not found: {source}")
                return 1
            print(f"── source: {source} (caller-supplied, --plugin {args.plugin})")
        else:
            source = str(build_fixture(workdir / args.plugin))
            print(f"── source: seeded synthetic {args.plugin} capture (seed={SEED})")

        print("── pass 1/2")
        w1, y1, f1, p1 = run_once(source, workdir, "run1", args.window, args.subbuckets,
                                  args.plugin)
        print("── pass 2/2")
        w2, y2, f2, p2 = run_once(source, workdir, "run2", args.window, args.subbuckets,
                                  args.plugin)

        n_windows = len(json.loads(w1))
        print(f"\n[1/4] idempotent window close ({n_windows} window(s), DESIGN_BRIEF §5.4)")
        if w1 == w2:
            print(f"  ✓ identical  sha256={hashlib.sha256(w1.encode()).hexdigest()[:16]}")
        else:
            failures += 1
            print("  ✗ WINDOW CLOSE IS NOT IDEMPOTENT — same events, same build, "
                  "different features.")
            print(_first_diff(w1, w2, "run1", "run2"))
            (workdir / "run1.windows.json").write_text(w1)
            (workdir / "run2.windows.json").write_text(w2)
            print(f"    dumped: {workdir}/run{{1,2}}.windows.json")

        print(f"\n[2/4] deterministic compiler ({len(y1.splitlines())} YAML lines, "
              "DESIGN_BRIEF §9)")
        if y1 == y2:
            print(f"  ✓ identical  sha256={hashlib.sha256(y1.encode()).hexdigest()[:16]}")
        else:
            failures += 1
            print("  ✗ SYNTH IS NOT DETERMINISTIC — same snapshot, different YAML.")
            print(_first_diff(y1, y2, "run1", "run2"))
            (workdir / "run1.twin.yaml").write_text(y1)
            (workdir / "run2.twin.yaml").write_text(y2)
            print(f"    dumped: {workdir}/run{{1,2}}.twin.yaml")

        failures += check_cast_sidecar(p1, p2, workdir)
        failures += check_features(f1, f2, workdir)

        if failures:
            print(f"\n✗ {failures} determinism invariant(s) BROKEN")
            args.keep = True
            return 1
        print("\n✓ all determinism invariants hold")
        return 0
    finally:
        if args.keep:
            print(f"  (workdir kept: {workdir})")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
