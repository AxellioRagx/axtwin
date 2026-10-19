*The image build pipeline and the two-tier image catalog every deployed template satisfies.*

## The two-tier image catalog

Every image the engine can deploy is described by two YAML files, kept deliberately separate:

| File | Path | Purpose |
|---|---|---|
| **Manifest** | `images/manifests/<image>.yaml` | What the image *is* — platform-independent: OS family, capability keywords it `provides:`, supported install-action kinds, lifecycle state. |
| **Binding** | `images/bindings/<platform>/<image>.yaml` | How the image *deploys* on one platform — storage, cloud-init support, hardware defaults, placement constraints, and (for Proxmox today) the build recipe. |

One manifest can have bindings on multiple platforms without changing the manifest — Proxmox ships
today; AWS/Azure/VMware bindings are additive siblings under `images/bindings/`. A node's declared
`requires:` capability keywords are checked against its bound image's `provides:` set before deploy
— this is the contract the validator's capability-intersection pass enforces (see
[03-engine.md](./03-engine.md)) and the contract an image build must satisfy (below). For the full
schema, see `docs/IMAGE_MANIFEST.md`.

Each image also carries a **lifecycle**: `current` → `deprecated` → `withdrawn`. A rebuild promotes
the new template to `current` and demotes the prior one to `deprecated` rather than deleting it, so
scenarios already pinned to the older `template_vmid` keep resolving and deploying.

## The image build pipeline

`axrange image build <image>` reproduces a Proxmox template from an upstream base image plus a
guest-agent bake, driven entirely from the operator's workstation over the same REST + SSH
transports the deploy-time adapter uses (with a broader credential scope — build additionally needs
VM create, disk import, VM destroy of the transient build VM, and template conversion). It is a
checked-in CLI verb, not a CI pipeline: at lab scale a build-when-needed script delivers
reproducibility and a git-diffable audit trail without the operational overhead of standing build
infrastructure.

The build flow:

1. Read the image's manifest (the capability contract) and binding (build parameters: base-image
   source, checksum source, provisioning steps, storage/node placement).
2. Fetch and verify the upstream base image, cached locally.
3. Stage a transient build VM on the target node and import the base image as its disk.
4. Bake it — a cloud-init `#cloud-config` preinstalls and enables `qemu-guest-agent`, then runs the
   image's declared provisioning steps (package installs, config, roles, scripts).
5. Wait for the bake to finish over the guest agent, run conformance (below), then seal the image
   (`cloud-init clean`, machine-id + SSH host key reset, clean shutdown) and convert it to a
   template.
6. Write the new `template_vmid` into the binding, demote the prior template to `deprecated`, and
   promote the new one to `current`. The operator reviews and commits the binding diff.

**`qemu-guest-agent` preinstall is the pipeline's job, not an operator's.** A hand-built template
that skips this pipeline still needs QGA preinstalled and auto-starting — the documented manual
runbook (untemplate → toggle the underlying volume read-write → install QGA → `cloud-init clean` →
reset machine-id and SSH host keys → poweroff → restore read-only → retemplate) is the fallback for
that case, and is exactly the sequence the pipeline automates for every image it builds.

### Two-layer conformance

A build only ships if the resulting template actually satisfies its manifest, checked two ways —
both must pass, and a failure fails the build (the binding's `template_vmid` is not updated):

- **Capability probes** — one concrete in-guest check per capability keyword the manifest
  `provides:` (e.g. a firmware-mode check, a kernel-tracing tool returning success), run over the
  guest agent before the image is sealed.
- **A one-host, untapped smoke-test** — after templating, the pipeline deploys a throwaway
  single-host scenario against the freshly-built template through the **real planner and adapter
  path**: the VM clones, boots, the guest agent answers, cloud-init reports done, and teardown is
  clean. This is the check that catches "fine in isolation, doesn't clone/boot cleanly through a
  real deploy" regressions that capability probes alone would miss — deliberately untapped, since
  it exercises clone/boot/agent/teardown only, not the capture plane.

### Windows images

The same CLI verb and the same manifest/binding/conformance contract cover Windows images too, on
a branch of the build flow specific to `os_family: windows`: rather than an upstream cloud image,
the build drives an **ISO install through an `autounattend.xml`** answer file — virtio-win drivers
injected, `qemu-guest-agent` and cloudbase-init baked in via `FirstLogonCommands`, sealed with
`sysprep /generalize`. This is the path the AD identity archetypes' domain-controller and
workstation images are built from — see [09-content-overlays.md](./09-content-overlays.md) for how
those images turn into a promoted forest at deploy time.

## Golden templates are tier-protected

Every image a build produces is registered as a golden template tagged `img-<image-slug>` in the
naming scheme every other deployed resource follows. That tag makes it structurally immune to every
tier's destroy path — a twin, traffic, tap, or exercise teardown refuses to touch an `img-`-tagged
resource unconditionally, the same way it refuses a standing Lab component. See
[02-twin-model.md](./02-twin-model.md) for the full protect/cascade matrix. This is what lets many
twin deploys and tear-downs clone from the same template without ever risking the template itself.

## Where this fits

The build pipeline is an orchestration consumer of the same substrate the deploy path uses — it
reuses the adapter's REST client, SSH backend, and config loading, and its smoke-test runs a real
planner + adapter deploy, exactly the way the CLI wires `validate → plan → deploy`
([03-engine.md](./03-engine.md)). The templates it produces are what every twin clones from and
what the capture plane's tapped bridges and sensors ultimately run on top of
([04-networking-capture.md](./04-networking-capture.md)). For the formal specs, see
`docs/IMAGE_BUILD.md` (the pipeline contract) and `docs/IMAGE_MANIFEST.md` (the two-tier schema).
