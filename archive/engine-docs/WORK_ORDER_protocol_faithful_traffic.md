# Work Order — Protocol-faithful traffic: a `drive: command` persona driver

> **STATUS: BUILT (axRange half) — 2026-09-02, Authority: user (accepted as proposed).** Code,
> tests and docs are done (commits `a48d4e5d` build + `a0f6bb67` review-round fix, merged
> `4ebdf49c`; CHANGELOG 2026-09-02). §5's three constraints held: the driver is Linux-only (§5.3,
> as scoped), no shipped persona uses `drive: command` yet (the tool set is meant to be baked into
> an image, not installed on a standing twin, §5.2 — see `docs/PRODUCT_GAPS.md` GAP-056), and the
> **gated `dev45` live-verify is still PENDING**, blocked on §5.1 (the aximprint companion — the
> internet sink becoming real services) and deliberately never run against the standing
> `corpapcn-twin` (§5.2). §3.2's fork was decided **option (a)** (a driver-owned per-proto argv
> template table, `axrange/personas/drivers/command.py`), as recommended. Everything below this
> line is the original proposal and reflects the DESIGN as accepted, not necessarily every
> implementation detail verbatim — see the CHANGELOG entry and the module docstring in
> `command.py` for the as-built specifics (containment via `_check_containment`, an
> `explicit_transports` honour/refuse path added in the review round, tool-probe output tagged
> `MISSING:`). Route: Pipeline (`/dev`) if accepted — code-writer → tester → code-reviewer →
> doc-writer → **gated `dev45` live-verify**
> (mandatory, and gated additionally on the aximprint companion of §5.1 — the axRange half alone
> cannot be proven on a twin whose sink cannot answer a real client).
> **Version-locked:** **one touch, additive and non-breaking, Authority: user required.**
> `docs/VALIDATOR.md`'s **`PSN-030`** enum widens from `scripted|replay|real-service` to include
> `command` — the rule **count is unchanged at 114** (`axrange/validator/catalog.py:692`'s
> `assert sum(_BY_CAT.values()) == 114` does not move, and neither does `.claude-doc-stats.json`'s
> `N catalog rules` claim), but widening a closed enum inside a version-locked spec follows the
> precedent set by `PSN-100`/`PSN-110` (CHANGELOG 2026-07-28, "a version-locked contract touch,
> additive and non-breaking"). **No `IR_SCHEMA` change** (personas are engine-registry content, not
> Scenario IR). **No `OP_CATALOG` change** — op count stays **40**; `OP-TFC-010 drive-persona`
> already persists `drive` as an opaque string and dispatches through `get_driver`
> (`axrange/o2control/traffic.py:1261`, `:1522`). **No `PLANNER`/`ADAPTER`/`IMAGE_MANIFEST` change,
> no new op type, no state-store schema bump.**
> **Spec:** this file + `docs/design/07-traffic-personas.md` §"drive kinds".
> **Companion (aximprint repo, NOT this one):** the sink must become real services — see §5.1.
> **Related gap:** aximprint `docs/JOURNAL_corpapcn_fidelity.md` **GAP-C20** ("persona emits are
> port-shaped, not protocol-faithful"), and the iteration-18 correction to GAP-C20's own stated
> scope that §7 turns on.

## 1. The problem

`axrange/personas/drivers/scripted.py` emits **connectivity shape**, and says so in its own module
docstring ("v1 fidelity note (deferred, not silently omitted)"): for each `emits[]` entry it opens a
socket to a resolved peer:port at a seeded cadence and pushes a tagged filler payload
(`_CLIENT_TEMPLATE`'s `FILLER = b'axrange-persona-payload '`, sized by
`_VOLUME_PROFILE[volume]["bytes"]`). Peer, port, transport, timing and byte volume are all correct.
The bytes on the wire are not the protocol named in `proto:`.

**This is now measurable on standing content, not a theoretical objection.** In
`twin-configs-cast/corpapcn-twin/corpapcn-twin.yaml`, a `dns-resolver` node installs **real bind9**
(`install: [{pkg: bind9}, {script: scripts/responder-dns-resolver.sh}]`, four such nodes — lines
151, 254, 271, 337). The generic responder that ships beside it
(`scripts/responder-dns-resolver.sh`, binding `53/udp 123/udp 389/tcp`) has an explicit `EADDRINUSE`
branch whose comment records the measurement: *"a dns-resolver node installs bind9 and `named` owns
:53 (measured on dev45 — the responder was competing with the very service that makes the role
real)."* So the server on :53 is genuinely `named`.

What reaches it is `cast-generic-endpoint-local`'s emit — `proto: dns`, `ports: [53]`,
`transport: udp`, `volume: low` — which `_VOLUME_PROFILE["low"]` resolves to **256 bytes** of
`axrange-persona <persona> dns ` + filler. bind9 discards it as malformed, never answers, and Zeek
records the exchange as bare **`udp`** where the source capture records **`dns`**.

**The servers are already real; the clients are not.** That asymmetry is the entire gap. Every other
half of the machinery — the persona document, cadence, volume, orchestrator-side peer resolution,
the transient-systemd launch, the QGA delivery path — is correct and reusable as-is.

## 2. What is already built and must be reused, not rebuilt

A `command` driver is a **new innermost step inside an existing pipeline**, not a new pipeline. Six
things stay exactly as they are:

**2.1 The persona document.** `emits[]` already carries everything a real client needs:
`proto`, `ports`, `peer_role`, `transport`, `cadence`, `volume`, `bytes`, `peer_scope`
(`axrange/personas/schema.py:120-161`). No new persona schema field is required for a first cut.

**2.2 Orchestrator-side peer resolution — the containment property, and the load-bearing one.**
`axrange/o2control/traffic.py`'s `_resolve_peers` (`:749`) and `_resolve_peer_sets` (`:857`) resolve
every `peer_role` to concrete twin host IPs **in the orchestrator**, and
`render_scripted_client` bakes them into the guest script as a literal
(`scripted.py`'s `EMITS = {emits!r}`, populated by `"peer_ips": resolve_emit_peers(...)`). **The
guest never resolves a name.** A twin host has no dependency on any resolver being up, no leakage of
twin naming into guest DNS, and a deterministic, inspectable client. **This must survive verbatim**,
and it constrains the command templates concretely (§3.2): `dig @<baked-ip> <qname>` is fine, because
`<qname>` is *payload* sent to a server at a baked address; `curl https://hostname/` is **not**,
because it makes the guest resolve. A hostname-bearing URL must go through `curl --resolve
<host>:<port>:<baked-ip>` or `--connect-to`, never through guest resolution.

**2.3 The launch primitive.** `config_exec/render.py:1762 systemd_daemon_start_block` —
`systemd-run --unit=<unit> --collect` with a fail-loud `systemctl is-active` check. **Never
`nohup … &`** (LES-086; a `nohup`'d child is reaped the instant the QGA exec session ends). Reused
verbatim by every driver via `drivers/base.py`.

**2.4 The instance identity + lifecycle helpers.** `drivers/base.py`'s `safe_instance_name` /
`instance_unit` / `instance_paths` / `stop_unit` / `heartbeat_unit` / `safe_label` /
`unsupported_os_family_error`. A `command` driver's `stop`/`heartbeat` should be the **same two
calls** the scripted driver's are.

**2.5 The delivery path.** `ga.write_file(...)` (base64-in-command, LES-002) then
`ga.exec(vmid, ["bash", launch_path])` — `ScriptedDriver._start_linux`, `scripted.py:736-766`.

**2.6 The dispatch.** `drivers/__init__.py:28-32`'s `_DRIVERS` dict and `get_driver` (`:35`) are the
one branch point in the whole system. `traffic.py` calls it twice (`:1261` apply, `:1522` stop) and
has no `drive`-keyed branch of its own. **Adding a fourth entry to `_DRIVERS` is the entire wiring
change** on the execution side.

## 3. The proposed model

### 3.1 `drive: command`

A fourth `drive:` kind whose rendered in-guest client, at each emit's firing instant, **executes a
command** instead of opening a socket. The loop, the gate, the jitter, the per-emit `next_fire`
scheduling and the one-summary-line-per-fire logging are structurally the same as
`_CLIENT_TEMPLATE`'s; only `fire()`'s body changes from `send_tcp`/`send_udp` to a
`subprocess.run(argv, timeout=…)`.

### 3.2 Where the command comes from

**Decide this deliberately; it is the design's one real fork.** Two candidate shapes, both viable:

- **(a) A per-proto template table in the driver** — `command.py` owns a
  `{proto: argv-template}` map (`dns → dig +tries=1 +time=2 @{peer} {qname}`,
  `https → curl -sk --max-time 5 --resolve …`, `ntp → chronyc -h {peer} tracking` or a one-shot
  `ntpdate -q`), mirroring `scripted._MULTICAST_GROUPS`'s precedent of a driver-owned wire-detail
  table. An unmapped `proto` is a **fail-loud authoring error**, exactly as an unmapped multicast
  proto already is (`resolve_emit_peers`, `scripted.py:226-234`) — LES-001: never silently
  substitute a plausible-looking wrong behavior. Personas stay portable and unchanged.
- **(b) An explicit `emits[].command:` field** — maximum expressiveness, but it makes personas carry
  guest-specific argv, pushes protocol knowledge into content, and needs a new PSN rule plus an
  injection-safety story for an operator-authored string. **Recommendation: (a) for v1**, with (b)
  available later as an override; the twin-agnostic-persona principle
  (`docs/design/07-traffic-personas.md`) argues for the driver owning the argv.

Whichever is chosen: the baked `peer_ips` list, `ports`, `transport` and the volume-derived
repetition count are the template's inputs, so §2.2's containment is structural rather than a
convention a template author has to remember.

### 3.3 What a `command` emit's `volume:` means

`_VOLUME_PROFILE`'s three knobs (messages / bytes-per-message / gap) do not map cleanly onto
"run `dig` N times". **Decide and pin explicitly rather than inheriting silently:** `messages`
becomes the invocation count and `gap_s` the inter-invocation sleep; `bytes` is **ignored for a
command emit** unless a template can spend it (e.g. an HTTP request whose body or requested object
size is parameterizable). An ignored knob must be **logged once at render time**, not silently
dropped — the existing `fails=` counter is the precedent for making an invisible non-event visible
(`scripted.py:353-357`).

### 3.4 Prerequisite tools in the guest

`dig` (`dnsutils`), `curl`, `chrony`/`ntpdate` are not in the `debian-12-cloud@v1` base image by
default. The driver must **fail loud** on a missing binary the way `ScriptedDriver._start_linux`
already does for `python3` (`scripted.py:747-752`) — never start a client that will log a
`command not found` on every fire for the life of the twin. §5.2 is why acquiring those tools is
harder than it looks.

## 4. Concrete axRange changes

| Touch point | Change |
|---|---|
| `axrange/personas/schema.py:35` | `DRIVE_KINDS = ("scripted", "replay", "real-service")` → `+ "command"` |
| `axrange/personas/drivers/command.py` | **new** — `CommandDriver(PersonaDriver)`: render + launch + `stop_unit`/`heartbeat_unit` |
| `axrange/personas/drivers/__init__.py:28-32` | one new `_DRIVERS` entry + the `__all__` export |
| `axrange/validator/catalog.py:533-535` | `PSN-030`'s `message_template` hardcodes the three names in prose — must be updated. The rule id, category, severity, pass and **count** are unchanged |
| `axrange/validator/checks/personas.py:94` | **no change** — it already reads `DRIVE_KINDS` |
| `axrange/validator/catalog.py:692` | **no change** — `assert sum(_BY_CAT.values()) == 114` still holds |
| `axrange/o2control/traffic.py` | **no change expected** to the dispatch (`:1261`, `:1522`) or the op inputs (`:1160-1181`). If the driver needs a per-assignment input, add its key to `_DRIVER_EXTRA_KEYS` (`:97`) — the existing allow-list, not a new mechanism |
| `axrange/config_exec/render.py:1762` | **no change** — reused |
| `docs/VALIDATOR.md:399` | `PSN-030`'s row text |
| `docs/design/07-traffic-personas.md:91-98` | the drive-kind table gains a fourth row |
| `tests/test_personas.py` | `PSN-030` acceptance of `command`; render determinism; the fail-loud unmapped-proto and missing-binary paths |

## 5. Three constraints that shape the design

These are the reason this work order exists as a proposal rather than a ticket. None of them is a
detail that can be settled during implementation.

### 5.1 A real client needs a real server — and that half is **aximprint's**

The twin's internet sink is currently an **echo server**: `internet-sink-1/2/4/8`
(`corpapcn-twin.yaml:211-252`, `count:` 1/2/4/8 = **15 hosts**), each running the generic
`responder.py` that `scripts/responder-internet-sink-*.sh` writes — it binds a list of `port/proto`
specs and echoes back whatever arrives (`_echo`: *"Echo rather than drain: a persona measuring bytes
needs the far side to actually move them back"*).

`curl -sk https://<sink>/` against that fails its TLS handshake. `dig @<sink>` against it gets its
own query echoed back, not an answer. **Protocol-faithful clients are useless without protocol-real
servers**, so this work order is only half a change: the sink must become a small set of real
services (nginx with a self-signed cert, a resolver, chrony), which changes its per-host footprint
(CPU/RAM, currently `cpu: 1, ram: 1Gi, disk: 10Gi`) and possibly the **binary-bucket fan-out
scheme** itself — the 1/2/4/8 role buckets exist to give `peer_scope: all` emits a
power-of-two choice of fan-out width, and that trick is cheap only while a sink host is a single
echo process.

**That half belongs to aximprint** (`/home/rocky/aximprint-axrange/src/aximprint/synth/compiler.py`
and `synth/responder.py`, which synthesize the responder scripts from the census). **Do not specify
it here.** Name it as a companion work order, sequence this one behind it, and let the aximprint
side decide the service set and the footprint.

### 5.2 Adding packages to a standing twin is not free — it trips `--update`'s mutation refusal

A twin config's `install:` actions become `provision-app` (`pkg:`) and `run-script` (`script:`) ops,
and **a `run-script` op's `script_content` is persisted verbatim in `inputs_resolved`**
(`axrange/planner/pass1_emit.py:1162`). `plandiff._diff_fields`
(`axrange/planner/plandiff.py:131-145`) compares the freshly-built op's resolved inputs against the
persisted `inputs_resolved_json` **key by key**; any difference classifies the op **Mutated**, and
`api/service.py:380-386` then refuses the whole `--update` (`:442`, naming op + field + old→new).
Mutation is refused, never redone — `docs/PLANNER.md` §9.2's redo-rules table is deliberately
unbuilt (`docs/WORK_ORDER_twin_incremental_update.md` §3.1: *"a partially-correct redo table is
worse than none"*).

So "just add `dnsutils` and `curl` to the resolver nodes' `install:` list" on the **standing**
138-VM `corpapcn-twin` is not an incremental edit. It is a full redeploy, or the same out-of-band
QGA push that `docs/WORK_ORDER_responder_op_row_drift.md` exists to clean up after. **A twin
deployed fresh with the tools baked in is cheap; retrofitting a standing one is not** — plan the
live-verify accordingly, and prefer baking the tool set into the image
(`docs/IMAGE_BUILD.md`) over adding `install:` actions, since an image change costs nothing on the
next twin and everything on this one.

### 5.3 Windows is unsolved, on both sides

The Windows client is a PowerShell script under a Scheduled Task
(`scripted.py`'s `render_scripted_client_windows` / `_start_windows`, launched via
`render.scheduled_task_start_block` — never a bare `Start-Process`, the same LES-086 class). A
`command` driver would need a **second, independently-designed** per-proto template table in
PowerShell (`Resolve-DnsName -Server`, `Invoke-WebRequest`, `w32tm /stripchart`), with no shared
argv table between the two.

And there is **no responder on Windows at all**: `docs/SESSION_STATE.md`'s recorded structural
ceiling #3 — *"the synthesised responder is a POSIX shell action; delivered to a Windows guest
`run-script` runs it as PowerShell and it dies on the first heredoc. Five nodes including the DC
still refuse every connection."* **Scope this work order Linux-only and say so**, rather than
shipping a Windows branch that cannot be proven.

## 6. Out of scope

- **The sink's real services** (§5.1) — aximprint's, by name, in a companion work order.
- **Windows** (§5.3) — Linux-only v1, explicitly.
- **A protocol-mix fingerprint axis.** This work order makes a `proto_mix` axis *possible*; building
  and calibrating one is aximprint's fidelity-loop work, not axRange's.
- **`emits[].command:` as an authored field** (§3.2b) — deferred behind the driver-owned table.
- **Retiring or changing the `scripted` driver.** It stays, unchanged and byte-identical: it is the
  right tool for volume/rate work (the max-capture-rate characterization, CHANGELOG 2026-08-13/14,
  depends on it), and a `command` driver will be strictly slower per unit of offered load.
- **Reaching the sink's reserved `internet`/`egress` peer tokens.** Those still put nothing on the
  wire (`docs/PRODUCT_GAPS.md` **GAP-048**); unaffected either way.

## 7. Value, honestly

**What this does NOT buy.** It will **not** move the fidelity loop's current axes.
`role_census`, `role_edges`, `fan_out_degree` and their `_local` variants all count **edges**, and
an edge row is recorded whether or not Zeek can name the protocol — GAP-C20 says exactly this in its
own "Scope, honestly" paragraph. Anyone pitching this as a fidelity-score win is wrong.

**What it does buy — three things, in ascending order of weight.**

1. **Correct role inference for the sink.** aximprint infers a host's role from its observed
   protocol/port profile. The sink currently reads as `dns-resolver`, and the journal's iteration-18
   entry traces the mechanism: after the iteration-17 repair the sink served 53, read as
   `dns-resolver` instead of `web-tier`, and that relabel collapsed `generic-endpoint→web-tier` from
   315 edges to 3. **This is a correction to GAP-C20's own stated scope, recorded in the journal at
   iteration 18** — protocol shape *"does not change how many edges exist, but it changes which
   ports carry them, which decides the sink's inferred role, which relabels hundreds of edges."* So
   the "does not move the axes" caveat above is true of the axes' *definitions* and false of their
   *measured values*, via role attribution. Cite the corrected version, not the original.
2. **A future protocol-mix axis becomes expressible at all.** The source twin is ssl/dns/ntp-shaped
   and the twin is udp/icmp-shaped; no persona setting can close that, because the driver cannot
   produce a protocol. Today that axis cannot even be defined.
3. **It is close to a precondition for the platform's stated purpose.** `CLAUDE.md`'s primary
   purpose is *"validate cyber defense solutions against repeatable attack scenarios in realistic,
   disposable environments,"* and detection rules match protocol **semantics**, not port numbers. A
   Suricata signature for DNS tunneling inspects the query name; a Kerberoasting detection matches
   RC4 in a TGS-REP; the AD detection ruleset that the purple-team loop already turns on
   (`suricata_ad.rules`, CHANGELOG 2026-06-24) fires on protocol fields. **None of them will ever
   fire on a byte-pusher hitting the right port.** Benign background traffic that cannot trigger a
   protocol-aware rule also cannot produce a realistic **false-positive** rate, which is half of what
   a detection-validation range is for. This is the strongest argument, and it is independent of the
   fidelity loop entirely.

## 8. Validation

**Unit/suite:**
1. `PSN-030` accepts `command` and still rejects an unknown kind; rule count still 114.
2. `get_driver("command")` returns the new driver; `traffic.py` needs no new branch (assert by
   driving a `command` assignment end-to-end against a fake `GuestAgent`).
3. Render determinism: same persona + peers + seed + instance_id ⇒ byte-identical client, matching
   the pin `render_scripted_client` already carries.
4. **Containment pin (the safety property):** every peer address in the rendered client is a literal
   baked by the orchestrator; assert no rendered command can cause guest-side name resolution
   (`curl` templates carry `--resolve`/`--connect-to`; no bare hostname URL).
5. Fail-loud: an unmapped `proto` raises at render; a missing in-guest binary raises at start.
6. `scripted` rendering is **byte-identical** before and after (a regression pin — this work order
   adds a branch, it must not touch the first).
7. An ignored `bytes:` on a command emit is logged, not silent (§3.3).

**Gated `dev45` live-verify** — blocked on §5.1, and deliberately **not** run against the standing
`corpapcn-twin` (§5.2: retrofitting tools onto it means a redeploy). On a small purpose-built twin
with the tools baked in and a real-service sink: a `dns` command emit produces a **real query with a
real answer** — verified in-guest (`dig` exit 0, an ANSWER section) **and** at the sensor, where Zeek
must record `dns`, not `udp`. Same for one TLS emit (`ssl`, not `tcp`). Then a clean
`tfc destroy` proving the transient unit is gone.

## 9. Depends on / sequence

- **Depends on:** the aximprint real-service sink (§5.1) for anything provable; nothing else unbuilt
  on the axRange side.
- **Sequence:** aximprint sink → this work order → (optional) a `proto_mix` fidelity axis.
- **Interacts with:** `docs/WORK_ORDER_responder_op_row_drift.md` — that work order is about the
  *mechanism* by which a standing twin's guests are allowed to diverge from their op rows, which is
  the same mechanism §5.2 says this change would have to abuse on a standing twin. Landing that one
  first makes this one's rollout story honest.
- **Does not block, and is not blocked by:** the aximprint↔axRange Console track
  (`docs/WORK_ORDER_aximprint_console.md`).
