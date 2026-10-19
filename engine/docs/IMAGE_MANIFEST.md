# Image Manifest & Binding — Formal Schema (v1)

Authoritative spec for the two-tier image catalog. Companion to `docs/IR_SCHEMA.md` (the IR side of the contract) and `docs/ARCHITECTURE.md` §2 (high-level picture). CHANGELOG entry "Image manifest + binding: two-tier model" dated 2026-05-21 captures the rationale.

**Status:** ratified for v1. Changes to either schema bump `manifest_schema_version` / `binding_schema_version` and require a CHANGELOG entry.

---

## 1. The two-tier model

Every image in the catalog is described by **two YAML files**:

| File | Path | Purpose | Schema |
|---|---|---|---|
| **Manifest** | `images/manifests/<image>.yaml` | What the image is. Platform-independent. | `manifest_schema_version: 1` |
| **Binding** | `images/bindings/<platform>/<image>.yaml` | How the image deploys on a specific platform. | `binding_schema_version: 1` |

v1 ships Proxmox bindings only. The structure exists so AWS, Azure, VMware bindings drop in later as sibling directories under `images/bindings/` without changes to the manifest schema.

```
images/
├── manifests/
│   ├── win11-finance.yaml
│   ├── ubuntu-server-22.yaml
│   ├── kali-rolling.yaml
│   └── vyos.yaml
└── bindings/
    ├── proxmox/
    │   ├── win11-finance.yaml
    │   ├── ubuntu-server-22.yaml
    │   ├── kali-rolling.yaml
    │   └── vyos.yaml
    ├── aws/                            # future
    └── azure/                          # future
```

**One file per logical image; all versions of that image inline in the same file.** Versions are not separate files because the version history is the most-read part of the manifest, and inline-versions means diffs show evolution rather than file appearance/disappearance.

---

## 2. Manifest schema

```yaml
manifest_schema_version: 1

image:
  id: win11-finance                    # required; matches the IR's reference
  description: |
    Windows 11 Pro 23H2 with finance-department baseline:
    Office 365, Teams, Adobe Reader, finance line-of-business apps.

  os:
    family: windows                    # required; one of: windows | linux | bsd
    distribution: windows-11-pro       # required; informational
    version: 23H2                      # required; informational
    architecture: x86_64               # required; x86_64 | aarch64

  versions:                            # required; non-empty; newest first
    - id: v4
      released: 2026-05-10
      status: current                  # current | deprecated | withdrawn
      provides:                        # capability set from the 16-keyword taxonomy
        - windows
        - host:uefi
        - host:secure-boot
        - host:tpm
        - kernel:edr-hooks
        - kernel:dotnet-clr
        - kernel:powershell-5plus
      install_support:                 # which install-action kinds this image accepts
        pkg: [winget, choco]           # list of supported package managers
        config: true
        role: [adds, iis, hyper-v]     # which `role:` shorthand values are valid
        container: false
        script: true
      first_boot_budget: 300           # seconds; planner timeout hint
      size_estimate: 40Gi              # uncompressed; planner disk-space hint

    - id: v3
      released: 2026-02-14
      status: deprecated
      replacement: v4                  # required when status is deprecated or withdrawn
      provides:
        - windows
        - host:uefi
        - host:secure-boot
        - host:tpm
        - kernel:dotnet-clr
        - kernel:powershell-5plus
      install_support:
        pkg: [winget, choco]
        config: true
        role: [adds, iis]
        container: false
        script: true
      first_boot_budget: 300
      size_estimate: 38Gi

  notes:                               # optional; free-form
    - "v4 adds Microsoft Defender for Endpoint pre-installed (kernel:edr-hooks)"
    - "v3 → v4: BitLocker now enabled by default"
```

### 2.1 Top-level

| Field | Required | Type | Notes |
|---|---|---|---|
| `manifest_schema_version` | yes | int | Currently `1`. |
| `image` | yes | object | The image record. |

### 2.2 `image`

| Field | Required | Type | Notes |
|---|---|---|---|
| `id` | yes | string | Matches the ID regex (`^[a-z0-9][a-z0-9_-]{0,62}$`). Referenced by `image:` in the IR. |
| `description` | no | string | Multi-line YAML. |
| `os` | yes | object | OS facts; see §2.3. |
| `versions` | yes | list[object] | Non-empty; newest first by convention. See §2.4. |
| `notes` | no | list[string] | Free-form. |

### 2.3 `os`

| Field | Required | Type | Notes |
|---|---|---|---|
| `family` | yes | enum | `windows` \| `linux` \| `bsd`. Must match the OS-family keyword in every version's `provides:`. |
| `distribution` | yes | string | Specific distro/edition name (e.g. `ubuntu`, `rhel`, `windows-11-pro`). Informational. |
| `version` | yes | string | Specific version (e.g. `22.04`, `23H2`). Informational. |
| `architecture` | yes | enum | `x86_64` \| `aarch64`. |

### 2.4 `versions[*]`

| Field | Required | Type | Notes |
|---|---|---|---|
| `id` | yes | string | Version label (e.g. `v4`, `2026-05-10`). Referenced as `<image-id>@<id>` from the IR. |
| `released` | yes | date | ISO 8601 date. |
| `status` | yes | enum | `current` \| `deprecated` \| `withdrawn`. See §2.5. |
| `replacement` | conditional | string | Required when `status` is `deprecated` or `withdrawn`. References another version `id` in this same manifest. |
| `provides` | yes | list[string] | Capability keywords drawn from the closed 16-keyword set (see `docs/IR_SCHEMA.md` §7). Must include exactly one OS-family keyword matching `image.os.family`. |
| `install_support` | yes | object | Which `install:` action kinds this image version accepts. See §2.6. |
| `first_boot_budget` | yes | int | Seconds. Planner timeout hint: how long the planner should wait for SSH/WinRM to come up after VM start before timing out. |
| `size_estimate` | yes | string | k8s-style size suffix (`Gi`, `Mi`). Planner disk-space hint. |

### 2.5 Lifecycle

| Status | Validator behavior |
|---|---|
| `current` | Pass. Recommended for new scenarios. |
| `deprecated` | Warning. IR validates but the validator emits a deprecation notice pointing at `replacement`. Allowed for reproducibility. |
| `withdrawn` | Error. IR fails validation; error message includes `replacement` as the recommended migration target. |

A withdrawn version may not be removed from the manifest — it stays for historical reference and so the validator can produce useful migration errors.

### 2.6 `install_support`

Mirrors the closed `install:` action set from `docs/IR_SCHEMA.md` §4.1.

| Field | Required | Type | Notes |
|---|---|---|---|
| `pkg` | yes | list[string] \| `false` | List of package managers this image supports (e.g. `[apt]`, `[winget, choco]`). `false` means no package-manager support. |
| `config` | yes | bool | Whether `install: { config: <path> }` actions work. |
| `role` | yes | list[string] \| `false` | List of valid `role:` shorthand names (e.g. `[adds, iis]`). `false` means no role support. |
| `container` | yes | bool | Whether `install: { container: <oci-ref> }` actions work (i.e. is a container runtime present and usable). |
| `script` | yes | bool | Whether `install: { script: <path> }` actions work (almost always `true` if any other install kind is supported). |

The validator cross-checks each `install:` action in the IR against the image's `install_support:`. Action kind not supported → error.

---

## 3. Binding schema (Proxmox)

```yaml
binding_schema_version: 1

binding:
  image: win11-finance                 # references manifests/win11-finance.yaml by id
  platform: proxmox

  versions:                            # one entry per manifest version, in the same order
    - id: v4
      storage:
        type: template                 # currently only `template` is supported
        node: any                      # `any` | <node-name> | [<node-list>]
        volid: local-zfs:vm-9104-disk-0
        template_vmid: 9104

      clone_mode: linked               # linked | full

      cloud_init:
        supported: true
        user: Administrator
        sysprep_required: true         # Windows-specific: image expects unattend.xml

      hardware:
        machine: q35                   # q35 | i440fx
        bios: ovmf                     # seabios | ovmf
        nic_model: virtio
        disk_bus: scsi
        scsi_controller: virtio-scsi-single
        agent: true                    # qemu-guest-agent installed
        tpm:
          enabled: true
          version: "2.0"

      placement_constraints:
        requires_host_caps:            # host:* keywords this image needs from the physical node
          - host:uefi
          - host:secure-boot
          - host:tpm
        avoid_nodes: []                # optional; specific Proxmox node names to skip

      clone_estimate: 90               # seconds (linked clone); planner timing hint

    - id: v3
      storage:
        type: template
        node: any
        volid: local-zfs:vm-9103-disk-0
        template_vmid: 9103
      clone_mode: linked
      cloud_init:
        supported: true
        user: Administrator
        sysprep_required: true
      hardware:
        machine: q35
        bios: ovmf
        nic_model: virtio
        disk_bus: scsi
        scsi_controller: virtio-scsi-single
        agent: true
        tpm:
          enabled: true
          version: "2.0"
      placement_constraints:
        requires_host_caps:
          - host:uefi
          - host:secure-boot
          - host:tpm
      clone_estimate: 110
```

### 3.1 Top-level

| Field | Required | Type | Notes |
|---|---|---|---|
| `binding_schema_version` | yes | int | Currently `1`. |
| `binding` | yes | object | The binding record. |

### 3.2 `binding`

| Field | Required | Type | Notes |
|---|---|---|---|
| `image` | yes | string | Image ID; must match an existing manifest's `image.id`. |
| `platform` | yes | enum | Currently only `proxmox`. |
| `versions` | yes | list[object] | One entry per manifest version. Versions present in the manifest but missing from the binding are non-deployable on this platform; the validator errors with a clear message. |

### 3.3 `versions[*]`

| Field | Required | Type | Notes |
|---|---|---|---|
| `id` | yes | string | Must match a `versions[*].id` in the manifest. |
| `storage` | yes | object | See §3.4. |
| `clone_mode` | yes | enum | `linked` \| `full`. |
| `cloud_init` | yes | object | See §3.5. |
| `hardware` | yes | object | See §3.6. |
| `placement_constraints` | yes | object | See §3.7. |
| `clone_estimate` | yes | int | Seconds. Planner timing hint: how long Proxmox takes to clone this template. |
| `build` | no | object | Image build recipe (base image source, bake/provision steps). Consumed by `axrange image build` (`docs/IMAGE_BUILD.md`); ignored at deploy time. See §3.8. |

### 3.4 `storage`

| Field | Required | Type | Notes |
|---|---|---|---|
| `type` | yes | enum | `template` (currently the only value). |
| `node` | yes | enum \| string \| list | `any` (template lives on shared storage; any node can clone) \| `<node-name>` \| `[<node-list>]` (template lives on local-only storage on specific nodes). |
| `volid` | yes | string | Proxmox volume ID. |
| `template_vmid` | yes | int | Proxmox VM ID of the template to clone from. |

### 3.5 `cloud_init`

| Field | Required | Type | Notes |
|---|---|---|---|
| `supported` | yes | bool | Whether cloud-init customization works. Almost always `true` for v1 images. |
| `user` | conditional | string | Default user for cloud-init customization. Required when `supported: true`. |
| `sysprep_required` | no | bool | Windows-specific. When `true`, the image was sysprep'd and the adapter must provide an unattend.xml. Default `false`. |

### 3.6 `hardware`

| Field | Required | Type | Notes |
|---|---|---|---|
| `machine` | yes | enum | `q35` \| `i440fx`. q35 is required for UEFI+TPM. |
| `bios` | yes | enum | `seabios` \| `ovmf`. ovmf is UEFI. |
| `nic_model` | yes | string | `virtio` is the typical choice for performance. |
| `disk_bus` | yes | string | `scsi`, `virtio`, etc. |
| `scsi_controller` | conditional | string | Required when `disk_bus: scsi`. `virtio-scsi-single` is typical. |
| `agent` | yes | bool | Whether qemu-guest-agent is installed in the image. Influences how the adapter checks readiness. |
| `tpm` | conditional | object | Required when image's `provides:` includes `host:tpm`. `{ enabled: true, version: "2.0" }`. |

### 3.7 `placement_constraints`

| Field | Required | Type | Notes |
|---|---|---|---|
| `requires_host_caps` | yes | list[string] | `host:*` keywords from the capability taxonomy. The planner unions these with IR-node `host:*` requirements when picking placement. |
| `avoid_nodes` | no | list[string] | Specific Proxmox node names to skip (e.g. nodes known to have local issues with this image). |

### 3.8 `build` (image build recipe)

> **Authority:** implementation (Claude Code). This section was landed with the imagebuild slice, exactly as `docs/IMAGE_BUILD.md` §5.1/§9 directed ("the concrete binding build-recipe field schema is defined when the imagebuild slice is implemented and documented in `docs/IMAGE_MANIFEST.md` §3 at that time").

The `build:` section is consumed by the image build pipeline (`docs/IMAGE_BUILD.md`), **not** by the deploy-time planner/adapter. It is **optional** — a binding without it deploys fine but cannot be (re)built via `axrange image build`. Per `docs/IMAGE_BUILD.md` §5.1 the recipe lives here (the binding), keeping the manifest the pure, platform-independent capability contract.

```yaml
build:
  base_image:
    url: https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2
    checksum_url: https://cloud.debian.org/images/cloud/bookworm/latest/SHA512SUMS   # OR `checksum:` inline
    checksum_algo: sha512                                    # sha256 | sha512
    filename: debian-12-genericcloud-amd64.qcow2             # cache name + SHA*SUMS match key
  disk_size: 8G            # optional: resize the imported cloud disk
  memory: 2048             # optional: build VM RAM (MB); default 2048
  cores: 2                 # optional: build VM cores; default 2
  provision: []            # optional: extra bake steps (IR §4.1 install-action dicts)
  probes: {}               # optional: per-capability probe command overrides
```

| Field | Required | Type | Notes |
|---|---|---|---|
| `base_image` | yes | object | The upstream cloud image to build on. See below. |
| `disk_size` | no | string | k8s-style size (e.g. `8G`). Resizes the imported cloud disk; omit to keep the upstream size. |
| `memory` | no | int | Build VM RAM in MB; default `2048`. Transient — not the deployed VM's RAM. |
| `cores` | no | int | Build VM vCPUs; default `2`. Transient. |
| `provision` | no | list[object] | Extra bake steps as IR §4.1 install-action dicts. The slice supports `pkg` / `pkg@version` / inline `script`; `config` / `role` / `container` are reserved (a loud error for now). |
| `probes` | no | map | Per-capability probe overrides: `<capability> → <shell command>` (exit 0 = pass). Defaults cover the closed Linux `provides:` subset. |

`base_image` fields:

| Field | Required | Type | Notes |
|---|---|---|---|
| `url` | yes | string | The upstream cloud image URL (qcow2). |
| `checksum_algo` | no | enum | `sha256` \| `sha512`; default `sha512`. |
| `checksum_url` | conditional | string | A `SHA256SUMS`/`SHA512SUMS`-style file; the entry matching `filename` is used. Required unless `checksum` is given. |
| `checksum` | conditional | string | Inline hex digest (takes precedence over `checksum_url`). One of `checksum`/`checksum_url` is required — the download is always verified. |
| `filename` | no | string | Cache filename + the name matched in the `SHA*SUMS` file; defaults to the URL basename. |

Notes:

- **`qemu-guest-agent` preinstall + auto-start is implicit** — the pipeline always bakes it (`docs/IMAGE_BUILD.md` §3 step 4), so it never appears in `provision:`.
- **Build VM hardware** (`machine` / `bios` / `scsi_controller`) is reused from the version's `hardware:` (§3.6), so the template is built with the same machine type it deploys with (the i440fx-vs-q35 choice is load-bearing — see the debian-12-cloud binding comment).
- **Cluster-environment build params** — storage, NIC bridge, snippet storage, build node, and the broader build credentials — live in the operator's `adapter.conf` as `BUILD_*` keys, **not** in the binding (`docs/IMAGE_BUILD.md` §5.2). The binding stays portable across operators; the environment-specific knobs stay operator-side.

---

## 4. The validator → planner → adapter handoff

When an IR says `image: win11-finance@v4` on node `h-fin` with `requires: [windows]`:

### 4.1 Validator

1. Loads `images/manifests/win11-finance.yaml`. Confirms `v4` exists with `status: current` or `deprecated` (not `withdrawn`).
2. Reads `versions[v4].provides:`. Checks `requires: [windows]` ⊆ `provides:`.
3. Loads the IR's `install:` actions targeted at `h-fin`. For each action, checks the action kind against `versions[v4].install_support:`.
4. Cross-checks OS-family consistency: `manifest.image.os.family` must match the OS-family keyword in `versions[v4].provides:` (sanity check the manifest itself).

The validator does **not** read the binding. The binding is for the planner and adapter.

### 4.2 Planner

1. Loads `images/bindings/proxmox/win11-finance.yaml` for `v4`.
2. Reads `placement_constraints.requires_host_caps:`.
3. Takes the **union** with any `host:*` keywords from the IR-node's `requires:`.
4. Filters live Proxmox cluster nodes by that union; also excludes `avoid_nodes:`.
5. Picks one host using a stable heuristic (e.g. least-loaded). The pick is recorded in the deploy plan so partial-redeploys land the VM on the same host.
6. Uses `clone_estimate` + `first_boot_budget` to set DAG-level timeouts.

### 4.3 Adapter

1. Reads the binding for `v4`.
2. Clones `template_vmid: 9104` on the selected node using `clone_mode: linked`.
3. Configures hardware per the binding (`machine`, `bios`, `nic_model`, `disk_bus`, `agent`, `tpm`, etc.).
4. Pushes cloud-init with `user: Administrator` and, because `sysprep_required: true`, an unattend.xml for the first-boot Windows sysprep specialization phase.

### 4.4 The union model in pictures

```
IR (h-fin):                       Binding (v4):
  requires:                         placement_constraints:
    - windows                         requires_host_caps:
    - host:tpm                          - host:uefi
                                        - host:secure-boot
                                        - host:tpm

   ────────────────┬──────────────────────
                   ▼
  Planner constraint = {windows} (OS) ∪ {host:tpm, host:uefi, host:secure-boot}
                                          (the host:* union)
```

Validator handles `windows` against the manifest. Planner handles the host-caps union against the live cluster.

The IR author **may** omit `host:uefi` and `host:secure-boot` because the binding declares them. The IR author **may** also over-declare for clarity; the result is the same constraint.

---

## 5. Conformance — what an image build must satisfy

The manifest is the **contract** image builds satisfy. A build is correct iff:

1. **OS family is what the manifest says.** A `windows` build that claims `provides: [linux]` is broken.
2. **Every `provides:` capability is actually present.** If the manifest claims `kernel:edr-hooks`, ETW (Windows) or eBPF + audit (Linux) must be functional in the built image. The build pipeline should verify this with a conformance test (running the image and probing each capability).
3. **Every `install_support:` action actually works.** If `install_support.container: true`, the image must have a working container runtime (podman or docker). If `install_support.pkg: [winget, choco]`, both winget and chocolatey must be installed and functional.
4. **`first_boot_budget` and `size_estimate` are honest.** Build-pipeline measurement should validate that real first-boot times don't exceed the declared budget and that the image's actual size is within reasonable bounds of `size_estimate`.

Conformance testing **is in v1** and is performed by the image build pipeline (`docs/IMAGE_BUILD.md` §4): a minimal per-capability probe for every keyword in `provides:`, plus an `axrange` smoke-test that deploys the built template through the real planner+adapter path. A build that fails conformance does not ship. (Items 3 and 4 above — full `install_support:` verification and `first_boot_budget`/`size_estimate` measurement — are honest targets the pipeline grows into; the v1 slice implements the `provides:` probes + smoke-test first. See `docs/IMAGE_BUILD.md` §4 and §9.)

---

## 6. Open follow-ups

- ~~Image build pipeline format~~ — **RESOLVED 2026-06-01 (Q6).** The build pipeline is the `axrange image build` CLI verb (`axrange/imagebuild/`), Linux-only for v1, building on upstream cloud images — **not** Packer. The Proxmox binding gains a build-recipe section (base image source, checksum source, bake steps); its concrete field schema lands with the imagebuild slice as an `implementation (Claude Code)` edit to §3. See `docs/IMAGE_BUILD.md`.
- Manifest signing / supply-chain integrity. Currently the integrity guarantee is "the manifest is in git, the image volid is in shared storage."
- Image deltas / patching across versions — currently each version is fully self-described. Less DRY, much simpler. Reconsider if the image catalog gets large.
- Declaration of pre-installed activity-simulator agents — possibly via a `behavior-agent:*` capability prefix extending the kernel-caps model, or a separate `installed_agents:` field on the manifest version.
- Validator UX for suggesting candidate versions when a requested version lacks needed capabilities (e.g. "v3 lacks `kernel:edr-hooks`; try v4").
- Multi-platform bindings: when adapter v2 ships (AWS or VMware), confirm that no manifest schema changes are needed and the binding tier alone absorbs the new platform.
