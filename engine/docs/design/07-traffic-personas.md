*The persona library — engine vocabulary for benign, twin-agnostic behavior
— and the Traffic tier (`tfc`) that binds it onto a deployed twin.*

## Two separate concerns

Getting a deployed twin to feel like a live, populated network — rather than
an idle collection of freshly-booted VMs — splits into two deliberately
separate concerns:

- **What a role does** — a workstation checks its inbox, browses the web,
  and talks to a file server; a domain controller answers Kerberos and LDAP;
  a historian polls PLCs over Modbus. This is a **persona**: a reusable,
  twin-agnostic behavioral archetype.
- **Which host plays which role, on this particular twin** — this is a
  **traffic profile**, bound onto an already-deployed twin by the **Traffic
  tier**.

A persona never mentions a specific twin, host, or IP. A traffic profile
never describes new behavior — it only assigns catalog personas onto the
twin's published host roles.

## The persona library

`axrange.personas` is an engine-vocabulary registry — modeled directly on
the sensor-type registry (`axrange.sensors`, see
[06-taps-sensors.md](./06-taps-sensors.md)): a scenario or traffic profile
author references a persona by id the same way they reference a sensor
type, without needing to know how it is implemented.

### What a persona declares

A persona is a small, declarative document: an id, an `os_family`
(`windows`/`linux`), a `drive` kind, and a list of **emits** — the traffic
signatures it produces. Each emit names a protocol, optional ports, a peer
role it talks to, a cadence (`diurnal` / `24x7` / a cyclic or burst window),
and a volume level (`low`/`med`/`high`/`bulk`). A persona can also `require`
one or more peer roles to be present in the twin at all (a domain-joined
workstation cannot function without a DC) or mark an individual peer as
`optional` — a peer it talks to only if one happens to exist (a dedicated
DNS resolver, a print server), rather than a hard dependency.

**What a volume level means.** A level resolves three knobs together: how
many messages fire per cadence tick, **how many bytes each carries**, and the
gap between them. The ladder spans about four orders of magnitude of offered
load, from a level meant for keepalive-grade chatter up to `bulk`, the
elephant-flow tier (order of tens of MiB per tick — a container-layer pull, a
nightly replication run). An emit that needs a size unlike its level's
default — many small messages, or few large ones — pins `bytes:` explicitly
and keeps the level's count and pacing. Cadence controls only *when*: both
`cyclic:<duration>` and `burst:<duration>` fire every `<duration>`, and
differ in *shape* — cyclic paces its messages at the level's gap, burst
sends them back-to-back.

**Transport, and talking to the segment.** An emit's `transport` is `tcp` or
`udp`, and is **derived from the protocol** unless pinned — so DNS, syslog,
RTP, and the link-local discovery protocols behave like themselves rather
than being forced through a TCP connect. This matters for more than realism:
a TCP connect spends ~9 packets to deliver one small payload and delivers
*no* bytes at all if nothing is listening on the peer port, whereas UDP is
fire-and-forget, so datagram emits are both the realistic and the reliable
way to reach a high packet rate. Beyond the reserved peer tokens for traffic
leaving the twin (`egress`, `internet`), an emit may target the local segment
itself — `broadcast` or `multicast` — which resolves to a literal wire
address rather than to any twin host, making mDNS/LLMNR/NBNS/SSDP/DHCP
chatter expressible. An emit may also set `peer_scope: all` to fan out across
**every** twin host holding its peer role (never the instance's own host),
which is how a subnet-sweeping scanner or a peer-to-peer mesh is modeled;
such a persona's flow count grows with the size of the twin rather than
adding a single flow.

A starter catalog covers a broad cross-section of role archetypes an
enterprise, ICS, or DMZ twin needs: office workstations and a power-user
variant, a domain controller, web/DB/file/mail/backup servers, a VPN
concentrator, a bastion jump host, an egress proxy, a monitoring collector,
and ICS-flavored roles — a historian, an HMI, a PLC, and a protocol gateway.
A second wave covers the **load shapes** the first lacked, chosen because
each stresses a different part of the capture plane: constant-bitrate media
(`voip-softphone`), sustained streaming and bursty CI pulls that put real
bytes on the WAN uplink (`streaming-media-client`, `ci-build-farm`),
replication (`db-replica`), a high-packet-rate/low-bytes-per-packet log
shipper (`log-shipper`, the profile that overruns a capture ring long before
a byte-heavy one does), segment broadcast chatter (`broadcast-chatter`), an
*authorized* scanner whose traffic is shaped exactly like reconnaissance
(`scanner-benign`, the false-positive source that forces detection rules to
be tuned on something better than "saw a port sweep"), and a peer-to-peer
update mesh (`p2p-update-peer`). Each is just a catalog document; adding a
new one is adding a new file, not new code.

### Five drivers

A persona's `drive` field selects how its `emits[]` are actually realized as
traffic on a deployed host:

| Drive | What it does |
|---|---|
| **scripted** | Generates the declared traffic pattern in-guest (a small client script driven by the persona's emits, cadence, and volume). Correct peer/port/transport/timing/volume — the *shape* of the traffic, not the protocol's own bytes. |
| **replay** | Replays a staged traffic template (e.g. a captured pcap) against a resolved peer, faithfully reproducing byte-for-byte content rather than a synthetic pattern. |
| **real-service** | Rides an already-installed real daemon on the host (a real DC's Kerberos/LDAP responses, a real web server) rather than fabricating traffic — the persona just asserts the daemon is present and healthy. |
| **command** | Runs a **real protocol client tool** at each emit's firing instant (`dig`, `curl`, `ntpdate`, `ping`) instead of pushing filler bytes at a port, so a capture records the protocol named in `proto:` rather than bare `tcp`/`udp`. **Two per-OS tables, one drive kind:** Linux runs POSIX client tools; a `os_family: windows` persona runs the **built-in domain-member engine** (`klist` / `gpupdate` / `nltest` / `net use` + `Copy-Item` / `Get-WmiObject` / `w32tm` / `Resolve-DnsName` / `Invoke-WebRequest`) as a Scheduled Task, driving Kerberos / LDAP / SMB / RPC-on-dynamic-high-ports / DNS / NTP / HTTP(S) — `axrange/personas/drivers/command_windows.py`, `docs/WORK_ORDER_protocol_engines.md` §5 E6. A Windows member addresses **names, never IP literals** (a ticket is issued against an SPN), so containment there is the router's egress phase + twin-only DNS + the sink, not a render-time argv check. |
| **browser** | Drives a **real headless Chromium** at a real *name* at each emit's firing instant, so one engine produces DNS, a real TLS handshake (real SNI, real JA3/JA4), HTTP/1.1 or HTTP/2 by ALPN, and — for a `quic` emit — QUIC/HTTP-3. Linux only. |

All five drivers share one uniform interface — `start` / `stop` /
`heartbeat` — so anything that binds personas onto hosts (the Traffic tier)
can drive any of them without a driver-specific branch of its own.

**`command` vs. `scripted` — protocol faithfulness vs. offered load.** The
two are siblings, not a replacement: `scripted` is the right tool whenever
byte volume or packet rate is the point (it is the one that can express an
elephant flow or a high-pps log shipper, and the max-capture-rate
characterization depends on it), and `command` is the right tool whenever
protocol *semantics* are the point. A detection rule matches a DNS query
name or an RC4 TGS-REP, never a port number — so benign background traffic
that cannot trigger a protocol-aware rule also cannot produce a realistic
false-positive rate, which is half of what a detection-validation range is
for. A `command` emit is strictly slower per unit of offered load, and its
`volume:` means *invocation count*, not byte size (a `bytes:` a template
cannot spend is announced once in the guest log rather than silently
dropped). The protos it can speak are a small, driver-owned table
(`dns`/`http`/`https`/`ntp`/`icmp` today); an unmapped proto is a fail-loud
authoring error, never a silent fallback to the byte-pusher.

**Containment: the guest never resolves a name.** Every peer address a
`command` client uses is resolved to a concrete twin host IP *in the
orchestrator* and baked into the rendered client as a literal (`dig @<ip>`,
`curl http://<ip>:<port>/`) — a twin host therefore has no dependency on
any resolver being up, no twin naming leaks into guest DNS, and the
delivered client is fully inspectable. This is enforced structurally at
render, not by convention: each template declares which of its argv tokens
are *addresses*, and a template whose address is a name with no explicit
`--resolve`/`--connect-to` binding to a baked IP refuses to render.

**`browser` — the web stack in one engine.** A single headless-Chromium page
load produces DNS (when it resolves), a real TLS handshake carrying a real
SNI and a real JA3/JA4 fingerprint, and HTTP/1.1 or HTTP/2 chosen by ALPN —
the flow family that dominates a measured corporate capture. A `quic` emit
additionally forces the origin to HTTP/3, because Chromium otherwise only
speaks QUIC to an origin it has seen advertise `Alt-Svc`, and a QUIC-labelled
flow that silently ran over TCP is a wrong answer nobody is told about. The
protos it drives are `http`, `https` and `quic`; anything else is a
fail-loud authoring error, never a silent skip. `volume:` means **pages per
firing** — how many *bytes* a page load costs is the server's choice, not the
client's, so a byte budget is announced as ignored once at arm time rather
than pretended. Which names it visits comes from the free-form
`knobs.browse_names` (names, not URLs — the string becomes the SNI and the
Host header, so a URL, a port, a path or an IP literal there is refused at
render), seeded-shuffled per instance so two hosts do not lock-step. v1 runs
one Chromium invocation per page load with a hard timeout: affordable at
measured twin flow rates, and it buys a stateless, inspectable fetch at the
cost of never exercising connection reuse (HTTP/2 multiplexing, QUIC 0-RTT
across loads) — a persistent-browser variant is the upgrade if the fork rate
ever becomes the bottleneck.

**Browser containment — the guest still need not resolve.** A browser cannot
take `curl --resolve`, so the `command` driver's argv-literal rule is
reproduced with Chromium's own lever: by default the rendered client carries
`--host-resolver-rules=MAP * <orchestrator-resolved peer IP>`, which means
Chromium resolves **nothing** while the URL — and therefore the Host header
and the TLS SNI — still carries the real name. The opposite mode is opt-in
per persona (`knobs.resolve_via_dns: true`), for a twin whose own
authoritative DNS, any-IP sink and egress-denying router make containment a
*network* property; it is not the default, because in a twin without that
triangle a guest that resolves normally is a guest that can reach the real
internet. The two modes are checked structurally at render (exactly one URL,
its host the real name; exactly one MAP rule to an IP literal, or none at
all), so they can never blur into each other.

**Prerequisite tools.** `dig`/`curl`/`ntpdate` — and Chromium, for the
browser engine — are not in the base cloud image; the driver probes for every
binary its persona needs and fails loud, naming the binary and its package,
rather than starting a client that logs `command not found` on every fire.
(The browser probe is *any-of*: Debian bookworm's `chromium` package installs
`chromium`, Ubuntu ships the same browser as `chromium-browser`, and a guest
with neither is refused at launch.) The tool set belongs in the **image bake**
(see [10-images.md](./10-images.md)) — adding an `install:`
action to a *standing* twin's config is refused by the incremental-update
diff, so an image change costs nothing on the next twin and a redeploy on
this one.

**Every driver launches its long-running in-guest process as a detached,
deterministically-named transient unit** — a `systemd-run`-style transient
unit on Linux, a Scheduled Task on Windows — never as a bare backgrounded
child (`nohup ... &`) of the guest-agent exec session that launched it. A
backgrounded child is reaped the instant that exec session ends; a detached
unit is not. This is the same launch primitive the standing sensor capture
path uses, reused here rather than reimplemented, and it is the load-bearing
reason persona traffic keeps running after the op that started it returns.

### Persona validation (PSN)

A dedicated **PSN** rule category checks persona catalog entries and their
usage — a well-formed id, a valid `os_family` and `drive`, known protocols,
a well-formed `peer_role`/cadence/volume/transport/peer_scope on every emit,
and that every hard (non-optional, non-reserved) peer role a persona emits to
is also listed in its own `requires[]`. These checks are **catalog-side**: they run against
the persona registry directly, not as part of the Scenario IR's four-pass
validation pipeline, the same "listed for completeness, checked on its own
schedule" posture the cluster rules have. For the full rule list, see
`docs/VALIDATOR.md` §5.12.

## The Traffic tier (`tfc`)

The Traffic tier is a standalone playout — a peer to the Tap playout
described in [06-taps-sensors.md](./06-taps-sensors.md) and the Exercise
playout described in [08-exercise-measurement.md](./08-exercise-measurement.md).
Like both of those, it binds something onto an **already-deployed** twin
rather than emitting its own infrastructure from an IR walk.

```
twin's published inventory (hosts[], each with a role)
                    │
                    ▼
     traffic profile: role → persona assignments
                    │
                    ▼
      resolve peers, resolve host vmids/ips
                    │
                    ▼
   drive-persona op, per assignment, over the persona drivers
                    │
                    ▼
      running persona instances on the twin's hosts
```

### Traffic profiles

A traffic profile is a small YAML document assigning personas onto a twin's
**published host roles** (or, less commonly, a specific host by id). This is
why the Traffic tier depends on a real, persona-typed `role:` field on every
IR node — a twin publishes its host roster with each host's role in its
inventory (see [02-twin-model.md](./02-twin-model.md)'s `te inventory`
verb), and a traffic profile assigns against that published roster rather
than against the raw scenario topology directly. An assignment can also
carry an intensity knob and, for the replay driver, a template plus which
host role it replays onto.

Binding an assignment resolves every persona's declared peer roles against
the twin's role→host map (round-robin across hosts sharing one role when
there is more than one), fails loudly if a hard-required peer role has no
host in the twin at all, and skips only the optional peers that are
genuinely absent.

### Applying a profile

Applying a traffic profile composes a plan out of one **`drive-persona`**
op per resolved assignment and runs it through the same engine executor
every other deploy lifecycle uses — the same state-tracked, resumable
execution as a twin deploy, just built by hand against an already-deployed
twin's inventory instead of walked out of a Scenario IR.

Applying is **idempotent**: re-applying the same profile re-asserts every
assignment (the driver's `start()` is itself convergent — it stops and
re-launches its unit rather than erroring on an existing one), and a
narrowed or changed re-apply runs a **de-scope pass** first — any persona
instance bound by a prior apply that the new profile omits is stopped and
released, so swapping a persona on a host or shrinking a profile's scope
can never leave a stale, orphaned instance still generating traffic.

### Lifecycle

The Traffic tier joins the shared CLI/API verb grid described in
[02-twin-model.md](./02-twin-model.md):

```
axrange tfc {validate,plan,deploy,status,info,destroy}
```

`tfc` is its own standalone deploy — `axrange tfc destroy` tears down only
its own persona instances — but a twin's own destroy cascades into it: `te
destroy` sweeps a twin's `tfc` deploy alongside its `tap` and `exr` deploys,
so tearing down a twin never leaves orphaned persona traffic behind. See
[11-api-ui.md](./11-api-ui.md) for the CLI/API surface in full and
[09-content-overlays.md](./09-content-overlays.md) for where a node's `role:`
and the rest of a twin's content are authored.
