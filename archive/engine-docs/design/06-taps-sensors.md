*The typed sensor registry, standing sensors, the tap-point vocabulary, the tap playout, and
config-declared external sinks.*

## Sensors are standing O2 components

A **sensor** is a typed receiver of mirrored traffic — a node that turns a mirror feed into an
analysis artifact (pcaps, IDS alerts, protocol logs). Sensors are **standing components of the O2
surface**, stood up once by `o2control` (see [05-o2-surface.md](./05-o2-surface.md)) and shared
across every twin an operator deploys. A twin's own deploy does not create a sensor VM — it only
records which of its links are tappable (see "The tap playout" below); wiring a tap to a sensor
is always a separate step.

### The sensor-type registry

Sensor types form an open, extensible catalog (`axrange/sensors/`), modeled the same way as the
persona and image registries — adding a new type is a registry entry, not a schema or engine
change.

| Type | Tool | Artifact |
|---|---|---|
| `tcpdump-sensor` | tcpdump | rotating pcaps |
| `suricata-sensor` | Suricata | alerts (`eve.json`/`fast.log`) |
| `zeek-sensor` | Zeek | connection/DNS/HTTP logs |

`suricata-sensor` and `zeek-sensor` clone a **pre-baked image** carrying their tool (built via the
image pipeline, [10-images.md](./10-images.md)) rather than installing it at deploy time — their
packages live outside what a sensor VM's network can reach at boot. `tcpdump-sensor` installs at
deploy time because tcpdump ships in the base distribution. Either way, the runtime install/start
path is **fail-loud**: a sensor whose tool can't install or won't start fails the deploy with the
reason, never leaving a dead daemon behind a green result.

### Capture supervision

A sensor's capture process launches as a **detached, deterministically-named transient systemd
unit**, never a bare backgrounded process. Launching a long-running capture as a plain
backgrounded job over the guest-agent exec channel is a trap: the moment the guest-agent session
that launched it ends, the process is reaped along with it, and the sensor silently stops
capturing while every deploy status still reports success. The detached-unit launch, paired with
an `is-active` start check, closes that gap and fails loud instead of failing silent.

Captures accumulate as timestamped pcaps rather than a fixed-size ring, and an in-guest watchdog
prunes oldest-first against disk-percentage and file-count thresholds. This is what lets a
sensor's captures **persist across a tap being removed and re-applied, and across the sensor VM
itself restarting** — retention is a property of the sensor, not of any one tap binding.

The wire mechanics underneath — the per-tap `tc` mirror, the keyed gretap, and the flow-based
decap at the sensor — are covered in [04-networking-capture.md](./04-networking-capture.md); this
document covers only how sensors and taps are declared, registered, and bound.

### External sensors

Not every capture destination is something axRange deploys. A hand-provisioned, operator-managed
appliance — the running example is a packetXpress capture/analysis box — can be declared as a
**config-declared external sensor** in `adapter.conf`'s `EXTERNAL_SENSORS` list: an id, its
management vmid, its transport IP, a type, and its capture interface. Declaring it this way is
enough, by itself, to make it a valid tap-plan target alongside any standing sensor `o2control`
deployed.

An external sensor differs from a standing one in exactly the ways its lifecycle demands:

- **Auto-protected from every tier's teardown.** Declaring it in `EXTERNAL_SENSORS` is sufficient
  protection on its own — no separate protected-name entry is needed, though an operator may add
  one for a node that is a protected asset but not itself a tap target.
- **`verify-capture` is intentionally, loudly skipped for it.** axRange's tooling has no
  guest-agent channel into an operator-owned box and never attempts one — no QGA read, no SSH, no
  in-guest configuration. Rather than silently reporting a capture heartbeat that never actually
  ran, every stage of the tap-plan lifecycle (validate, plan, apply, info) surfaces an explicit
  warning that capture through this sink is unverified by axRange and must be confirmed
  out-of-band, on the sink's own tooling.
- **axRange's only obligation toward it is the mirror plumbing** — the keyed gretap aimed at its
  transport IP and the tap-side `tc` mirror. Everything on the receiving end (the appliance's
  network configuration, its own capture tool, its own persistence across a reboot) is the
  operator's responsibility, not axRange's.

## Tap points: a SPAN-session vocabulary

Every capture in axRange is a SPAN session under the hood: a scenario's vnet is the switch, a
VM's NIC is a switch port on it, and a sensor is the SPAN destination. A **tap point** just names
a *set of ports* to mirror — it is identified by *where it sits in the topology*, never by a
traffic direction. This holds even where a north-south/east-west split would be meaningless (a
flat, single-segment twin still needs tap points), and where a directional label would be
actively ambiguous (a segment carries both lateral and boundary-crossing traffic on the same
wire; a multi-homed router's "direction" depends on which neighboring segment you reason from).
`scope` (what kind of place) + `target` (which place) is the whole identity. An earlier revision
of this vocabulary also carried an open, free-form `labels[]` an author could attach for selection
convenience (including `ew`/`ns` when that genuinely described the point); `labels[]` is removed
entirely (2026-07-28, `docs/WORK_ORDER_tap_eligibility.md` D3, part of dropping the named/labeled
catalog it belonged to) — `scope` + `target` is now the *only* identity a tap point has.

### Direction is derived, never authored

Every port mirrors both directions by default. That's fine for a single port, but for any *set*
of two or more ports on the same segment it means the same frame gets mirrored more than once —
once from the sender's port, again from the receiver's. A tap point's scope therefore also
determines a **capture direction**, worked out by the resolver rather than written by an author:

- a **multi-port** scope mirrors **rx only** (the frames a port *sent* into the segment) — every
  frame is then captured exactly once, at its point of entry;
- a **single-port** scope mirrors **both directions** — with only one port in the set there is no
  sibling to pair with, so rx-only would lose everything sent *to* it.

This is a mechanism-level rule ([04-networking-capture.md](./04-networking-capture.md) covers the
`tc` filters that realize it), surfaced here because it's what makes each scope answer a
genuinely different question.

### The four scopes

| Scope | Target | Ports in the set | Direction | Answers |
|---|---|---|---|---|
| `port` | `{host, net}` | one — that host's leg on that net | tx & rx | "everything this one interface carries" |
| `segment` | a network id | every port on the net, **routers included** | rx only | "everything happening on this network" |
| `perimeter` | a network id | every router port attached to that net | tx & rx | "everything this network exchanges with anywhere else" |
| `boundary` | two network ids | the joining router's port on each net | rx each side | "specifically what flows between these two networks" |

**`port`** is the full conversation on one switch port — a crown-jewel host, a suspect box,
anything you want both sides of. It's the only scope with no completeness precondition, since
there's nothing else in the set to leave a hole.

**`segment`** mirrors every port on the network, without exception — router legs are deliberately
included, because a router's leg is what carries traffic *arriving* from another segment;
excluding it creates a silent coverage hole for exactly the off-net traffic a segment tap is
meant to see. rx-only is exactly-once **only under full coverage**: leaving some of a net's ports
untapped means traffic between an excluded port and an included one is missed entirely, not
merely under-sampled. A partial view of a segment is authored as N separate `port` bindings, not
as a `segment` binding with some hosts left out — a `segment` binding always implies mirroring
the whole net.

**`perimeter`** mirrors every router port on a network, both directions, and is the
highest-value single tap point in most twins: it sees everything a segment exchanges with any
other segment or the internet while excluding the intra-segment chatter that never leaves, and it
sees that traffic in the segment's **own address form** — pre-NAT outbound, post-de-NAT inbound —
so an internal host stays directly identifiable in both directions. `perimeter` also covers the
**twin's external edge**: `wan` is a network id like any other, so `perimeter: wan` resolves to
every router's WAN uplink in one rule, with no separate mechanism needed for "the edge of a
segment" versus "the edge of the twin."

**`boundary`** captures specifically the traffic between two named, non-WAN networks — an
internal conduit such as an IT/OT boundary — each direction exactly once as it enters the far
side. It's narrower than `perimeter`: a network's `perimeter` point sees its traffic to
*everywhere*, a `boundary` point between two named nets sees only the traffic between *those
two*. Two limits apply to `boundary` (and to `perimeter`, for the same reason): traffic a router
**drops** is invisible — rx on each side means only what's actually forwarded is seen, so pairing
a `boundary`/`perimeter` point with a `segment` point on the source net is what catches what a
firewall blocked; and both scopes observe the **post-routing** form only — rewritten MACs,
decremented TTLs, and any NAT already applied.

An older scope set (`segment`/`boundary`/`host`/`leg`) is retired: `leg` is renamed to `port` (it
names the thing being tapped, not the topology object it happens to be), the old `host` scope
(every leg of one host) is dropped in favor of either N `port` bindings or a `selector:` query,
and `perimeter` is new — it names a place ("this network's edge") neither old scope could
express, and is what makes the WAN edge addressable at all.

### The WAN edge

A router's WAN uplink is a real, taggable port — mechanically identical to any other tap (a `tc`
mirror on the router's own NIC, never on the shared uplink bridge itself, so cluster management
traffic sharing that bridge is never in scope). `wan` is the author-facing alias for the twin's
external network in a tap-plan selector; naming a single router's uplink on a twin with more than
one router drops to a `port` binding (`{host: <router>, net: wan}`) instead.

Tap points are **derived, not published**. A tap plan names a scope + target and the resolver
computes the matching port set on demand, directly against the twin's full published topology
(`hosts[].nets[]` — the same attachment list [02-twin-model.md](./02-twin-model.md)'s `te
inventory` reports, and the same one the Traffic tier reads for persona-assignable host roles, see
[07-traffic-personas.md](./07-traffic-personas.md)). There is no separate, browsable tap-point
catalog anywhere in a twin's inventory or its config — the named/labeled catalog (`id`/`labels[]`
as an author-declared namespace) was dropped 2026-07-28 (`docs/WORK_ORDER_tap_eligibility.md`)
along with the twin-side `tap:` authoring it depended on (see "Tap eligibility" below). A
`tap_point:` binding still addresses a **derived** id, computed the same way at bind time —
`<net>` for `segment`, `<a>--<b>` for `boundary`, `<net>-perimeter` for `perimeter`,
`<host>:<net>:<nic>` for `port` — it just isn't listed anywhere ahead of time for browsing.

### Selecting and binding a tap point

A tap-plan binding may address a target three ways — exactly one is required per binding:

- the mechanical `tap:` interface id (the tap plan's own addressing field, naming a specific
  interface directly — not to be confused with the twin-IR `tap:` flag that eligibility removed;
  see "Tap eligibility" below);
- a `tap_point:` id, derived the same way at bind time (`<net>` for `segment`, `<a>--<b>` for
  `boundary`, `<net>-perimeter` for `perimeter`, `<host>:<net>:<nic>` for `port`) — not a lookup
  into a published catalog, since there is no longer one;
- a `selector:` matching on `scope` and/or `target` — any selector must carry at least one real
  matching criterion (an all-unset selector can never silently resolve to "every tap point in the
  twin"); omitting `target` is valid and matches every point of that scope. `labels[]`-based
  matching was dropped along with the named/labeled catalog (2026-07-28) — there is no labels
  field left anywhere to match on.

When two bindings in the same plan claim overlapping ports for the same sensor — a `segment`
binding and a `port` binding on one of that segment's own hosts, say — the resolver **unions the
required directions per (port, sensor)** and installs exactly one mirror, rather than one binding
silently overriding the other's direction.

### Tap eligibility

**The twin substrate is entirely tap-agnostic** (2026-07-28, `docs/WORK_ORDER_tap_eligibility.md`,
Authority: user). There is no `tap:` field anywhere in the Scenario IR any more — the earlier
three granularities (`nodes[].tap`, `nodes[].nets[].tap`, `networks[].tap`) are all REMOVED, and a
leftover one now fails validation loudly (`STR-200`) instead of being silently dropped. A twin's
deploy carries no tap concept of its own at all: it emits no OP-MIR op of any kind, and a twin
config's `tap_points:` block is gone too (a stale one now errors rather than silently no-op'ing).

Every interface a twin's deploy ever attaches (`hosts[].nets[]`, sourced unconditionally from
`attach-vm-to-vnet` for every leg) is capture-eligible, full stop — no allow-list, no hint, no
default to fall back on. `segment` genuinely means "every port on the net," including a host
nobody thought to flag, because there is no flag left to forget. A router's WAN uplink is no
longer a special case either: it is carried in the same published topology as `net_id: "__wan__"`
like any other port, so `perimeter: wan` resolves the ordinary way rather than through a dedicated
eligibility exception.

The tap plan is therefore the **sole** authority on what gets captured — a decision (which link,
to which sensor) that always belonged there and now has nowhere else it could live. The trade-off
is a deliberate posture change, not a regression: a plan that used to resolve to a hand-flagged
handful of ports may now resolve to every port on a large segment the first time it's applied
against a full-topology twin — see the capacity guardrail below.

### Current limits

- **Dropped traffic is invisible** to `boundary` and `perimeter` — both only see what a router
  actually forwards. Pair either with a `segment` point on the source net to catch what a
  firewall blocked.
- **A VXLAN-bridged pair of segments joined only through the underlay fabric** — no single host
  holding legs on both, e.g. a spine-mediated pair — has no derivable `boundary` or `perimeter`
  point; both scopes key off a joining host, and a fabric underlay has none.
- **No per-binding traffic filtering.** A bound sensor receives everything the matched ports'
  direction rule delivers; there's no way yet to narrow a binding's traffic with an expression
  before it reaches the sensor.
- **No hard cap on fan-out or port count.** Full-topology eligibility makes "bind every port to
  one sensor" trivial to author, and each fan-out destination duplicates every frame on the shared
  transport wire. `tap plan`/`tap deploy` warn (non-blocking) when a resolved plan crosses a large
  port-count or fan-out threshold, naming the duplication; the apply still proceeds.

## The tap playout

Binding a twin's tap points to standing O2 sensors is its own lifecycle, separate from both the
twin's deploy and the sensor's stand-up. It runs as a standalone `tap-<te>` deploy — reusing the
same engine plan/deploy machinery `o2control` uses everywhere else, composed against the twin's
published topology and the sensor's published inventory rather than by reaching into either
directly.

### The binding matrix — a tap plan

A tap plan is a list of `tap → [sensors]` bindings, keyed to a target twin:

```yaml
tap-plan:
  target: <twin-scenario-or-deploy>
  bindings:
    - tap: corp-net          # fan-out: one tap, many destinations
      sensors: [suricata-1, tcpdump-1]
    - tap_point: idmz-conduit
      sensors: [tcpdump-2]
      mode: exclusive        # this tap point goes to exactly one sensor
```

- **Fan-out (1:N):** the tap gets one mirror action chained per destination sensor — each
  destination receives its own keyed gretap, so N sensors means N copies of the traffic on the
  transport wire.
- **Exclusivity:** a tap point marked `mode: exclusive` may be claimed by only one binding across
  the *entire* plan, not just checked within its own entry — an overlapping fan-out or exclusive
  binding on the same underlying interface is rejected at apply time.

### Apply and destroy

`axrange tap {validate,plan,deploy,status,info,destroy}` — the same shared verb grid the other
tiers use (see [02-twin-model.md](./02-twin-model.md)). Applying a plan is idempotent: every apply
re-asserts the full set of bindings it describes, and a **de-scope pass** removes the gretaps and
mirrors for any sensor missing from the newly-applied plan, so narrowing or reshuffling a plan
never leaves a stale mirror running. Tearing down the tap deploy removes only its own tap-tagged
resources, leaving the twin and the standing sensors untouched; tearing down the twin itself
cascades into tearing down its tap deploy too (see the protect/cascade matrix in
[02-twin-model.md](./02-twin-model.md)).

The tap playout discovers what it needs from two places it never bypasses: the **tappable set**
comes directly from the twin's own published topology (`hosts[].nets[]`, the same full attachment
list `te inventory` reports — there is no separate tap-intent bookkeeping op any more, see "Tap
eligibility" above), and each sensor's **transport IP and identity** comes
from `o2control`'s standing inventory (see [05-o2-surface.md](./05-o2-surface.md)) — or, for an
external sensor, from its `EXTERNAL_SENSORS` declaration. This keeps the tap playout from needing
to re-parse a Scenario IR or reach into either the twin's or the surface's internals directly.

## Predefinable tap plans

A tap plan's `target` is optional, and when given, does not have to be a literal deploy id — it
may instead be a scenario or twin-config name, resolved at apply time to the newest live twin
deploy of that scenario. `tap validate`/`plan`/`deploy` all accept a `--target` override on top of
whatever the plan file itself specifies. This is what lets a tap plan be authored once and checked
into a library the way a twin config is, rather than welded to one specific deploy id the moment
it is minted — a twin config's own folder can ship its portable tap plans alongside it (see
[09-content-overlays.md](./09-content-overlays.md) for twin configs). A plan that still hardcodes
a literal deploy id as its target keeps that same welded-to-one-deploy behavior; naming a scenario
instead is what buys reusability across redeploys.

## Where this fits

- [04-networking-capture.md](./04-networking-capture.md) — the underlying `tc`/gretap wire
  mechanics the tap playout composes.
- [05-o2-surface.md](./05-o2-surface.md) — the O2 surface and `o2control`'s standing-sensor
  inventory this playout binds against.
- [02-twin-model.md](./02-twin-model.md) — the Tap tier's tag, name shapes, and its place in the
  teardown protect/cascade matrix.
- [09-content-overlays.md](./09-content-overlays.md) — twin configs, which can ship portable tap
  plans alongside their scenario definition.
