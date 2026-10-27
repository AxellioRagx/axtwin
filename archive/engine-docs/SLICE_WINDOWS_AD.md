# Slice design — Windows AD identity (W0 imagebuild → W1 substrate → W2 identity)

> **STATUS: W0 (Windows imagebuild) IMPLEMENTED + LIVE-VERIFIED on dev45 — `axrange image build windows-server-2022`; re-baked to template VMID 101 with the W1 OOBE fix, 4/4 probes (2026-06-17). W1 (Windows substrate) IMPLEMENTED + LIVE-VERIFIED on dev45 — the clone-OOBE/os-ready path was hardened 2026-06-17 (International-Core + Skip*OOBE in the clone unattend so OOBE auto-completes to IMAGE_STATE_COMPLETE; a SYSTEM-context `SetupComplete.cmd` drops the os-ready marker with no logon — commit 0b606fa, §4). W2 SCOPED 2026-06-16 (§6) — first increment is W2a + W2b; `join-host-to-domain` (now an explicit `node.joins:` IR field, superseding `I1=infer`) / local users / archetype content deferred to later increments. **W2a (`role: adds` DC promotion + `wait-domain-ready`) IMPLEMENTED + LIVE-VERIFIED on dev45 2026-06-17 — deploy `win-w2a-v3-059da956`, 16/16 ops, `(Get-ADDomain).DNSRoot == corp.axr.lab` (§6.1). W2b (`create-domain-user`, domain branch) IMPLEMENTED + LIVE-VERIFIED on dev45 2026-06-17 — deploy `win-w2b-v1-42e030b3`, 17/17 ops, `Get-ADUser alice` = enabled in `corp.axr.lab`, member of `Domain Admins` (§6.2). `join-host-to-domain` (explicit `node.joins:` IR field) IMPLEMENTED + **LIVE-VERIFIED on dev45 2026-06-24** (§6.3) — deploy `w2-join-verify-4bd64c05`, 26/26 ops, `Get-ADComputer ws01` present in `corp.axr.lab` + member-side `PartOfDomain=True`; member hosts join the domain; supersedes `I1=infer`. **The full W2 increment (W2a + W2b + join) is now live-verified.** **Local users (`create-domain-user` local branch — `New-LocalUser` / `useradd`, explicit `host:`) IMPLEMENTED + live-verified on dev45 2026-06-24 — BOTH OS paths (Linux `useradd` + Windows `New-LocalUser`) — §6.6; archetype content (local-user identities in `ad-domain-lab` + `purple-team-range`) added.**
> This is the scope design for the overlay slice's **third increment — Identity**
> (`docs/SLICE_OVERLAY.md` §3/§8), scoped to **Windows Active Directory**. Unlike the
> Data and Defense increments, Identity is not one config-class op on existing
> substrate — the substrate itself (a Windows guest that boots, specializes, and runs
> PowerShell) does not exist yet. So this increment is **three stacked slices**, and the
> AD logic is the smallest of the three.
>
> **Authority:** user — the two load-bearing scope forks were the user's calls
> (2026-06-16): (1) **target Windows AD**, not Samba-AD-on-Linux; (2) **extend the
> imagebuild pipeline for Windows**, not a hand-built template or design-only deferral.
> Decomposition, milestone sequencing, and the channel decision (QGA-PowerShell) are
> Claude Code's. Recorded in CHANGELOG 2026-06-16.
>
> Companion to `docs/SLICE_OVERLAY.md` (the Identity increment this fulfills),
> `docs/IMAGE_BUILD.md` (W0 reopens its explicitly-deferred "Windows out of v1" door —
> §1/§2), `docs/IR_SCHEMA.md` §8.1 (the `overlays.identity` surface — already ratified +
> validated), `docs/OP_CATALOG.md` §5.5 (the three OP-OVL identity ops — already ratified),
> and `docs/SLICE_CONFIG.md` (the `role`-class install kind W2 finally builds).

---

## 1. Why this is three slices, not one

The ratified contract is fully scaffolded — IR `overlays.identity` (§8.1) parses + validates,
the three OP-OVL identity ops have signatures (OP_CATALOG §5.5), and the `domain-ready` /
`overlay-ready` barriers exist. As with Data and Defense, **only emission is missing** at the
overlay layer. But the pivotal fact (OP_CATALOG §5.5, line 673) is that **domain creation is
not an OP-OVL op** — *"An AD domain is created by the domain controller's `provision-app` with
`role: adds`"* — so the DC underneath the three identity ops is an **OP-APP `role`-class**
provisioning, and that is gated out today (`pass1_emit.py:_classify_install` rejects `role`).

Targeting **Windows** AD makes the gap deeper still: a survey of `axrange/` found that the
manifest/binding *declare* Windows (`os_family: windows`, `install_support.role: [adds]`,
`cloud_init.supported: true`, `sysprep_required: true`, `agent: true`) but **every execution
path is Linux-only**:

| Stack layer | Declared (contract) | Implemented (reality) |
|---|---|---|
| Image build | `os_family: windows` | Linux upstream-cloud-image only; **Windows explicitly out of v1** (`IMAGE_BUILD.md` §1/§2) |
| First-boot | `cloud_init.supported`, `sysprep_required` | `_render_userdata` emits Linux `#cloud-config` only; `sysprep_required` captured (`pass1_emit.py:593`) then **ignored** |
| Guest channel | QGA (`agent: true`) | `GuestAgent.write_file` / callers hardcode `sh -c` / `/bin/bash` — **no PowerShell** |
| `wait-os-ready` | `os_family` input threaded | always `cloud-init status --wait`; `os_family` **ignored** (`rest.py:620-652`) |
| `role: adds` | `install_support.role: [adds]` | `_classify_install` rejects `role` (gated, `pass1_emit.py:1117-1141`) |
| Config backend | — | Ansible / apt / bash / python3 only |

And `win11-finance@v4` (what `ad-domain-lab` / `purple-team-range` currently put on `dc01`) is
a **Windows 11 *client*** — it cannot be promoted to a domain controller at all, and dev45 has
no Windows template of any kind. So there is no usable substrate today: the increment must
**build the Windows execution substrate, then the AD logic on top.**

## 2. Decomposition — three work areas

| Slice | What it delivers | Reusable beyond AD? |
|---|---|---|
| **W0 — Windows imagebuild** | `axrange image build` produces a reproducible, sysprepped **Windows Server** template (ISO + autounattend + virtio-win + sysprep seal + Windows capability probes). | Yes — every Windows scenario needs templates |
| **W1 — Windows substrate** | A Windows guest that **boots, specializes, answers QGA, and runs PowerShell config**: first-boot specialization, the QGA-PowerShell channel, a Windows `wait-os-ready`, and Windows `provision-app` (`pkg`/`script`). | Yes — the archetypes' win11 workstations need it |
| **W2 — Windows AD identity** | `role: adds` DC promotion + the OP-OVL identity ops + host→domain join (an **explicit `node.joins:` IR field**, superseding the ratified `I1=infer`) + archetype content. **Complete (W2a+W2b+join+local users+archetypes, §6, all live-verified on dev45 2026-06-24).** | This is the Identity increment proper |

The **channel is QGA-PowerShell, not WinRM.** WinRM hits the same isolated-SDN reachability
wall that already forced `wait-os-ready` off orchestrator-side SSH onto the in-guest QGA
channel (`rest.py:625-626`), and it reuses none of the existing auth. The only new work is
**OS-branching the command/file-write wrapping** on the existing QGA transport.

## 3. Milestones — sequenced by live-verify gate

The W0/W1/W2 areas interlock (W0's conformance smoke-test — `IMAGE_BUILD.md` §4.2 — already
deploys the template through the real planner+adapter and checks "boots + QGA answers +
first-boot done", which *is* W1's first-boot + `wait-os-ready`). So sequence by **what becomes
live-verifiable**, not by rigid slice boundaries:

- **M1 — "a Windows VM boots, specializes, and answers QGA through `axrange deploy`."**
  W0 template production (ISO+autounattend+virtio+QGA+sysprep) **+** W1 first-boot
  specialization **+** W1 Windows `wait-os-ready`. This is also W0's smoke-test passing.
  **First live-verify gate.**
- **M2 — "Windows hosts are configurable."** W1 QGA-PowerShell channel (`write_file` +
  exec wrapping) **+** Windows `provision-app` (`pkg` via winget/choco, `script` via
  PowerShell). Live-verify: a PowerShell `provision-app` runs in-guest, confirmed over QGA.
- **M3 — "an AD domain stands up and identities exist."** **W2a+W2b (§6):** `role: adds` DC
  promotion **+** `wait-domain-ready` **+** `create-domain-user` (domain). Live-verify: DC
  promoted, `wait-domain-ready` clears, a domain user created (`Get-ADUser`). **Extension —
  DONE (2026-06-24):** `join-host-to-domain` (explicit `node.joins:` field) **+** local users
  (both OS paths) **+** archetype content — "a workstation joined" (`Get-ADComputer`) live-verified.

## 4. W0 — Windows imagebuild

The Linux build flow (`IMAGE_BUILD.md` §3) imports an upstream cloud `qcow2` and bakes via
cloud-init. **Windows has no generic cloud image** — it is built from an **install ISO driven
by an `autounattend.xml`** (fully unattended Setup), with **virtio-win** storage/net drivers
injected, the guest tools installed (qemu-guest-agent for Windows; optionally cloudbase-init),
post-install PowerShell customization, then **sysprep `/generalize /oobe /shutdown`** to seal,
then `qm template`.

Extension points (real, grounded in the current tree):

| File | Linux today | Windows branch |
|---|---|---|
| `imagebuild/bake.py` `render_bake_userdata` | `#cloud-config` | render an **`autounattend.xml`** (+ `SetupComplete.cmd` / `FirstLogonCommands` installing QGA + cloudbase-init + provisioning) |
| `imagebuild/bake.py` `_provision_to_cloud_config` | `pkg`→apt, `script`→`/bin/sh` | `pkg`→winget/choco, `script`→PowerShell |
| `imagebuild/bake.py` `seal_commands` | cloud-init clean / machine-id / ssh keys | `sysprep /generalize /oobe /shutdown /unattend:` |
| `imagebuild/bake.py` `_DEFAULT_PROBES` | `/sys` `/proc` `/dev` tests | PowerShell probes (`Get-WindowsFeature AD-Domain-Services`, `Get-Tpm`, `$PSVersionTable`, …) |
| `imagebuild/builder.py` | `os_family` defaults `"linux"` (line 136) | branch the orchestration on it: ISO+autounattend instead of qcow2 import |
| `imagebuild/conformance.py` `build_smoke_scenario_yaml` | `requires: [linux]` | a `requires: [windows]` smoke variant (the M1 gate) |
| `IMAGE_BUILD.md` §3 steps 2–3 | fetch+verify cloud qcow2; import as disk | fetch+verify **install ISO + virtio-win ISO**; boot ISO with autounattend |

New image: a **`windows-server-2022`** manifest (`os_family: windows`; `provides:` incl.
`windows`, `host:uefi`, `host:tpm`, `kernel:powershell-5plus`, `kernel:dotnet-clr`;
`install_support: { role: [adds, …], script: true, pkg: [winget, choco], config: true }`) +
its Proxmox binding (build recipe: ISO source/checksum, virtio-win source, autounattend
parameters; hardware `q35`/`ovmf`/`tpm 2.0`/`virtio-scsi`; `sysprep_required: true`).

Auth: imagebuild already has the broader `qm` scope (`IMAGE_BUILD.md` §5.2) — reuse it (ISO
attach via `qm set --ide2`, no `importdisk`).

**Done (W0):** `axrange image build windows-server-2022` produces a sysprepped template; the
Windows capability probes pass; the smoke-test deploys/boots/QGA-answers/teardown clean
(this last part needs the W1 first-boot + `wait-os-ready` branches — the M1 interlock).

**Implemented 2026-06-16 (CHANGELOG):** `bake_windows.py` (autounattend + provisioning + sysprep
+ PowerShell probes), the `build.windows:` recipe, the `os_family` branch across `builder.py` /
`proxmox_build.py` / `conformance.py`, and the `windows-server-2022` manifest+binding — 26 unit
tests (290 total). The Linux path moved verbatim into `_linux_stage_to_seal`. **Live-verified on
dev45 (2026-06-16):** `axrange image build windows-server-2022` produced template VMID 102 with
4/4 probes passing, after six "resolve by building" fixes (UEFI boot-key; autounattend windowsPE
+ specialize schema — `publicKeyToken`/`versionScope`, `Disk` element order, `/IMAGE/INDEX`,
Shell-Setup element order, ≤15-char NetBIOS ComputerName; virtio drive-letter via multi-letter
`DriverPaths` + a volume-search QGA install — see CHANGELOG 2026-06-16). The Windows smoke-test is
skipped until W1 (the M1 interlock).

**Clone-OOBE + os-ready hardening (2026-06-17, commit 0b606fa — the W2a-unblocking W1 fix).**
The first re-bakes still produced a clone that never specialized: a guest probe showed
`ImageState=IMAGE_STATE_UNDEPLOYABLE`, stuck on the OOBE "Hi there" region screen, with the
os-ready marker never dropped → `wait-os-ready` failed → DC promotion never started. Two
template-side fixes:
- `render_sysprep_unattend` now adds a **`Microsoft-Windows-International-Core`** component
  (Input/System/UI/UserLocale) to the `oobeSystem` pass plus **`<SkipMachineOOBE>` /
  `<SkipUserOOBE>`**. The "Hi there" screen is region/keyboard — governed by International-Core,
  **not** the `<OOBE><Hide*>` flags (which alone left the clone stalled). With these, OOBE
  auto-completes to `IMAGE_STATE_COMPLETE`.
- The os-ready signal moved out of the unattend FirstLogonCommands into a SYSTEM-context
  **`C:\Windows\Setup\Scripts\SetupComplete.cmd`** (`render_setup_complete_cmd` +
  `proxmox_build.stage_setup_complete`, baked before sysprep in `builder.py` step 7). It runs
  once as SYSTEM at the end of Setup — no interactive logon — ensuring QGA auto-start and dropping
  the `axr-osready` marker. This decouples readiness from AutoLogon/FirstLogonCommands/cloudbase-init
  (the clone never reached an interactive logon, so the old marker-drop never ran).

cloudbase-init is **not** a W2a dependency (its W0 MSI install silently never produced a service;
the generalized computer name suffices for a single-DC forest, and SetupComplete.cmd covers
readiness). Re-instating it for per-deploy hostname/password is a follow-up.

## 5. W1 — Windows substrate

- **First-boot specialization.** Branch `op_configure_cloud_init` / `_render_userdata`
  (`rest.py:516`, `:850`) on `os_family`: render the Windows specialization (a cloudbase-init
  userdata if baked in W0, or an `Unattend.xml` specialize pass) — hostname, Administrator
  password (from the binding's `cloud_init.user`/sysprep), ensure QGA is running. Finally
  **honor the `sysprep_required` flag** that `pass1_emit.py:593` already threads and the
  adapter currently ignores.
- **QGA-PowerShell channel.** Branch `GuestAgent.write_file` (`guest_agent.py:112`, today
  `sh -c "cat > …"`) to a Windows file-write (QGA `guest-file-write`, or a PowerShell
  here-string), and the config-backend exec wrapping (`config_exec/backend.py`) to
  `["powershell","-NoProfile","-File", …]` / `-Command`. Keyed on `os_family` threaded from
  the manifest. **Same QGA transport and auth.**
- **`wait-os-ready` Windows branch.** `op_wait_os_ready` (`rest.py:620-652`) currently always
  runs `cloud-init status --wait`; branch on the already-threaded `os_family` input
  (`pass1_emit.py:728-736`) to poll a Windows readiness signal (QGA answers + a
  sysprep/cloudbase-init-complete marker, or a known service).
- **Windows `provision-app`.** Config backend: `pkg`→winget/choco, `script`→PowerShell. (The
  archetypes' win11 workstations need this independent of AD.)

**Done (W1):** a Windows host deploys, specializes, `wait-os-ready` clears, and a
`provision-app` (a winget `pkg` + a PowerShell `script`) runs in-guest, verified over QGA.

## 6. W2 — Windows AD identity

> **Scope ratified 2026-06-16 (Authority: user).** Two forks fix this increment's shape:
> (1) the **first increment is W2a + W2b only** — a DC plus a domain user — not the whole
> identity slice; (2) `join-host-to-domain` (built later) is an **explicit IR field**, *not*
> the ratified `I1=infer` activity-actor inference. The CHANGELOG 2026-06-16 entry records the
> `I1=infer` supersession. Decomposition and code-location mapping below are Claude Code's.

W2 is the AD logic — the point of the whole Windows-AD slice. It splits cleanly because the
ratified ordering chain (OP_CATALOG §5.5/§5.7) already separates **standing the domain up**
(`role: adds` → `wait-domain-ready` → `domain-ready[$fqdn]`) from **populating it**
(`create-domain-user`) from **joining hosts to it** (`join-host-to-domain`). The first increment
takes the first two links; the join and local users come later.

### 6.1 — W2a: `role: adds` DC promotion + `wait-domain-ready`

**The pivotal fact (OP_CATALOG §5.5, line 673):** *domain creation is the DC's `provision-app`
with `role: adds`* — an **OP-APP** op, not an OP-OVL op. So W2a's foundation is lifting the
`role` gate, and the domain stands up through the *existing* `provision-app` path (its Windows
branch from W1.4), not new overlay machinery. `provision-app` stays config-class.

- **Lift the role gate.** `_classify_install` (`pass1_emit.py:1124-1149`) raises on `role` today.
  Branch `role: adds` through to `provision-app`'s `install_actions` (the pkg emission, `:854`),
  carrying the action `{"role": "adds"}` alongside any pkg actions. Other `role:` values stay a
  loud error (a `role`-kind fast-follow). `provision-app` is still one op, config-class — `role`
  is just another install-action kind it dispatches.
- **Resolve the domain FQDN at emit time.** `Install-ADDSForest` needs `-DomainName <fqdn>`.
  The fqdn comes from the DC app's `provides: [{domain: <fqdn>}]` (App.provides is a `list[dict]`,
  `ir/schema.py:107`; IR_SCHEMA §4 example) — thread it into the provision-app op inputs as
  `domain_fqdn`. A `role: adds` app with no `provides.domain` is an EmissionError (a validator
  CAP rule is the follow-on).
- **The Windows promotion body** (`config_exec/backend.py` `_provision_app_windows:188`;
  `render.py` `render_windows_provision`): give `role: adds` a **dedicated reboot-tolerant
  path**, separate from the choco pkg loop — `Install-WindowsFeature AD-Domain-Services
  -IncludeManagementTools` then `Install-ADDSForest -DomainName <fqdn>
  -SafeModeAdministratorPassword <pw> -InstallDns -Force`. `Install-ADDSForest` **reboots the
  DC**, so the op must survive the QGA drop and wait for the guest to come *back* (QGA answers
  again), then confirm. This mirrors W0's `sysprep_seal` shape (`proxmox_build.py:589` — tolerate
  the benign QGA-dies transport error, then poll a power/readiness signal) — except W2a waits for
  the VM to **return**, not stop. Slow (~10-15 min): expect W0-style live-verify iterations.
- **Domain-admin / `SafeModeAdministratorPassword` source.** Lab default for the first increment
  (like W1's `_WINDOWS_DEFAULT_ADMIN_PW = "Axr4nge-Lab!"` in `rest.py`); threading from the IR is
  a follow-on. Decide at build.
- **Emit `wait-domain-ready`** (OP-OVL-010, a *wait* op). After `provision-app[$dc_app]`; polls
  *inside the DC guest* over QGA (`Get-ADDomain` until AD answers — LDAP/Kerberos up). It is the
  predecessor of the `domain-ready[$fqdn]` barrier (OP-BAR-040). One per domain.

> **Implemented 2026-06-16; LIVE-VERIFIED on dev45 2026-06-17.** `render.py`
> `render_windows_adds_promotion` (Install-WindowsFeature AD-DS → Install-ADDSForest
> `-NoRebootOnCompletion`, idempotent-guarded, NetBIOS ≤15) + `config_exec/backend.py`
> `_promote_dc_windows` (run promotion, drive the reboot, wait for the guest to return via a
> boot-id change — the QGA-drop/return handling) and `_wait_domain_ready` (poll `Get-ADDomain`).
> `pass1_emit` lifts `role: adds` through `_classify_install` into `provision-app`, resolves the
> forest FQDN from the DC app's `provides:[{domain}]`, and emits `wait-domain-ready` (scope
> `{domain, dc_app}`) + the `domain-ready` barrier. **Registry:** `wait-domain-ready` is
> config-class (ConfigBackend, like `wait-actor-ready`); two star-tail edge corrections
> (`provision-app[$dc_app@*]`, `wait-domain-ready[$domain@*]`) — see OP_CATALOG §5.5. 16 tests in
> `tests/test_windows_ad.py`; 321 total.
>
> **Live-verify (dev45, PVE 9.2, 2026-06-17):** deploy `win-w2a-v3-059da956` — 16/16 ops
> COMPLETED, 0 failed; inside the DC `(Get-ADDomain).DNSRoot == corp.axr.lab` (NetBIOS `CORP`,
> `Windows2016Domain`, ADWS + NTDS running). **The blocker was the W1 substrate, not W2a:** the
> sysprepped clone never reached `IMAGE_STATE_COMPLETE` (it stalled on the OOBE "Hi there" region
> screen → `IMAGE_STATE_UNDEPLOYABLE`), so the os-ready marker never dropped and `wait-os-ready`
> failed. Two template-side fixes (commit 0b606fa, CHANGELOG 2026-06-17) cleared it — see the W1
> note in §4 below. SafeMode password is the lab default `_WINDOWS_DEFAULT_SAFEMODE_PW`; the DC
> runs its own AD-integrated DNS (Install-ADDSForest `-InstallDns`).

### 6.2 — W2b: `create-domain-user` (domain branch) + the identity overlay

- **Unblock the identity reject** (`pass1_emit.py:299`) and add an identity branch to
  `_emit_overlays` (`:1004`), mirroring the data/defense emission already there.
- **Emit `create-domain-user`** (OP-OVL-020) for each non-`external` identity that has a
  `domain:`. Scope `{identity, host}`; resolve `target_host_id` = the **DC host** (the host whose
  `role: adds` app `provides` that `identity.domain`). After `domain-ready[$domain]`. The
  **local-user branch** (`New-LocalUser` / `useradd`, no-`domain:` identities) is **IMPLEMENTED +
  live-verified** (2026-06-24, §6.6) — bound to an explicit `host:`, after `host-ready[$host]`.
- **The barriers.** `domain-ready[$fqdn]` (OP-BAR-040, predecessor `wait-domain-ready`) and
  `overlay-ready[identity:$id]` (OP-BAR-050, predecessor `create-domain-user`). The first
  increment has no consumer of `overlay-ready[identity:*]` yet (the activities that would consume
  it are deferred), but emit it for contract-completeness.
- **The Windows body** (`config_exec` backend, over QGA-PowerShell on the **DC**, after
  `domain-ready`): `New-ADUser -Name <id> -AccountPassword <pw> -Enabled $true`, plus
  `Add-ADGroupMember` per `groups:` entry. Keep the first cut to groups + enabled; richer
  `privileges:` mapping is a follow-on.

> **Implemented + LIVE-VERIFIED on dev45 2026-06-17 (W2b).** `pass1_emit`
> lifts the identity reject and adds an identity branch to `_emit_overlays`: per non-`external`
> identity with a `domain:`, it resolves the DC host (`_dc_host_for_domain` — the `role: adds` app
> providing that domain) and emits `create-domain-user` scoped **`{identity, host, domain}`** —
> the `domain` scope key is what lets the registry `after domain-ready[$domain]` resolve (an
> adjustment to §6.2's `{identity, host}`) — with `target_vmid` from the DC's clone-vm. `render.py`
> `render_create_domain_user` renders an idempotent `New-ADUser` (`Get-ADUser` guard) + best-effort
> `Add-ADGroupMember` per group; `config_exec/backend.py` `_create_domain_user` runs it on the DC
> over QGA-PowerShell with a lab-default password (`_WINDOWS_DEFAULT_USER_PW`, enabled +
> non-expiring). `external:` identities emit no op (decision I2); no-`domain:` (local) identities are
> a clear deferred EmissionError. **`overlay-ready[identity:*]` is *not* emitted** — matching the
> existing data/defense overlays (nothing consumes it under the inline-steps activity MVP); it lands
> when an activity depends on a specific identity. 10 tests in `tests/test_windows_ad.py`; 331 total.
> Plan-verified against the dev45 catalog: a DC + a domain user emit `provision-app →
> wait-domain-ready → domain-ready → create-domain-user`.
>
> **Live-verified on dev45 2026-06-17.** `deploy-dev45/scenario-w2b-verify.yaml` (DC `dc01`
> providing `corp.axr.lab` + `overlays.identity` user `alice` in `Domain Admins`) → deploy
> `win-w2b-v1-42e030b3` → `Result: COMPLETED`, 17/17 ops, 0 failed (first try, no fixes — the W2a
> DC promotion ran unchanged). On the DC over QGA-PowerShell: `Get-ADUser alice` →
> `SamAccountName=alice, Enabled=True, DN=CN=alice,CN=Users,DC=corp,DC=axr,DC=lab`;
> `Get-ADPrincipalGroupMembership alice` → `Domain Users, Domain Admins` (the `Add-ADGroupMember`
> group-add landed). This closes the W2a+W2b done-criteria (§6.5).

### 6.3 — `join-host-to-domain`: explicit IR field, not inference (IMPLEMENTED + LIVE-VERIFIED)

Host→domain join is an **explicit IR field** —
`node.joins: <domain-fqdn>` on the joining node — **not** the ratified `I1=infer` (infer a join
from an activity placing a domain actor on a host, OP_CATALOG §5.5/§5.6). This is a **deliberate
deviation**, recorded in the CHANGELOG (Authority: user) as superseding decision `I1=infer`:

- **Deterministic** — the join is declared, not derived from activity placement (which may not
  exist, or may place several actors on the host).
- **No activity-layer coupling** — identity bring-up no longer depends on the activity layer's
  actor→host resolution (§5.6), which the OP-ACT engine does not implement for domain actors.
- **A small, honest IR change** — one optional `joins:` field on `Node` (`ir/schema.py:71`), vs.
  a planner-internal inference invisible in the IR. The op signature (OP-OVL-030) is unchanged;
  only its *trigger* moves from inferred to declared.

`deploy-behavior-agent`'s conditional `join-host-to-domain[$host@$domain]` predecessor
(OP_CATALOG §5.6) then keys off the explicit field rather than the inference. The same explicit
shape is the likely answer for **local-user host binding** (§6.2's deferred branch): the
`IdentityEntry` (`ir/schema.py:177`) has **no `host:` field**, and an explicit `host:` on local
identities parallels the explicit `joins:` decision — decided when local users are built.

> **Implemented 2026-06-17 (W2 join — unit-tested + plan-verified; live-verify pending).** A new
> optional `joins: <fqdn>` on `Node` (`ir/schema.py`; Pydantic `extra=allow`, no validator break).
> `pass1_emit._emit_overlays` emits `join-host-to-domain` per joining node (scope `{host, domain}`),
> gated on `domain-ready[$domain]` + `host-ready[$host]`, threading the domain's DC IP
> (`_dc_host_for_domain` → the DC node's static IP — the host's DNS for the join) and the joining
> host's vmid. A non-Windows `joins:` or a domain with no DC is a clear EmissionError.
> `render.py` `render_join_to_domain` points the host's physical-NIC DNS at the DC, then
> `Add-Computer -DomainName -Credential <NETBIOS>\Administrator -Force` (idempotent — skips if
> already joined); `config_exec/backend.py` `_join_host_to_domain` runs it and drives the
> reboot-tolerantly (reusing W2a's boot-id wait). **Deterministic credential:** the W2a promotion
> now sets the local Administrator password to the lab default (`net user Administrator`), so the
> post-promotion *domain* Administrator credential is reliable. 9 tests in `tests/test_windows_ad.py`;
> 340 total. Plan-verified against the dev45 catalog (`deploy-dev45/scenario-w2-join-verify.yaml`):
> a DC + a member emit `provision-app → wait-domain-ready → domain-ready → join-host-to-domain`.
>
> **LIVE-VERIFIED on dev45 2026-06-24.** `deploy-dev45/scenario-w2-join-verify.yaml` — a minimal
> Windows-only Simple-zone topology (no router/egress): `dc01` + `ws01`, both cloning
> `windows-server-2022@v1` (template 101), `ws01` carrying `joins: corp.axr.lab`. Deploy
> `w2-join-verify-4bd64c05` → **`Result: COMPLETED`, 26/26 ops, 0 failed** — the full W2 chain ran
> end to end: both clones specialized through the W1 substrate → `provision-app[ad-dc@dc01]` promoted
> the `corp.axr.lab` forest (reboot-tolerant) → `wait-domain-ready` cleared → `create-domain-user`
> made `alice` → **`join-host-to-domain[corp.axr.lab@ws01]` joined the member.** Verified over
> QGA-PowerShell: on the DC `Get-ADComputer ws01` → `CN=WS01,CN=Computers,DC=corp,DC=axr,DC=lab`,
> `DNSHostName=ws01.corp.axr.lab`, Enabled; on the member `Win32_ComputerSystem.PartOfDomain=True,
> Domain=corp.axr.lab`. `Get-ADUser alice` = enabled, `Domain Users + Domain Admins` (W2a+W2b
> re-confirmed in the same run). `axrange destroy` tore it down. **First try, no fixes** — the
> in-flight static-IP + hostname + SDN-ordering fix (commit `4f3fc0d`) that the prior attempt was
> interrupted before validating held. **This closes the W2 §6.3 done-criterion — the entire W2
> increment (W2a + W2b + join) is now live-verified on dev45.**

### 6.4 — Local users + archetype content (IMPLEMENTED + live-verified, see §6.6)
- ~~**Local users**~~ → **DONE (§6.6).** `create-domain-user`'s local branch (`New-LocalUser` on
  Windows / `useradd` on Linux) keyed off an explicit `host:` on no-`domain:` identities.
- ~~**Archetype content**~~ → **DONE.** `ad-domain-lab` / `purple-team-range` already carry a
  `windows-server-2022@v1` DC + `overlays.identity` domain users + win11 join clients (W2a/W2b/join
  commits); §6.6 adds local-user identities to both (a local workstation admin; a Linux service
  account) so the archetypes exercise the full identity surface.

### 6.5 — Done-criteria (W2a + W2b)

A minimal scenario on dev45 (reuse the W1.5 deploy recipe): a `windows-server-2022` DC node + an
app `install: [{role: adds}]` with `provides: [{domain: <X>}]` + one `overlays.identity` domain
user in `<X>`. **Deploy → the DC clones/specializes (W1) → `provision-app[dc, role:adds]` promotes
the DC (reboot-tolerant) → `wait-domain-ready[<X>]` clears → `create-domain-user` runs on the DC →
`Get-ADUser` confirms the user exists in the domain.** (DC networking — its own DNS, likely a
static IP, single-DC single-forest — resolved by building; see §7.)

### 6.6 — Local users (IMPLEMENTED + LIVE-VERIFIED 2026-06-24)

The local-user host-binding fork (§6.3) resolved the same explicit way as `node.joins:`: an
**optional `host:` on a no-`domain:` identity** (`IdentityEntry.host`). `create-domain-user`
(OP-OVL-020) is now the single op for both — its catalog signature already said *"For identities
with no domain:, creates a local user on the relevant host"* and *"`domain-ready[$domain]` for domain
users and `host-ready[$host]` for local users — the planner selects per entry."*

- **Emission** (`pass1_emit._emit_overlays`): a no-`domain:` identity emits `create-domain-user`
  scoped **`{identity, host}`** (no `domain`), `target_host_id` = the explicit `host:`, `os_family`
  resolved from that node's image, `target_vmid` from the host's clone-vm — and **no `domain_fqdn`
  input** (the backend's local-vs-domain switch). A no-`domain:`/no-`host:` identity is a clear
  EmissionError; an unknown `host:` is rejected.
- **Conditional `after`** (registry): `create-domain-user.after = (domain-ready[$domain],
  host-ready[$host])`. `_resolve_pattern` drops a pattern whose scope var the consumer lacks, so a
  domain user resolves both (host-ready[$host=DC] is a redundant-but-valid predecessor) and a local
  user resolves only `host-ready[$host]`.
- **Render** (`render_create_local_user`): Windows → `New-LocalUser` (idempotent `Get-LocalUser`
  guard) + `Add-LocalGroupMember` per group; Linux → `useradd -m -s /bin/bash` (idempotent `id`
  guard) + `chpasswd` + `usermod -aG` per group. **Backend** (`_create_local_user`): delivers per
  `os_family` over the same QGA channel (`-File` PowerShell on Windows, `sh` on Linux), runs on the
  host's vmid, fail-loud on non-zero exit.
- **Archetype content:** `ad-domain-lab` gains `wsadmin` (local `Administrators` on the win11 `ws01`
  — the local-admin-reuse / PtH lateral target); `purple-team-range` gains `wks-admin` (local
  Windows admin on `ws1`) + `ops-local` (local Linux `sudo` account on `file-srv`), exercising both
  OS paths. Both archetypes validate + plan (domain users → `create-domain-user[$domain@$dc@$id]`,
  local users → `create-domain-user[$host@$id]`).

> **LIVE-VERIFIED on dev45 2026-06-24 (Linux path).** `deploy-dev45/scenario-localuser-verify.yaml`
> — one debian host, `overlays.identity: opsuser (host: h-a, groups: [sudo])`. Deploy
> `localuser-verify-d2c6c2fa` → `COMPLETED`, 14/14 ops; in-guest `id opsuser` →
> `uid=1001(opsuser) … groups=…,27(sudo)`, home `/home/opsuser`, shell `/bin/bash` (the group-add
> landed); `axrange destroy` clean.
>
> **LIVE-VERIFIED on dev45 2026-06-24 (Windows path too).** `deploy-dev45/scenario-winlocaluser-verify.yaml`
> — one `windows-server-2022@v1` host (template 101), no DC/domain, `overlays.identity: wsadmin
> (host: ws01, groups: [Administrators])`. Deploy `winlocaluser-verify-09a8c9f6` → `COMPLETED`,
> 14/14 ops; in-guest `Get-LocalUser wsadmin` → Enabled, never-expiring, and local `Administrators`
> contains `WS01\Administrator, WS01\wsadmin` (the `Add-LocalGroupMember` landed); `axrange destroy`
> clean. **Both OS paths of the local-user branch are now live-verified.** 7 new tests in
> `tests/test_windows_ad.py` (446 total).

## 7. Open questions (resolve by building, per the `ADAPTER.md` §7 / `IMAGE_BUILD.md` §9 pattern)

**Resolved by W0/W1 (live-verified on dev45, 2026-06-16):**

- ~~**Windows Server SKU + ISO source + licensing.**~~ → **2022 eval ISO** (180-day), source +
  checksum in the `windows-server-2022` binding recipe.
- ~~**First-boot mechanism.**~~ → **cloudbase-init** (W1.3 `_render_windows_userdata`).
- ~~**QGA on Windows** (file-write API, exec wrapping).~~ → PowerShell `[IO.File]::WriteAllText`
  for `write_file`, `powershell -Command` for `shell_exec` (W1.1). The **reboot-reconnect**
  half stays open for W2 (`role: adds` promotion) — addressed by §6.1's reboot-tolerant path
  (mirror W0 `sysprep_seal`).

**Genuinely open for W2 (resolve at the W2a/W2b live-verify):**

- **DC networking.** AD wants the DC as its own DNS, likely a static IP — confirm the single-DC
  single-forest assumptions, functional level, and DNS wiring.
- **Reboot handling for `role: adds`.** `Install-ADDSForest` reboots; the `provision-app` op must
  survive the QGA drop and wait for the guest to return (§6.1 mechanics).
- **Domain-admin / `SafeModeAdministratorPassword` source.** Lab default vs. thread from the IR
  (§6.1) — decide at build.
- ~~**Local-user host binding.**~~ → **RESOLVED, built + live-verified (§6.6, 2026-06-24):**
  `IdentityEntry` gained an optional `host:` field, resolved the same explicit way as
  `node.joins:` — decided for both OS paths (Windows `New-LocalUser` / Linux `useradd`).

## 8. Deferred / out of this effort

~~**Deferred to later W2 increments (will be built — §6.4):** `join-host-to-domain` …; local
users …; archetype content.~~ → **all three DONE** — `join-host-to-domain` (§6.3),
local users both OS paths (§6.6), and archetype content (§6.4), each implemented +
live-verified on dev45 (2026-06-24).

**Out of this effort entirely (at the time; three have since shipped as separate slices):**

- ~~The AD **attack chain** (OP-ACT: LLMNR/Responder, Kerberoast, BloodHound/DCSync)~~ →
  **shipped + LIVE-VERIFIED on dev45 2026-06-24** as `docs/SLICE_AD_ATTACK.md` — riding this
  increment's identities unchanged; `secretsdump` DCSync succeeded against the DC built here.
- ~~The AD **defense overlay**~~ → **shipped + LIVE-VERIFIED on dev45 2026-06-24**, two planes:
  network IDS (an in-scenario `suricata-sensor` with an AD ruleset, `docs/SLICE_OVERLAY.md`) and
  **host-side** (native Windows Security-log audit — 4768/4769 Kerberos, 4662 DCSync via a
  domain-root SACL — the `install-defense-agent` Windows branch, §6.6-adjacent; not Sysmon/Wazuh
  as originally sketched here).
- **"Vulnerable-AD" weakening** — `config`/`role` follow-on. **Shipped + LIVE-VERIFIED on dev45
  2026-06-24** (SPN/OU/ACL/delegation weakening — `IdentityEntry.spn`/`asrep_roastable`/`ou`/
  `password`/`acl`/`delegation`; CHANGELOG 2026-06-24/25).
- **Multi-DC / RODC / child domains / trusts**, Group Policy / GPOs, ADCS — single-DC forest only.
- A **Linux/Samba-AD** `role` branch — a later alternative path, not this effort.
- **WinRM** as a channel — rejected in favor of QGA-PowerShell (§2).
