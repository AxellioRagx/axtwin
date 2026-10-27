# Image Build Pipeline (v1)

Authoritative spec for the image build pipeline. Companion to `docs/IMAGE_MANIFEST.md` (the contract a build must satisfy) and `docs/ADAPTER.md` (whose REST/SSH transport and auth discipline this pipeline reuses). Resolves OPEN_QUESTIONS Q6; ratified in the CHANGELOG entry dated 2026-06-01 "Q6 resolved: image-build pipeline is `axrange image build`".

**Status:** design ratified for v1; first implementation slice authorized (see §8). The build flow, conformance model, recipe location, auth posture, and lifecycle integration below are the contract. Exact REST endpoints, probe command forms, and the binding build-recipe field schema are filled in during implementation and become CHANGELOG-able `implementation (Claude Code)` refinements (see §9), exactly as `docs/ADAPTER.md` §7 deferred its op-by-op call details.

---

## 1. What the image build pipeline is

A CLI verb — `axrange image build <image>` — in a new submodule `axrange/imagebuild/`. It runs from the **operator's workstation** (the same place the rest of `axrange` runs), drives the Proxmox cluster over the **existing REST + SSH paths** (the adapter's transports), and produces three things:

1. A Proxmox **template** (a `qm template`-converted VM) for the named image.
2. An **updated Proxmox binding** (`images/bindings/proxmox/<image>.yaml`) with the new `template_vmid` written in.
3. A **conformance report** (probe results + smoke-test result).

It is **not** CI. There is no Jenkins, no build host, no daemon — the pipeline is a checked-in, version-controlled, parameterized script. See §2.

The pipeline knows about the manifest and the binding; it does not know about scenarios, the canvas, or AI. Its job is to make a deployed image actually *be* what its manifest (`docs/IMAGE_MANIFEST.md`) declares, and to prove it (§4).

**Scope: Linux (upstream cloud images) + Windows (ISO + autounattend).** The Linux path builds on top of **upstream distro cloud images** (e.g. `debian-12-genericcloud-amd64.qcow2`). The **Windows path** — added 2026-06-16 as **W0** of the Windows-AD identity slice (`docs/SLICE_WINDOWS_AD.md`, CHANGELOG 2026-06-16) — builds **from an install ISO driven by an `autounattend.xml`** (virtio-win drivers injected, `qemu-guest-agent` + cloudbase-init baked in FirstLogonCommands, `sysprep /generalize` seal). The build flow (§3), conformance model (§4), and recipe location (§5) are shared; only steps 2-7 branch by `os_family`. *(This supersedes the original v1 "Windows image building is explicitly out of v1" scope; W0 is implemented + unit-tested, end-to-end live-verify pending the ISOs.)*

## 2. Why a CLI verb, not CI (v1)

v1 targets a **single-lab operator profile**: one cluster (`pxtwin1`), a build-when-needed cadence, an image catalog of roughly 5–10 Linux images. That profile justifies a script, not standing infrastructure.

- CI's value — many builds, many triggers, parallelism, an audit trail, build history — does not apply at this scale yet.
- CI's cost is real: a build host has its own OS to patch, its own plugins to keep compatible, its own secrets plumbing, and it couples "I'm rebuilding my image baseline" to the lab cluster's load and availability.
- A checked-in CLI verb using the same REST/SSH/config paths as the adapter delivers the parts that matter at this scale — reproducibility, version control, and audit-via-`git log` — at a fraction of the operational surface.

The door to CI opens when the project goes **multi-operator** or wants **scheduled rebuilds** for security patching. Neither is v1. (Windows images — once a CI-door candidate — are now built by the same per-image CLI verb, `os_family`-branched, no CI: see the Windows W0 note in §1.) When one of those lands, CI is a CHANGELOG-able addition and this pipeline's flow (§3) is what a CI job would invoke.

## 3. Build flow

Design-level. Exact `qm` / REST forms are implementation (§9).

1. **Read inputs.** Load `images/manifests/<image>.yaml` and `images/bindings/proxmox/<image>.yaml`. The manifest is pure input (declares `provides:`, `install_support:`, lifecycle). The binding supplies build parameters (the build-recipe section, §5; plus `storage`, `node`, `clone_mode`) and **receives** output (`template_vmid`).
2. **Fetch + verify the base image.** Pull the upstream cloud image named in the binding's build recipe; verify it against the recipe's checksum source; cache it locally on the orchestrator (`~/.axrange/cache/`). `--refresh-cache` invalidates.
3. **Stage a build VM.** On the target node, create a temporary build VM (a transient VMID, distinct from the eventual template VMID) and import the cloud image as its disk.
4. **Bake.** Boot the build VM with a cloud-init `#cloud-config` that performs per-image provisioning: **preinstall and enable `qemu-guest-agent`** (this is the operator-side prerequisite `CLAUDE.md` used to carry — the pipeline now owns it), plus the image's declared provisioning steps (the `install:` action kinds from `docs/IR_SCHEMA.md` §4.1 — `pkg`, `pkg@version`, `config`, `role`, `container`, `script`).
5. **Wait for bake completion** via the guest agent (`/agent/exec cloud-init status --wait`), bounded by a build timeout.
6. **Run conformance** (§4). On failure, fail the build (unless `--ignore-conformance`).
7. **Seal.** `cloud-init clean`; reset machine-id; reset SSH host keys; clean shutdown. (These are the seal steps from the 2026-05-29 manual template-rebuild runbook, now scripted.)
8. **Templatize.** `qm template` the build VM. Write the resulting VMID into the binding's `versions[<v>].storage.template_vmid`.
9. **Lifecycle promotion** (§6). Demote the prior `template_vmid` for the image to `deprecated`; the new one becomes `current`. The operator reviews and commits the binding diff.

## 4. Conformance model — minimal + smoke-test

A build is correct iff the built image satisfies its manifest (`docs/IMAGE_MANIFEST.md` §5). The pipeline verifies this in **two layers; both must pass**:

### 4.1 Capability probes (minimal)

One probe per keyword in the manifest's `provides:` set, run inside the booted build VM (via the guest agent) before seal. The Linux capability subset is closed and small — the OS-family keyword (`linux`), the `host:*` keywords, and the `kernel:*` keywords — so the probe set is finite and does not drift. Each probe is a concrete check (e.g. `host:uefi` → `/sys/firmware/efi` exists; a `kernel:*` tracing capability → the relevant tool returns 0). A probe failure means the image does not provide what its manifest claims → build fails.

### 4.2 axrange smoke-test

After templatization, the pipeline deploys a **throwaway one-host (untapped) scenario** against the freshly-built template **through the real planner + adapter path**, and confirms: the VM clones, boots, the guest agent answers, cloud-init reports done, and teardown is clean. This catches integration regressions the capability probes alone would miss (the template is fine in isolation but doesn't clone/boot cleanly through `axrange deploy`). The smoke-test scenario needs no sensor and no mirror — it exercises clone/boot/agent/teardown only; the mirror path is the adapter's concern and is already covered by the deploy slice.

### 4.3 Failure handling

Conformance failure **fails the build** — the manifest is the contract, and a template that fails conformance does not ship (the binding's `template_vmid` is not updated, no lifecycle promotion happens). `--ignore-conformance` is a **debugging-only** escape hatch that proceeds past a failed probe/smoke-test with a loud warning; it is never the default.

## 5. Build recipe location + auth

### 5.1 Recipe lives in the binding

The build recipe — base image source, checksum source, and the bake/provisioning steps — lives in the **Proxmox binding** (`images/bindings/proxmox/<image>.yaml`), not the manifest. The manifest is unchanged and remains the pure, platform-independent **capability contract**; conformance (§4) is what bridges "the manifest says X" to "the built image does X."

Rationale: for a Proxmox-only v1, splitting the recipe into a platform-independent "what to install" tier (manifest) and a platform-specific "which base image / storage" tier (binding) would be premature abstraction — there is no second platform to share the intent with, and the binding already changes per build (`template_vmid` is written there). Putting the whole recipe in the binding is the lower-disruption v1 choice and avoids bumping the ratified manifest schema.

**Deferred:** when a second platform adapter ships (VMware/cloud), lift the platform-independent portion of the recipe (the provisioning intent) up into the manifest so it is shared across bindings. CHANGELOG-able then.

The concrete binding build-recipe field schema is defined when the imagebuild slice is implemented and documented in `docs/IMAGE_MANIFEST.md` §3 at that time (an `implementation (Claude Code)` edit) — this spec fixes the *location* and the *contract*, not the field names.

### 5.2 Auth — its own, broader credential scope

The build pipeline uses a credential scope **broader than the deploy-time adapter token**. Deploy-time auth (`docs/ADAPTER.md` §5) is scoped to SDN + VM lifecycle + VM config; building additionally needs **VM create, disk import, VM destroy** (cleanup of the transient build VM), and **template conversion**. The exact role delta over the deploy token is an implementation finding (§9).

Credential discipline is identical to the adapter's: build credentials live on the orchestrator host (config file or environment), are read by the build process at build time, and are **never** in the manifest, the binding, the IR, or the state store.

## 6. Lifecycle integration

The image lifecycle (`current → deprecated → withdrawn`) is defined in `docs/IMAGE_MANIFEST.md` §2.5. A successful build:

- Writes the new `template_vmid` into the binding for the built version.
- **Demotes** the prior `template_vmid` for that image to `deprecated` — it stays resolvable so older IRs still validate and deploy.
- Marks the freshly-built version `current`.

The operator reviews and commits the binding diff (the build does not commit on the operator's behalf — same git discipline as everywhere else in the project; `CLAUDE.md` Git Policy).

## 7. Cadence

**Manual.** The operator decides when to rebuild — there are no scheduled or triggered builds in v1 (that is the door to CI, deferred per §2). An optional `axrange image build --check-upstream` that **reports** (does not act on) the availability of a newer upstream point release is a possible refinement, not a v1 commitment.

## 8. First implementation slice (authorized 2026-06-01)

- New submodule `axrange/imagebuild/`, parallel to `axrange/adapter_proxmox/` in the `axrange/` monorepo. Reuses the adapter's REST client, SSH backend, and config loading.
- `axrange image build <image>` implementing the §3 flow, with **`debian-12-cloud` as the first target** — reproducing the lab cluster's template 610 (`dtdc-debian-template`) from the upstream Debian 12 generic-cloud image plus the QGA bake, rather than the manual untemplate→modify→retemplate runbook.
- Conformance = the minimal capability probes (§4.1) + the one-host smoke-test (§4.2).

**Done-criteria for the slice:**

1. `axrange image build debian-12-cloud` fetches the upstream cloud image, stages and bakes a build VM, and produces a template with `qemu-guest-agent` preinstalled + auto-start enabled.
2. Conformance passes: the capability probes for the `debian-12-cloud` manifest's `provides:` all succeed, and the one-host smoke-test deploys/boots/tears-down cleanly.
3. The binding's `template_vmid` is updated to the new template, and the prior version is demoted to `deprecated`.
4. A subsequent `axrange deploy` of the **existing tapped slice fixture** (`tests/fixtures/ir/valid_slice/scenario.yaml`) against the rebuilt template completes 25/25 with pcaps landing at the sensor — i.e. the rebuilt template is deploy-equivalent to the hand-built one.

## 9. Open within Q6 (resolved by building, per the `docs/ADAPTER.md` §7 pattern)

Answered empirically during slice implementation, not in further design chat:

- Exact REST endpoints / parameters for VM-create, disk-import, and template-convert.
- Exact per-capability probe command forms for the Linux `provides:` subset.
- The exact build-time REST role delta over the deploy-time adapter token.
- Cloud-image source URLs + checksum sources per distro.
- Ubuntu support — Debian is the slice target; Ubuntu cloud images are structurally identical and should fall out, but this is unverified until built.
- The concrete binding build-recipe field schema (documented in `docs/IMAGE_MANIFEST.md` §3 when the slice lands).
- Whether the smoke-test deploys via subprocess (`axrange deploy`) or by importing the planner directly — an implementation choice, not a contract question.
