*The five deployable tiers, their resource-name tags, the teardown protect/cascade matrix, and
the lifecycle verbs that operate on them.*

## The five tiers

Every resource axRange creates on the virtualization backend carries a tier tag baked into its
deterministic name. The tag *is* the blast radius: it is what lets a destroy operation know
which live resources belong to it and which belong to something else entirely.

| Tier | Tag | Name shapes | What it is | Lifecycle |
|---|---|---|---|---|
| **Lab** | `o2` | `o2z<5hex>` zone · `o2v*` vnet · `o2br_*` bridge · `o2-*` VM | The standing observability/orchestration surface — sensors, telemetry storage, and the attack/defense server stack. | Persistent. Stood up once via `axrange o2 deploy`; protected — never swept by a `te`/`tap`/`tfc`/`exr` destroy. Its own destroy (`axrange o2 destroy`) can sweep its own resources. |
| **Twin** | `te` | `te<6hex>` zone · `tev<5hex>` vnet · `tebr_*` bridge · `te-<6hex>-<host>` VM | A deployed target environment — the infrastructure, apps, and overlays a scenario describes. | `axrange te deploy` stands it up; `axrange te destroy` **cascades** to its standalone `exr`/`tfc`/`tap` deploys first, then sweeps it. |
| **Traffic** | `tfc` | `tfc<5hex>` zone · `tfcv<4hex>` vnet · `tfcbr_*` bridge · `tfc-<6hex>-<host>` VM | Benign persona traffic bound onto an already-deployed twin's published host roles. | Its own standalone deploy; `axrange tfc destroy` is self-only (also swept by the twin's cascade). |
| **Tap** | `tap` | `tapg_<6hex>` gretap · `tapm-<vmid>` mirror | The capture bindings (mirror + source gretap) connecting a twin's tappable links to standing Lab sensors, realized as its own `tap-<te>` deploy. | `axrange tap deploy`/`destroy` apply and tear down independently of the twin (also swept by the twin's cascade). It is a resource name-shape tag rather than a distinct deploy tier — a tap-binding deploy is itself tagged `te` and is scoped to only its own capture ops by a structural discriminator, not by a separate tier value. |
| **Exercise** | `exr` | `exr<5hex>` zone · `exrv<4hex>` vnet · `exrbr_*` bridge · `exr-<6hex>-<host>` VM | An ephemeral attack/defense/measurement run bound to an already-deployed, instrumented twin. | `axrange exr deploy`/`run`; `axrange exr destroy` is self-only (also swept by the twin's cascade). Persists until an explicit destroy or cascade — there is no automatic reap. |

A sixth name shape, `img-<image-slug>`, marks golden template images. Images are never swept by
any tier's destroy.

## Why five tiers

The tiers separate concerns that a single, undifferentiated deploy would otherwise bundle
together:

- The **Lab** (`o2`) is infrastructure you stand up once and keep running — sensors and
  telemetry storage need to persist across many twin deploys and tear-downs, not be re-created
  each time.
- The **Twin** (`te`) is the environment itself: the thing a scenario describes and the thing
  being defended.
- **Traffic** (`tfc`) is optional and orthogonal to the twin's own definition — the same twin can
  be exercised with different traffic profiles without redeploying it.
- The **Tap** binding is also orthogonal: which sensor captures which link is a capture-plane
  decision, made and changed independently of what the twin looks like.
- The **Exercise** (`exr`) is the one genuinely ephemeral tier — a single attack/defense/
  measurement run, replayable, torn down when the run is done, leaving the twin (and its
  traffic and taps) standing.

This separation is what makes the replay loop work: deploy a twin once, attack it, measure
detection coverage, tune defenses, then replay the *same* exercise against the *same* twin to
measure the delta — without re-standing-up the environment or its capture plane each time. See
[08-exercise-measurement.md](./08-exercise-measurement.md) for the replay mechanism.

## The teardown protect/cascade matrix

Every tier's destroy path runs through a single guard that decides, per resource, whether it
belongs to the tier being torn down. The guard generalizes across all six name-shape tags
(`te`/`tap`/`o2`/`img`/`tfc`/`exr`):

- A destroy of tier **X** refuses to touch any resource whose name shape belongs to a foreign
  tier, **unless** an explicit cascade exception applies.
- The only built-in cascade exception is **`te → tap`/`tfc`/`exr`**: a twin destroy also destroys
  its standalone exercise, traffic, and tap deploys, each cascade isolated from the others (one
  cascade failing does not block the rest, nor the twin's own sweep).
- **Cascades run first, in reverse-of-create order: `exr` → `tfc` → `tap` → `te`.** Every sibling
  tier binds into the twin's *live* VMs — the tap tier's de-scope pass re-asserts a per-VM mirror
  hookscript, the traffic tier's persona stop is a guest-agent call into the guest — so a cascade
  sequenced after the twin's own VM purge cannot do its job. Because those steps are best-effort by
  design (they warn rather than fail), getting this order wrong does not fail a destroy; it reports
  success under a wall of warnings and hides a genuinely stuck teardown.
- **`img`** resources, and any operator-declared protected external name (for example a
  hand-provisioned external sensor), are refused unconditionally — by every tier, including
  their own.
- The **Lab** (`o2`) tier is the one asymmetric case: an `o2` destroy *can* sweep its own `o2`
  resources (it is not self-protected the way `img` is), while every other tier's destroy can
  never touch `o2` resources in either direction.

```
            te destroy ──┬─→ cascades → exr destroy (own exr resources)  ─┐
                         ├─→ cascades → tfc destroy (own tfc resources)  │ first,
                         ├─→ cascades → tap destroy (own tap resources)  ┘ in order
                         └─→ then sweeps its own te resources

            tap / tfc / exr destroy ──→ sweeps only its own resources (self-only)

            o2 destroy ──→ sweeps only its own o2 resources

            img resources ──→ refused by every tier's destroy, unconditionally
```

An unrecognized or unexpected name shape fails **closed**: it is refused rather than assumed
safe to sweep. For the full name-shape reference and the underlying naming module, see
`docs/CONVENTIONS.md` §2.

## Lifecycle verbs

Four of the five tiers share one verb grid:

```
axrange {te,o2,tap,tfc} {validate,plan,deploy,status,info,destroy}
```

- `validate`/`plan`/`deploy` take a scenario/spec positional argument.
- `status`/`info`/`destroy` default to the latest deploy of that tier when no id is given.
- `te` additionally has `inventory [ID] [--json]` — a deployed twin's published binding
  contract (its host roster, including each host's full network-attachment topology), which
  the Traffic tier consumes for host roles and the Tap tier resolves tap points against
  directly (`docs/design/06-taps-sensors.md`) — there is no separate published tap-point catalog.

The **Exercise** tier uses a reduced verb set, since it binds a spec to an already-deployed
twin rather than validating and planning its own infrastructure from scratch:

```
axrange exr {deploy,run,status,destroy}
```

Outside the grid: `list-deploys [--tier {te,o2,tap,tfc,exr}]`, `te inventory [ID]`,
`twin-config list|show`, `serve` (the control-plane API), and `image build`.

## Where each tier is covered in depth

- **Twin (`te`)** deploy mechanics — IR, validator, planner, adapter, destroy — are in
  [03-engine.md](./03-engine.md).
- **Lab (`o2`)** — the surface layer's watch/act/steer planes and the `o2control` thin client —
  is in [05-o2-surface.md](./05-o2-surface.md).
- **Tap** — the typed sensor registry, tap-point vocabulary, and tap-plan playout — is in
  [06-taps-sensors.md](./06-taps-sensors.md).
- **Traffic (`tfc`)** — the persona library and drivers, and host roles — is in
  [07-traffic-personas.md](./07-traffic-personas.md).
- **Exercise (`exr`)** — dual-mode attack, replay, and ATT&CK-coverage measurement — is in
  [08-exercise-measurement.md](./08-exercise-measurement.md).

For the canonical, byte-exact name-shape table and the naming module that produces and checks
every name, see `docs/CONVENTIONS.md` §2.
