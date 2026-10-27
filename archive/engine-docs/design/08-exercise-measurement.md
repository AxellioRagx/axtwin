*The Exercise tier — ephemeral attack/defense playout against a deployed
twin — and the measurement layer that turns "did the defense catch it?"
into a number.*

## Scope: the twin is just the environment

A deployed twin (`te` tier) is the environment and nothing more: infra,
apps, and the identity/data/defense overlays a scenario describes. A bare
`te deploy` runs no attack, no activity, and no adversary agent — none of
that lives in a twin's own deploy at all. Attack, activity, adversary
plants, and measurement are entirely the **Exercise tier's** job, applied to
a twin that already exists.

This separation is what makes the replay loop possible: the twin (and its
capture taps, and its traffic) stays standing across many exercise runs, so
attacking it, measuring detection coverage, tuning defenses, and attacking
it again compares runs against a fixed, unchanging environment rather than
a freshly-reprovisioned one each time.

## The Exercise tier (`exr`)

The Exercise tier is a standalone playout — a peer to the Tap playout
([06-taps-sensors.md](./06-taps-sensors.md)) and the Traffic playout
([07-traffic-personas.md](./07-traffic-personas.md)). Like both, it binds
something onto an **already-deployed** twin rather than emitting its own
infrastructure from an IR walk; unlike them, the run it binds is
**ephemeral**, not a standing, converge-in-place binding.

```
  exercise spec: attack.activity and/or attack.adversary, against a twin
                            │
                            ▼
       compose a plan from the SAME ops a twin's own overlays use
       (deploy-behavior-agent / start-activity / wait-actor-ready,
        install-agent for an adversary plant)
                            │
                            ▼
          run through the same engine executor, ephemeral deploy
                            │
                            ▼
    attack/activity traffic + adversary agent, on the twin's own hosts
                            │
                            ▼
        truth markers + detections join in O2 → coverage matrix
                            │
                            ▼
                explicit `exr destroy` (or a `te` cascade)
```

### Exercise spec

An exercise spec is a small named YAML document (loaded the same way a
tap-plan or traffic profile is) that names the target twin and describes
the attack:

```yaml
exercise:
  twin: <twin-deploy-id>
  attack:
    activity:                    # internal, engine-run mode
      id: ad-kill-chain
      host: attacker              # a host_id in the twin's published inventory
      seed: 42
      inline: { steps: [...] }    # or a schedule block
    adversary:                    # external, manual Caldera mode
      id: red-sandcat
      targets: [ws01, ws02]
      server: http://caldera.o2.lab:8888
  defense: [ { note: "..." } ]    # informational only
  measurement: { run_label: ..., techniques: [...] }
```

At least one of `attack.activity` / `attack.adversary` is required; both
can be present in the same run.

### Dual-mode attack

An exercise run drives attack traffic in either or both of two modes:

- **Internal activity** — the engine's own inline-steps runner executes a
  sequence of steps over the guest-agent channel, one-shot, against the
  twin's own hosts. The corpus AD kill chain — recon → LLMNR poisoning →
  SMB/LDAP enumeration → AS-REP roasting → Kerberoasting → DCSync — is
  authored this way, and runs against a real domain controller and a real
  attacker host in the twin.
- **External Caldera** — an adversary agent (Sandcat) is planted on one or
  more twin hosts, and the actual attack operation is driven manually by an
  operator through the Caldera server in the O2 surface layer (see
  [05-o2-surface.md](./05-o2-surface.md)). The exercise tier's job here is
  only to plant the agent; playing out the operation is a human-driven
  step, not something the engine automates.

Both modes reuse ops that already exist and already have a config-class
execution backend — no new op type is introduced for the Exercise tier.

The choice between the two is a property of the attack, not a platform
preference: a deterministic, repeatable, self-contained attack — the AD kill
chain is the running example — belongs in internal activity, because its
whole value is reproducing byte-for-byte identically across replays.
Anything interactive, adaptive, or resembling a real C2 operation belongs on
Caldera, where a human operator improvises. The same O2 sensors and truth
markers measure both modes identically, so this choice affects only how the
attack is driven, not how it is scored.

### Two runs, one twin

Because a twin persists across exercise runs, more than one run can, in
principle, target the same twin. The tier enforces one safety rule: two
concurrent runs against the same twin may only target **disjoint** hosts —
the in-guest agent handle a run installs is host-scoped, so two runs
sharing a host could stop or reconverge each other's agent with no way to
tell them apart. A new run that would overlap an existing, still-live run's
target hosts is refused; the correct sequence is destroy-then-replay, which
is exactly the replay loop the tier exists for.

### Replay identity — `playout_hash`

Every exercise run computes a **`playout_hash`** over its composed plan's
operations, scopes, and resolved inputs — order-independent and
run-id-independent, so it reflects only what the run actually *does*, not
which run it happened to be. Re-running the same exercise spec (with the
same seed) against the same twin produces the identical hash. This is what
makes a "replay" assertable rather than merely intended: two runs with
matching `playout_hash` values are provably the same attack, which is the
precondition for treating a coverage difference between them as a genuine
measurement of defense improvement rather than noise from a slightly
different attack.

### Lifecycle

The Exercise tier uses a reduced verb set, since it binds a spec to an
already-deployed twin rather than validating and planning its own
infrastructure from scratch:

```
axrange exr {deploy,run,status,destroy}
```

Every apply mints a brand-new ephemeral deploy — there is no resume/reuse
across calls, unlike `tfc`/`tap`'s converge-in-place model. A run persists
until it is explicitly destroyed, or until its twin is destroyed (`te
destroy` cascades into an exercise run's teardown alongside `tap`/`tfc`,
each cascade isolated so one failing does not block the others). There is
no automatic reap: a finished run stays present, with its truth markers and
detections still queryable, until an operator or a cascade tears it down.

The overlap guard above is worth stating plainly as a real limit, not an
implied one: it serializes sequential re-applies against the same hosts, it
does not implement true concurrent-run isolation on shared hosts.

## Measurement — did the defense catch it?

Measurement is the payoff the whole replay loop exists for: turning "did
the defense catch the attack?" from an operator's read of raw logs into a
per-run coverage number, keyed to the ATT&CK techniques the attack actually
executed.

### The data spine

Two streams, joined in Loki:

```
ATTACK TRUTH   [axr-truth] run=<run-id> technique=<T> host=<h> ts=<ts>
                       (emitted per technique-labeled activity step,
                        by the activity runner itself)

DETECTIONS     Suricata / Wazuh / host-audit alerts
                       (already flowing to Loki from the O2 surface layer)

COVERAGE       per run-id: join TRUTH x DETECTIONS on
               (technique, host, [ts, ts+delta]) -> covered | missed
```

`technique:` is a free-form ATT&CK-technique label an author attaches to an
inline activity step, or to an adversary entry — it is the only new piece
of authored content this layer needs. The activity runner mints a run-id
per execution and emits one truth marker per technique-labeled step it
actually runs, shipped to Loki alongside every other O2 telemetry stream.

A detection is technique-resolved either because the detecting rule
already self-labels its ATT&CK id (modern Suricata/Wazuh/Sigma rulesets
often do), or through a fallback rule-to-technique map for rules that
don't. A technique counts as **covered** for a run when at least one
detection carrying the same technique id fires on the same host within a
tunable delta window of the corresponding truth marker; otherwise it counts
as **missed**.

### Where the join logic lives

A dedicated `axrange.measurement` subpackage holds the join logic (parsing
truth markers, resolving a detection's technique via the self-label/
fallback map, and computing per-run coverage) as a standalone leaf — the
engine never imports it, the same "engine never imports the O2-facing
consumer" boundary the sensor registry and persona library both keep. It is
a reader of Loki data, not a participant in any deploy's execution path.

### Reading coverage

A Grafana coverage dashboard, in the O2 surface layer, is the operator-
facing view: a truth-marker timeline, a detections timeline, and a
per-run-id technique-coverage matrix, all reading the same Loki streams the
engine ships to and the O2 detectors already populate. See
[05-o2-surface.md](./05-o2-surface.md) for the Loki/Grafana stack this
reuses.

### Closing the loop

```
deploy twin → attack RUN1 → coverage(RUN1)
                                   │
                     tune / add a defense overlay
                                   │
             replay the SAME exercise spec → attack RUN2 → coverage(RUN2)
                                   │
              compare RUN1 vs RUN2 (same playout_hash) → did coverage improve?
```

This comparison is only meaningful because RUN1 and RUN2 are provably the
same attack (matching `playout_hash`, and a seeded activity schedule
producing the identical sequence of steps each time). Measurement plus
replay identity together are what let an operator say, with evidence rather
than intuition, that a defensive change actually improved detection —
`docs/SLICE_AD_ATTACK.md` covers the AD kill-chain content this loop measures against.
See [07-traffic-personas.md](./07-traffic-personas.md) for the
sibling Traffic playout, [09-content-overlays.md](./09-content-overlays.md)
for where adversary overlays and activity content are authored, and
[02-twin-model.md](./02-twin-model.md) for the Exercise tier's place in the
five-tier model.
