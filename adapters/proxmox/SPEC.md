# Proxmox adapter

## What it is

The deploy platform the loop runs on today: it materializes a planned twin as
Proxmox SDN zones, vnets, OVS bridges and QEMU VMs cloned from golden
templates, and carries the capture plane (per-tap `tc` mirror into a keyed
gretap). It serves the **deploy** step of the loop and is the substrate the
traffic, tap and exercise tiers run on.

The op→backend contract is the version-locked `docs/contracts/ADAPTER.md`;
this file is the plugin-level index over it and does not restate it.

## Seam

Implements `axrange.planner.executor.Adapter` — one method,
`run(op, resolved_inputs, ctx) -> outputs`, raising `AdapterError` — as
`ProxmoxAdapter`, which routes each catalog op to a REST backend (what the
Proxmox API exposes) or an SSH backend (OVS/`tc` work the API does not expose).

Beyond `Adapter`, the engine reaches the platform through four further
surfaces that the platform seams in `docs/ARCHITECTURE.md` §13 name as
protocols. Today they are concrete Proxmox modules imported directly:

| Seam | Proxmox module | Used by |
|---|---|---|
| guest exec (ping / exec / file in / file out) | `guest_agent.GuestAgent` over `rest.RestClient` (QGA) | personas, `o2control` exercise and containment, config execution |
| inventory (VM → IP / node, cluster version, live resources) | `resolve.py` | planner access, API targets and status, platform-origin deploys |
| resource naming (tier-tagged names, VMID) | `naming.py` | planner inventory/state/teardown, image build |
| teardown (sweep by tier tag, protect guard) | `teardown.ProxmoxTeardown` | planner, API, `o2control` |

Code: `engine/src/axrange/adapter_proxmox/`. It moves under this folder as its
own package once those four seams are protocols the engine imports instead of
these modules; until then the engine and the adapter import each other and a
relocation would only formalize that cycle.

Selected by a target's `platform: proxmox` (the default when a target names no
platform), see `api/Spec.md` A4.

## Config

`config/adapter.conf` (gitignored; example in `config/adapter.conf.example`),
read by `adapter_proxmox.config.load_config`:

| Key | Purpose |
|---|---|
| `PROXMOX_API_URL`, `PROXMOX_API_TOKEN`, `PROXMOX_VERIFY_TLS` | REST backend; a scoped API token, never a root password |
| `OVS_SSH_HOST`, `OVS_SSH_USER`, `OVS_SSH_KEY` | SSH backend; a key *path* for the operator account on the node |
| `GUEST_SSH_PUBKEY_FILE` | injected into guests by cloud-init |
| `DEFAULT_NODE` | placement when the plan does not choose |
| `MIRROR_GRETAP_TXQUEUELEN` | capture-path queue depth (`0` opts out) |
| `NETWORK_SENSOR_VMID`, `NETWORK_SENSOR_IFACE` | the appliance-adjacent network sensor |

Image bindings: `images/bindings/proxmox/<image>.yaml`
(`docs/contracts/IMAGE_MANIFEST.md`), carrying `template_vmid`, storage and
hardware defaults.

An unrecognized key or a missing required key fails the deploy before any op
runs.

## Acceptance

The capture-flow gate in root `Claude.md` is the acceptance: a twin deploys,
drives traffic, and its tapped traffic reaches a sensor with climbing capture
metrics. The first-slice done-criteria in `docs/contracts/ADAPTER.md` §6 are
the minimal form of the same gate.

- **Automated:** the planner and adapter tests under `engine/tests/` run the
  op routing, naming, idempotent check-then-act and teardown guards against a
  fake adapter and recorded REST/SSH exchanges; no live cluster in the suite.
- **Live:** `axrange te deploy` against a real node, then `axrange tap deploy`
  and the sensor's capture counters.

## Notes

- Capture mirrors at the VM's tap interface, not the bridge, so it is the same
  in Simple and VLAN zones; do not reintroduce a per-VM OVS mirror
  (`docs/contracts/ADAPTER.md` §8 records why).
- Every created resource carries a tier tag in its name and the teardown guard
  refuses foreign tiers; new resource kinds must go through `naming.py`.
- The gretap carries a deep FIFO qdisc on purpose; the node default AQM drops
  mirrored frames.
