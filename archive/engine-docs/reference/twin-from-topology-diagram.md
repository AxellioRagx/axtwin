# Building a twin from a topology diagram

*Reference note, 2026-09-08. Origin: a design discussion in the main session (Authority:
implementation (Claude Code), recorded at the user's request). Not a work order and not a
design-of-record; it explains an option against the platform as it stands and names what would
have to be built. Related: `docs/design/13-golden-twin-fidelity.md` (the rows),
`docs/design/14-operator-assertions.md` (the operator channel and the assertions file),
`docs/design/15-console-canvas.md` (the canvas), `docs/WORK_ORDER_console_canvas.md` (S2–S7).*

## 1. Can it be done with what we have and what we are building?

Yes: today by hand, and the Console being built is exactly the tool that makes it a drawing
exercise. The evidence model was designed for this case, though a "diagram only" twin reads as
low-fidelity by design.

**What a diagram gives you.** Segments with their CIDRs, the routers and how they connect, the
hosts with names, roles and addresses, and the conduits between segments. That is precisely the
structural half of the fidelity spec (rows 1 asset, 2 topology, 8 dependency). What a diagram does
not give you: which ports each host listens on, who talks to whom, when, and how much. Those are
rows 5, 9 and 10, the behavioral half, which only a capture or a reference export can fill.

**Today, without the Console.** A twin config is a hand-authored YAML: segments, nodes with images
and roles, conduits, a router, optional listen sets and install scripts. The shipped
`twin-configs/corp-enterprise/` and the seven-host `twin-configs/svc-verify/` bundle are both
diagram-to-YAML by hand, and `svc-verify` shows the full loop — a domain controller and a joined
member included — validating and planning in seconds. A diagram can be transcribed into a twin
config now, then validated, planned and deployed. Nothing in the engine needs a capture.

**With what we are building.** The assertions file (`axr-assertions/1`) is the operator's channel,
and its record set covers this case: `add` a host with key, kind, segment, role, image, listen;
`add` a segment with its CIDR and zone type; `add` a conduit between two segments. Compile an
assertions file with no capture behind it and the compiler emits a twin whose every fact carries
the `asserted` class, the top of the precedence order. The Console's S5 slice is the drawing
surface for exactly those records: place a segment, drop hosts on it, draw a conduit, save, apply.
"Give me a diagram, get a twin" becomes the S5 loop with an empty observed side.

**Three limits, and what closes each.**

1. **A diagram is not machine-readable.** Someone transcribes it: an operator, or an
   image-to-assertions step that reads a drawing and emits the file for correction in the canvas.
   That step is not designed; it is a natural small addition, since the target format already
   exists and is schema-published (`src/aximprint/schemas/axr-assertions-1.schema.json`, vendored
   at `axrange/api/schemas/`).
2. **An asserted-only twin scores golden on rows 1, 2 and 8 and cannot be scored on rows 5, 9,
   10.** The scorecard says so honestly (`not-computed`). If the diagram comes with a service
   inventory, `listen` on each `add` gets row 5's server half: the real-daemon recipes need only a
   listen set to drive them.
3. **The compiler currently expects a PIT.** A cast with an assertions file and no observations is
   the one path not yet exercised; the deploy gate treats an asserted host as fully occupied by
   rule, but `aximprint cast` takes a store and a PIT today. That is a small aximprint change: a
   cast from assertions alone.

Sequence, if this becomes a first-class path: finish Console S3–S5 (at the top of the order as of
2026-09-08), add the assertions-only cast on the aximprint side, then decide whether the
image-to-assertions reader is worth building or whether transcribing in the canvas is fast enough.
A twenty-segment enterprise diagram is perhaps an hour of drawing in the canvas; the reader saves
that hour and introduces a class of errors the canvas would then have to catch.

## 2. Which kind of diagram: physical or logical?

**Logical.** The twin is a logical construct, and the only thing a physical diagram carries that a
logical one lacks is exactly what the platform deliberately does not reproduce.

**What the twin needs is what a logical diagram shows.** A logical diagram is drawn in the twin's
own vocabulary: subnets with CIDRs, VLANs, the routers and firewalls that sit between them, the
hosts on each subnet with names, addresses and roles, and the allowed paths between segments.
Every one of those maps directly to a twin-config element (segment, conduit, router, node) and to
an assertions record. The transcription is nearly one-to-one, so an image-to-assertions reader has
a small, well-defined target, and what it gets wrong is easy to see in the canvas because the
canvas draws the same abstraction.

**What a physical diagram shows is what the twin abstracts away.** Switches, patch panels, trunk
ports, uplinks, rack positions, cable runs. The Proxmox SDN materializes a subnet as one vnet
regardless of how many physical switches carried it in the source network; there is no place in
the IR for a switch, and the architecture makes the substrate tap-agnostic with a per-port mirror
plane on the hypervisor. A physical diagram therefore forces an inference step the logical one
skips: derive the broadcast domains from the switch-and-VLAN wiring, then discard the wiring. That
inference is where the errors live. A trunk carrying six VLANs looks like one line; two switches on
one VLAN look like two segments; a firewall drawn as a box with four cables says nothing about
which subnets it routes between. And a physical diagram usually omits addresses and roles
entirely, since they are not physical facts.

**Taps: three different facts, and which diagram carries each.** (Corrected 2026-09-08: an earlier
draft of this note said a logical diagram "cannot say" where the taps are. That overstated it.)
A tap is three facts, not one.

- **Tap scope** — *what* is captured: "every port on the finance subnet", "everything crossing
  between corp and the DMZ". This is a logical fact about a segment or a boundary, and it is exactly
  the vocabulary the tap tier already speaks (`port` / `segment` / `perimeter` / `boundary`,
  `docs/design/06-taps-sensors.md`). A capture mark drawn on a segment or on a conduit in a logical
  diagram maps one-to-one onto a tap-plan binding. A physical diagram gives scope only by inference
  from where the span port sits.
- **Tap vantage** — what the capture *could* see: a span on the core uplink saw inter-segment
  traffic only, never east-west chatter inside a segment or traffic local to an access switch. This
  bounds the observed evidence and matters for the fidelity spec's observation-channel tagging. A
  physical diagram carries it inherently; a logical diagram carries it only as an annotation on the
  tap mark ("vantage: core uplink, inter-segment only"), which most authors omit because they are
  drawing the network, not the monitoring.
- **Tap mechanics** — *which* switch, mirror port, span session. Physical only — and the twin does
  not need it: the mirror plane is a per-port `tc` mirror on the hypervisor, and the tap tier
  resolves scopes against the twin's full topology with no allow-list. Record the source vantage as
  a note on the imprint, not as structure.

Consequence for a reader: if the diagram convention includes capture marks, an image-to-assertions
step can emit a **tap plan** beside the twin — something a physical diagram can never yield without
a person translating span ports into scopes. The tap row strengthens the case for the logical input,
rather than weakening it.

| | Logical | Physical |
|---|---|---|
| Maps to segments, conduits, routers, hosts | Directly | After deriving broadcast domains from wiring |
| Carries CIDRs, VLAN ids, addresses, roles | Usually | Rarely |
| Carries firewall placement and allowed paths | Yes, as the drawn boundaries | Only as cabling |
| Tap scope (what is captured) | Yes, as a mark on a segment or conduit; maps to the tap tier directly | Only by inference from the span port's position |
| Tap vantage (what the capture could see) | Only if annotated; usually omitted | Inherently |
| Tap mechanics (which port, which session) | No | Yes, and the twin does not need it |
| Reader's error class | Misread labels, caught in the canvas | Wrong segmentation, invisible until the twin misbehaves |
| Fidelity rows it can satisfy | 1, 2, 8 (structural) | 1 and 2 partially, 8 poorly |

**Practical consequence for a reader's design.** Accept a logical diagram as the input contract
and treat a physical one as out of scope rather than as a degraded case. If only a physical
diagram exists, the right first step is a logical diagram derived from it by someone who knows the
VLAN plan, since the VLAN-to-subnet mapping lives in the network team's head or in the switch
configs, not in the picture. The far better companion to a physical diagram is the switch and
firewall config export, which is precisely the reference-ingest channel already designed
(`aximprint` `docs/WORK_ORDER_reference_ingest.md`): switch VLAN tables and interface descriptions
give the segments and hosts with real evidence behind them, and firewall rules give the conduits
and, later, the per-rule policy that row 7 needs. That is the `imported` class, one step below
`asserted` and far above anything read off a drawing.

**In one line:** logical diagram in, assertions (and, with capture marks, a tap plan) out, corrected
in the canvas; physical facts arrive through config exports, not pictures.
