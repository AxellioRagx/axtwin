# LESSONS — feed-forward institutional memory (`LES-NNN`)

A living register of **durable, actionable lessons** the project has paid for once and should never re-learn: bug-classes, silent-success traps, platform/tool gotchas, ordering invariants, dead-ends, reversal hazards, reuse rules, and positive patterns. Each entry is a **rule future work honors**, not a diary — the `code-writer` and `code-reviewer` subagents consult it, a `PreToolUse` hook auto-injects the entries whose `Scope:` matches the file being edited (`scripts/inject_lessons.py`), and the `doc-writer` appends a new entry whenever a bug fix or a review finding surfaces something durable.

**Positioning** (distinct from its neighbors):

- `docs/CHANGELOG.md` — what *was decided/built* (chronological, Authority-marked). Lessons link back via `Origin:`.
- `docs/PRODUCT_GAPS.md` — known *deficiencies* still open (`GAP-NNN`).
- **`docs/LESSONS.md` (this file)** — *actionable rules* to apply going forward, extracted from the above so a hook/agent can consume them.
- User/team working *preferences* live in the memory store (`~/.claude/projects/-home-rocky-axRange/memory/`), not here.

**Never delete an entry.** If a lesson is superseded, keep it and add a forward-pointing note (same discipline as `PRODUCT_GAPS.md`). IDs are stable — never renumber or reuse.

## Entry format

```
## LES-NNN — <short imperative title>

**Class:** bug-class | silent-success-trap | platform-quirk | tool-gotcha | ordering-invariant | dead-end | reversal-hazard | reuse-rule | pattern | convention | perf-cost | security | env-fact
**Scope:** <file / module / pattern a hook matches on — e.g. `axrange/adapter_proxmox/ssh.py`, `adapter_proxmox`, "PVE 9.2", "any in-guest QGA write">
**Consumers:** <subset of: code-writer, code-reviewer, tester, live-verifier>
**Rule:** <the actionable directive — what to do or not do>
**Origin:** <CHANGELOG date / GAP-NNN / commit back-link>
```

`Scope:` drives auto-injection: path/module tokens are matched against the file being edited; phrase-only scopes ("PVE 9.2") ride the `--index` load at SessionStart instead.

---

## LES-001 — Assert capture is live; never trust a green deploy for capture

**Class:** silent-success-trap
**Scope:** OP-MIR ops (`attach-gre-tunnel`/`install-ovs-mirror`), `verify-capture`, any tap→mirror→sensor path
**Consumers:** code-writer, tester, live-verifier
**Rule:** Treat "mirror op returned success" as meaningless for capture. Every capture path must prove a frame reached the sensor (inject a keyed sentinel, confirm it decapped) before `mirror-ready`/`start-activity` proceed; a deploy that reports all ops COMPLETED with zero frames is the expected failure mode, not an edge case.
**Origin:** CHANGELOG 2026-06-03 (SDN broke the OVS mirror: 25/25 COMPLETED, zero frames, silently), 2026-06-04 (Option 1 liveness gate), 2026-06-19 (`verify-capture` OP-MIR-040)

## LES-002 — Deliver in-guest file content as base64-in-command, never `input-data`

**Class:** platform-quirk
**Scope:** `adapter_proxmox/guest_agent.py` `write_file`; any QGA `agent/exec` in-guest write on PVE 9.2 (Linux and Windows)
**Consumers:** code-writer, code-reviewer, live-verifier
**Rule:** Never pass file bytes via the QGA `input-data` parameter — PVE 9.2 500s with `Wide character … Agent.pm line 135` (`encode_base64($input_data)`) on any non-ASCII content, killing every write. Embed the payload as pure-ASCII base64 in the command args instead: Linux `printf %s <b64> | base64 -d > path`; Windows base64 + `[IO.File]::WriteAllBytes`. This covers verify-capture, deploy-sensor, provision-app, seed-dataset, and all AD config writes. **Amendment (2026-09-21):** the base64-in-command args route has its OWN ceiling — PVE `agent/exec` refuses a **single argument ≥ 131072 bytes**, reported as `HTTP 596 Broken pipe` (names the transport, not the size; binary-searched to the exact byte on PVE 9.2). Hit by a 161 KB sink-corpus installer that base64'd into one argument. Fix: `write_file` must **chunk** — split the base64 across multiple arguments, and across multiple requests past ~192 KiB, verifying the delivered byte count in-guest — and a large payload should be shrunk at the source where possible (gzip+base64 took the same corpus from 161 KB to 54,550 bytes). Any new large in-guest write must budget for both ceilings: no non-ASCII in `input-data`, and no single argument ≥ 128 KiB either.
**Origin:** CHANGELOG 2026-06-23 (B3, Linux fix), 2026-06-24 (Windows `write_file` regression), 2026-09-21 (the 131072-byte argument ceiling, chunked `write_file` + gzip+base64 corpus)

## LES-003 — Make SDN `create-*` idempotency match every PVE wording AND check-then-act

**Class:** bug-class
**Scope:** `adapter_proxmox/rest.py` `op_create_*` (bridge/zone/vnet/subnet); the `apply-sdn-config` barrier
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Guard each SDN create with a GET pre-check (`_subnet_exists`/`_zone_exists`/…) as the primary idempotency mechanism, and a broadened string backstop matching all PVE collision phrasings — `already exists` / `already defined` (PVE 9.2 subnets) / `already registered`. A single unguarded create-failure halts the shared `apply-sdn-config` barrier, so no vnets materialize, VMs boot with no network, and `configure-router` hangs on IP discovery — a narrow sensor-path miss wedges the whole deploy.
**Origin:** GAP-004; CHANGELOG 2026-07-01

## LES-004 — Assert resource state in-band at deploy time; post-start hooks don't fire on adopted VMs

**Class:** silent-success-trap
**Scope:** any op relying on a PVE `post-start`/`pre-stop` hookscript (`install-ovs-mirror`, tap re-assert); Model C check-then-act ops
**Consumers:** code-writer, code-reviewer, live-verifier
**Rule:** Because Model C adopts an already-running VM (start-vm is a no-op), the PVE post-start hook never fires on a redeploy — so any state a hook installs (OVS mirror, tap membership) must ALSO be asserted in-band when the op runs and the VM is already `running`. Install the hook for future boots and check-then-act now; don't assume boot will apply it.
**Origin:** CHANGELOG 2026-05-29 (`install-ovs-mirror` assert-at-deploy-time; `apply-sdn-config` re-assert taps after ifreload)

## LES-005 — On VyOS `vbash` apply, gate on the SSH exit code first, then read stderr markers — not the commit exit code

**Class:** silent-success-trap
**Scope:** `adapter_proxmox/ssh.py` `_configure_vyos_router_ssh`/`_configure_routing`; `ssh.ssh_connect_failed`
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Check the SSH transport result (rc 255 / `Permission denied` / connection markers) BEFORE scanning vbash output — otherwise an auth/connect failure with no VyOS error marker false-succeeds against a wholly unconfigured router. Then detect real config errors by VyOS stderr markers, NOT the exit code: `commit` returns non-zero on an idempotent "No configuration changes" re-run.
**Origin:** CHANGELOG 2026-06-30 (finding B, SSH-failure masking), 2026-06-10 (commit stderr-marker detection)

## LES-006 — Write cross-op `after:` predecessors in star-tail form `[$x@*]`

**Class:** ordering-invariant
**Scope:** `ops/registry.py` `after:` clauses; pass-3 ordering resolver
**Consumers:** code-writer, code-reviewer, tester
**Rule:** When an op must order after a multi-scope producer, use the star-tail pattern (e.g. `provision-app[$host@*]`, `wait-domain-ready[$domain@*]`, `deploy-behavior-agent[$act@*]`). The pass-3 resolver matches on the producer's FULL scope-value set, so a single-scope `[$host]`/`[$act]` never resolves against a `{app,host}`- or `{domain,dc_app}`-scoped op and the edge silently never forms (latent for any op not yet emitted).
**Origin:** CHANGELOG 2026-06-12 (OP-ACT), 2026-06-16 (W2a `wait-domain-ready`/`domain-ready`)

## LES-007 — `--fresh` does NOT re-run `completed` ops; op_id is keyed by plan, not deploy — **RESOLVED 2026-07-07**

**Class:** silent-success-trap
**Scope:** `planner/executor.py` resume short-circuit (`get_op(op_id)`); `axrange deploy --fresh`
**Consumers:** code-writer, tester, live-verifier
**Rule:** Don't rely on `--fresh` to rebuild cluster state — it mints a new `deploy_id` but the resume check keys off the plan-based `op_id`, so ops marked `completed` under a prior deploy of the same plan are skipped. If resources were destroyed out-of-band, `--fresh` can report "25 ok" having created nothing. To truly redeploy, clear the prior op rows for the plan (or scope the resume check to the current `deploy_id`).
**Origin:** CHANGELOG 2026-06-04 (adjacent finding under the tc/gretap entry)

> **RESOLVED (CHANGELOG 2026-07-07).** The `ops`/`op_attempts` tables now key on a composite
> `(deploy_id, op_id)` PRIMARY KEY (`SCHEMA_VERSION=2` + a fail-loud `StateStoreSchemaError` guard on
> an old DB) — two deploys of one plan hold independent op rows, so `--fresh` no longer skips
> `completed` ops from a prior deploy, and the workaround here (clear the state DB) is no longer
> necessary. **This also surfaced the other half of the bug, previously undocumented:** a `destroy`
> after `--fresh` used to sweep **nothing** (no rows uniquely belonged to the new `deploy_id`) yet
> still reported DESTROYED — a live-resource leak with a false-success report, the exact failure
> shape LES-001's "never trust a green deploy" rule warns about. **The lesson's *pattern* stays
> instructive even though the specific bug is fixed:** any identity/PK scheme keyed on "the thing
> that's supposed to be stable" (here, `op_id` stable *across replays of the same plan*, §10 of
> `docs/PLANNER.md`) can silently alias two *different* real-world instances (two deploys) that
> happen to share that stable key. When adding a new state-store table keyed by a plan-derived
> deterministic ID, ask explicitly whether `deploy_id` (or the equivalent per-instance identity)
> needs to be *part of the key*, not just a column. See LES-030 for the generalized rule and
> `docs/PLANNER.md` §7.2.


## LES-008 — Use `machine: pc` (i440fx) for Debian cloud images; q35 hides `/dev/sr0`

**Class:** silent-success-trap
**Scope:** `images/bindings/proxmox/*.yaml` `hardware.machine`; Debian generic-cloud templates
**Consumers:** code-writer, live-verifier
**Rule:** Bind Debian cloud images to `machine: pc`, not q35. Under q35 the Debian 6.1 cloud kernel doesn't expose the cidata ISO as `/dev/sr0`, so `ds-identify` finds no datasource, the systemd generator masks every cloud-init service, and the ENTIRE first-boot/provision pipeline goes silent with no error.
**Origin:** CHANGELOG 2026-05-29 (Slice MVP bring-up on pxtwin1)

## LES-009 — `host-ready` must list `start-vm` as a predecessor for `agent:false` nodes

**Class:** ordering-invariant
**Scope:** `ops/registry.py` `host-ready` barrier; VyOS / any `agent: false` image
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Keep `start-vm[$host]` as a direct predecessor of the `host-ready` barrier. Agent nodes chain start-vm→wait-guest-agent→wait-os-ready→host-ready, but `agent:false` nodes skip the `wait-*` ops — leaving the barrier (and `configure-router` after it) with no predecessors, so it fires before the VM ever starts and hangs on guest-IP discovery.
**Origin:** CHANGELOG 2026-06-10 (VyOS `vyos-ssh-set`)

## LES-010 — Emit a unique per-leaf L3VNI Router-MAC on linked clones

**Class:** bug-class
**Scope:** `adapter_proxmox/fabric_render.py` `_l3vni_rmac`; EVPN symmetric-IRB / `br<l3vni>`
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Derive the L3VNI Router-MAC per-leaf from the loopback (`02:<vni_hi>:<vni_lo>:<last 3 octets of loopback>`, LAA unicast), never letting the `br<l3vni>` bridge auto-inherit a MAC. All linked clones auto-derive the SAME bridge MAC; FRR stamps it into every Type-5 it originates and DENIES any inbound Type-5 whose RMAC == the local RMAC (`self mac`), so every leaf rejects every peer's cross-leaf routes while Type-2/3 look fine. (Keep this distinct from the intentionally-shared L2VNI anycast MAC.)
**Origin:** GAP-003; CHANGELOG 2026-07-01

## LES-011 — OVS mirror plumbing: unique per-bridge port names, and `add bridge mirrors` (append), never `set`

**Class:** bug-class
**Scope:** `adapter_proxmox/naming.py` `gre_port_for_bridge_sensor`; `ssh.py` mirror hook / in-band assert
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Two invariants for OVS: (1) OVS interface names are globally unique within an `ovs-vsctl` instance — derive per-(bridge,sensor) names like `gre_<6hex>` (≤15 chars, underscore not hyphen), never a constant like `gre-out` (the 2nd tapped bridge collides). (2) Attach mirrors with `add bridge "$br" mirrors @m` and remove-then-destroy on teardown; `set bridge mirrors=@m` REPLACES the column, so a second tapped host's hook silently wipes the first host's mirror.
**Origin:** CHANGELOG 2026-06-01 (`gre_port_for_bridge`), 2026-05-29 (`add` not `set`)

## LES-012 — gretap decap must be flow-based `type gretap external`; avoid `gre0`/`gretap0`; it won't survive a node reboot

**Class:** platform-quirk
**Scope:** sensor capture iface; keyed gretap netdev creation
**Consumers:** code-writer, live-verifier
**Rule:** Terminate keyed mirror traffic on a flow-based `ip link add … type gretap external` iface — a fixed-`remote`/`key 0` gretap silently drops keyed frames. Never name it `gre0`/`gretap0`: the `ip_gre` module auto-creates those on load, so the add fails "File exists" — use `gtcap0`/`gre_<hex>`. And know that an `ip link` gretap netdev does NOT persist across a Proxmox node reboot (unlike the retired OVS GRE port); a resume-safe redeploy recreates it.
**Origin:** CHANGELOG 2026-06-01 (per-tunnel key + flow-based sensor), 2026-06-09 (#3 `gretap0` collision), 2026-06-04 (node-reboot caveat)

## LES-013 — `configure-router` NAT must be reboot-persistent, using tools already on the base image

**Class:** bug-class
**Scope:** `adapter_proxmox/rest.py` `op_configure_router` (Debian/QGA path)
**Consumers:** code-writer, code-reviewer, live-verifier
**Rule:** Install forwarding + LAN→WAN masquerade as a systemd oneshot (`axr-router-nat.service`, `After=network-online`, `RemainAfterExit`) that re-detects the WAN and re-applies on every boot, plus an immediate apply. A Debian cloud router reboots on first boot, so a runtime-only NAT leaves `ip_forward=0` and LAN hosts silently lose egress. Prefer `nft`/`iptables` already present on the base image; do NOT `apt-get install nftables` (chicken-and-egg — it needs the egress it's meant to provide — and it silently no-ops).
**Origin:** CHANGELOG 2026-06-24 (router-nat-verify)

## LES-014 — VyOS EVPN/IRB render syntax: `route-target both`, and no `next-hop-vrf` static leak

**Class:** platform-quirk
**Scope:** `adapter_proxmox/fabric_render.py` `_render_irb`; VyOS 1.5 / FRR
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Emit the L3VNI import/export target as `route-target both <asn>:<l3vni>` — VyOS 1.5 rejects the `route-target vpn both …` token. Do NOT render a tenant-VRF default via `route 0.0.0.0/0 next-hop <gw> next-hop-vrf default`: `next-hop-vrf` is invalid VyOS (`Set failed`) and leaves a partially-applied config; VRF internet egress is a separate border-originated design.
**Origin:** CHANGELOG 2026-06-30 (findings C + D)

## LES-015 — Build VyOS templates offline, key-baked, with `hw-id` stripped and no commit

**Class:** platform-quirk
**Scope:** VyOS template prep; `configure-router` guest-IP discovery on the PVE host
**Consumers:** code-writer, live-verifier
**Rule:** Rebuild VyOS templates OFFLINE by editing the disk (guestfish/virt-customize) WITHOUT booting — booting before re-templatizing taints first-boot so clones never DHCP. Bake `service ssh` + `eth0 address dhcp` + the orchestrator pubkey into `config.boot`, and strip `hw-id` as a RAW edit with NO subsequent commit (a `commit` re-pins hw-id, breaking deterministic net→eth NIC naming). VyOS has no QGA, so the PVE host must have `arp-scan` installed for the orchestrator to discover the router's WAN IP by MAC.
**Origin:** CHANGELOG 2026-06-10 (`vyos-ssh-set`), 2026-06-30 (offline rebuild `vyos-fabric-key2`)

## LES-016 — Windows clone substrate: drive OOBE via `International-Core` + a SYSTEM-context `SetupComplete.cmd`, and respect NetBIOS/Sam length limits

**Class:** platform-quirk
**Scope:** `imagebuild/bake_windows.py` sysprep unattend + `SetupComplete.cmd`; `create-domain-user`
**Consumers:** code-writer, live-verifier
**Rule:** Make a sysprepped clone auto-complete OOBE by adding a `Microsoft-Windows-International-Core` component (Input/System/UI/UserLocale) to `oobeSystem` plus `SkipMachineOOBE`/`SkipUserOOBE` — the first Server OOBE screen is region/keyboard, governed by International-Core, NOT the `<OOBE><Hide*>` flags (Hide* alone stalls forever at "Hi there"). Drop the os-ready marker + QGA-autostart from a `C:\Windows\Setup\Scripts\SetupComplete.cmd` that runs once as SYSTEM at end of Setup (no interactive logon — AutoLogon/FirstLogonCommands never run because the clone never reaches a logon). Keep ComputerName ≤15 (NetBIOS) and SamAccountName ≤20.
**Origin:** CHANGELOG 2026-06-17 (W1 OOBE fix + SetupComplete.cmd), 2026-06-16 (W0 NetBIOS ≤15), 2026-06-17 (W2b Sam ≤20)

## LES-017 — Gate `wait-domain-ready` on ADWS + NTDS + Netlogon with 3 consecutive stable probes

**Class:** bug-class
**Scope:** `config_exec/backend.py` `_wait_domain_ready`
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A one-shot `Get-ADDomain` success is not "domain ready": ADWS flaps during post-DCPROMO settling, so require ADWS + NTDS + Netlogon all `Running` AND `Get-ADDomain` returning the forest root for `_DOMAIN_READY_STABLE` (3) consecutive probes (a failure resets the streak). Otherwise `create-domain-user`'s `New-ADUser` fails "Unable to find a default server with Active Directory Web Services running."
**Origin:** CHANGELOG 2026-06-24 (adws-harden-verify)

## LES-018 — PVE `/agent/ping` is POST not GET, and list-valued REST bodies must serialize as repeated `key=val`

**Class:** platform-quirk
**Scope:** `adapter_proxmox/rest.py` QGA calls (`op_wait_guest_agent`, `op_wait_os_ready`, `agent/exec`)
**Consumers:** code-writer, code-reviewer
**Rule:** Call `/agent/ping` with POST — GET returns HTTP 501 and a naive retry loop burns the whole timeout before halting. For array-typed PVE parameters (e.g. `agent/exec` `command`), flatten list-valued body keys into repeated `command=cloud-init&command=status&command=--wait` pairs, not a single joined value.
**Origin:** CHANGELOG 2026-05-29 (agent/ping POST + array body serialization)

## LES-019 — SDN SNAT egress is Simple-zone only; VLAN zones reject `exitnodes`

**Class:** platform-quirk
**Scope:** `create-subnet` SNAT; any `egress: true` / sensor-transport net on a VLAN zone
**Consumers:** code-writer, live-verifier
**Rule:** Only rely on `create-subnet` SNAT egress on a Simple zone — PVE materializes the gateway + masquerade there. On a VLAN zone, PVE rejects the `exitnodes` property and creates neither gateway IP nor SNAT rule, so `egress: true` is declared-but-inert; a VLAN-zoned segment needs a router VM (or a pre-baked tool) for egress.
**Origin:** CHANGELOG 2026-06-09 (Phase 1 live-verify), 2026-06-23 (sensor transport egress)

## LES-020 — `axrange destroy`: subnets before vnet; remove OVS bridges via the node-network API

**Class:** ordering-invariant
**Scope:** `adapter_proxmox/teardown.py`; `axrange destroy`
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Delete a vnet's subnets BEFORE the vnet (PVE 400s otherwise, then the zone delete fails). Remove an OVS underlay bridge with `DELETE /nodes/<n>/network/<br>` then `PUT /nodes/<n>/network` — NOT `ovs-vsctl del-br`, which clears only live datapath state and leaves the bridge defined in `/etc/network/interfaces`, so the next SDN apply / `reloadnetworkall` silently recreates it. Re-check every resource name against the deterministic-naming invariants before touching it so `vmbr0`/operator bridges can't be swept.
**Origin:** CHANGELOG 2026-06-23 (B4 subnet-aware destroy; teardown blast-radius guards), 2026-06-09 (OVS-bridge cleanup gap follow-up)

## LES-021 — PVE 8.x renamed `qm importdisk` → `qm disk import`

**Class:** tool-gotcha
**Scope:** `imagebuild/proxmox_build.py` build-VM disk import; PVE 8.x+
**Consumers:** code-writer, live-verifier
**Rule:** Use `qm disk import` for the build-VM disk-import step; `qm importdisk` was renamed in PVE 8.x. Parse the output for the volid but fall back to the conventional `<storage>:vm-<vmid>-disk-0` name so the parse is robust across output formats.
**Origin:** CHANGELOG 2026-06-01 (imagebuild slice, pxtwin1 PVE 8.4)

## LES-022 — In PowerShell render, construct typed objects with `[Type]::new(...)`, not `New-Object`

**Class:** tool-gotcha
**Scope:** `config_exec/render.py` Windows/AD renderers (ACL ACEs, etc.)
**Consumers:** code-writer, code-reviewer
**Rule:** When a constructed object is passed as a method argument (e.g. `$acl.AddAccessRule([System.DirectoryServices.ActiveDirectoryAccessRule]::new(...))`), use `[Type]::new(...)` — `New-Object Type(...)` won't parse as a method argument and the ACE never applies.
**Origin:** CHANGELOG 2026-06-25 (dangerous-ACL live-verify fix)

## LES-023 — Debian `python3-impacket` example scripts live under `/usr/share/doc/...`, not on PATH

**Class:** env-fact
**Scope:** attacker apps / AD kill-chain activities on Debian
**Consumers:** code-writer, tester
**Rule:** Invoke `GetNPUsers.py`/`GetUserSPNs.py` by path — Debian's `python3-impacket` ships them under `/usr/share/doc/python3-impacket/examples/`, NOT on PATH (only the `impacket-*` wrappers like `impacket-secretsdump` are on PATH).
**Origin:** CHANGELOG 2026-06-24 (AD attack chain live-verify)

## LES-024 — Bake fragile / external-repo tools into the image at build time; don't apt-install in the isolated guest

**Class:** pattern
**Scope:** `imagebuild/` recipes; `provision-app` `pkg:`; sensor images (suricata/zeek)
**Consumers:** code-writer, live-verifier
**Rule:** Any tool whose package is flaky over apt or lives outside the guest's base repos (ansible, suricata via bookworm-backports, zeek) must be baked at image-build time, where the build host has real `vmbr0` egress — the isolated scenario guest has only a local mirror and cannot apt-install on demand. For the apt installs that do run, ensure `update_cache` first (a fresh clone's package lists are empty). When re-checking a baked daemon, match its real process comm (e.g. `pgrep -f 'suricata.*axrange'`, since Suricata's comm is `Suricata-Main`) and clear any stale pidfile before restart.
**Origin:** CHANGELOG 2026-06-23 (suricata-sensor bake + start fixes), 2026-06-02 (ansible baked), GAP-002 (apt fragility)

## LES-025 — Honor the module import/reuse direction

**Class:** reuse-rule
**Scope:** module import graph — `imagebuild/`, `config_exec/`, `planner/`, `adapter_proxmox/`, `scenario_ai_research/`
**Consumers:** code-writer, code-reviewer
**Rule:** Reuse downward, never upward: `imagebuild` and `config_exec` reuse `adapter_proxmox`'s `RestClient`/`run_ssh`/`GuestAgent` (one REST/SSH/QGA implementation). The planner emits ops carrying data and must NOT import the adapter or `config_exec` (the backend renders at execute time). `scenario_ai_research/` may import axRange's validator/planner/op-catalog; axRange must never import from `scenario_ai_research/`.
**Origin:** CHANGELOG 2026-06-01 (imagebuild reuses adapter), 2026-06-02 (`GuestAgent` factored out), 2026-06-03/2026-06-12 (planner stays config_exec-free), 2026-06-11 (scenario-ai-research one-directional coupling)

## LES-026 — tcpdump-sensor bring-up: use a large finite `-W`, and re-allow tcpdump's `@privileged` syscalls for `-Z`

**Class:** tool-gotcha
**Scope:** `o2/tcpdump-sensor/bin/capture-start`; `o2/tcpdump-sensor/systemd/capture.service`
**Consumers:** code-writer, live-verifier
**Rule:** Set tcpdump `-W 999999999`, not `-W 0` — tcpdump 4.99+ (Debian 12) rejects `0`, and any small `-W <n>` switches to numeric-counter filenames that overwrite instead of preserving the strftime template (retention is the watchdog's job). Under the hardened `SystemCallFilter=@system-service` minus `@privileged @resources @debug @mount`, re-allow exactly `bpf setuid setgid setgroups capset @chown` — tcpdump needs `bpf` for the eBPF filter and the setuid/…/capset/@chown set for its `-Z <user>` privilege drop and post-drop pcap writes.
**Origin:** CHANGELOG 2026-05-29 (`-W 0`→finite; focused syscall allowlist)

## LES-027 — Carry write-action confirmations as query params, not only in a DELETE body

**Class:** platform-quirk
**Scope:** `api/app.py` `DELETE /api/deploys/{id}`; Grafana datasource-proxy write verbs
**Consumers:** code-writer, code-reviewer
**Rule:** Accept confirmation flags like `allow_destroy` as a query parameter (`?allow_destroy=true`) in addition to (optional) body, effective as `query OR body`. Sending a request BODY on an HTTP DELETE is poorly supported — Grafana's datasource proxy and many HTTP intermediaries drop DELETE bodies, turning an authorized destroy into a 400/422.
**Origin:** GAP-005; CHANGELOG 2026-06-29

## LES-028 — Observe a detached deploy through `state.db` and node tools, not the block-buffered logfile

**Class:** tool-gotcha
**Scope:** deploy observation during bring-up; `deploy-dev45/opstat.py`, `naming.py`
**Consumers:** live-verifier, tester
**Rule:** A backgrounded/detached deploy's logfile is block-buffered (no TTY) and lags reality — read live progress from `state.db` via `deploy-dev45/opstat.py` (dumps `ops` + failed/halted `op_attempts` with `error_class`/`error_message`) and cross-check on the node with `qm list`/`qm screendump`. Search for clones by their deterministic 6-digit VMID (`100000–999999`), not a name-prefix id — `te-<6hex>-<host>` is only the VM display name, never the vmid. *(Example refreshed 2026-07-08: was `axr-<6hex>-<host>` before the resource-ownership-tags rename, CHANGELOG 2026-07-08 — the underlying lesson is unchanged.)*
**Origin:** `deploy-dev45/opstat.py`; `adapter_proxmox/naming.py` (`100000 + n%900000`, `te-<6hex>` display name); bring-up practice

## LES-029 — Test scripts/ helpers via spec_from_file_location; redirect their file I/O by monkeypatching module-global path constants

**Class:** convention
**Scope:** tests of `scripts/` non-package helpers; `scripts/*.py` with a `REPO = Path(__file__)...` constant (e.g. `doc-stats.py`, `inject_lessons.py`)
**Consumers:** code-writer, tester
**Rule:** Load a non-importable `scripts/` helper (hyphenated names like `doc-stats.py`, or any file outside the package) with `importlib.util.spec_from_file_location(name, scripts/<file>)` + `exec_module` — passing the real path preserves the script's `__file__`-derived `REPO`/`LESSONS` resolution, so it reads the real registries/docs with no wiring. To exercise a file-reading path (e.g. a drift check) without touching real repo files, monkeypatch the script's module-global path constants (`REPO`, `_DOC_FILES`) to a `tmp_path`, and stub any function that shells out (`count_tests` -> nested pytest).
**Origin:** code-review of tests/test_pipeline_scripts.py (2026-07-07)

## LES-030 — A plan-stable ID is not a deploy-stable primary key; key state-store rows by `(deploy_id, plan_id)` when the same plan can be deployed more than once

**Class:** bug-class
**Scope:** `axrange/planner/state.py` — any new table keyed off a deterministic op/plan-derived ID; general pattern for state-store schema design
**Consumers:** code-writer, code-reviewer
**Rule:** `op_id` is deterministic and stable **across replays of the same plan** by design (`docs/PLANNER.md` §4.2/§10 — that's what makes resume-safety work). That is a different property from "unique per deploy." A table PRIMARY KEY'd on `op_id` alone silently aliases two *different* deploys of the *same* plan — the second deploy's `get_op(op_id)` finds the first deploy's row and treats it as its own history. Fixed by a composite `(deploy_id, op_id)` PRIMARY KEY on `ops`, and adding `deploy_id` to `op_attempts`. When adding any new state-store table keyed by a plan-derived deterministic ID, ask explicitly: "can two live deploys of the same plan exist at once?" — if yes, `deploy_id` (or equivalent) must be part of the key, not just an FK column that's easy to forget to filter on.
**Origin:** LES-007 (extends); CHANGELOG 2026-07-07 (composite PK fix, `--fresh` root cause + the previously-undocumented `destroy`-after-`--fresh` leak)

## LES-031 — A schema-version guard must be a pre-flight check on EVERY open, and its fresh-init path must be provably atomic

**Class:** pattern
**Scope:** `axrange/planner/state.py` `StateStore.__init__` / `SCHEMA_VERSION` / `PRAGMA user_version`; any component owning a durable on-disk schema
**Consumers:** code-writer, code-reviewer, tester
**Rule:** When a schema changes in a way old code can silently misinterpret (e.g. a PK shape change), don't just bump application logic — stamp a `SCHEMA_VERSION` into the store itself (`PRAGMA user_version` for SQLite) and check it on every open, raising a distinct exception type (`StateStoreSchemaError`) on mismatch rather than proceeding with best-effort compatibility. Separately: the fresh-DB init path (DDL + version stamp) must be one atomic unit — issuing them as two statements/transactions leaves a window where a concurrent opener sees tables but `user_version` still `0`, which the guard would misread as "ancient/no version stamped." `sqlite3.Connection.executescript()` auto-commits any pending transaction as part of running the script, so folding the `PRAGMA user_version=N` INTO the same script string as the CREATE TABLE/INDEX statements makes the whole init atomic for free — don't split it into a separate `execute()` call after `executescript()`.
**Origin:** CHANGELOG 2026-07-07 (`SCHEMA_VERSION=2` guard)

## LES-032 — Split-brain/lease-loss detection must derive from the lease's own renewal outcome, not a diff over shared resource state

**Class:** dead-end / pattern
**Scope:** `axrange/planner/locks.py` `Heartbeat`; any cross-process coordination built on a heartbeat-renewed lease
**Consumers:** code-writer, code-reviewer
**Rule:** The only race-free signal that "this process may no longer be the single writer" is **this process's own lease-renewal failing**. A tempting alternative — periodically diff the holder of each shared resource-lock key against what this process expects to hold — is a dead end: it false-positives the instant a legitimate concurrent deploy of a *different* scenario takes a shared correctness key it's entitled to, aborting a perfectly healthy deploy. Wire the guard (`Heartbeat.lost` events polled by the executor) at the single site that constructs the executor for a live deploy (`run_deploy_executor`) — a guard defined on one object but never read by the object that needs it is dead code; assert the wiring itself in a test (construct the real call path, not just the `Heartbeat` class in isolation), not just the class's unit behavior.
**Origin:** CHANGELOG 2026-07-07 (heartbeat lease-loss abort; the per-resource-key holder-diff approach explicitly tried and dropped)

## LES-033 — Model-C "already exists" idempotency backstops must be name/identity-guarded, and an idempotent-no-op except must match the specific wording, not swallow broadly

**Class:** bug-class
**Scope:** `axrange/adapter_proxmox/rest.py` / `ssh.py` Model-C `op_create_*` / `op_clone_*` handlers; any check-then-act idempotency backstop that catches a "conflict" error string
**Consumers:** code-writer, code-reviewer
**Rule:** Two related traps in Model C's check-then-act idempotency: (1) when an op adopts-by-id an existing resource on a conflict (e.g. `clone-vm` finding a VMID already in use), it must verify the existing resource is actually **this deploy's** resource (name-guard, e.g. a `te-<plan>-...` naming match — `axr-<plan>-...` before the 2026-07-08 resource-ownership-tags rename) before treating it as "already done" — otherwise it can silently adopt/report-success against an unrelated, foreign VM at that ID (`vmid-collision-foreign`). (2) A backstop `except`/string-match that swallows "already exists" as a success case must match the **specific wording an op's failure mode actually produces**, not blanket-swallow every conflict-shaped error — PVE says "already exists" for some SDN object kinds and "already defined" for others (subnets); a narrow match backstops the wrong-wording case by accident-of-history, not by design (see GAP-004, CHANGELOG 2026-07-01, the direct precursor to this lesson's general form).
**Origin:** CHANGELOG 2026-07-07 (`clone-vm` name-guard; GAP-004 2026-07-01 for the wording-match half)

## LES-034 — Validate every binding of a fan-out reference; never build a last-wins single-value map when the same key can appear more than once

**Class:** bug-class
**Scope:** any validator rule (or planner emission) that builds a `dict[ref_id, X]` from a `bindings:` list where `ref_id` is not guaranteed unique (e.g. `app→host` — one app can bind to multiple hosts)
**Consumers:** code-writer, code-reviewer
**Rule:** When building a lookup map from a list of bindings keyed by an entity ID that CAN repeat (a fan-out relationship, e.g. one app deployed to several hosts), use `dict[id, list[value]]` (`setdefault(...).append(...)`) and check **every** value in the list — never `dict[id, value]`, which makes the last binding win and silently un-checks every earlier one. This is the same shape of bug as `NET-080`'s attachment-count miss: don't restrict a check to one of several equally-valid representations of "the same relationship" (`net:` vs `nets:`) — union or fan-out over all of them.
**Origin:** CHANGELOG 2026-07-07 (`CAP-020` last-wins fix; `NET-080` `net:`/`nets:` union fix, same session)

## LES-035 — A daemon-start renderer must clear a stale pidfile before start and assert liveness via an argv-present process match, not a bare comm-name match

**Class:** silent-success-trap
**Scope:** `axrange/config_exec/render.py` daemon start/stop blocks (suricata, zeek, any long-running in-guest process started over QGA); extends LES-024
**Consumers:** code-writer, tester, live-verifier
**Rule:** Two failure modes for a rendered daemon-start script, both silent: (1) a leftover pidfile from a prior run aborts a redeploy's start attempt (the daemon refuses to start "already running" against a dead pid) — always `rm -f` the pidfile immediately before starting. (2) a liveness check by bare process/comm name (`pgrep -x suricata`) can miss the real process name (suricata's comm is `Suricata-Main`, not `suricata`) or match an unrelated process; match on an **argv-present, deploy-specific token** instead (`pgrep -f 'suricata.*axrange'`, or a `-U /run/zeek-<id>.status` flag baked into the invocation) so the check is both correct and collision-proof across concurrent sensors. Extend this to every renderer that starts a daemon, not just the one it was first found in — this pass brought the suricata network-sensor renderer to parity with `deploy-sensor`'s already-fixed version via a shared `_suricata_start_block` helper.
**Origin:** LES-024 (extends); CHANGELOG 2026-06-23 (original suricata pgrep/pidfile fix in `deploy-sensor`); CHANGELOG 2026-07-07 (parity fix in the network sensor renderer)

## LES-036 — PowerShell double-quoted-string interpolation needs backtick/`$`/`"` escaping AND doubled single-quotes; don't assume one PS quoting style covers both contexts

**Class:** platform-quirk
**Scope:** `axrange/config_exec/render.py` PowerShell renders using a `-Filter "..."` or any double-quoted interpolated string (AD cmdlets, `Get-ADComputer -Filter`); Windows guest-side PS rendering generally
**Consumers:** code-writer, tester
**Rule:** PowerShell has two quoting rules that both matter and are easy to conflate: inside a **double-quoted** string, `$` triggers variable interpolation and `` ` ``/`"` need backtick-escaping (a computer account name like `WS01$` needs the `$` escaped or it's read as a variable reference); inside a **single-quoted** string, no interpolation happens but a literal `'` must be doubled (`''`). A helper that only handles one of these (e.g. only doubling `'`) breaks the other context. Use a dedicated escape helper per quoting context (this pass added `_ps_dq_escape` for the double-quoted `-Filter` case) rather than reusing a single-quote escaper for a double-quoted string or vice versa.
**Origin:** CHANGELOG 2026-07-07 (`_ps_dq_escape` for AD `-Filter`, handling both `$`-in-computer-accounts and apostrophes)

## LES-037 — CLI and API entry points must share one plan-build/deploy helper; don't let two call sites reimplement "build plan, run executor, release resources"

**Class:** reuse-rule
**Scope:** `axrange/cli/main.py`, `axrange/api/service.py`; any second consumer of the planner/executor beyond the first
**Consumers:** code-writer, code-reviewer
**Rule:** Once a second caller needs "validate → plan → run the executor → release deploy resources," factor it into a shared helper (`build_plan_and_ctx` / `run_deploy_executor` / `release_deploy_resources`) rather than letting the CLI and API each carry their own copy of the sequencing — a fix to one (e.g. the `--fresh`/composite-PK fix, or wrapping teardown in a `Heartbeat`) silently doesn't reach the other otherwise. This is an instance of the general LES-025 reuse direction, specialized to "orchestration sequencing," not just "backend client code."
**Origin:** LES-025 (extends); CHANGELOG 2026-07-07 (shared helpers factored out of `cli/main.py` and `api/service.py`)

## LES-038 — Every mergeable include section needs a matching entry in the merge-skip set; scan for duplicate IDs per-fragment before merge, scoped to known ID-bearing sections

**Class:** bug-class
**Scope:** `axrange/ir/parser.py` `_merge_fragment_into_main` / `_MERGEABLE_LIST_PATHS` / `_collect_ids`; any include/fragment-merge system with a "known top-level keys" allowlist
**Consumers:** code-writer, code-reviewer
**Rule:** Two coupled invariants for a fragment-merge system built from a "known sections" list: (1) when a new mergeable top-level section is added (here, `fabric:`), it must be added to **both** the list of sections that get merged AND the list of sections the fallback "unknown top-level key" branch skips — missing the second re-processes an already-handled section as if it were unrecognized. `fabric:`'s scalar sub-keys (`fabric_asn`, `tenant`) were being silently dropped because only `fabric.regions` (list-mergeable) was wired into the merge, not the scalars. (2) A duplicate-ID scanner backing a strict-no-override rule (`INC-010`) must be scoped to the IR's actual **known ID-bearing sections**, not "recurse into every dict looking for a key named `id`" — the latter false-trips on unrelated free-form value-maps (e.g. `activity[].inline.steps[].id`). Run the scan **per-fragment, before that fragment is merged into main**, so a real collision is attributed to the fragment file that actually introduced it, not always blamed on the main file.
**Origin:** CHANGELOG 2026-07-07 (`fabric:` include-merge fix; `_collect_ids` scoping fix)

## LES-039 — A conformance-gated write to a shared config/binding file must be revertible on failure, including the FIRST write when there was no prior value to restore

**Class:** bug-class
**Scope:** `axrange/imagebuild/binding_io.py` (`write_template_vmid`/`clear_template_vmid`), `builder.py` unwind path; any pipeline that writes a persistent pointer (a binding, a manifest field) speculatively before full conformance is proven
**Consumers:** code-writer, code-reviewer
**Rule:** If a pipeline writes a pointer field (e.g. `template_vmid`) into a shared binding BEFORE the thing it points to has passed its full conformance gate, the failure-unwind path must be able to fully undo that write — including the case where there was **no prior value** (first build for that binding). An "update to old value" undo function only handles the update case; write a symmetric "unset/pop the key entirely" undo (`clear_template_vmid`) for the first-write case, or a partial-failure unwind leaves the binding pointing at a resource the same unwind is about to destroy — looking valid in a diff while being dangling in reality. Related: a subprocess/tool "wait for completion" exit code (`cloud-init status --wait`) can be a **completion** signal without being a **success** signal — some tools treat inner-step failures as non-fatal to their own top-level exit code. Follow an apparent-success wait with a structured status read (`cloud-init status --format json` → `errors[]`) and fail loud on any reported error; don't take exit-code 0/2 alone as proof the underlying provisioning actually worked.
**Origin:** CHANGELOG 2026-07-07 (`clear_template_vmid`; `_assert_cloud_init_clean` `errors[]` check)

## LES-040 — Gate a dual-entry-point validator skip on the caller's declared mode flag, never on an inferred proxy condition

**Class:** bug-class
**Scope:** `axrange/validator/engine.py` `_build_context` / any validator check with a legitimate skip case reachable from more than one entry point (`validate_structural` vs `validate_full`); generalizes to any dual-entry-point system where one entry point intentionally runs with a reduced/empty environment
**Consumers:** code-writer, code-reviewer, live-verifier
**Rule:** When a validator rule needs to skip under one specific, intentional circumstance (here: `REF-120` skipping image-reference resolution because `validate_structural`'s canvas/shape-only Pass 1+2 entry point deliberately runs against an empty `ManifestRegistry()`), gate the skip on an explicit flag the calling entry point sets (`ctx.skip_unknown_image_lifecycle`, set only by `validate_structural`) — never on an **inferred proxy** for that circumstance (here, the first cut used "the registry happens to be empty"). A proxy condition is satisfiable by an unintended path too: `validate_full` (the deploy path) handed an empty registry by operator mistake (e.g. a wrong `--images` path) also matched "registry is empty" and silently skipped, so `axrange validate` PASSED over a completely unloaded image set and the real failure didn't surface until deploy time, as an opaque `clone-vm: template_vmid missing`. This is the general form of the mode/proxy distinction: a skip condition must answer "does the CALLER say this is expected?", not "does the DATA happen to look like the expected case?" — the latter can't distinguish the intentional case from a real failure that produces the same-shaped data. This is a live-verify catch: unit tests (which construct the registry deliberately) never exercised the "empty by mistake" path, only a real cluster / real CLI invocation with a bad flag did.
**Origin:** CHANGELOG 2026-07-07 (`REF-120` fix, corrected same-day after dev45 live-verify)

## LES-041 — Normalize a size/quantity unit at the adapter boundary to the exact form the target endpoint accepts; don't assume a sibling field's normalization covers it, and don't let a broad `except` mask a systematic 400

**Class:** bug-class
**Scope:** `axrange/adapter_proxmox/rest.py` `op_configure_vm_hardware` (`_normalize_pve_disk_size`); any adapter op that passes an IR-side quantity (binary-unit `Gi`/`Mi`, or any other unit convention) straight through to a REST/CLI param with a different unit-format expectation
**Consumers:** code-writer, code-reviewer
**Rule:** The IR's `resources:` block uses Kubernetes-style binary suffixes (`10Gi`, `512Mi`) uniformly, but each PVE endpoint that consumes a size has its own accepted format — `memory` wants a bare MB integer (already handled by `_parse_mem_mb`) while `/resize`'s `size` param wants a **decimal**-suffixed string (`10G`, not `10Gi`) and 400s on the binary form. Two lessons compound here: (1) don't assume that because one field of a resource block (`ram`) is normalized, a sibling field (`disk`) is too — check each target endpoint's actual accepted format and write a matching normalizer (`_normalize_pve_disk_size`), don't `str(inp[...])` a raw IR value through. (2) this bug was **completely invisible** for as long as a blanket `except AdapterError: pass` sat around the resize call — the 400 fired on every single `Gi`-unit disk resize and was silently swallowed as if it were the intended "shrink/same-size, ignore" case; tightening that `except` to match only the specific shrink/same-size wording (a separate same-day fix) is what exposed this bug live, on dev45, rather than in a unit test (mocks don't reject malformed params the way a real PVE API does). A broad `except` doesn't just hide its one intended benign case — it can hide a systematic, 100%-reproducing bug behind it indefinitely.
**Origin:** CHANGELOG 2026-07-07 (`_normalize_pve_disk_size`, found during the same dev45 live-verify as LES-040; the disk-resize `except`-narrowing fix that exposed it is CHANGELOG 2026-07-07, entry above)

## LES-042 — `tsc --noEmit` in the Grafana control-panel plugin has ~118 pre-existing TS2686 errors; don't treat it as a change-detection signal without diffing against a clean baseline

**Class:** tool-gotcha
**Scope:** `o2/grafana/plugins/axellio-axrange-control-panel` (any file in this plugin); `npm run typecheck` in this package
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Standalone `tsc --noEmit` in this plugin reports roughly 118 `TS2686 'React' refers to a UMD global, but the current file is a module. Consider adding an import instead` errors spread across ~10 files, unrelated to any given change — a gap between the plugin's webpack/`swc-loader` build config (`jsc.transform.react.runtime: 'automatic'`, which needs no `React` import; CHANGELOG 2026-06-25) and standalone `tsc`'s own JSX-runtime resolution, which doesn't pick up the same setting. This is a pre-existing baseline, not a regression signal. To verify a change is type-clean in this plugin: `git stash` your diff and re-run `tsc --noEmit` to capture the baseline error set, restore the diff, re-run, and diff the two error sets (or scope `tsc` to just the changed files) — don't read a red `npm run typecheck` here as "my change broke something" without that comparison. `npm run test:ci` (jest) is the real green/red signal for this plugin; the build itself (`build-plugin.sh` / webpack) also succeeds despite the standalone-`tsc` noise.
**Origin:** CHANGELOG 2026-07-08 (SPA canvas layout / 7th bake-off renderer — `canvas.ts`/`canvas.test.ts` verified clean by this stash-diff method against the pre-existing ~118-error baseline)

## LES-043 — Assert a shared producer module is actually on disk with its new exports before claiming green; don't trust "consumers + tests updated"

**Class:** silent-success-trap
**Scope:** `axrange/adapter_proxmox/naming.py`; any module every consumer depends on for exported symbols (a single-source-of-truth producer/predicate module); Pipeline mode multi-agent handoffs generally
**Consumers:** code-writer, code-reviewer, tester
**Rule:** When renaming or adding exported symbols in a module every consumer imports from, "the consumers and tests were updated" is not sufficient evidence the change is real — a reverted or unpersisted edit to the producer file itself leaves every consumer referencing phantom symbols, and a plain `git status`/diff review can miss it if the stray revert happened transiently mid-pipeline (e.g. a subagent's own `git checkout` step touched the wrong path). Before declaring the change done, positively assert the producer file is on disk with the new symbols and the package actually imports (`python -c "import axrange.planner"` or a fresh `pytest --co`) — not just that the consumer/test diffs look right. This is the general form of "don't trust a downstream symptom as proof of an upstream fix."
**Origin:** CHANGELOG 2026-07-08 (resource-ownership-tags implementation — a subagent's stray `git checkout` transiently reverted `naming.py` mid-pipeline; caught before landing because the import was actually re-verified, not assumed)

## LES-044 — A deterministic-naming-scheme rename strands pre-rename live resources; destroy them with the pre-rename code before deploying the rename

**Class:** reversal-hazard
**Scope:** `axrange/adapter_proxmox/naming.py` and any deterministic-resource-naming scheme change; `planner/teardown.py`'s positive shape-match guards; any live cluster carrying deploys from before the rename
**Consumers:** code-writer, code-reviewer, live-verifier
**Rule:** Renaming a deterministic resource-naming scheme (e.g. `axr*` → `te*`/`tap*`) means resources created under the OLD scheme match neither the NEW positive teardown guards nor any cleanup path — a `destroy` run with the new code against an old-named deploy reports success ("swept") having actually swept nothing, silently leaking the real resources on the cluster. Before deploying a naming-scheme change to a live cluster: destroy every pre-rename deploy using the **pre-rename** code first (or use a clean cluster), and record what was actually swept. This is a live-cluster-only hazard invisible to the unit/pytest suite (mocks don't carry pre-existing state across a scheme change).
**Origin:** `docs/WORK_ORDER_resource_tags.md` §5; CHANGELOG 2026-07-08 (resource-ownership-tags implementation)

## LES-045 — A resource-name-shape dependency can hide outside `naming.py`'s own callers, as a hardcoded literal prefix string; grep for the literal too, not just call sites

**Class:** bug-class
**Scope:** `axrange/adapter_proxmox/rest.py` (the live SSH tap-reassert bridge scan); any future rename of a `naming.py`-produced shape
**Consumers:** code-writer, code-reviewer
**Rule:** Not every place that depends on a `naming.py`-produced name shape calls into `naming.py` to do it — `rest.py`'s live OVS tap-reassert shell scan had the old bridge prefix baked directly into a shell command string (`grep '^vbr_'`), not derived from a `naming.py` call. A grep over `naming.py`'s call sites alone would have missed it. Any future rename of a produced shape must also grep the repo for the **literal old prefix string** (not just producer/predicate function-call sites) to find these hardcoded-elsewhere dependencies.
**Origin:** CHANGELOG 2026-07-08 (resource-ownership-tags implementation — `rest.py`'s `grep '^vbr_'` → `grep '^tebr_'` fix)

## LES-046 — Audit every already-drifted path variant during a rename, and watch for a "directory created here, file written there" split

**Class:** bug-class
**Scope:** `o2/tcpdump-sensor/install.sh`; any installer/setup script that `install -d`s a directory in one step and separately `install`s/`cp`s a file into a path derived from the same token in a later step; any terminology-rename sweep normalizing multiple config/runtime paths built from one retired token
**Consumers:** code-writer, code-reviewer, tester
**Rule:** When normalizing drift during a rename, grep for every path VARIANT actually present on disk/in-tree, not just the one canonical old/new pair a work order names — a prior partial rename can leave a THIRD, drifted spelling (`/etc/capture-o2/tcpdump-sensor/` found alongside the expected `/etc/capture-sink/`, both meaning the same config path) that a two-column find/replace table misses entirely. Separately: when a script both creates a directory (`install -d -m 0755 /usr/share/doc/X`) and later writes a file into a *differently-spelled* path (`install ... /usr/share/doc/Y/file`, no `-D`), that split is a latent bug invisible to any test — it only fires on a truly fresh install where neither path pre-exists, a state no CI/unit run naturally exercises, so it survives indefinitely until a real fresh deploy hits it.
**Origin:** CHANGELOG 2026-07-09 (sink→sensor/capture terminology retirement — `o2/tcpdump-sensor/install.sh`'s doc-dir-created-here/README-written-there split; the `/etc/capture-o2/tcpdump-sensor/` drifted variant found alongside `/etc/capture-sink/`)

## LES-047 — A terminology-retirement acceptance grep must also cover filenames, and must carve out the ticket that names the retired term

**Class:** pattern
**Scope:** any acceptance-grep / `git grep` "zero occurrences" gate closing a terminology-rename `WORK_ORDER_*.md`; renaming sweeps generally
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A content-only grep (`grep -r <term> **/*.ext`) passes clean while a file whose NAME still carries the retired term sits untouched — `tests/test_sink_routing.py` collected fine under pytest and had zero "sink" in its body, yet the token survived in the path until a separate filename check caught it. Always pair a content grep with a filename search (`git ls-files | grep -i <term>`, or `find -iname '*<term>*'`) and `git mv` any hit before declaring the sweep clean. Separately: a self-referencing spec file that documents the retired term BY NAME (a `WORK_ORDER_<term>_rename.md`) will fail its own gate forever — the acceptance grep needs an explicit, permanent exclusion for the spec that names the term it retires (alongside frozen CHANGELOG history), not just for files whose prose is merely allowed to still mention the term in a historical sense.
**Origin:** CHANGELOG 2026-07-09 (sink→sensor/capture terminology retirement — `git mv tests/test_sink_routing.py tests/test_sensor_routing.py` post-review fixup; `docs/WORK_ORDER_sink_rename.md`'s self-exclusion from its own acceptance grep)

## LES-048 — Read the retired term's senses before blind-replacing; don't collapse two concepts into one word or produce a stutter

**Class:** bug-class
**Scope:** any find-replace-driven terminology sweep (prose or code) where the retired term carries more than one distinct meaning in context (e.g. `sink` meant the physical appliance, AND the generic receiver/role, AND the capture *plane*, in different sentences)
**Consumers:** code-writer, code-reviewer
**Rule:** A retired term can carry multiple distinct senses that map to DIFFERENT correct replacement words — collapsing every occurrence onto one default replacement is wrong even when the result is still grammatical. "capture sink" (the plane/role) must become "capture", not "capture sensor" (a phrase that already means something else — the receiver node); "network-sensor sensor" is a stutter produced by replacing "sink" inside a phrase that already said "sensor" one word later. Before applying a mechanical replace, read each hit's sentence for (a) which sense of the retired term it's using, and (b) whether the target word already appears nearby — pick the sense-correct replacement or qualify the name, instead of always applying the default mapping.
**Origin:** CHANGELOG 2026-07-09 (sink→sensor/capture terminology retirement — "network-sensor sensor" double-word fix in `axrange/config_exec/render.py`; "sink-network-sensor Suricata" → "appliance-adjacent network sensor Suricata" disambiguation)

## LES-049 — Citing a frozen CHANGELOG/spec entry by title: anchor on the date, and never paraphrase inside quotation marks

**Class:** convention
**Scope:** any `docs/*.md` prose that cites a CHANGELOG entry or another frozen/version-locked doc by title in quotation marks
**Consumers:** code-writer, code-reviewer
**Rule:** When prose points at a historical CHANGELOG/spec entry, the **date** is the immutable anchor (CHANGELOG entries are never rewritten — "don't rewrite history" per this file's own discipline). If the entry's title is placed inside quotation marks, it must be the exact, verbatim title — silently editing a retired word inside a quoted title (e.g. rewording "per-tap sink routing" to "per-tap sensor routing" inside the quotes) falsifies the citation, since the quoted text no longer matches what that CHANGELOG entry actually says. Either preserve the title verbatim inside the quotes, or drop the quotes entirely and use a descriptive by-date pointer (e.g. "see the 2026-06-03 per-tap sensor routing entry") that's free to paraphrase because it never claimed to quote.
**Origin:** CHANGELOG 2026-07-09 (sink→sensor/capture terminology retirement — citation fixes in `docs/IR_SCHEMA.md` and `docs/design/05-o2-surface.md`)

## LES-050 — Re-derive doc counters (rule/op/test totals) from a single source command during an audit; never trust a cross-doc copy, and re-sum a doc's own internal tables against its own headline

**Class:** bug-class
**Scope:** any `docs/*.md` prose stating a validator rule count, op-type count, or pytest count; `docs/VALIDATOR.md` §5 (per-category rule tables vs. headline), `docs/OP_CATALOG.md` (op-type count), `scripts/doc-stats.py`; doc-audit / doc-refresh work orders generally
**Consumers:** doc-writer, code-reviewer
**Rule:** During a doc audit, re-derive every counter from a single authoritative source command — `from axrange.validator import list_rules` (or equivalent registry enumeration) for rule counts, `list_actions_and_waits()` + `list_barriers()` for op-type counts, `pytest --collect-only -q` for test counts — never trust a number because another doc (or the doc's own headline paragraph) already states it. `scripts/doc-stats.py --check` polices whole-document headline numbers against these source commands, but it does **not** catch a doc contradicting *itself*: `docs/VALIDATOR.md`'s lead paragraph said "98 total rules" while its own §5.1–§5.10 per-category tables summed to only 90 — a whole ratified category (FAB, 8 rules, already shipped in `docs/design/04-networking-capture.md`) had no table at all, only a headline mention. This was caught only by hand-summing the category tables against `list_rules()`'s real count during the 2026-07-10 doc audit, not by `doc-stats.py` (which is a whole-doc-vs-source check, not an internal-consistency check) and not by trusting the headline number at face value. When auditing a doc with per-category/per-section breakdown tables that are supposed to sum to a stated total, always re-sum the tables and compare to both the source-of-truth count AND the doc's own stated total — a doc can drift internally even when its top-line number happens to be technically current-looking.
**Origin:** CHANGELOG 2026-07-10 (doc audit — `docs/VALIDATOR.md` §5.11 FAB table gap; `docs/WORK_ORDER_doc_audit.md`)

## LES-051 — A pkill-then-relaunch delivery command must never carry its own match pattern in the invoking shell's argv; put it in a written script and exec that file

**Class:** bug-class
**Scope:** any in-guest QGA delivery of a "kill prior instance, start new instance" launch (systemd-less daemon/loop restarts); `axrange/config_exec/render.py` launcher renderers; any future renderer that shells `pkill -f <pattern> ...; <relaunch> &` as one inline command
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A command executed as `sh -lc "pkill -f '<pattern>' ...; setsid <relaunch matching pattern> &"` self-kills: the invoking shell's OWN argv (the `sh -lc "..."` string handed to `ga.exec`) contains the literal `<pattern>` substring too, so `pkill -f <pattern>` matches and SIGTERMs the shell that is about to run the relaunch, before the relaunch ever executes. Deliver the kill-then-launch logic as a WRITTEN SCRIPT FILE on disk and exec that file by path (`ga.exec(vmid, ["bash", launch_path])`) instead — the file's path is a different string from the pattern being matched (or the caller can trivially ensure it is), so the launcher process's own argv no longer collides with its `pkill -f` target.
**Origin:** CHANGELOG 2026-07-11 (activity schedule executor — the jittered-activity launcher, `render.py`'s launcher-script renderer; code-review mandatory fix during the schedule work order build)

## LES-052 — A host-side simulation of an in-guest seeded RNG sequence must be execution-tested against the actual guest path, and must fold/parse its own inputs identically

**Class:** verification-idiom
**Scope:** any deterministic host-side re-derivation of a value an in-guest script also computes from the same seed (e.g. `jitter_sequence()`/`jitter_sequence_from_activity()` mirroring the in-guest scheduler's draw order); seeded-replay features generally
**Consumers:** code-writer, tester
**Rule:** When a host-side helper exists purely to let a test (or an operator-facing tool) predict what a seeded in-guest process will do, it is a SEPARATE implementation of the same algorithm by construction — RNG draw order, duration-string parsing, and seed-folding must match the in-guest renderer EXACTLY, and "the unit tests for both pass" does not prove they agree with each other. Write at least one test that renders the actual in-guest script text (or otherwise executes the real code path) and asserts the host-side prediction matches its output byte-for-byte / value-for-value, not just that each side is internally self-consistent.
**Origin:** CHANGELOG 2026-07-11 (activity schedule executor — `jitter_sequence()` vs. the rendered in-guest scheduler loop)

## LES-053 — Find "the one standing resource" by a tag-filtered query, never a scan of a capped recent-N list

**Class:** bug-class
**Scope:** any lookup for a long-lived/standing resource (an `o2_`-tiered deploy, a singleton config, a "the current X") among a growing history table; `axrange/planner/state.py` (`latest_deploy_for_tier`, `deploys_by_tier`)
**Consumers:** code-writer, code-reviewer
**Rule:** A standing resource that was created once and never re-created is, by definition, likely to be among the OLDEST rows in a history table, not the newest — a discovery query that does `SELECT ... ORDER BY created_at DESC LIMIT N` (or otherwise scans only a capped recent window) will silently miss it once enough newer (e.g. `te_`) deploys have accumulated past the cap. Discover a standing/singleton resource by an indexed, unbounded, tag-filtered query (`deploys_by_tier`, `WHERE tier = ?`) instead of by position in a recency-ordered scan.
**Origin:** CHANGELOG 2026-07-11 (A1 O2 control — `latest_deploy_for_tier` code-review fix)

## LES-054 — Every teardown resource-kind step needs a positive tier-name-shape gate, not just the range/`_protected` pair — check both destroy directions

**Class:** security
**Scope:** `axrange/planner/teardown.py` (`_protected()`, the per-resource-kind destroy steps, especially the VM-name path); any future resource kind added to the tier-tag scheme
**Consumers:** code-writer, code-reviewer
**Rule:** A blast-radius guard built from two halves — a positive shape-match ("this name looks like MY tier") plus a negative `_protected()` check ("refuse if it looks like a protected tier") — must apply BOTH halves to every resource kind's destroy path, not just the kinds where the negative check was first added. Audit for a resource kind (e.g. VM names) that only has the negative half wired, since a shape that neither the current tier's own positive match nor `_protected()`'s negative match recognizes can slip through unguarded. Also check this in BOTH directions once a tier can destroy itself (e.g. `axrange o2 down` sweeping `o2_*`): the guard that must let a tier touch its OWN resources while still refusing every OTHER tier's destroy needs the positive/negative pair checked per-direction, not assumed symmetric.
**Origin:** CHANGELOG 2026-07-11 (A1 O2 control — the own-tier teardown refinement + the symmetric VM-name-path gate, code review)

## LES-055 — A synthesized entry that reuses an existing op's emission must replicate that op's CONDITIONAL scope derivation, not just its shape

**Class:** bug-class
**Scope:** any planner code that builds a synthesized/default IR-entry-shaped dict and hands it to an existing emission function meant for hand-authored entries (`axrange/overlays/default_agents.py` → `install-defense-agent`/`install-agent` emission in `pass1_emit.py`); any "default sweep reuses the authored path" pattern
**Consumers:** code-writer, code-reviewer
**Rule:** When an authored entry's emission derives its `after:` edges conditionally (e.g. a defense-agent op targeting the AD domain controller gets `after=domain-ready[$domain]` instead of the generic `host-ready[$host]`, because the DC's role arms a domain-scoped dependency), a SYNTHESIZED entry standing in for an authored one must run through the exact same conditional-scope derivation, not a simplified/default-only path — otherwise ordering edges silently drop for the synthesized case even though the op itself gets emitted and the deploy looks green.
**Origin:** CHANGELOG 2026-07-11 (A3 Stage A — default-agent sweep on the DC, replicating `install-defense-agent`'s domain-scope gating; code review)

## LES-056 — A per-target overlay fan-out must apply the SAME node-kind eligibility filter the blanket default sweep applies

**Class:** bug-class
**Scope:** any overlay/sweep code with two entry points into the same target set — an explicit per-entry fan-out (`overlays.adversary[].targets:`) and a blanket default sweep (`scenario.default_agents`) — where only one of the two filters out ineligible node kinds; `axrange/planner/pass1_emit.py` (adversary-overlay target processing vs. the default-agent sweep)
**Consumers:** code-writer, code-reviewer, tester
**Rule:** If a blanket sweep skips `kind: router`/`kind: switch` nodes because the underlying op requires QGA (only `kind: host` has it), any OTHER code path that can reach the same op via an explicit per-target list (a fan-out binding, not the sweep) must apply the identical kind filter — otherwise an author who names a router/switch in the explicit list produces an op that hangs at deploy time (waiting on a QGA channel that will never exist) instead of failing at validate time. This is why `REF-240` exists as a validator-side backstop, but the emission-side filter should still match the sweep's, defense-in-depth.
**Origin:** CHANGELOG 2026-07-11 (A3 Stage A — `overlays.adversary[].targets:` kind-eligibility fix + `REF-240`, code review)

## LES-057 — A content-hashed op_id gives DEDUP, not RE-ASSERTION; the resume-skip executor needs an explicit force-reset for a playout op whose binding can narrow or revert

**Class:** bug-class
**Scope:** any playout/config op whose `op_id` (or state-store key) is derived by hashing its CURRENT inputs, run through the standard resume-skip executor (skip `completed` op_ids on re-apply); `axrange/o2control/taps.py` (`force_reset_ops`)
**Consumers:** code-writer, code-reviewer
**Rule:** Hashing an op's content into its `op_id` is a good DEDUP mechanism (the same binding re-applied produces the same op_id, so a no-op re-apply correctly skips). It is NOT a re-assertion mechanism: if a binding narrows (fan-out drops a sensor) or reverts (goes back to a prior config), the op_id for the NOW-current binding may already exist in `completed` state from an EARLIER apply, and the resume-skip executor will skip it as already-done — silently leaving stale infrastructure (a gretap key, a mirror rule) wired for the wrong target. A playout that must GUARANTEE its infra matches its CURRENT binding on every apply needs to force-reset the authoritative ops back to `pending` before each run (`force_reset_ops`), not rely on op_id novelty alone.
**Origin:** CHANGELOG 2026-07-11 (A2 tap playout — a real traffic-mis-routing bug on narrow/revert, code review)

## LES-058 — A playout realized as a SEPARATE deploy does not inherit its target's teardown cascade; wire it explicitly or the "still cascades" design claim is false

**Class:** reversal-hazard
**Scope:** any design that splits a bound/dependent lifecycle (a tap-plan, a sidecar config) into its OWN deploy row (separate `deploy_id`, own state-store entry) rather than emitting inside the parent deploy; `axrange/planner/teardown.py` / `axrange/o2control/taps.py` (`cascade_destroy_for_te`)
**Consumers:** code-writer, code-reviewer
**Rule:** The engine's teardown only knows how to tear down the resources recorded against ONE `deploy_id`. The moment a dependent playout moves from "emitted inside the parent's deploy" to "its own separate deploy" (for independent apply/destroy/redeploy), the parent's `destroy` no longer automatically reaches it — a design document that still claims "a `te` destroy cascades to its `tap`" after this split is describing a NOT-YET-BUILT behavior unless an explicit cascade call (`cascade_destroy_for_te`, looked up by a deterministic `tap-<te_deploy_id>` plan-id convention) is added to the parent's destroy path. Verify the design doc's cascade claim against the actual destroy code, not the other way around.
**Origin:** CHANGELOG 2026-07-11 (A2 tap playout — the standalone `tap-<te>` deploy + `cascade_destroy_for_te`; reconciles `docs/design/06-taps-sensors.md`)

## LES-059 — Tearing down a tunnel endpoint without re-asserting the surviving mirror that names it leaves a dangling reference and state-vs-reality divergence

**Class:** bug-class
**Scope:** any teardown/de-scope path that removes one of several fan-out destinations (a gretap, a mirror output) while others survive; `axrange/o2control/taps.py`'s de-scope pass; sibling to LES-011 (OVS mirror append-not-replace) but at TEARDOWN time rather than attach time
**Consumers:** code-writer, code-reviewer, tester
**Rule:** When a fan-out mirror (`tc`/OVS `mirred`) chains multiple destination gretaps and one destination is removed (a sensor de-scoped out of a tap-plan), the SURVIVING mirror rule must be re-asserted (rebuilt without the removed destination), not left as-is — leaving it as-is means the mirror still references a gretap netdev that no longer exists, a state where the deploy reports success but the surviving destinations silently stop receiving mirrored traffic. A de-scope pass must always re-assert the full mirror shape for what remains, not just delete the one thing that's leaving.
**Origin:** CHANGELOG 2026-07-11 (A2 tap playout — the de-scope pass, code review)

## LES-060 — A tri-state `Optional[bool]` flag defaulting to ON must be tested with `is not False`, never truthiness; grep every read site when flipping a flag's default

**Class:** bug-class
**Scope:** any `Optional[bool] = None` IR/config field whose absence means "on" (not "off," which plain `if flag:` truthiness would imply); `axrange/ir/schema.py` `Scenario.default_agents`; any future default-flip of an existing opt-in field to opt-out
**Consumers:** code-writer, code-reviewer
**Rule:** A flag with three real states — `None` (unset, defaults ON), `True` (explicit ON), `False` (explicit OFF) — is broken by `if flag:` (treats `None` as falsy, i.e. OFF, the opposite of the intended default) and must be gated as `if flag is not False:` (only the explicit-off state suppresses it). When flipping an existing flag's default from opt-in to opt-out (or vice versa), grep every read site of that field across the codebase, not just the one the feature work touched — a stale truthiness check elsewhere silently reverts the new default.
**Origin:** CHANGELOG 2026-07-11 (A3 Stage A — `scenario.default_agents` default-ON, user decision 2026-07-11; `pass1_emit.py`'s `default_agents_flag is not False` gate)

## LES-061 — Flipping a synthesized-resource sweep from opt-in to default-on needs a dedup keyed on the same on-host artifact, canonicalized identically in the dedup check AND the render/backend

**Class:** bug-class
**Scope:** any "install X by default unless already present" sweep layered on top of an existing "install X if explicitly authored" mechanism, where both can target the same host; `axrange/overlays/default_agents.py` (`hosts_with_explicit_defense_tool`, `EXPORTER_TOOL_ALIASES`)
**Consumers:** code-writer, code-reviewer
**Rule:** When a default sweep is added alongside an existing explicit/authored mechanism that can produce the SAME on-host artifact (both `install-defense-agent`'s default Wazuh entry and a hand-authored `overlays.defense` entry write `/var/ossec/etc/ossec.conf`), the sweep must dedup against the authored entries by the artifact's real identity, canonicalized the SAME way on both sides of the comparison — an exact-string, case-sensitive `tool:` match (as currently implemented) is a known-incomplete canonicalization: an author writing `Node-Exporter` instead of `node-exporter` won't be recognized as "already covers the default," and the sweep double-installs. Track this explicitly as a gap (see `docs/PRODUCT_GAPS.md` GAP-014) rather than assuming exact-match dedup is complete.
**Origin:** CHANGELOG 2026-07-11 (A3 Stage A — `EXPORTER_TOOL_ALIASES` dedup, code review; `docs/PRODUCT_GAPS.md` GAP-014)

## LES-062 — An origin-URL renderer built as `x if "://" in x else f"http://{x}:PORT"` must be paired with operator docs that forbid `host:port` (no scheme) as input, and a fetch failure downstream must not be silently swallowed as deploy success

**Class:** silent-success-trap
**Scope:** any renderer that infers a URL scheme by string-sniffing `"://"` in an operator-supplied config value (`axrange/config_exec/render.py::render_sandcat_agent`'s `origin` construction from `overlays.adversary[].server`)
**Consumers:** code-writer, code-reviewer, live-verifier
**Rule:** A renderer that does `origin = server if "://" in server else f"http://{server}:PORT"` silently double-appends the port if the operator supplies `host:port` (no scheme) as `server:` — the branch that should have fired ("no scheme, add one") instead treats `host:port` as scheme-less too and produces `http://host:port:PORT`. Document unambiguously that the field accepts EITHER a bare host OR a full `scheme://host:port` URL, never `host:port`. Separately: because a wrong origin still renders a syntactically valid script, the in-guest agent's fetch-from-origin failure must not be swallowed as a deploy-success signal — it is currently a WARNING (not a failure), meaning a misconfigured origin reports `Result: COMPLETED` with the agent never actually checking in; treat this class of "the op succeeded but the thing it built doesn't work" as needing an explicit downstream verification step (cf. LES-001's capture-liveness precedent), not just a log line.
**Origin:** CHANGELOG 2026-07-11 (A3 Stage B — Sandcat origin renderer, code review)

## LES-063 — Publish an agent→server port on the protocol the server's CURRENT version actually listens on; a hardcoded `/udp` or `/tcp` suffix can't be fixed by changing the port number alone

**Class:** platform-quirk
**Scope:** any docker-compose/firewall port mapping for a third-party agent/server pair whose protocol has changed across major versions (Wazuh 4.x's agent-event channel `1514` is TCP; earlier Wazuh generations used UDP); `o2/compose/docker-compose.yml`
**Consumers:** code-writer, code-reviewer
**Rule:** When wiring a well-known agent/server port for a third-party product, verify the CURRENT major version's protocol, not folk knowledge from an earlier version or a stale blog post — Wazuh manager `1514` moved from UDP to TCP at 4.x, so a compose file (or firewall rule) hardcoding `/udp` for that port silently breaks agent enrollment no matter what the port NUMBER says, and the mistake won't surface until an agent tries to connect. Pin the protocol explicitly in the port mapping (`"1514:1514/tcp"`, not a bare `"1514:1514"` that inherits an assumed default) and cite the version this was verified against.
**Origin:** CHANGELOG 2026-07-11 (A3 Stage B — Wazuh manager port mapping, `o2/compose/docker-compose.yml`)

## LES-064 — A docker `healthcheck` that shells to `wget`/`curl` is invalid on a slim/distroless base image; use the interpreter that's actually guaranteed present, and don't trust a presence-only test to catch it

**Class:** tool-gotcha
**Scope:** any docker-compose `healthcheck:` stanza for a service built `FROM python:*-slim` (or any slim/distroless base) that shells `CMD-SHELL wget ...` / `curl ...`; `o2/compose/docker-compose.yml` (Caldera healthcheck)
**Consumers:** code-writer, code-reviewer, tester
**Rule:** `python:3.11-slim` (and similar slim bases) do not ship `wget` or `curl` by default — a healthcheck command referencing either exits 127 (command not found), which Docker reports as unhealthy FOREVER even though the service inside is running and serving correctly. Use an interpreter that IS guaranteed present in the image (`python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:PORT/', timeout=3) else 1)"` when the base is a Python image) instead of assuming common CLI tools are there. A compose-schema test that only checks "every service declares a `healthcheck:` key" (presence-only) will pass on a broken healthcheck command — if the test suite can't actually run `docker build`/`docker compose up` (author-box no-egress constraint), at minimum eyeball every healthcheck command against the base image's known contents, and note it as unverified-until-live-bring-up rather than claiming it's proven.
**Origin:** CHANGELOG 2026-07-11 (A3 Stage B — Caldera healthcheck, `o2/compose/docker-compose.yml`; `tests/test_o2_compose.py`'s presence-only coverage gap)

## LES-065 — A rendered-then-reparsed wire format split across an engine/leaf import boundary needs a round-trip test; the two sides can't share a constant, so they WILL drift silently without one

**Class:** verification-idiom
**Scope:** any producer/consumer pair separated by the LES-025 one-directional-import boundary (the engine renders a log line format; a downstream leaf subpackage parses it) — `config_exec/render.py`'s `[axr-truth]` marker emission vs. `axrange/measurement/`'s parser
**Consumers:** code-writer, tester
**Rule:** When an engine-side renderer emits a specific text/line format (`[axr-truth] run=<id> technique=<T> host=<h> ts=<..>`) and a SEPARATE leaf subpackage (which the engine must never import, per LES-025) parses that same format back out, the two sides cannot share a single format-string constant across the import boundary — so nothing prevents them drifting independently except a test that exercises BOTH: render a real marker with the renderer, feed it through the parser, and assert the round-trip recovers the original fields. Testing each side against its own hand-written fixture string proves each side is internally consistent, not that they agree with each other.
**Origin:** CHANGELOG 2026-07-11 (A4 measurement — `[axr-truth]` marker round-trip test, `axrange/measurement/`)
**Update (2026-07-13):** generalized beyond the text-log-line case by **LES-068** — the same round-trip
discipline applies to dict-shaped op `inputs_resolved`/`outputs` contracts persisted through the state
store, not just rendered text markers.

## LES-066 — Embed render-time values into generated Python source via `json.dumps`, not `repr`, so a shared-helper refactor doesn't masquerade as a behavior change

**Class:** pattern
**Scope:** any Python code-generator that interpolates a host-supplied string/value into a rendered `.py` script as a literal (`config_exec/render.py`'s renderers, including `_render_loki_tail_shipper`)
**Consumers:** code-writer, code-reviewer
**Rule:** When embedding a render-time value into generated Python source as a literal, prefer `json.dumps(value)` over `repr(value)` (or manual quoting) — both produce a syntactically valid Python string literal for ordinary inputs, but `json.dumps`'s quoting is STABLE and independent of the interpreting language's own repr conventions, so refactoring two renderers to share one helper (as happened when the sensor Loki shipper and the truth-marker shipper were unified under `_render_loki_tail_shipper`) doesn't produce an incidental quoting-style diff that a reviewer or a test has to reason about as a possible behavior change. Assert on the DECODED value in tests, not on the literal's exact quote-character choice.
**Origin:** CHANGELOG 2026-07-11 (A4 measurement — `_render_loki_tail_shipper` refactor, code review)

## LES-067 — The `PreToolUse` Edit/Write hook resolves `scripts/hooks.py` relative to the session's cwd, not the repo root; a subagent whose cwd ≠ repo root gets silent Edit/Write failures — **RESOLVED 2026-07-20**

**Class:** tool-gotcha
**Scope:** any Claude Code subagent session (Pipeline `/dev` batch agents especially) whose working directory is set to something other than the repo root (e.g. a session cwd of `docs/`); `.claude/settings.json`'s `PreToolUse` hook commands (`exec $PY scripts/hooks.py inject`, and siblings for `compile`/`stop`/`session-start`)
**Consumers:** code-writer, code-reviewer, doc-writer, tester — every subagent role
**Rule:** `.claude/settings.json`'s hook commands invoke `scripts/hooks.py` by a REPO-ROOT-RELATIVE path (`scripts/hooks.py`, not an absolute path or one anchored via `git rev-parse --show-toplevel`), executed by the invoking shell from whatever the CURRENT session's cwd happens to be. When a subagent's cwd is NOT the repo root (observed independently across four subagents in the same pipeline run, one with cwd `docs/`), every `Edit`/`Write` tool call silently fails with an error like "can't open .../docs/scripts/hooks.py" — the hook script simply isn't where the shell looked for it. The reliable workaround inside an affected session is to use `Bash` (heredocs / `python3 -c` / `sed`) instead of `Edit`/`Write` for that session. The DURABLE fix belongs in the harness, not per-session workarounds: anchor the hook invocation via `git rev-parse --show-toplevel` (or an absolute path baked in at settings-generation time) so it is cwd-independent — flagged here for whoever next touches `.claude/settings.json`, since a `doc-writer` session's remit doesn't extend to editing repo hook/permission configuration itself.
**Origin:** CHANGELOG 2026-07-11 (O2 build arc — confirmed across 4 independent subagent sessions during the A1–A4 + schedule work orders)

> **RESOLVED (CHANGELOG 2026-07-20).** All five `.claude/settings.json` hook commands
> (`SessionStart`, `PreToolUse` Edit/Write/MultiEdit, `PreToolUse` Bash commit-guard, `PostToolUse`
> Edit/Write/MultiEdit, `Stop`) now wrap the invocation as
> `sh -c 'R=$(git rev-parse --show-toplevel 2>/dev/null || echo .); PY=$([ -x "$R/.venv/bin/python" ]
> && echo "$R/.venv/bin/python" || echo python3); exec $PY "$R/scripts/hooks.py" <verb>'` — every path
> is resolved from the repo root via `git rev-parse --show-toplevel`, not the invoking shell's cwd
> (falls back to `.` outside a worktree, matching the prior behavior there). `scripts/hooks.py` itself
> was already cwd-independent (`REPO = Path(__file__).resolve().parent.parent`); the bug was purely in
> the invocation string. Functionally verified from `cwd=docs/`: the OLD-style command reproduced the
> bug (`python3: can't open file '.../docs/scripts/hooks.py'`, exit 2); the NEW-style command resolved
> and ran correctly (exit 0) for `session-start`, `inject` (with real `tool_input` stdin), and
> `commit-guard` (non-commit passthrough). See LES-087 for a self-lockout hazard hit while landing
> this fix.

## LES-068 — The round-trip-test discipline (LES-065) applies to ANY producer/consumer contract crossing a serialization boundary, not just rendered text log-line markers — including dict-shaped op `inputs_resolved`/`outputs` persisted through `state.db`

**Class:** verification-idiom
**Scope:** any producer/consumer pair where one op's `outputs` (or another op's `inputs_resolved`) is a structured (dict/list-of-dicts) value that a DIFFERENT reader consumes after a real persistence round-trip through the state store (`op_to_row`/`barrier_to_row`/`StateStore.insert_ops` and the corresponding read-back) — e.g. `axrange/ops/registry.py` op `outputs` read by a downstream consumer that isn't the op's own adapter handler, such as `axrange/o2control/taps.py`'s `_scan_te_inventory`/`_tapped_iface_set` reading `record-tap-intent`'s `tapped_nics`; generalizes LES-065 beyond the engine/leaf text-marker case to same-repo, same-import-direction op-to-op dict contracts
**Consumers:** code-writer, tester
**Rule:** The same idea as LES-065 applies even when both sides live in the same package and "obviously" share a schema in the code today: build a real plan, persist it through the ACTUAL serialization path the system uses at runtime (not a hand-built fixture dict standing in for it), read it back through the ACTUAL consumer parser, and assert the round-trip recovers the producer's original values field-for-field. A test that asserts the producer's shape against one fixture and the consumer's parser against a second, independently hand-written fixture proves each side is internally self-consistent, not that they still agree with each other after a refactor renames a field or changes a type on only one side. Applied directly in `test_record_tap_intent_producer_consumer_roundtrip` (`tests/test_o2_taps.py`) when `record-tap-intent` (OP-MIR-005) replaced `install-ovs-mirror`'s `tapped_nics` input as A2's tap-discovery source.
**Origin:** CHANGELOG 2026-07-13 (sensor-agnostic te build, `docs/WORK_ORDER_sensor_agnostic_te.md`)

## LES-069 — Retiring the plumbing that FEEDS a component without also retiring or re-wiring the component leaves a silent-blind path

**Class:** silent-success-trap
**Scope:** `axrange/planner/pass1_emit.py`, `axrange/ops/registry.py`, any op removal/retirement — especially OP-MIR ops and anything feeding a `sensors:`/`deploy-sensor`/agent-style downstream emission; generalizes to any change that deletes or reroutes an op another still-emitted op or deployed component (VM/agent/sensor) depends on for its cluster-side effect
**Consumers:** code-writer, code-reviewer, tester
**Rule:** When you remove the op(s) that supply a downstream component (here `attach-gre-tunnel`/`verify-capture` feeding the in-scenario `sensors:` VM), you MUST in the same change either (a) also remove/gate the now-unfed component's emission, (b) re-point it to the new supply, or (c) gate it with an assert/verify — never leave it emitting unchanged. A component that "stands up" but is starved of its input reports COMPLETED while doing nothing, and if you also removed the verify step there is nothing left to catch it. Grep every still-emitted op for `inputs_from_outputs`/`after` references to the removed op AND every deployed component (VM/agent/sensor) whose runtime function depended on the removed op's cluster-side effect — not just the ops directly named in the change's own description.
**Origin:** CHANGELOG 2026-07-13 (sensor-agnostic te review — `deploy-sensor` left emitting after `attach-gre-tunnel`/`verify-capture` removal; caught before ship by tier-gating on `plan.tier == "o2"`)

## LES-070 — A focused verify TE that doesn't exercise agent enrollment must set `default_agents: false` when its network has no egress

**Class:** pattern
**Scope:** any small/focused verify TE authored to prove one narrow mechanism (blast-radius, tap wiring, schedule timing, etc.) on a network with no `kind: router`/SNAT egress; `target-environments/` verify-scenario authoring in general; supersedes needing to re-discover GAP-015 per verify run
**Consumers:** code-writer, tester, live-verifier, whoever next authors a focused verify TE
**Rule:** `scenario.default_agents` defaults ON (LES-060) and its exporter agent apt-installs over egress the TE may not have. A focused verify TE that isn't testing agent enrollment itself should set `default_agents: false` explicitly, rather than relying on the scenario having a router — this was hit live again on 2026-07-15 (the blast-check TE hung on the exporter install; tap-verify/schedule-verify were fixed proactively by setting the flag before deploy). GAP-015 (`docs/PRODUCT_GAPS.md`) remains the tracked product-level gap; this lesson is the authoring-side workaround to apply every time a new narrow verify TE is written until GAP-015 is actually fixed.
**Origin:** CHANGELOG 2026-07-15 (O2 arc gated live-verify batch, dev45)

## LES-071 — A teardown step's log line can read as a failure while the underlying resource removal is still correct; don't equate "scary warning" with "not idempotent" without checking the resource directly

**Class:** tool-gotcha
**Scope:** `axrange/o2control/taps.py` / `axrange/planner/teardown.py`'s mirror de-scope path (the "mirror-descope host=X: install-ovs-mirror: no tapped NICs / gre_port resolved" line); any teardown step whose diagnostic message is emitted from a code path that ALSO independently performs the actual removal
**Consumers:** code-writer, code-reviewer, live-verifier
**Rule:** Every `o2 tap destroy` / te-destroy-cascade run on dev45 (2026-07-15) logged a "mirror-descope … no tapped NICs / gre_port resolved" line that reads exactly like a failed removal — but a direct `ovs-vsctl` check on the bridge each time confirmed the mirror WAS actually removed (`tebr_869293` had zero `tapm_` entries). The message is emitted from a discovery helper that can't re-resolve `tapped_nics` at teardown time (the TE's tap-intent state may already be gone by then), which is a real but COSMETIC gap — the actual removal runs on a different, still-correct code path. Before treating a scary teardown log line as a bug, verify the resource directly (`ovs-vsctl`, `qm config`, etc.); don't assume log severity tracks actual outcome. Flagged as a minor fix candidate (downgrade the message or resolve `tapped_nics` at teardown time) rather than urgent — no leak, no error result.
**Origin:** CHANGELOG 2026-07-15 (O2 arc gated live-verify batch, dev45 — every tap teardown run)

## LES-072 — A tap-plan's `target:` is a plan-hash-derived deploy-id, not a stable scenario name; ANY edit to the target TE's IR invalidates every tap-plan file that names it

**Class:** bug-class
**Scope:** `docs/design/06-taps-sensors.md` / `axrange/o2control/taps.py`'s tap-plan spec (`{target: <te-deploy-id>, bindings: [...]}`); any authored `tapplans/*.yaml` file kept alongside a TE for repeated `o2 tap apply` runs; `docs/RUNBOOK_o2_live_verify.md`
**Consumers:** code-writer, live-verifier, whoever authors/maintains a tap-plan file across more than one deploy of the same TE
**Rule:** A TE's deploy-id is derived from its plan hash (LES-030's "plan-stable ID, not deploy-stable" pattern applies here too), so a tap-plan's `target:` field — which hardcodes that deploy-id — silently goes stale the moment the TE's IR changes for ANY reason, even one unrelated to tapping (e.g. flipping `default_agents: false` per LES-070). This was hit live on 2026-07-15: adding `default_agents: false` to `tap-verify.yaml` changed its deploy-id from `tap-verify-8e6d299a` to `tap-verify-c82bd2b5`, silently orphaning `deploy-dev45/tapplans/plan1.yaml`'s hardcoded `target:` until it was manually resynced. Treat a tap-plan's `target:` as needing a resync check (`axrange list-deploys` cross-referenced against the tap-plan file) any time the target TE's IR is touched, not just when tapping itself changes — this is a standing authoring trap, not a one-off mistake, and is called out explicitly in `docs/RUNBOOK_o2_live_verify.md` now.
**Origin:** CHANGELOG 2026-07-15 (O2 arc gated live-verify batch, dev45 — tap-plan resync after a `default_agents` edit)

**Partially addressed 2026-07-25 (`docs/WORK_ORDER_tap_target_resolution.md`):** a tap plan's
`target:` can now be authored as a stable scenario/twin-config **name** instead of a deploy id — it
resolves to the newest LIVE `te` deploy of that scenario at apply time (`resolve_target_deploy`), and
a plan may omit `target:` entirely in favor of a `--target <name>` CLI/API override. Either form
sidesteps this staleness trap, because the plan file never hardcodes a deploy id to go stale in the
first place. This lesson stays ACTIVE for the case it still applies to: a plan that hardcodes a
LITERAL deploy id as `target:` is exactly as staleness-prone as before — name resolution is an
authoring choice, not a structural fix that makes literal-id plans safe.

## LES-073 — Comparing observed live-guest fire intervals against the seeded jitter FORMULA is a valid substitute for a literal same-seed redeploy, when the sequence is deploy-invariant by construction

**Class:** verification-idiom
**Scope:** any seeded-reproducibility claim (`Schedule.interval` + `Activity.seed`, `jitter_sequence_from_activity`) where the live-verify checklist calls for "redeploy with the same seed and confirm identical sequence"; `docs/WORK_ORDER_schedule.md` §4
**Consumers:** live-verifier, doc-writer, whoever next designs a determinism live-verify step
**Rule:** If the sequence a seeded mechanism produces is PROVABLY deploy-invariant (a pure function of `seed`+`activity_id`+bounds, computed identically regardless of which deploy_id ran it — true for `jitter_sequence_from_activity`), then confirming ONE live run's observed intervals match the formula's output is logically equivalent to confirming two independent deploys would match each other — a literal second deploy adds no additional proof, only wall-clock cost. This is a legitimate, cheaper substitution for a live-verify checklist step, PROVIDED it is recorded HONESTLY as a substitution (not silently presented as if the literal step ran) — see CHANGELOG 2026-07-15's SCHED entry, which states plainly that the redeploy itself was not run. Do not generalize this to mechanisms whose reproducibility depends on external state (network timing, adapter races) rather than a pure seeded function — those still need the literal two-run comparison.
**Origin:** CHANGELOG 2026-07-15 (O2 arc gated live-verify batch, dev45 — SCHED phase determinism check)

## LES-074 — `pip check` cannot catch a runtime dependency incompatibility inside declared-but-loose version ranges; only a real request against a real route can, and a swallowed exception needs an un-swallow, not a guess

**Class:** verification-idiom
**Scope:** any Python service whose framework/library chain reports errors through a generic top-level handler that logs a bare "something went wrong" instead of the real traceback (`aiohttp`'s default 500 handler is the concrete case — `o2/caldera/Dockerfile`); any pinned-`requirements.txt` build where an incompatibility is suspected among the pins themselves rather than in an unpinned transitive
**Rule:** `axrange/caldera:5.0.0` returned a bare 500 with **no traceback** on every HTTP request; `pip check` reported the installed set as internally consistent (all declared version-range constraints were satisfied) because the real culprit — `yarl>=1.13` rejecting a `Host:` header containing `:`, breaking `aiohttp==3.9.3`'s `BaseRequest.url` — was an UNPINNED transitive dependency whose resolved version simply happened to violate an assumption none of the pinned packages' own metadata declared. `pip check` only validates declared constraints against each other; it cannot know that `aiohttp==3.9.3` silently assumes `yarl<1.13` unless that's actually pinned somewhere. The only thing that caught this was monkeypatching `aiohttp`'s `RequestHandler.handle_error` (or equivalent) to print the swallowed traceback from a real in-process request — a swallowed/logged-generically exception does NOT mean "no traceback exists," it means the traceback is being thrown away by a handler designed for production hygiene, not debuggability. Before trusting a plausible-sounding hypothesis about *which* pinned package is wrong (this bug's own work order originally guessed `apispec`/`webargs` version skew and was wrong), un-swallow the real exception first — it is cheaper than iterating on guesses, and it is the only way to catch an unpinned-transitive class of bug that `pip check` structurally cannot see.
**Origin:** CHANGELOG 2026-07-15 (O2 server-stack bring-up — Caldera GAP-016 `yarl`/`aiohttp` root-cause diagnosis, overturning the work order's `apispec`/`webargs` hypothesis)

## LES-075 — A `docker build`-time smoke gate that boots a background server + probes + kills it in one `RUN` must not run under `set -e` (the post-kill `wait` returns non-zero and would false-fail before your own exit code); heredoc-in-`RUN` parses fine under podman/buildah's classic builder

**Class:** tool-gotcha
**Scope:** any Dockerfile `RUN` step that backgrounds a server process, probes it, then kills it and inspects the probe's own exit status in the same shell invocation (`o2/caldera/Dockerfile`'s build-time smoke gate is the concrete case); any `RUN` using a `<<'EOF'` heredoc under podman 5.8.2/buildah's classic (non-BuildKit) parser
**Rule:** A pattern like `python3 server.py & PID=$!; python3 - <<'PY' ; STATUS=$? ; kill "$PID" ; wait "$PID" ; exit $STATUS` depends on `wait` after `kill` returning a shell exit status that reflects "process was killed" (128+SIGTERM, typically 143) rather than the probe script's own `$STATUS` — capture `$STATUS` from the probe BEFORE the `kill`/`wait` sequence and `exit` that captured value explicitly (not the trailing command's own exit code), and do not add a leading `set -e` to this class of `RUN` (a `wait` on a just-killed PID legitimately returns non-zero and `set -e` would abort the RUN before your explicit `exit $STATUS` line ever runs) — the base image here (`python:3.11-slim`, shell-form `RUN`, dash) has no `-e` by default, so this is a "don't add it," not a "remove it" note; flag it if a future refactor moves to an explicit `bash -c` or adds `SHELL ["/bin/bash", "-euo", "pipefail", "-c"]`. Separately: a shell heredoc (`<<'PY' ... PY`) embedded inside a backslash-continued multi-line `RUN` parses correctly under podman 5.8.2 / buildah's classic Dockerfile parser with no `# syntax=docker/dockerfile` BuildKit directive needed — don't assume heredoc-in-RUN requires BuildKit before trying it. Related live-env fact worth flagging for whoever next touches the compose `caldera` service's stop/restart behavior: Caldera's `server.py` does not trap `SIGTERM` (the smoke gate's plain `kill` here works because the build-time process is short-lived and exits on its own once probed; a long-running compose service may need `stop_grace_period` tuned, or `docker/podman compose stop` will wait out the full grace period before SIGKILL on every teardown).
**Origin:** CHANGELOG 2026-07-15 (O2 server-stack bring-up — Caldera GAP-016 build-time smoke gate, `o2/caldera/Dockerfile`)

## LES-076 — A `distroless/*:debug`-tagged base image DOES bundle busybox (incl. `wget`); don't false-flag a healthcheck against it as an LES-064 violation without checking the actual base build

**Class:** tool-gotcha
**Scope:** LES-064 companion — any docker-compose `healthcheck:` audit against a base image whose tag includes `:debug` (Google's `distroless/*` family specifically: `distroless/static:debug`, `distroless/base:debug`, etc.); `grafana/loki:3.3.2`'s image in `o2/compose/docker-compose.yml`
**Rule:** LES-064 established that a `wget`/`curl` healthcheck is invalid on a slim/distroless base because those tools usually aren't present — but "distroless" is not one uniform thing: Google's `distroless` images ship a `:debug` variant specifically to add a busybox shell + common utilities (including `wget`) for exactly this kind of operational need, while the plain (non-debug) tag has none of that. `grafana/loki:3.3.2` builds `FROM gcr.io/distroless/static:debug`, so its `wget`-based healthcheck is CORRECT, not a LES-064 violation, even though "distroless" in the base name would trigger the LES-064 heuristic on a skim. Before flagging any distroless-based healthcheck, check which exact tag the image was actually built from (`docker history` / the upstream Dockerfile), not just "distroless" as a keyword match.
**Origin:** CHANGELOG 2026-07-15 (O2 server-stack bring-up — five-service compose healthcheck audit, `o2/compose/docker-compose.yml`)

## LES-077 — An "authored but never actually built/run" Dockerfile or compose stack's FIRST real build/bring-up will surface preconditions no earlier review caught; a git submodule UI plugin that ships source-only needs its OWN build step, and a bind-mount an init-time process writes into cannot also be `:ro`

**Class:** silent-success-trap
**Scope:** any component whose header/comments say "AUTHORED + NOT BUILT HERE" or similar (this stack's stated posture, `o2/README.md` "author + validate; live bring-up is human-gated"); any Dockerfile that `git clone --recursive`s a submodule containing a frontend/UI plugin (Caldera's `magma` submodule is the concrete case) without a build step for it; any docker-compose volume mount that a container's own init-time process (not just the operator) writes into
**Rule:** Two related first-real-build findings from `o2/caldera/Dockerfile` and `o2/compose/docker-compose.yml`, both invisible to a config-only/schema-only review because neither is a syntax error: (1) Caldera's `magma` web-UI plugin is a git submodule shipping SOURCE only — `app/api/rest_api.py` unconditionally serves `plugins/magma/dist/assets/` at startup, so a Dockerfile that clones the submodule but never runs `npm ci && npm run build` inside it crashes the server on EVERY boot with `ValueError: No directory exists at '.../plugins/magma/dist/assets'`, closing the loop GAP-016's write-up left open ("LES-064's neighbor finding"). (2) Grafana's `GF_INSTALL_PLUGINS` env var writes plugin archives into `/var/lib/grafana/plugins` at container init — the SAME path `o2/compose/docker-compose.yml` bind-mounts the repo's `../grafana/plugins` directory into, read-only; a `:ro` mount there makes Grafana fail to start on every boot (`failed to extract plugin archive: ... read-only file system`). Both bugs are structurally the same class: a component authored and schema/syntax-validated but never actually executed will have precondition bugs (an unbuilt artifact a runtime path assumes exists; a mount-mode conflict between two writers of the same path) that only a REAL `docker build` / `docker compose up` surfaces — budget for a first-real-build pass as its own verification step, not something a code review or a compose-lint can substitute for.
**Origin:** CHANGELOG 2026-07-15 (O2 server-stack bring-up, closing the GAP-016 "LES-064's neighbor finding" pointer); precondition fixes present in `o2/caldera/Dockerfile` (magma build) and `o2/compose/docker-compose.yml` (grafana plugins bind-mount) as of this entry

## LES-078 — A `pkill -f <pattern>` self-kill risk isn't only "the invoking shell's own argv contains the pattern" (LES-051) — a DIFFERENT delivery-path file whose name is a superstring of the pattern can match too

**Class:** bug-class
**Scope:** `axrange/config_exec/render.py`'s `_render_loki_tail_shipper` (and any future renderer that derives a `pkill -f <pattern>` target from a path also used, with a suffix appended, as the delivery wrapper's own filename); `ConfigBackend._deploy_sensor`'s convention of writing a bootstrap to `<artifact_path> + ".sh"`; any QGA-delivered "kill prior instance" bootstrap in general
**Consumers:** code-writer, code-reviewer, tester
**Rule:** LES-051 already covers one self-kill shape (the invoking shell's own `sh -lc "..."` argv containing the literal pkill pattern). This is a SIBLING, distinct shape: the bootstrap script here derived its `pkill_pattern` from `shipper_path` (e.g. `.../ship-<id>.py`), while the wrapper script actually delivering that bootstrap over QGA is written to `shipper_path + ".sh"` (`.../ship-<id>.py.sh`, `ConfigBackend._deploy_sensor`'s convention) — `.py` is a literal substring of `.py.sh`, so `pkill -f 'ship-<id>.py'` matched and SIGTERMed the WRAPPER FILE ITSELF (a real file being executed by path, not an inline shell argv) before it ever wrote or launched the actual shipper. Neither LES-051's fix (deliver as a written script, exec by path) nor a plausible-looking code review would catch this, because the wrapper here ALREADY WAS a written script — the bug is that the pattern derivation didn't account for a *different, related* file elsewhere in the same delivery chain sharing a substring relationship with it. Rule going forward: before deriving any `pkill -f <pattern>` target from a path, enumerate every OTHER file in the same delivery/bootstrap chain and check none of them contain the pattern as a substring — or better, avoid `pkill -f <path-derived-pattern>` entirely in favor of a named, addressable mechanism (see LES-079).
**Origin:** CHANGELOG 2026-07-15 (O2 server-stack bring-up / A4 live-verify — GAP-020, `axrange/config_exec/render.py`'s `_render_loki_tail_shipper` fix)

## LES-079 — A backgrounded `nohup ... &` launched over a QGA (or any execute-and-disconnect) channel is reaped when the delivering session ends; use a detached, named `systemd-run --unit=<id>` transient instead

**Class:** platform-gotcha
**Scope:** any config-class op delivering a long-running background process over `qemu-guest-agent` exec (`axrange/config_exec/`'s renderers in general — sensor Loki shippers, activity truth shippers, and any future "launch a daemon over QGA" pattern); any other execute-and-disconnect delivery channel with the same property (a one-shot RPC exec, not an interactive session)
**Consumers:** code-writer, code-reviewer, tester
**Rule:** `nohup <cmd> >log 2>&1 &` is the standard Unix idiom for "background this and let me disconnect" over an INTERACTIVE session (SSH, a terminal) — but `qemu-guest-agent`'s `exec` RPC is not an interactive session, it is closer to a one-shot fire-and-collect call, and this was proven live on dev45 (2026-07-15, GAP-020): a `nohup`-backgrounded shipper process did NOT survive past the QGA exec call that launched it, while the SAME logical launch, done instead as a detached, deterministically-named transient unit (`systemd-run --unit=<stable-id> --collect python3 <script> ...`), DID survive. Prefer `systemd-run --unit=<id> --collect` (with a `setsid ... </dev/null &` fallback for a guest with no systemd) over `nohup ... &` for any process that must outlive a QGA exec call — and give it a STABLE, per-instance-derived unit name so a redeploy can `systemctl stop <id>` / `systemctl reset-failed <id>` it idempotently before relaunching, with no `pkill` needed at all (closes the LES-078 self-kill class structurally, not just for this one bootstrap).
**Origin:** CHANGELOG 2026-07-15 (O2 server-stack bring-up / A4 live-verify — GAP-020, `axrange/config_exec/render.py`'s `_render_loki_tail_shipper` fix, live-proven on dev45 VM 102)

## LES-080 — A co-process that refuses to start on a config file it doesn't own (uid=0) can't be satisfied by a git-tracked bind mount; disable it at the supervisor level instead

**Class:** tool-gotcha
**Scope:** `o2/compose/docker-compose.yml`'s `wazuh-manager` service (filebeat co-process, GAP-017); any container image whose init system (s6, supervisord, etc.) auto-starts a co-process that validates the OWNERSHIP of a config file before reading it; any docker-compose bind mount of a git-tracked file into a path such a process reads
**Consumers:** code-writer, code-reviewer, live-verifier
**Rule:** the first fix attempt for GAP-017 (a stub `filebeat.yml` bind-mounted read-only, empty inputs/no indexer output) was necessary but NOT sufficient: filebeat 8.x (bundled in `wazuh/wazuh-manager:4.9.2`) refuses to start on a config file it does not own (`"config file (...) must be owned by the user identifier (uid=0) or root"`), and a file tracked in git and delivered via a docker-compose bind mount necessarily carries the HOST's file ownership (uid 1000 on the build/dev box here) — git has no mechanism to force a checked-out file to be owned by a specific uid, so this class of "the co-process demands root ownership of its config" cannot be satisfied by ANY git-tracked bind-mounted file, no matter how correct its contents are. The durable fix is to disable the co-process at the SUPERVISOR level instead of trying to satisfy its config-loading path at all — for an s6-managed image, an empty `down` file at `/etc/services.d/<service>/down` (here: `o2/wazuh/filebeat-down` → `/etc/services.d/filebeat/down:ro`) stops the service from ever launching, so it never reaches the ownership check that was crash-looping the container. Recognize this class early: if a "just mount a valid config" fix doesn't hold on first real live bring-up, check whether the co-process demands file ownership before reading, not just valid syntax.
**Origin:** CHANGELOG 2026-07-15 (O2 server-stack bring-up — GAP-017, `o2/wazuh/filebeat-down`, live-proven on dev45 VM 102)

## LES-081 — A Proxmox VM's default `kvm64` CPU model lacks the x86-64-v2 baseline modern glibc-linked images assume; use `-cpu host` (or an explicit v2+ model) for any VM expected to run recent Debian/glibc binaries

**Class:** live-env-fact
**Scope:** any Proxmox VM cloned/created without an explicit `cpu:` override (defaults to `kvm64`); any workload built against a glibc/toolchain baseline assuming x86-64-v2 (Debian 12+/Ubuntu 22.04+ era binaries increasingly do, `wazuh/wazuh-manager:4.9.2`'s bundled glibc is the concrete case here); the O2-services VM stand-up procedure (`docs/WORK_ORDER_o2_server_stack.md` §2)
**Consumers:** live-verifier, whoever next provisions a Proxmox VM meant to run a modern container/binary stack
**Rule:** Proxmox's default `kvm64` CPU model is a maximally-portable baseline (targets the original x86-64 ISA, no v2/v3/v4 feature levels) chosen for cross-hardware live-migration compatibility, not for running modern software — and it silently under-provisions CPU features until something actually needs them and fatal-errors. Hit live on dev45 VM 102 (2026-07-15): the Wazuh manager container fatal-errored on boot (`Fatal glibc error: CPU does not support x86-64-v2`) purely because the VM's CPU model was the Proxmox default `kvm64`, not because of anything wrong in the container/compose config. Fixed with `qm set 102 -cpu host` (exposes the physical host's full feature set to the guest) + a reboot. Any VM being stood up specifically to run modern glibc-linked workloads (most current container images) should get an explicit `-cpu host` (or at minimum a v2-or-higher named model) at creation time, not left on `kvm64` — don't wait for a fatal-glibc-error to discover this per VM.
**Origin:** CHANGELOG 2026-07-15 (O2 server-stack bring-up — the O2-services VM stand-up, `docs/WORK_ORDER_o2_server_stack.md` §2, live-proven on dev45 VM 102)

## LES-082 — A shared DB `tier` column across two structurally-different deploy shapes needs a secondary STRUCTURAL discriminator on EVERY read path, not only destroy — and the subset test must reject the vacuous empty-set match

**Class:** bug-class
**Scope:** `axrange/planner/state.py` (`deploys_matching` and any future tiered query); `axrange/o2control/taps.py` (`is_tap_plan_deploy`, `_is_tap_shaped_op_names`); `axrange/cli/main.py` (`_latest_te_deploy`, `cmd_list_deploys`, and any future default-pick/status/info handler); any future deploy shape that reuses an existing `tier` value for a structurally distinct kind of deploy
**Consumers:** code-writer, code-reviewer
**Rule:** Extends LES-054/LES-053's guard discipline from *destroy* to *every* read path. A tap-plan playout deploy is stamped `tier="te"` (the state store has no separate `"tap"` deploy-level tier) and named `tap-<target-te-deploy-id>`, so ANY query that does `WHERE tier = 'te'` to find "the latest real TE" — not just `destroy_taps`'s original guard — will wrongly surface a tap-plan playout unless it also excludes the `tap-` `plan_id` prefix. But the prefix alone is a NAMING convention a genuine TE scenario could coincidentally collide with (`plan_id="tap-foo"`), so any path that needs to positively *identify* tap-plan deploys (not just exclude them from a TE default) must additionally check a STRUCTURAL discriminator: the deploy's persisted op-name set is a non-empty subset of exactly the ops a tap-plan can ever carry (`{attach-gre-tunnel, install-ovs-mirror, verify-capture}`). Audit every path that picks a default/latest/filtered deploy for a tier that overloads onto another tier's column value — read (`status`/`info`), list (`list-deploys --tier`), and default-pick (bare-arg commands) — not only the destroy guard where this class of bug was first caught (LES-054). Critically, the subset test must be `bool(op_names) and op_names <= ALLOWED_SET`, never bare `op_names <= ALLOWED_SET`: the empty set is a vacuous subset of any set, so a deploy with zero recorded ops yet (e.g. mid-create, or a genuine TE literally named `tap-*` before its first op lands) must not structurally pass as tap-shaped merely because it hasn't recorded anything yet.
**Origin:** CHANGELOG 2026-07-16 (CLI lifecycle unification — the `te` vs. `tap-<te>` read-path disambiguation across `te status`/`te info`/`te destroy`/`list-deploys --tier`, code review)

## LES-083 — A `--json` command sharing progress-printing helpers with human output needs an explicit `quiet=` path threaded to EVERY json-capable branch, not just the tested one; on failure, emit the diagnostic to stderr and rely on the exit code, never half-JSON on stdout

**Class:** pattern
**Scope:** `axrange/cli/main.py` (`_load_planning_inputs`'s `quiet=` parameter, `cmd_te_info --json`, and any future CLI verb that adds a `--json` output mode over a code path shared with a human-readable progress-printing verb); any CLI whose `--json` mode reuses helper functions originally written only for human console output
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A helper like `_load_planning_inputs` that normally writes progress lines to stdout ("Validating ...", "  validate: ...") for a human-facing verb (`te plan`/`te deploy`) becomes a correctness bug, not just a cosmetic one, when a NEW `--json` verb (`te info --json`) reuses it: an unconditional progress line printed before the JSON payload corrupts a consumer's `stdout` parse (the whole point of `--json` is a script/pipe reading exactly one JSON document from stdout). Fix this by threading an explicit `quiet=` parameter through the shared helper — but audit EVERY call site the helper has, not just the one branch under active test, since a `--json`-capable command often has more than one path into the same helper (e.g. a file-path/spec branch vs. an id/live-deploy branch) and it is easy to quiet the first without noticing the second doesn't call the helper at all (and so needs no quieting) or does (and does). On a FAILURE path, the same discipline applies in reverse: never print a partial/empty JSON object to stdout on error — write the diagnostic to stderr as usual and rely on a non-zero exit code, so a consumer parsing stdout either gets a complete, valid JSON document or nothing at all, never a corrupted or half-written one.
**Origin:** CHANGELOG 2026-07-16 (CLI lifecycle unification — `te info --json` quiet threading through `_load_planning_inputs`, code review)

## LES-084 — "Latest" over a resumable/revivable row must order by a field that advances on every transition (`updated_at`), not create-time (`started_at`); classify EVERY lookup call site as creation-resume vs. teardown/read, and prefer the newest NON-terminal row for a destroy bare-default

**Class:** bug-class
**Scope:** `axrange/planner/state.py` (`latest_deploys`, `latest_deploy_for_tier`, `deploys_matching`, `find_deploy_for_plan`, and any future "latest of X" query over the `deploys` table or an equivalent resumable/revivable row); `axrange/cli/main.py` (`cmd_deploy`, `_latest_te_deploy`, `cmd_destroy`, `cmd_o2_destroy`, `cmd_tap_destroy`, and any future bare-default resolver); `axrange/api/service.py` (`create_deploy`); `axrange/o2control/{control,taps}.py` (`stand_up_surface`, `apply_tap_plan`, `latest_tap_plan_deploy`); any future table with a create-time column that a resume/revive path does NOT touch, plus a terminal-transition column that DOES
**Consumers:** code-writer, code-reviewer, live-verifier
**Rule:** A row that can be resumed/revived (its identity persists across re-runs, e.g. a deploy keyed by a deterministic `plan_id`) keeps its ORIGINAL create-time timestamp forever — only a mutation-time timestamp (`updated_at`) advances on each transition. Ordering "latest" by create-time alone therefore silently misranks a resumed/revived row behind an unrelated row that merely started more recently — live-repro'd on dev45 (2026-07-16, CHANGELOG "CLI bare-default deploy-targeting safety fix"): a bare `te destroy` printed `Result: DESTROYED` against a STALE deploy while the just-resumed/just-created one stayed live. Fix in two parts, both required: (1) order by `COALESCE(updated_at, started_at) DESC`, not `started_at DESC`, everywhere a "latest" query backs a bare default; (2) a TERMINAL row (here: `destroyed`) must be classified per call site, not treated uniformly — on the DEPLOY-CREATION resume path (`te deploy`/`o2 deploy`/`tap deploy`/`create_deploy`, i.e. anything NOT passed `--fresh`) a terminal top match is NO-MATCH (fall through to minting a fresh id, exactly as `--fresh` already does — never silently revive a destroyed row in place), while on a TEARDOWN or READ-ONLY path (`destroy_taps`'s target lookup, `cascade_destroy_for_te`, `status`/`info` bare-defaults) the terminal row must stay visible (idempotent-no-op / "inspect what you just destroyed" are both legitimate). Audit every lookup call site individually against this two-way split — don't assume one `exclude_destroyed` default serves all of them. Finally, because the terminal transition ITSELF bumps `updated_at` (e.g. `mark_destroyed`), a just-destroyed tombstone becomes the newest row under the corrected ordering — so a DESTROY verb's bare-default specifically must additionally prefer the newest NON-terminal row (a second, narrower `exclude_destroyed=True` query) and print an explicit "already destroyed; nothing to do" rather than resolving to the tombstone and reporting a misleading `DESTROYED` while a genuinely newer, live row of the same kind goes untouched.
**Origin:** CHANGELOG 2026-07-16 (CLI bare-default deploy-targeting safety fix — dev45 live smoke on `deploy-dev45/scenarios/blast-check.yaml`, following the CLI lifecycle unification build; `axrange/planner/state.py`, `axrange/cli/main.py`, `axrange/api/service.py`, `axrange/o2control/{control,taps}.py`)

## LES-085 — `tcpdump -C` appends its size-rotation counter AFTER the `-w` filename's extension; any pcap-management glob must be extension-agnostic, and must be exercised against a real `-G`+`-C` run to catch it

**Class:** tool-gotcha
**Scope:** any shell glob/`find` pattern over a directory of tcpdump-rotated pcaps (`cap-*.pcap` vs `cap-*`); `axrange/config_exec/render.py` (`_render_capture_pcap_watchdog`, `render_sensor_setup`'s tcpdump-sensor branch); `o2/tcpdump-sensor/bin/capture-watchdog` and any future port of it; any future sensor/appliance retention or reaping logic built on top of tcpdump's own rotation
**Consumers:** code-writer, code-reviewer, tester, live-verifier
**Rule:** with `tcpdump -w cap-%Y%m%d-%H%M%S.pcap -G <seconds> -C <MB>`, the strftime template in `-w` is expanded only when a TIME rotation (`-G`) fires, producing a fresh base filename `cap-<ts>.pcap`; a SIZE rotation (`-C`) firing before the next `-G` tick does NOT re-expand the template — it instead appends a bare numeric suffix AFTER the full filename INCLUDING its extension: `cap-<ts>.pcap`, `cap-<ts>.pcap1`, `cap-<ts>.pcap2`, .... An extension-anchored glob like `cap-*.pcap` therefore matches only the first file of each `-G` window and silently misses every `-C`-rotated sibling — under sustained traffic, the majority of a busy sensor's pcaps become invisible to a retention/prune/list/ship script built on that glob, defeating disk-based retention with no error anywhere (the watchdog runs, finds "nothing to prune," and the disk fills). Caught live 2026-07-17 in the standing-O2-sensor capture fix (`_render_capture_pcap_watchdog`'s glob was `cap-*.pcap`, changed to extension-agnostic `cap-*`, safe here because the capture directory holds only this sensor's pcaps). The trap is compounded by test coverage: light/idle traffic in dev or a short live-verify window rarely triggers a `-C` size rotation at all, so a glob bug like this passes every quick smoke test and only surfaces under real sustained load — any pcap-glob claim needs a dedicated `-G`+`-C`-forcing test (or a live run long/busy enough to force at least one `-C` rotation), not just a rotation-config presence check.
**Origin:** CHANGELOG 2026-07-17 (Standing O2 sensor capture supervision + retention fix — code review MUST-FIX, `axrange/config_exec/render.py`)

## LES-086 — A background `nohup … &` delivered over a QGA guest-exec session is reaped when the session ends; launch in-guest daemons as named, detached transient systemd units instead

**Class:** pattern
**Scope:** any in-guest daemon/long-running process launched via `GuestAgent.exec`/`ga.exec` (`axrange/config_exec/render.py`'s render functions — `render_sensor_setup`, `_render_loki_tail_shipper`, `render_activity_truth_shipper`, and any future in-guest bootstrap that starts a process meant to outlive the delivering command); `axrange/config_exec/backend.py` (`ConfigBackend` handlers that deliver a render over QGA)
**Consumers:** code-writer, code-reviewer
**Rule:** generalizes GAP-020 beyond the one shipper it was first found on: a process started with a bare backgrounded `nohup <cmd> >log 2>&1 &` inside a qemu-guest-agent `exec` call is a CHILD of that exec session, and is REAPED the moment the session that launched it ends — `nohup` alone (which only blocks SIGHUP) does not save it, because QGA's session teardown is not a SIGHUP, it is the session's process group going away. This affected tcpdump and zeek in `render_sensor_setup` (fixed 2026-07-17, following the identical fix already applied to the Loki shipper for GAP-020) — a standing sensor's capture daemon appeared to start (the `nohup` command itself returned success) but was silently dead moments after the deploy op completed, with no error anywhere in the op's result. The durable fix is to launch any such daemon as a DETACHED, DETERMINISTICALLY-NAMED transient systemd unit — `systemctl reset-failed U; systemctl stop U; systemd-run --unit=U --collect bash -c '<cmd> >log 2>&1'` — followed by a fail-loud `systemctl is-active --quiet U || exit 1` liveness check that dumps the unit's journal + log on failure. This is BOTH a correctness fix (survives the QGA session boundary) and a supervision upgrade (systemd tracks the daemon's real pid directly, so `is-active` is a stronger liveness check than an argv/comm `pgrep` match) AND a simplification (`--collect` makes a re-deploy's restart idempotent with no `pkill`+reap race — there is no `pkill` needed at all, sidestepping LES-051's self-collision concern entirely). Audit EVERY render function that starts a background process over QGA for the bare `nohup … &` pattern, not just the one instance a bug report names — this is a repeating shape, not a one-off.
**Origin:** CHANGELOG 2026-07-17 (Standing O2 sensor capture supervision + retention fix — dev45 investigation finding tcpdump/zeek dead post-deploy; `axrange/config_exec/render.py`, generalizes GAP-020)

## LES-087 — Editing a `PreToolUse` hook command in `.claude/settings.json` is a self-lockout hazard: a broken hook (exit code 2) blocks the very tools needed to fix it

**Class:** tool-gotcha
**Scope:** `.claude/settings.json`'s `hooks.PreToolUse` entries (any matcher — `Edit|Write|MultiEdit`, `Bash`), and by extension any harness config whose own correctness gates the tool calls needed to edit it; whoever next hand-edits a hook `command` string
**Consumers:** code-writer, code-reviewer, tester, doc-writer — anyone touching `.claude/settings.json` hooks
**Rule:** while landing the LES-067 fix, an intermediate edit to a `PreToolUse` hook command dropped the space between `hooks.py"` and the verb, producing a malformed shell string (`hooks.py"inject`, `hooks.py"commit-guard`). A `PreToolUse` hook that exits non-zero (here, code 2 from the shell's own parse/exec failure) **blocks the tool call it gates** — and because the same malformed edit sat on BOTH the `Edit|Write|MultiEdit` matcher and the `Bash` matcher simultaneously, it instantly locked the main session out of Edit, Write, AND Bash at once (Read still worked, since no hook gates it). This is a genuine self-lockout: fixing the broken hook string requires Edit/Write (to fix the JSON) or Bash (to sed/python3 -c it), both blocked; spawning a subagent to do the repair was denied by the permission classifier. The only way out was a **user-run `!`-prefixed shell command** (bypasses the model's own `PreToolUse` hooks) — or, as a fallback, `git checkout .claude/settings.json` to discard the broken edit outright. Mitigations for next time: (a) test a hook command's exact shell string via a throwaway `Bash` run (or `sh -n`) BEFORE writing it into `settings.json`, never edit the live hook string directly and hope; (b) keep any hook-command edit minimal and mechanically diffable (e.g. a scripted string substitution) rather than a hand-typed rewrite, to avoid exactly this kind of one-character slip; (c) know the escape hatch going in — a user `!` command or `git checkout .claude/settings.json` — before you need it, since neither is available to the model mid-lockout.
**Origin:** CHANGELOG 2026-07-20 (hook invocation cwd-independence / LES-067 fix — self-lockout hit and recovered mid-landing)

## LES-088 — A protect-guard keyed only on a runtime-resolved attribute fails OPEN when resolution fails; pair it with a resolution-independent key

**Class:** bug-class
**Scope:** `axrange/planner/teardown.py` (`_protected`, `destroy_deploy`'s VM loop) and any future guard that decides "must not touch" from an attribute fetched at destroy-time (a PVE display name, a live status, a resolved IP); any blast-radius/protect check built on top of a lookup that can itself fail or return `None`
**Consumers:** code-writer, code-reviewer
**Rule:** A guard like `is_protected(name)` is only as strong as the lookup that produces `name` — if `backend.vm_name(vmid)` fails to resolve (transient API hiccup, a renamed/unlabeled VM, a race), the guard has nothing to check against and, absent an explicit fallback, falls straight through to the ordinary "is this in my managed range" check: fail-OPEN, the exact opposite of the guard's intent. The fix is a SECOND, resolution-independent key checked FIRST, before the name lookup is even attempted — here, `protected_vmids` (an explicit, config-declared vmid allowlist) is checked before `backend.vm_name()` is called at all, so a naming hiccup can never unprotect a node that identity-based protection was supposed to cover unconditionally. Any protect/deny-list guard built on a single runtime-resolved attribute should get this same treatment: ask "what happens if the resolution step itself fails or returns nothing," and if the honest answer is "falls through to the permissive path," add the resolution-independent axis.
**Origin:** CHANGELOG 2026-07-21 (external sink nodes — XPress-SW teardown protection, code-review hardening of `_protected`/`destroy_deploy`)

## LES-089 — A capture path axRange structurally cannot self-verify must surface "capture UNVERIFIED," not skip silently (extends LES-001)

**Class:** silent-success-trap
**Scope:** `axrange/o2control/taps.py` (`_compose_ops`'s external-sensor branch, `TapPlanStatus.warnings`/`TapInfo.warnings`/`TapBindingInfo.capture_verified`); any future tap/capture/heartbeat path that has a legitimate, permanent reason it cannot QGA/SSH-verify a destination (an operator-managed appliance, a black-box third-party sink, anything outside axRange's reach by design, not just by current implementation gap)
**Consumers:** code-writer, code-reviewer, live-verifier
**Rule:** LES-001 says never trust a green deploy for capture when verification is merely not-yet-built. This extends it to the case where verification is impossible BY DESIGN — a config-declared external sensor (e.g. `XPress-SW`) has no QGA guest-exec available to axRange at all, so `verify-capture` cannot ever run against it, not just "hasn't been wired yet." The wrong fix is to quietly `continue` past the heartbeat and let the tap-plan apply report `completed` with no signal anything is different. The right fix: thread a `warnings` collector through the whole resolve→compose→status/info path and surface an explicit, per-binding "capture UNVERIFIED" message on every human/API-facing output (CLI `[warn] ...` lines, an inline `[capture: UNVERIFIED (external sink)]` per binding, an API `warnings: [...]` field) — so "completed" and "completed, but I couldn't confirm capture" are never visually indistinguishable. Generalizes to any future sink/target axRange composes ops against but cannot reach into for verification: the unverifiable condition must reach the operator's eyes, every time, not just the return code.
**Origin:** CHANGELOG 2026-07-21 (external sink nodes — config-declared EXTERNAL sensor + the loud capture-UNVERIFIED warning)

## LES-090 — Adding a fail-loud raise to a widely-called loader requires auditing EVERY call site, or installing one top-level handler

**Class:** bug-class
**Scope:** `axrange/adapter_proxmox/config.py` (`load_config`, now able to raise `ConfigError`); `axrange/cli/main.py` (`main()`'s dispatch); any function called from many independent entry points (a config loader, a shared parser, a common validator) that changes from "never raises" to "raises on bad input"
**Consumers:** code-writer, code-reviewer, tester
**Rule:** `load_config` gained a new failure mode (`ConfigError` on a malformed `EXTERNAL_SENSORS` value) but is called from many independent CLI/API/o2control entry points (`cmd_tap_validate`, `cmd_tap_info`, `cmd_tap_plan`, `apply_tap_plan`'s internal load, `cmd_destroy`, `tear_down_surface`, `destroy_deploy` in `api/service.py`, `serve`, `image build`, ...). Adding local `try/except ConfigError` at only the two call sites under active test leaves every other path dumping a raw traceback on the exact same bad input. Two valid fixes, and this project used both: add local handling where a caller wants a SPECIFIC error message/exit code (`cmd_tap_validate`, `cmd_tap_info`), AND install one TOP-LEVEL catch around the entire dispatch (`main()`'s new `try/except ConfigError` wrapping the whole tier/verb/command dispatch) as a backstop for every path that doesn't have its own — belt-and-suspenders, not either/or. When a previously-total function grows a new raise, grep every call site before assuming a couple of local catches are suffient coverage.
**Origin:** CHANGELOG 2026-07-21 (external sink nodes — top-level `ConfigError` handler in `cli/main.py:main()`)

## LES-091 — A sentinel value on a shared dataclass needs every consumer to branch on it explicitly, not just the one it was introduced for

**Class:** pattern
**Scope:** `axrange/o2control/control.py` (`SensorInventoryEntry.deploy_id`); `axrange/o2control/taps.py` (every function reading `sensor_by_id`/`SensorInventoryEntry` — `_compose_ops`, `_sensor_vmid`, `_tap_binding_infos`, `tap_info_from_deploy`); any future sentinel added to an existing shared type to mean "this instance doesn't have the property every OTHER instance of this type has"
**Consumers:** code-writer, code-reviewer, tester
**Rule:** `SensorInventoryEntry.deploy_id == ""` was chosen to mean "config-declared external sensor, no A1-standing deploy backs it, never QGA-reach into it" — reusing the existing `SensorInventoryEntry` type rather than adding a new one, so every consumer that already handled a standing sensor now silently receives one that violates an assumption ("every sensor has a deploy_id I can look up ops against") the type previously guaranteed. Introducing a sentinel on a shared type is not complete until every consumer of that type is individually audited and made to branch on the sentinel explicitly — here: `_compose_ops` skips the verify-capture compose, `_sensor_vmid`/ op-input resolution must not attempt a vmid lookup, `_tap_binding_infos`/`tap_info_from_deploy` set `capture_verified=False` for it. A sentinel silently reaching one un-audited consumer (e.g. a future op-input resolver assuming `deploy_id` is always a real, queryable id) reintroduces exactly the class of implicit-reach-into-an-external-node bug this whole feature exists to prevent.
**Origin:** CHANGELOG 2026-07-21 (external sink nodes — `SensorInventoryEntry.deploy_id == ""` sentinel threaded through `o2control/taps.py`)

## LES-092 — On a NetworkManager-managed guest, a hot-added NIC gets an auto DHCP profile that silently sits IP-less on an isolated (no-DHCP) transport net; diagnose via `nmcli con show` + bridge FDB, fix with a static profile — not a reboot

**Class:** platform-quirk
**Scope:** any NetworkManager-managed guest (RHEL/Rocky/Fedora family; packetXpress's `XPress-SW`, Rocky Linux 10.2, is the concrete case) that gets a NIC hot-added post-boot onto an isolated/no-DHCP-server network (the axRange transport vnet is exactly this); any future config-declared `EXTERNAL_SENSORS` bring-up; whoever next diagnoses "the sink isn't reachable at its configured transport IP"
**Consumers:** live-verifier, whoever next brings up an external-sensor tap target, code-reviewer (don't accept "must be a NIC-enumeration/reboot issue" as a diagnosis without checking `nmcli`/FDB first)
**Rule:** Live-verifying the dev45 XPress-SW tap path (2026-07-21) found the actual root cause of an unreachable sink: the hot-plugged capture NIC (`qm set 103 -net1 virtio,bridge=...`) enumerated inside the guest **immediately, no reboot needed** (came up as `ens19`, link-UP, MAC already learned in the PVE bridge FDB) — but NetworkManager had auto-created a DHCP-mode connection profile ("Wired connection 2") for it, which sat stuck `activating` forever because the isolated transport net has no DHCP server, leaving the interface with **no IPv4 address at all**. The fix is a **static NM profile**, not a reboot: `nmcli con mod "Wired connection 2" ipv4.method manual ipv4.addresses <transport_ip>/<prefix> ipv4.never-default yes connection.autoconnect yes && nmcli con up "Wired connection 2"` — `ipv4.never-default yes` matters because the transport net must never become the guest's default route (that has to stay on its management interface). **Diagnostic pattern for next time:** don't assume "unreachable = NIC didn't enumerate, try a reboot" — check `nmcli con show`/`nmcli dev status` first (a profile stuck in `activating` or `connecting (getting IP configuration)` is the tell) and cross-check the PVE bridge's FDB for the interface's MAC (MAC present + link-UP = the NIC itself is fine; the gap is purely IP assignment). **Put the transport IP on the PHYSICAL capture NIC (`ens19` here), never on the `gre-cap` decap device** — `gre-cap` (a flow-based `gretap external`) is purely a decap/merge point and needs no IP of its own; only the underlying physical interface needs the routable transport address. Separately, `gre-cap`'s own persistence-across-reboot is NOT NetworkManager's job — it's owned by whatever capture software brings it up (packetXpress's `pxcapture`/`pxswcapture` services, or a small systemd unit if those aren't used), and was found still runtime-only (not yet persisted) even after the IP fix landed. **The one thing that remains genuinely true and durable:** if the appliance's QGA guest-exec is disabled, axRange itself has no QGA channel into it and `verify-capture` cannot run against it (LES-089's loud-warning behavior exists for exactly this) — but that is narrower than "inaccessible": a granted SSH credential (as happened here) is a perfectly valid manual config/diagnostic channel, just not one axRange's own tooling drives.
**Origin:** CHANGELOG 2026-07-21 (external sink nodes — dev45 XPress-SW live-verify; root-caused and fixed via SSH; `docs/PRODUCT_GAPS.md` GAP-026, narrowed to "end-to-end tap-capture verify still pending")

## LES-093 — A deterministic-name producer must round-trip its own predicate on DEGENERATE input (empty/all-non-alphanumeric ids), or teardown's positive shape gate makes the resource permanently un-destroyable

**Class:** bug-class
**Scope:** `axrange/adapter_proxmox/naming.py` — every `*_name_for_*`/`*_vm_name_for_component` producer paired with an `is_*` predicate (`vm_name_for_host`, `o2_vm_name_for_component`, `tfc_vm_name_for_component`, `exr_vm_name_for_component`, and any future tier's VM-name family); any producer/predicate pair added later
**Consumers:** code-writer, code-reviewer, tester
**Rule:** `bridge_name_for_net` already had an `or "x"` guard for exactly this reason (an empty-after-sanitize `net_id` would otherwise produce a bare `tebr_` that its own `is_te_bridge` predicate then rejects — a producer emitting a name its own positive gate refuses). That fix did NOT propagate: when the `tfc`/`exr` VM-name producer families were added (`docs/WORK_ORDER_tier_split.md`), they copied the OLDER, unfixed `vm_name_for_host`/`o2_vm_name_for_component` shape (`_ALPHANUM.sub("-", host_id)` with no fallback), reproducing the same class of bug in two new places before it was caught by testing degenerate input (empty string, an id that's entirely non-alphanumeric) against the round-trip. Since `planner/teardown.py`'s VM-path guard is a POSITIVE shape gate (a name must match `is_*_vm_name` to be swept), a producer/predicate mismatch on degenerate input doesn't just look wrong — it makes that resource invisible to its own tier's destroy, permanently. Whenever a new deterministic-name producer is added (or an existing family is extended to a new tier), test it against the SAME degenerate inputs its siblings are already tested against, not just typical ones — a sibling bug already found and fixed elsewhere in the same module is not proof it won't recur; it has to be checked directly at every new call site.
**Origin:** CHANGELOG 2026-07-23 (Tier split KEYSTONE — `docs/WORK_ORDER_tier_split.md` code review, `vm_name_for_host`/`o2_vm_name_for_component`'s `or "x"` fix)

## LES-094 — Never label a guard exception "defense-in-depth" without checking who actually writes the rows it covers

**Class:** pattern
**Scope:** `axrange/planner/teardown.py` (`_CASCADE_ALLOWED_FOREIGN`, the `te -> tap` cascade entry); any future comment characterizing a guard/cascade exception as "vestigial"/"defense-in-depth"/"unreachable in practice"; code review of any protect-guard change
**Consumers:** code-writer, code-reviewer
**Rule:** an earlier pass of this module's comments described the `te -> tap` cascade exception in `_protected()` as pure defense-in-depth — reasoning that a `te` deploy's own `ops` table no longer records real `tap_*` resources post-sensor-agnostic-`te`, so the exception looked redundant. That characterization was WRONG and nearly got the exception deleted as dead weight: `axrange.o2control.taps`'s tap-plan playout deploy is ITSELF stamped `tier="te"` (there is no separate `"tap"` deploy-level tier in `naming.py`'s `_VALID_TIERS`), so every row in a `tap-<te>` deploy's `ops` table is `tier="te"` while producing real `tapg_*`/`tapm-*` names — the exception is load-bearing for that deploy's OWN destroy, not a hypothetical. Before writing off a guard exception as "defense-in-depth" or "no longer reachable," trace who actually calls the function/writes the tier tag it covers — a codebase-wide grep for every writer of the field the guard reads, not just the caller currently under review.
**Origin:** CHANGELOG 2026-07-23 (Tier split KEYSTONE — `docs/WORK_ORDER_tier_split.md` code review MUST-FIX #3, `axrange/planner/teardown.py`)

## LES-095 — A published contract derived from a CONDITIONALLY-emitted op inherits that condition as a coverage hole; ask "under what IR does this op not fire?" before wiring a contract to it

**Class:** bug-class
**Scope:** `axrange/planner/inventory.py` (`build_twin_inventory`'s source-of-facts choice — unconditional ops only); any future published/consumed contract derived from recorded op state; any reviewer of a "read this fact off op X's recorded inputs/outputs" design
**Consumers:** code-writer, code-reviewer
**Rule:** an earlier revision of the twin inventory sourced `role`/`segment`/`ip`/`os` from `OP-MIR-005 record-tap-intent`'s recorded inputs, additively threading new fields onto it — but `record-tap-intent` only ever emits for a host with `>=1` tapped NIC. An UNTAPPED twin (no `tap:` anywhere in its IR) would therefore publish an all-`None` roster, and — worse — editing a `tap:` declaration on a redeploy would silently change what the *unrelated* Traffic tier could bind to, re-coupling two tiers the design explicitly keeps independent (`docs/design/02-twin-model.md`). The fix is to source every published fact from an op that fires unconditionally for the population the contract claims to cover (`clone-vm`/`attach-vm-to-vnet` fire for every host/leg regardless of tap status). Before wiring any published contract to "read fact X off op Y's recorded state," ask explicitly: under what IR shape does op Y not emit at all, and does the contract's claimed coverage survive that case?
**Origin:** CHANGELOG 2026-07-23 (Tier split KEYSTONE — `docs/WORK_ORDER_tier_split.md` code review MUST-FIX #2, `axrange/planner/inventory.py`)

## LES-096 — Additive op inputs never reach an already-`completed` op; a resumed deploy publishes the field as absent forever, until `--fresh`

**Class:** silent-success-trap
**Scope:** `axrange/planner/inventory.py` (every `.get(...)` defensive read); any future field added to an existing op's `inputs_resolved`/`outputs` shape that a NEW read path (a published contract, a new consumer) depends on; the planner's Model-C resume semantics generally
**Consumers:** code-writer, code-reviewer, live-verifier
**Rule:** an op's `op_id` is derived from its SCOPE, not its resolved inputs' content, and the executor's resume path is `INSERT OR IGNORE` against that `op_id` — so adding a new field to, say, `attach-vm-to-vnet`'s `inputs_resolved` never backfills it onto a deploy whose `attach-vm-to-vnet` op is already `completed` in `state.db`. A pre-existing deploy publishes that field as absent (not stale, not wrong — simply never-written) FOREVER, until the scenario is redeployed with `--fresh` (a fresh `deploy_id`, fresh op rows). Any new read path built on top of "an op's recorded inputs/outputs" must (a) use defensive `.get(...)` access that degrades to `None`/empty rather than raising on a missing key, and (b) document in its own docstring that older deploys will show partial data until re-deployed — never assume a schema-stable field is uniformly present across every recorded deploy in a live `state.db`.
**Origin:** CHANGELOG 2026-07-23 (Tier split KEYSTONE — `docs/WORK_ORDER_tier_split.md` review documentation item #14, `axrange/planner/inventory.py`)

## LES-097 — Tier/tag vocabularies must live in ONE exported table, or a new tier's names end up owned by nobody — refused by no guard, sweepable by every other tier; unknown-tier branches must fail closed

**Class:** bug-class
**Scope:** `axrange/adapter_proxmox/naming.py` (`TIER_PREDICATES`, `TIER_SHAPE_CHECKS` — the single owned source of the tier vocabulary); `axrange/planner/teardown.py` (`_shape_checks_for_tier`, `_protected()` — both now DERIVE from `naming.py`'s tables instead of hand-maintaining a second copy); any future module that needs to enumerate "which tiers exist" or "what shape does tier X's name have"
**Consumers:** code-writer, code-reviewer
**Rule:** before this work order, `planner/teardown.py` hand-maintained its own `tier -> (vnet_ok, zone_ok, vm_name_ok)` mapping, separate from `naming.py`'s own producer/predicate definitions — two places that had to agree, with nothing enforcing that they did. Adding `tfc`/`exr` to one but forgetting the other would have produced exactly the failure mode a blast-radius guard must never have: a tier tag that naming.py can PRODUCE but teardown.py's guard doesn't RECOGNIZE — refused by no positive shape check (so its own destroy can never clean it up) and, depending on which side of the split the bug landed on, potentially swept as a false positive by an unrelated tier's destroy. The fix: one exported table (`TIER_PREDICATES`/`TIER_SHAPE_CHECKS`) that every consumer derives from, with import-time coverage asserts proving every tier in `_VALID_TIERS` has a row. Just as important: any function that keys off "which tier is this" must FAIL CLOSED (refuse/return all-`False`) on a tier it doesn't recognize, not silently fall through to a default tier's behavior — the exact trap the OLD `_shape_checks_for_tier` had (anything not literally `"o2"` fell through to the `te` tuple).
**Origin:** CHANGELOG 2026-07-23 (Tier split KEYSTONE — `docs/WORK_ORDER_tier_split.md` review MUST-FIX #4/#5, `axrange/adapter_proxmox/naming.py`, `axrange/planner/teardown.py`)

## LES-098 — Failing closed at the DECISION POINT isn't failing closed END-TO-END; audit every later use of the same unvalidated value

**Class:** bug-class
**Scope:** `axrange/planner/teardown.py` (`destroy_deploy`'s `own_tier` validation vs. its later `underlay_bridge_for_plan(deploy.plan_id, tier=own_tier)` call); any function that validates an input early but keeps using the SAME unvalidated raw value later in its own body, or passes it to a callee that doesn't share the first check's guard
**Consumers:** code-writer, code-reviewer
**Rule:** making `_shape_checks_for_tier` fail closed (return an all-`False` tuple for an unrecognized tier) closed the DANGEROUS half of the unknown-tier trap — no more silently borrowing another tier's name shapes. But that fix, by itself, did NOT stop `destroy_deploy` from reaching `underlay_bridge_for_plan(deploy.plan_id, tier=own_tier)` roughly 200 lines later in the SAME function, with the SAME unrecognized `own_tier` value, and having THAT call raise a bare, untyped `ValueError` mid-sweep — after some resources were already destroyed and before `store.release_lease()` ran, leaving a held lease blocking the very retry that would fix the underlying tier-registration gap. The actual fix was to validate `own_tier` against `naming.TIER_SHAPE_CHECKS` and raise the module's OWN typed `TeardownError` at the very top of `destroy_deploy`, before a single resource is touched — closing the trap end-to-end, not just at the one call site under review. When hardening a guard, trace every LATER use of the same raw value the guard was meant to protect, not just the first place it's checked.
**Origin:** CHANGELOG 2026-07-23 (Tier split KEYSTONE — `docs/WORK_ORDER_tier_split.md` review MUST-FIX #4 second round, `axrange/planner/teardown.py::destroy_deploy`)

## LES-099 — A consolidation refactor that narrows a derived map's KEY DOMAIN is not behavior-preserving even when every field matches; diff review checks fields, not which keys exist at all

**Class:** bug-class
**Scope:** `axrange/o2control/taps.py` (`_scan_te_inventory`'s re-expression over `build_twin_inventory`, specifically `net_bridge`); any future "reuse this existing derivation instead of hand-rolling a second one" refactor that reshapes one data structure into another with the same field NAMES
**Consumers:** code-writer, code-reviewer, tester
**Rule:** the first cut of re-expressing `_scan_te_inventory` as a thin adapter over the new `build_twin_inventory` looked correct on a field-by-field diff — same `net_bridge: dict[str, str]`, same `vlan_underlay: Optional[str]` shape — but it actually resolved `bridge` only for nets that already had a tap point in `twin.tap_points`, silently SHRINKING `net_bridge`'s key domain versus the ORIGINAL inline scan, which back-filled from a Simple zone's per-net `create-ovs-bridge` op (fires for every declared network, tapped or not) and the VLAN zone's single scenario-scoped underlay (applies to every net too). This narrowing was invisible on the APPLY path (every net queried there always has a tap point, by construction) but bit on the DROP path (`_delete_dropped_gretaps`): a net_id from a PRIOR apply's recorded ops that no longer has a tap point in a resumed/edited-`tap:` redeploy would fail to resolve a bridge and silently orphan a gretap netdev on the cluster. Reviewing a consolidation refactor by diffing FIELD SHAPES ("same keys, same types") is not enough — check which VALUES/KEYS actually populate the map under every code path the OLD and NEW implementations traverse, especially a drop/cleanup path that queries the map for entries the apply path never does.
**Origin:** CHANGELOG 2026-07-23 (Tier split KEYSTONE — `docs/WORK_ORDER_tier_split.md` review MUST-FIX second round, `axrange/o2control/taps.py::_scan_te_inventory`, `axrange/planner/inventory.py`)

## LES-100 — A load-bearing registry invariant guarded only by a module-level `assert` is UNENFORCED under `python -O`; mirror it in a test

**Class:** verification-idiom
**Scope:** `axrange/adapter_proxmox/naming.py` (the import-time coverage asserts backing `TIER_PREDICATES`/`TIER_SHAPE_CHECKS` — "every tier in `_VALID_TIERS` has a row"); any future module-level `assert` intended to enforce a structural invariant at import time; `tests/test_adapter_proxmox.py`
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Python strips `assert` statements entirely when run with `-O`/`-OO` (or `PYTHONOPTIMIZE` set) — a bare module-level `assert` meant to catch "someone added a tier to `_VALID_TIERS` without registering it in `TIER_PREDICATES`/`TIER_SHAPE_CHECKS`" silently does nothing in that mode, and nothing in this codebase's tooling forces `-O` off everywhere it might run (a packaged deploy, a differently-configured CI runner, an operator's own environment). The assert is still worth keeping as an immediate, load-bearing signal for the normal (non-`-O`) dev/test path, but it must not be the ONLY enforcement: the same invariant needs an explicit `pytest` test that imports the module and asserts the coverage directly, so the check runs regardless of interpreter flags. Any module-level `assert` that exists to protect a structural invariant (not just a debug sanity check) should be treated as a hint for developers, with the actual enforcement living in a test.
**Origin:** CHANGELOG 2026-07-23 (Tier split KEYSTONE — `docs/WORK_ORDER_tier_split.md` review, `axrange/adapter_proxmox/naming.py`'s import-time coverage asserts, `tests/test_adapter_proxmox.py`)

## LES-101 — "Match everything" must be an explicit request, never what an empty/degenerate filter degrades to — and check EVERY entry point a filter can reach, not just the one under test

**Class:** bug-class
**Scope:** any selector/filter object matched against a catalog to select a subset for a
destructive or capture-scoping action (`axrange/planner/tappoints.py`'s `TapPointSelector`/
`matches_selector`; `axrange/o2control/taps.py`'s `parse_tap_plan` + any future selector-shaped IR
field); any place a value object can be constructed from BOTH a file parser and a direct API/code
path
**Consumers:** code-writer, code-reviewer, tester
**Rule:** `matches_selector` checks each of `id`/`scope`/`target`/`labels` only if the selector sets
it — an all-unset `TapPointSelector` therefore matches vacuously against EVERY check and selects the
entire catalog. That's a reasonable implementation for the matcher itself, but it means "select
everything" must never be allowed to happen BY DEFAULT/BY OMISSION — it has to be something a caller
asks for on purpose. `docs/WORK_ORDER_tap_points.md`'s build found this gap reachable through TWO
independent doors: the file-based `parse_tap_plan`, and — separately — the API's `POST
/api/o2/taps` DTO path, which built a `TapPointSelector` directly and bypassed the file parser's
checks entirely. Fixing only the parser (the path under active test) would have left the API path
open. The fix (`has_criteria()`, a "does this selector carry ≥1 effective criterion" gate) had to be
called at BOTH boundaries — every place raw operator input becomes a selector object, not just the
one the work order's own examples exercised. Generalizes beyond tap points: any filter/selector
whose "no criteria set" state is representable at all needs an explicit reject at construction/parse
time, and a reviewer must enumerate every code path that can construct one, not just the primary one.
**Origin:** CHANGELOG 2026-07-24 (Tap points BUILT — `docs/WORK_ORDER_tap_points.md` review M1/M2,
`axrange/planner/tappoints.py::has_criteria`, `axrange/o2control/taps.py::parse_tap_plan` +
`POST /api/o2/taps`)

## LES-102 — A "fail loud" fix is only loud if the caller's sink isn't `/dev/null`; audit every call site's log/warning collector, not just the raise/return path

**Class:** bug-class
**Scope:** any function taking a `log=`/callback-style diagnostic sink, especially one that used to
be a no-op stub during earlier development (`axrange/o2control/taps.py`'s `_delete_dropped_gretaps`
→ `apply_tap_plan`'s `log=` wiring); any code review that verifies "does this raise/return the right
thing" without also checking "does anything actually consume what it emits"
**Consumers:** code-writer, code-reviewer, tester
**Rule:** the dropped-gretap warning path in `apply_tap_plan` was correctly *computed* — a real
`log(...)` call fired on every orphaned-gretap skip — but the caller wired it to `log=lambda _m:
None`, so the warning was thrown away before it ever reached `TapPlanStatus.warnings`, the CLI, or
the API. This is a distinct failure mode from "the check is missing" or "the check is wrong" — the
check was present and correct, the SINK was the bug, and it's invisible to a review that only reads
the warning-generation code, not its wiring. Any time a function accepts a log/callback sink
parameter, trace it all the way to where a human or a caller-facing DTO actually reads it before
declaring the diagnostic "surfaced" — a `lambda _m: None` (or an unread list, or a swallowed
exception) anywhere on that path silently defeats the entire fix.
**Origin:** CHANGELOG 2026-07-24 (Tap points BUILT — `docs/WORK_ORDER_tap_points.md` review M6,
`axrange/o2control/taps.py::apply_tap_plan`'s `log=warnings.append` fix)

## LES-103 — A catalog DERIVED from optional author declarations publishes silent holes; check it against the ACTUALLY SHIPPED content before claiming the capability, not just synthetic unit fixtures

**Class:** verification-idiom
**Scope:** any derivation/grouping layer that infers a structure from optional per-node IR fields
across a content library (`axrange/planner/tappoints.py`'s `derive_default_tap_points`, specifically
`boundary` scope; any future twin-config/persona-catalog derivation with the same "only fires if an
author remembered to declare X" shape); `target-environments/*.yaml`
**Consumers:** code-writer, code-reviewer, tester
**Rule:** the first cut of `derive_default_tap_points` only emitted a `boundary` tap point when at
least one of the two joining segments' legs was already tapped. Every synthetic unit-test fixture
passed, because the fixtures were HAND-BUILT to include a tapped joining leg. Checked against the
real `target-environments/` content, every single multi-homed router/leaf/border node that actually
JOINS two segments — `ot-ics-range`'s `conduit`, the design brief's own headline `idmz-conduit`
example — had its leaf hosts tapped but never the joining node itself, so `boundary` derived to
NOTHING across the entire shipped catalog, including this very work order's own gated live-verify
subject. A catalog derived from optional, per-author declarations is only as complete as what
authors actually remembered to declare, and a synthetic test fixture is written by someone who
already knows what the derivation needs — it can't catch a gap the derivation logic and the content
both share. The fix requires running the derivation over the REAL shipped content (a full
`build_plan`+`Executor` round trip against real IRs, `tests/test_target_environments_boundary_taps.py`)
before scheduling any live-verify or claiming the capability works — this caught the gap in review,
which is exactly why the archetypes needed a content edit (`tap: true` on eight boundary-forming
nodes) as part of this same build, not a follow-on.
**Origin:** CHANGELOG 2026-07-24 (Tap points BUILT — `docs/WORK_ORDER_tap_points.md` review M4,
`axrange/planner/tappoints.py::derive_default_tap_points`, `target-environments/*.yaml` boundary-node
`tap: true` edits, `tests/test_target_environments_boundary_taps.py`)

## LES-104 — A declared MODE that only validates its own entry is not enforced; cross-entry invariants need a post-resolution check across the WHOLE set, not a per-item parse-time check

**Class:** bug-class
**Scope:** any per-item field in a list of bindings/rules that claims an invariant spanning MULTIPLE
items (`axrange/o2control/taps.py`'s `TapBindingSpec.mode: exclusive`; any future "this entry claims
sole ownership of X" field in a batch spec)
**Consumers:** code-writer, code-reviewer, tester
**Rule:** `mode: exclusive`'s only enforcement, before this build, was a parse-time check on the
SAME binding: `len(sensors) == 1`. Nothing checked whether some OTHER binding in the same tap-plan
also targeted the same interface(s) — a second, overlapping `fanout` (or even another `exclusive`)
binding could silently fan an "exclusive" point out to an extra sensor, exactly the traffic
mis-routing/data-exposure class this module's own docstring already warns about for a different
mechanism (`install-ovs-mirror`'s append-not-replace obligation). The fix
(`resolve_tap_plan_bindings`'s post-resolution scan comparing every `exclusive` binding's claimed
interfaces against every OTHER binding's claimed interfaces) had to run AFTER all bindings were
resolved to their concrete interface sets — a per-binding parse-time check structurally cannot see
this, because it only ever looks at one entry in isolation. Any field whose name implies an
invariant about the REST of the set ("exclusive," "unique," "sole," "only") needs its enforcement
scoped to the whole set, checked after every entry is resolved, not validated locally per-entry and
assumed complete.
**Origin:** CHANGELOG 2026-07-24 (Tap points BUILT — `docs/WORK_ORDER_tap_points.md` review M3,
`axrange/o2control/taps.py::resolve_tap_plan_bindings`'s cross-binding exclusivity enforcement)

## LES-105 — Require a "≥1 effective criterion" gate at BOTH the parse boundary AND the resolution boundary — a value object built directly in code bypasses the parser entirely

**Class:** pattern
**Scope:** any two-boundary system where the same value-object type can be constructed either by
parsing untrusted input (a file/API DTO) OR directly in code (engine-internal call sites) —
`axrange/planner/tappoints.py`'s `TapPointSelector` (constructible by `axrange.o2control.taps`'s
`_selector_from_dict` from untrusted YAML/JSON, or by any future engine-side caller building one
directly); generalizes to any "validate at the edge" design where the validated type is also a
public, directly-constructible dataclass
**Consumers:** code-writer, code-reviewer, tester
**Rule:** `has_criteria()` (LES-101) has to be called at the FILE-parse boundary (`parse_tap_plan`)
and, independently, at the API-DTO boundary (`POST /api/o2/taps`, which builds a selector from a
different untrusted-input shape) — but a THIRD path exists structurally: nothing stops engine-side
code from constructing a bare `TapPointSelector()` directly and handing it to
`resolve_tap_plan_bindings` without going through either parser. Because `TapPointSelector` is a
plain, publicly-importable dataclass (not a private type only the parser can construct), "validate
at the edges" is not actually sufficient — the untrusted-edge checks catch operator-authored plans,
but not a future internal caller that builds the value object directly and skips both known parse
boundaries. The durable pattern: when a value type can be constructed from MULTIPLE origins (parsed
input, AND direct code construction), place at least one invariant check at the type's OWN
construction/consumption boundary (here, `resolve_tap_plan_bindings`'s resolution step is the
resolution-time backstop) in addition to every known parse boundary — don't rely on enumerating every
call site that might construct one.
**Origin:** CHANGELOG 2026-07-24 (Tap points BUILT — `docs/WORK_ORDER_tap_points.md` review M2/M1,
`axrange/planner/tappoints.py::has_criteria`, `axrange/o2control/taps.py` resolve-time backstop)

## LES-106 — `GuestAgent.write_file` is UTF-8-lossy; a binary in-guest payload needs a bytes-native base64 path

**Class:** bug-class
**Scope:** any adapter/config-exec code that delivers a payload into a guest over QGA where the
payload can be arbitrary bytes, not text (`axrange/adapter_proxmox/` guest-agent file writes;
`axrange/personas/drivers/replay.py`'s staged pcap; any future in-guest binary artifact delivery)
**Consumers:** code-writer, code-reviewer
**Rule:** `GuestAgent.write_file` (the existing, widely-used QGA write path — the base64-in-command
mechanism established for the PVE-9.2 wide-char regression, LES extending the CHANGELOG
2026-06-23 fix) assumes its `content` is a `str` and round-trips it through UTF-8 encode/decode
somewhere in the pipeline — perfectly correct for rendered scripts/config text, but **silently
corrupts** arbitrary binary bytes (a captured pcap being staged for `tcpreplay`) the moment any byte
sequence isn't valid UTF-8. The fix is a **separate, bytes-native** `write_binary_file` entry point
that base64-encodes raw `bytes` directly (no intermediate `str` decode/encode round-trip) and
delivers it the same base64-in-command way. Don't reuse a text-oriented guest-write helper for a
binary payload just because the transport mechanics (base64-in-command) are identical — the
encoding boundary (`bytes` in vs. `str` in) has to be a distinct, type-checked entry point, or a
"looks like it should just work" call silently mangles the artifact with no error at all (the write
succeeds; only the replayed traffic downstream is wrong).
**Origin:** CHANGELOG 2026-07-24 (Persona library BUILT — `docs/WORK_ORDER_persona_library.md`,
`axrange/personas/drivers/replay.py`'s pcap staging, extending LES-002/the PVE-9.2 base64-in-command
pattern to a bytes-native variant)

## LES-107 — A rule set registered in the shared catalog but executed only by its own caller isn't runtime-enforced until a consumer actually wires it up; a test must exercise the SHIPPED catalog, not a private fixture

**Class:** verification-idiom
**Scope:** any new validator/registry rule category added catalog-side without an IR-pipeline entry
point (`axrange/validator/checks/personas.py`'s `PSN-0xx`; the existing `CLU` precedent this
follows; any future catalog-side-only rule category)
**Consumers:** tester, code-reviewer
**Rule:** Registering `PSN-010..090` in `axrange/validator/catalog.py` makes them show up in
`list_rules()`/`--explain`/the rule-count audit, but that alone proves nothing about whether the
checks actually *catch* a bad persona — `validate_persona`/`validate_catalog` are plain functions
nothing in `engine.py`'s Pass 1-4 dispatch calls. The verification that actually matters is a test
that runs `validate_catalog()` (no arguments — defaulting to `from axrange.personas.registry import
PERSONAS`, the real shipped 20-persona catalog) and asserts every entry comes back clean, **plus** a
second test that deliberately corrupts one catalog persona (bad `os_family`, dangling `peer_role`,
etc.) and asserts the corresponding `PSN-0xx` fires. A test that only builds synthetic in-memory
`Persona` fixtures never proves the SHIPPED catalog content is actually clean — it proves the check
function works in isolation, which is a weaker and different claim. The general form (already true
of CLU, now true of PSN): when a rule category's only consumer is a future/manual caller rather than
the automatic IR pipeline, the test suite is the one place that closes the loop between "the rule
exists" and "the rule was actually run against real content."
**Origin:** CHANGELOG 2026-07-24 (Persona library BUILT — `docs/WORK_ORDER_persona_library.md`,
`tests/test_personas.py`'s whole-catalog pass-4 assertion)

## LES-108 — Catalog-content validation belongs in `validator/checks/<domain>.py` reading the registry; never a `validate.py` inside the content package importing the validator

**Class:** pattern
**Scope:** any engine-vocabulary content registry that needs its own catalog validated against the
shared `Finding`/`Severity`/`Rule` vocabulary (`axrange/personas/` + `axrange/validator/checks/
personas.py`; the precedent `axrange/sensors/` + `checks/references.py`'s REF-210 already set;
applies to any future registry of this shape)
**Consumers:** code-writer, code-reviewer
**Rule:** the first cut of persona validation put a `validate.py` module *inside*
`axrange/personas/` that imported `axrange.validator.finding`/`Severity` directly — a content
package reaching UP into the validator. Code review flipped the dependency direction: the check
logic moved OUT to `axrange/validator/checks/personas.py`, which imports the persona registry DOWN
(`axrange.personas.schema`/`registry`) — the identical direction `checks/references.py`'s REF-210
already uses for `axrange.sensors`. The rule this generalizes: a content/vocabulary package
(`personas`, `sensors`, and any future sibling) must stay a **pure leaf** — it may be imported BY
the validator, planner, adapter, but must never import ANY of them itself, not even the shared
`Finding` vocabulary. All catalog-content validation logic lives in `axrange/validator/checks/
<domain>.py`, which is allowed to import the content registry (validator → registry is the one
sanctioned direction), never the reverse. Verify this holds with an explicit dependency-direction
test (an ast/import-graph check on the content package's modules), not just code-review vigilance —
the whole point is that a future contributor adding a "quick validation helper" inside the content
package is the exact mistake this lesson exists to prevent.
**Origin:** CHANGELOG 2026-07-24 (Persona library BUILT — `docs/WORK_ORDER_persona_library.md`
code-review fix, `axrange/validator/checks/personas.py`'s module docstring)

## LES-109 — An author-facing template that expands into a version-locked schema must resolve ALL params BEFORE building the target dict, and feed the result through the REAL validate+plan — one seam, never a shadow path

**Class:** pattern
**Scope:** any templating/expansion layer that produces content for a version-locked contract
(`axrange/twinconfig/expand.py` → Scenario IR / `docs/IR_SCHEMA.md`; any future author-facing
template that targets a schema the project has committed to freezing)
**Consumers:** code-writer, code-reviewer, doc-writer
**Rule:** `docs/design/02-twin-model.md`'s "no `IR_SCHEMA` version bump" property isn't
incidental — it's a structural consequence of where `axrange/twinconfig/expand.py` resolves
`${NAME:default}` params: entirely BEFORE the Scenario-IR `dict` is built (`resolve_params` +
`substitute` run first; `TwinConfig.model_validate` only sees already-substituted, real-typed
values), and the expansion's ONLY output is a plain dict shaped exactly like a hand-written
`target-environments/*/scenario.yaml`, fed through `axrange.ir.parser.parse_ir` /
`axrange.validator.validate_full` / `axrange.planner.build_plan` completely UNCHANGED — no second
validate/plan code path, no bypass. This is the general pattern for any future author-facing
template layer sitting above a version-locked schema: resolve every parameter/template construct at
the template's OWN layer, produce a fully-concrete instance of the target schema, and route that
instance through the SAME real entry points every hand-authored instance uses. A shadow
validate-lite/plan-lite path (even one that "does the same checks") is what actually breaks the
version-lock guarantee — the lock only holds if there is structurally one path through the
contract, not two paths that happen to agree today.
**Origin:** CHANGELOG 2026-07-24 (Twin configs BUILT — `docs/WORK_ORDER_twin_configs.md`,
`axrange/twinconfig/expand.py`'s module docstring + the real `build_plan`+`Executor` round-trip
tests for all 5 starter configs)

## LES-110 — Every coercion of an author-supplied field at an expansion boundary must raise the module's TYPED error, never a bare `ValueError`/`TypeError`

**Class:** pattern (corollary of LES-090)
**Scope:** any expander/loader coercing untrusted author-supplied scalars into typed values
(`axrange/twinconfig/expand.py`'s `int(count)` for `nodes[].count`; any future field coercion in
`axrange/twinconfig/`, `axrange/personas/`, or a similar content-expansion module)
**Consumers:** code-writer, code-reviewer
**Rule:** when a twin-config author writes `count: "twenty-five"` instead of `count: 25` (or any
other malformed scalar an expander must coerce — a param substitution gone wrong, a typo'd
integer), the coercion (`int(spec.count)`) must raise the module's OWN typed error
(`TwinConfigShapeError`/`TwinConfigParamError`, per `axrange/twinconfig/errors.py`) with a message
naming the offending field and role — never let a bare `ValueError`/`TypeError` from `int()`
propagate up uncaught. A bare built-in exception reads as an internal crash (a traceback pointing
at `expand.py` internals) rather than an actionable authoring mistake, and — same failure class as
LES-090 — every call site that invokes the expander needs to know WHICH exception type to catch;
a bare `ValueError` is indistinguishable from a real bug elsewhere in the same function using the
same exception type. This is the general corollary of LES-090's "audit every call site" lesson
applied to VALUE coercion specifically: wrap every author-input coercion at an expansion boundary
in a try/except that re-raises as the module's typed error, at the point of coercion, not
downstream.
**Origin:** CHANGELOG 2026-07-24 (Twin configs BUILT — `docs/WORK_ORDER_twin_configs.md`,
`axrange/twinconfig/expand.py`'s `count` coercion fix)

## LES-111 — When a declarative source carries selector-load-bearing metadata a downstream version-locked schema has no field for, expanding to booleans-only SILENTLY drops it — carry it out-of-band, get a confirmed bump, or document the inertness explicitly; never imply label-selection works when only flags survive

**Class:** dead-end / reversal-hazard
**Scope:** any expansion layer translating a richer declarative source into a narrower target
schema that only has boolean/flag fields for what the source expresses as typed, labeled objects
(`axrange/twinconfig/expand.py`'s `tap_points[]` → IR `tap:` booleans; any future expander facing
the same source-richer-than-target shape)
**Consumers:** code-writer, code-reviewer, doc-writer
**Rule:** a twin config's `tap_points[].id`/`labels[]`/`default_sensor` are real, typed, and
correctly drive `_apply_tap_points`'s materialization — but the version-locked Scenario IR they
expand into has no field to carry `id`/`labels` themselves, only a boolean `tap:` flag per
network/node/leg. The temptation is to treat "materialization works" as "the feature works" — it
doesn't: `docs/design/02-twin-model.md`'s whole selling point (`selector: {labels: [ns]}`
binding) is **structurally impossible** for a config-sourced twin once only the boolean survives,
because nothing downstream (`TwinInventory`/`derive_default_tap_points`) has any record of what the
author actually wrote. This is a DEAD END for the current architecture, not a bug: no code fix
within `expand.py` can restore the labels, because the schema it targets has nowhere to put them.
The only ways out are (a) a confirmed, version-locked `IR_SCHEMA` bump adding a real `tap_points:`
carrier field, or (b) an out-of-band side-channel the tap-plan binding step could separately
consult. Until one of those lands, the CORRECT engineering response is what this build did:
implement the flag-only materialization (it's still useful — `id`/`scope`/`target` selection keeps
working), and **document the inertness explicitly and loudly** (CHANGELOG, design brief, a
PRODUCT_GAPS entry) rather than let a reader assume label-selection works because the schema field
technically exists and is technically consumed. Never let "the input field is parsed and used for
something" imply "the input field's full advertised capability is delivered."
**Origin:** CHANGELOG 2026-07-24 (Twin configs BUILT — `docs/WORK_ORDER_twin_configs.md`,
`docs/PRODUCT_GAPS.md` GAP-030, `docs/design/02-twin-model.md` built-status note)

## LES-112 — Re-asserting and de-scoping are TWO SEPARATE obligations for any standing/idempotent playout — a force-reset on apply only re-runs ops the new plan still contains, it says nothing about ones the new plan dropped

**Class:** bug-class
**Scope:** any standalone playout that re-applies a changed spec against standing, driver-managed
state via a force-reset-to-`pending` idempotency trick (`axrange/o2control/traffic.py`'s
`apply_traffic_profile`; `axrange/o2control/taps.py`'s `install-ovs-mirror` M1 fix it mirrors; any
future playout with the same "re-apply must re-assert changed instances" shape)
**Consumers:** code-writer, code-reviewer
**Rule:** force-resetting every composed op back to `pending` on every apply (LES-057's fix, reused
here) correctly makes a CHANGED assignment re-launch with fresh content — but it only touches ops
**present in the new plan**. It says nothing about an instance the new plan **dropped** (a narrowed
traffic profile, a persona removed) or **swapped** (a persona changed on the same host — since
`instance_id = f"{host_id}-{persona_id}"`, a swap changes the systemd unit's NAME, not just its
content). Without a separate de-scope pass, a dropped instance just keeps running forever (only a
full destroy would ever stop it), and a swap is worse — the OLD unit keeps running **alongside** the
newly-started one, both emitting simultaneously, silently corrupting the very profile just applied.
The fix pattern (`_stop_dropped_instances`, mirroring `taps.py`'s `_delete_dropped_gretaps`): after a
successful executor run, diff the previously-`completed` ops against the new resolved set, stop
every instance NOT in it via its own driver/teardown path, and mark its op row destroyed
(`StateStore.mark_op_destroyed`, LES-113) so read-only status surfaces stop reporting it as live.
Any future playout built on the same "force-reset to re-assert" idempotency trick needs this SECOND
pass too — it is not optional, and it is not implied by the first one.
**Origin:** CHANGELOG 2026-07-24 (Traffic tier (`tfc`) BUILT — `docs/WORK_ORDER_traffic_tier.md`,
`axrange/o2control/traffic.py`'s `_stop_dropped_instances`, code-review MUST-FIX)

## LES-113 — `reset_incomplete_ops` reverts EVERY non-`completed` row (including `destroyed`) back to `pending` on a re-apply, so a per-op `mark_op_destroyed` is not durable across it — correctness must rest on the op's absence from the new plan + the driver actually stopped, and a read-only surface must filter to COMPLETED, never "exclude destroyed"

**Class:** silent-success trap
**Scope:** any playout using `StateStore.reset_incomplete_ops` on a resumed/re-applied deploy
alongside a per-op `mark_op_destroyed` de-scope mechanism (`axrange/o2control/traffic.py`'s
`_stop_dropped_instances` + `apply_traffic_profile`'s resume path; **`axrange/o2control/taps.py`'s
`_bindings_summary_from_ops` + `tap_info_from_deploy`** — scope widened 2026-07-27, see below; any
future playout combining both mechanisms)

> **Scope widened 2026-07-27 (GAP-044/GAP-045).** This rule was recorded, implemented in
> `traffic.py`, and then **not applied to `taps.py` for three months — because this `Scope:` line
> named only `traffic.py`.** Both tap read-back surfaces rendered non-`COMPLETED` rows as live state,
> and the GAP-044 fix (refreshing op inputs at attempt-start) made the omission newly reachable: an
> op that fails lock resolution or times out acquiring a lock records new inputs with `attempts=0`,
> adapter never called. **A lesson is only as good as the scope line someone greps.** When adding a
> playout that reuses an existing idempotency mechanism, grep LESSONS for that mechanism and add the
> new module to the matching `Scope:` — do not wait to rediscover the rule the expensive way.
**Consumers:** code-writer, code-reviewer, tester
**Rule:** `mark_op_destroyed` (new this build, distinct from the full-deploy `mark_destroyed`) sets
ONE op row's status to `destroyed` so a read-only summary (`traffic_status`/`traffic_info_from_deploy`)
stops reporting a de-scoped instance as active. But `reset_incomplete_ops` — called on EVERY re-apply
against an existing deploy, before the executor runs — reverts every row that isn't `completed` back
to `pending`, and `destroyed` is one of those non-`completed` statuses. So a `destroyed` row from a
PRIOR apply's de-scope pass does not stay `destroyed` across a SUBSEQUENT apply — it flips back to
`pending`, and if that instance's `instance_id` happens to be back in the new plan (e.g. an operator
re-adds a previously-dropped assignment), the op re-runs normally, which is actually fine; the trap is
assuming the `destroyed` marking is itself a durable, load-bearing fact that some OTHER logic can
rely on later. It isn't — it is a point-in-time annotation for a status READ, not a permanent record.
The durable truth is always: (1) is this instance's `instance_id` present in the CURRENT apply's
freshly-resolved set, and (2) was its driver actually told to stop. A read-only surface must
therefore filter positively to `status == COMPLETED` ("this is what's live right now"), never
negatively to "not `destroyed`" — the latter would resurrect a `pending`-reverted-but-not-yet-
re-run row as a false "active" the moment `reset_incomplete_ops` touches it, before the executor has
confirmed anything.
**Origin:** CHANGELOG 2026-07-24 (Traffic tier (`tfc`) BUILT — `docs/WORK_ORDER_traffic_tier.md`,
`axrange/planner/state.py`'s `mark_op_destroyed`, code-review verification pass on
`_assignments_summary_from_ops`/`traffic_info_from_deploy`'s `COMPLETED`-only filter)

## LES-114 — When a new config-class op's natural handler package would create a real import cycle, give the op its own small `Adapter`-protocol backend in the CALLING package instead — don't force the handler location or invert the cycle

**Class:** pattern (reuse rule)
**Scope:** any new op whose obvious handler home (an existing `ConfigBackend`/similar dispatch class)
already has a dependency that would point back at the new op's own domain package (`axrange/
o2control/traffic.py`'s `PersonaBackend` vs. `axrange.config_exec.ConfigBackend` +
`axrange.personas.drivers`; any future `o2control` playout adding a config-class op whose natural
domain package is already imported by an existing handler)
**Consumers:** code-writer, code-reviewer
**Rule:** `OP-TFC-010 drive-persona`'s obvious adapter-side home would be `axrange.config_exec.
ConfigBackend` (every other config-class op routes there) — except `axrange.personas.drivers`
already imports `config_exec.render`'s `systemd_daemon_start_block` primitive, so `config_exec`
importing `axrange.personas` back to handle `drive-persona` would be a REAL cycle, not a stylistic
concern. The fix is not to relocate `personas` under `config_exec`, nor to strip the shared
primitive out of `personas.drivers` (both would cost more than they'd save) — it's to give the new
op its OWN small `Adapter`-protocol-shaped backend (`.run(op, inputs, ctx)`) living in the CALLING
package (`o2control`, which already legitimately imports both `planner`/`adapter_proxmox` (the
engine) and `axrange.personas` (engine vocabulary) with no cycle either direction), and hand it to
`run_deploy_executor` as BOTH the `adapter=` and `config_backend=` slots — valid precisely because
the new op is always one `idempotency_class` (here, `CONFIG`), so only one of the two slots is ever
actually dispatched to; the other becomes a deliberate fail-loud trip-wire (`AdapterError` naming the
unsupported op) if that assumption is ever violated by a future op sharing the same backend. This is
the general shape any future op should follow when its "obvious" handler already imports the new
op's own domain: don't force a relocation or invert the cycle — add a purpose-built backend at the
call site that already sits on the correct side of both dependency directions, and use the
executor's existing duck-typed backend seam to plug it in.
**Origin:** CHANGELOG 2026-07-24 (Traffic tier (`tfc`) BUILT — `docs/WORK_ORDER_traffic_tier.md`,
`axrange/o2control/traffic.py`'s module docstring + `PersonaBackend`)

## LES-115 — A spec field whose value doesn't reach the determinism/replay hash must be wired-in or fail-loud; a "hash-equal" claim is a promise, not a description of what happens to be hashed

**Class:** silent-success trap
**Scope:** any spec/playout that advertises "same input ⇒ same replay/output" via a computed hash
(`axrange/o2control/exercise.py`'s `playout_hash()`; any future tier making a determinism/replay
claim keyed off a subset of its own declared spec fields)
**Consumers:** code-writer, code-reviewer, tester
**Rule:** the exercise spec's top-level `seed` field existed in the schema and was described as the
thing that makes a replay identical — but an early cut computed `playout_hash()` off the existing
`ir_hash`-shaped inputs (attack/defense/measurement content) without folding the top-level `seed`
into it, so two runs with *different* `seed` values produced the *same* hash: the field was present,
documented, and completely inert with respect to the one property it was named for. A "hash-equal
implies replay-identical" claim is a promise about *every* field that's supposed to affect the
outcome, not a description of whatever the implementation happened to feed the hash function. The
fix is either (a) wire the field into whatever downstream mechanism actually varies the replay (here:
fold `seed` into `activity.seed`, the field the OP-ACT engine's own seeded schedule/jitter already
consumes — LES-driving precedent: CHANGELOG 2026-07-10's `Schedule.interval`/`Activity.seed`), or (b)
if a field genuinely can't be threaded through yet, raise loud at spec-resolution time rather than
silently accepting and ignoring it. Never let a determinism-relevant field type-check and validate
while being dead weight — the failure mode is a user trusting a replay is seeded when it silently
isn't, discovered only much later when two "identical" runs produce different traffic.
**Origin:** CHANGELOG 2026-07-24 (Exercise tier (`exr`) BUILT — `docs/WORK_ORDER_exercise_tier.md`,
`axrange/o2control/exercise.py`'s `playout_hash()`, code-review verification pass on the top-level
`seed` field)

## LES-116 — A cross-deploy overlap/uniqueness guard that reads persisted state but holds no shared mutex spanning check→persist only prevents the SEQUENTIAL collision, never the concurrent one

**Class:** reversal hazard / dead-end (documented limit, not a bug to "just fix" casually)
**Scope:** any standalone playout tier adding a same-target overlap or uniqueness guard by reading
current persisted state before creating a new deploy row (`axrange/o2control/exercise.py`'s
same-host overlap guard; any future tier's "refuse if X is already running against this twin" check)
**Consumers:** code-writer, code-reviewer, tester
**Rule:** the `exr` tier's same-host overlap guard reads every other non-terminal `exr` deploy bound
to the same twin, checks whether its own target hosts overlap theirs, and refuses if so — a
read-then-persist pattern with **no lock held across both steps**. This correctly serializes the
tier's actual supported use (a sequential replay loop: run, wait/destroy, replay) — a later
invocation always sees the earlier one's persisted state and correctly refuses/allows. It does
**NOT** prevent a genuinely concurrent double-apply: two invocations issued close enough together
can both read "no overlap yet" before either persists, and both proceed. Do not describe such a
guard as "prevents overlapping runs" unqualified — state precisely that it "serializes sequential
re-apply," document the concurrent gap explicitly (a `docs/PRODUCT_GAPS.md` entry, not a silent
omission), and if the invariant must hold under true concurrency, the fix is a twin-scoped lock
(mutex) held across the check-and-persist window, modeled on the planner's existing per-scenario
deploy lease (`docs/PLANNER.md` §6.2) — do not reach for a "just add more read-checks" band-aid,
which cannot close a TOCTOU race no matter how many extra reads it adds.
**Origin:** CHANGELOG 2026-07-24 (Exercise tier (`exr`) BUILT — `docs/WORK_ORDER_exercise_tier.md`,
`axrange/o2control/exercise.py`'s overlap guard, code-review verification pass; `docs/PRODUCT_GAPS.md`
GAP-034)

## LES-117 — A destroy-time cascade over N sibling tiers must isolate each sibling's UNEXPECTED exceptions, or one sibling's bug skips the rest and fails the parent's own teardown

**Class:** bug-class (error-handling gap)
**Scope:** any destroy/teardown path that cascades to more than one independent sibling deploy tier
(`axrange destroy`'s cascade to `tap`/`tfc`/`exr` from a `te` destroy; any future tier added to that
cascade list)
**Consumers:** code-writer, code-reviewer, tester
**Rule:** a `te destroy` cascades to three independent sibling tiers (`tap`, `tfc`, `exr`, after this
build). Catching only each cascade function's own *typed* error (e.g. `TapPlanNotFoundError`) is not
enough — an unexpected exception in one sibling's cascade (a bug, a transient adapter error, a
malformed persisted row) must not be allowed to propagate out of the loop uncaught, because that
would (a) skip every sibling cascade sequenced after it, leaving those resources orphaned with no
error surfaced about THEM specifically, and (b) abort the parent `te` destroy's own teardown entirely
even though the `te`'s own resources were never the problem. The fix is to wrap **each** sibling
cascade call individually in its own broad `try/except Exception` (not just its documented typed
exception), log/record the failure against that specific sibling, and continue to the next sibling
and then the parent's own teardown regardless — surfacing every sibling's failure (e.g. via
`DestroyResponse.cascade_errors` on the API side) rather than swallowing all-but-the-first or
aborting on the first. Isolating exceptions per sibling in a fan-out cascade is a distinct
obligation from having per-sibling typed errors in the first place — both are needed, neither
substitutes for the other.
**Origin:** CHANGELOG 2026-07-24 (Exercise tier (`exr`) BUILT — `docs/WORK_ORDER_exercise_tier.md`,
`axrange/planner/teardown.py`'s per-sibling cascade isolation for `tap`/`tfc`/`exr`, code-review
MUST-FIX)

## LES-118 — A shared netdev keyed on a COARSER dimension than the de-scope diff's granularity is a data-loss trap: diff at resource-IDENTITY granularity, never at the logical-pair granularity that merely REFERENCES it

**Class:** bug-class (silent-success trap / capture-plane data loss) — flagship of this session
**Scope:** any playout that de-scopes (drops/narrows) a set of logical bindings where more than one
binding can resolve to the SAME underlying shared resource (`axrange/o2control/taps.py`'s gretap
de-scope on a VLAN zone, where one keyed gretap device is shared across every vnet routed to a given
sensor; any future de-scope pass over a many-to-one logical-pair→resource mapping)
**Consumers:** code-writer, code-reviewer, tester
**Rule:** a keyed gretap netdev is named per `(underlay-bridge, sensor)` and is legitimately **shared**
by every `(net, sensor)` pair that happens to route through that bridge/sensor combination on a VLAN
zone. The de-scope drop-diff computed "pairs no longer needed" at `(net, sensor)` granularity and then
deleted the gretap device belonging to each dropped pair — but a dropped pair's device could be the
SAME device a still-needed pair also references. Deleting it tore down LIVE capture for the surviving
pair, silently, while the apply still reported `completed` and a `verify-capture` heartbeat that had
already run moments earlier still read green — the heartbeat was accurate at the instant it ran; the
delete happened right after. **The general rule: when N logical bindings can map onto the same
physical/shared resource, a de-scope/drop diff MUST be computed at the resource's own identity
(does ANY surviving binding still reference this exact resource?), never at the per-binding pair
level (is THIS pair still needed?).** The latter is only safe when the mapping is 1:1; the moment it
becomes N:1, per-pair diffing deletes shared state out from under a sibling that still needs it. Any
future teardown/de-scope pass touching a shared resource (a bridge, a mirror, a shared subnet, a
shared credential) must ask "does the fixture I'm about to delete have ANY other live referrer?"
before deleting, not "is the one relationship I'm looking at still wanted?".
**Origin:** CHANGELOG 2026-07-25 (Twin-model arc: dev45 live-verify Stages 1–4 PASSED, fix #7 —
`axrange/o2control/taps.py`'s gretap-device-granularity de-scope diff, Stage 3 `tap_points` re-shard)

## LES-119 — A cross-OS driver's stop/teardown path must recover the instance's actual `os_family`, never assume Linux by default

**Class:** bug-class
**Scope:** any driver/backend dispatch that branches on `os_family` for start but shares one
teardown/stop code path (`axrange/personas/drivers/base.py`/`_stop_instances`; any future cross-OS
driver whose stop logic is written after its start logic and inherits the same instance record)
**Consumers:** code-writer, code-reviewer, tester
**Rule:** `_stop_instances` was written to iterate persisted instance records and stop each one — but
it resolved `os_family` inconsistently from how `.start()` had resolved it, defaulting to the Linux
(`systemctl`/`pkill`) stop path for records that were actually Windows (Scheduled Task) instances. A
cross-OS component's stop/teardown path must recover the SAME `os_family` fact the start path used —
persisted alongside the instance record, not re-derived or defaulted — and dispatch its stop logic on
that recovered fact exactly as strictly as the start path dispatched on it. Silently defaulting to one
OS family's stop mechanics on a differently-typed instance either no-ops (leaves it running) or
errors confusingly; neither is acceptable in a fail-loud codebase.
**Origin:** CHANGELOG 2026-07-25 (Twin-model arc: dev45 live-verify Stages 1–4 PASSED, fix #5 —
`axrange/personas/drivers/base.py` `_stop_instances` os_family leak, Stage 4 `traffic_tier`
live-verify)

## LES-120 — A Windows Scheduled Task used as a long-running daemon needs `-ExecutionTimeLimit 0`, or Windows kills it after its default runtime cap

**Class:** platform/tool gotcha
**Scope:** any Windows-guest driver launching a long-running process via `schtasks`/
`Register-ScheduledTask` intended to run indefinitely (`axrange/config_exec/render.py`
`scheduled_task_start_block`; any future Windows daemon-equivalent launch)
**Consumers:** code-writer, code-reviewer
**Rule:** Windows Scheduled Tasks default to a finite execution time limit (commonly 72 hours, but
non-zero regardless) — a task launched as the Windows analog of a Linux `systemd-run --unit=
--collect` detached daemon will still be killed by the Task Scheduler once that limit elapses unless
`-ExecutionTimeLimit 0` (or the equivalent `<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>` in the task
XML) is set explicitly. This is the Windows-side sibling of the `nohup`-gets-reaped class
(GAP-020/GAP-025): a launch mechanism that LOOKS durable (detached from the launching QGA session) can
still have a silent, delayed self-termination baked into its own defaults. Any future Windows
"survive the session, run forever" launch must explicitly disable the time limit, not just detach
from the launching process.
**Origin:** CHANGELOG 2026-07-25 (Twin-model arc: dev45 live-verify Stages 1–4 PASSED, fix #5 —
`axrange/config_exec/render.py` `scheduled_task_start_block`, Stage 4 live-verify — confirmed the task
survived the QGA session AND kept running across the observation window)

## LES-121 — A boundary/router-adjacent tap's verify-capture sentinel must be injected from a LEAF on the segment, never from the router itself, or the injection can escape the mirrored path entirely

**Class:** bug-class (silent-success trap avoided by design, but easy to get backwards)
**Scope:** any capture-heartbeat injector targeting a `boundary`-scoped tap point that sits at a
router's leg (`axrange/o2control/taps.py`'s verify-capture sentinel injection; any future boundary/
perimeter tap point whose mirrored interface is a router or gateway device)
**Consumers:** code-writer, code-reviewer, tester
**Rule:** a `boundary` tap point mirrors traffic crossing a router's leg — but injecting the sentinel
FROM the router means the router's own reply/broadcast follows its OWN routing table, which for a
boundary/WAN-adjacent leg is very likely to egress out the WAN rather than loop back across the
mirrored segment leg at all. The correct injector is a single-homed LEAF host ON the segment: its
broadcast/sentinel traffic floods across the segment's own L2 domain and DOES cross the router's
mirrored port, because the leaf has no alternate route to take. **The general rule: when picking an
injection point for a mirror/tap heartbeat, the injector must be a host whose traffic is FORCED across
the tapped point by topology (no alternate path), not a host (like the router itself) that has other
paths available and might reasonably take one of them.** A green heartbeat from the wrong injector can
look identical to a real one while silently proving nothing.
**Origin:** CHANGELOG 2026-07-25 (Twin-model arc: dev45 live-verify Stages 1–4 PASSED, fix #2 —
`axrange/o2control/taps.py` boundary sentinel injector, Stage 3 `tap_points` live-verify — first real
boundary tap point on a real twin)

## LES-122 — Porting a POSIX ping-payload sentinel to Windows: use `[Net.NetworkInformation.Ping]::Send(buffer)` with a UNICAST target, never assume a .NET broadcast-ping primitive exists

**Class:** platform/tool gotcha
**Scope:** any capture-heartbeat/sentinel mechanism ported from a Linux/POSIX `ping -p <hex-payload>`
pattern to a Windows guest (`axrange/config_exec/render.py`
`render_capture_sentinel_windows`; any future Windows-side reimplementation of a Linux wire-heartbeat
primitive)
**Consumers:** code-writer, code-reviewer
**Rule:** the Linux verify-capture sentinel embeds a token in an ICMP echo payload via `ping -p
<hex>`, broadcasting where an L2-flood reach is needed. Windows has **no built-in broadcast-ping**
equivalent reachable from PowerShell/.NET — `[Net.NetworkInformation.Ping]::Send` requires a specific
unicast destination and a byte buffer (the payload), not a broadcast address. The Windows port
therefore targets the segment's gateway (or another specific, topology-forced host) directly with the
token embedded in the ping's data buffer, rather than trying to replicate a broadcast semantic that
doesn't exist on the platform. Any future POSIX-primitive port to Windows should check for an
analogous "the underlying platform primitive just doesn't support this mode" gap before assuming a
1:1 translation is possible — the fix is usually a different (but topologically equivalent) target,
not a workaround to force the missing semantic.
**Origin:** CHANGELOG 2026-07-25 (Twin-model arc: dev45 live-verify Stages 1–4 PASSED, fix #3 —
`axrange/config_exec/render.py` `render_capture_sentinel_windows`, Stage 3 `tap_points` live-verify —
first heartbeat from a Windows injector)

## LES-123 — An optional/soft escape hatch on a fail-loud invariant must gate STRICTLY on the explicit flag; the hard (default) path must keep raising exactly as before

**Class:** pattern (positive — reuse rule for future "make a strict rule less strict" refinements)
**Scope:** any validator/rule refinement that adds an opt-out for a previously-unconditional
requirement (`PSN-090`'s `emits[].optional: true` exemption, `axrange/validator/checks/personas.py`;
any future "most cases must satisfy X, but an explicitly-marked case may not" rule narrowing)
**Consumers:** code-writer, code-reviewer, tester
**Rule:** PSN-090 originally required every persona's `emits[].peer_role` to appear in that persona's
own `requires[]`, unconditionally — correct for a "hard" peer (the DC a workstation MUST reach), but
too strict for a "soft" peer the persona talks to only if present (a print server, a dedicated DNS
resolver) that a flat/minimal twin config legitimately omits. The fix is NOT to loosen PSN-090
generally (that would silently let a genuinely-required peer go unrequired) — it is to add an
explicit, positive `optional: true` field on the `emits[]` entry itself, and gate the exemption
STRICTLY on that field being true: an entry without `optional: true` set must still fail exactly as
before if its peer isn't in `requires[]`. The general shape: when narrowing a fail-loud rule to
tolerate a real exception, the exception must be an explicit, author-visible opt-in on the exact case
that needs it — never a general loosening of the rule's default behavior, which would silently let
unrelated genuinely-broken cases through unnoticed.
**Origin:** CHANGELOG 2026-07-25 (Twin-model arc: dev45 live-verify Stages 1–4 PASSED, fix #4 —
`axrange/personas/schema.py` `Emit.optional`, `axrange/validator/checks/personas.py` PSN-090
refinement, Stage 4 `traffic_tier` live-verify)

## LES-124 — An "informational"/follow-on spec field that never reaches any driver's behavior is a latent surprise, not a harmless placeholder — wire it in or fail loud, the same discipline as LES-115

**Class:** silent-success trap
**Scope:** any spec field documented as affecting run-time behavior that a build ships without
actually threading through to the mechanism that would honor it (`axrange/o2control/traffic.py`'s
assignment-level `schedule` field before this fix; any future profile/spec field labeled
"informational (follow-on)" or similar in its own doc/docstring)
**Consumers:** code-writer, code-reviewer, tester
**Rule:** the traffic-profile assignment's `schedule` field (`24x7`/`business-hours`) was present in
the schema, accepted by validation, and documented as controlling when a persona's traffic emits — but
nothing downstream read it; every assignment ran on its cadence-derived default regardless of what
`schedule` said. This is the same class LES-115 named for the exercise tier's inert `seed` field: a
field that type-checks and validates while being completely dead weight with respect to the behavior
it's named for is worse than not having the field at all, because an operator setting it reasonably
believes it's doing something. The fix pattern is always one of two: (a) wire the field into the
actual downstream mechanism (here: `schedule` now sets the driver's emit gate — `always` for `24x7`,
`business_hours` for `business-hours`, cadence-derived if unset), or (b) if wiring isn't ready yet,
fail loud at parse/resolve time rather than silently accepting and ignoring the value. Never ship a
spec field whose only observable effect is passing validation.
**Origin:** CHANGELOG 2026-07-25 (Twin-model arc: dev45 live-verify Stages 1–4 PASSED, fix #6 —
`axrange/o2control/traffic.py` + `axrange/personas/drivers/scripted.py` schedule-override wiring,
Stage 4 `traffic_tier` live-verify)

## LES-125 — A green `verify-capture` heartbeat proves the mirror path was intact AT THE MOMENT it ran; it is not evidence the path stays intact afterward — a subsequent de-scope/re-apply can silently tear it down right after

**Class:** live-env fact / verification idiom (what a heartbeat does and does NOT prove)
**Scope:** any tier whose done-criterion is "verify-capture passed" (`axrange/o2control/taps.py`'s
capture heartbeat; any future apply/re-apply pass that runs a heartbeat check and then performs
further state changes, such as a de-scope, afterward)
**Consumers:** code-writer, code-reviewer, tester, doc-writer (framing "verify-capture PASSED" claims
in docs/runbooks)
**Rule:** `verify-capture` is a POINT-IN-TIME heartbeat — it proves a sentinel traversed the tap →
mirror → sensor path at the instant it ran, nothing more. LES-118's gretap-de-scope bug is the
concrete proof: the heartbeat had genuinely passed, and moments later an unrelated de-scope pass
(over-broadly diffing at the wrong granularity) deleted the shared gretap device the just-verified
path depended on — capture was gone, but nothing re-ran verify-capture to notice, and the apply's own
result still said `completed`. Any doc, runbook, or done-criterion that phrases this as
"verify-capture PASSED ⇒ capture is durably working" is overclaiming; the correct claim is
"verify-capture passed at time T" — a SEPARATE, later check (a re-run of verify-capture, or an
explicit "confirm capture survives an unrelated re-apply" step, as Stage 3's re-shard check now does)
is required to claim durability across any subsequent state change. Treat every "the heartbeat is
green" claim as scoped to the instant it ran, not as a standing guarantee.
**Origin:** CHANGELOG 2026-07-25 (Twin-model arc: dev45 live-verify Stages 1–4 PASSED, fix #7's
root-cause narrative — `axrange/o2control/taps.py`, Stage 3 `tap_points` re-shard live-verify)

## LES-126 — A twin-config edit can't resume a standing deploy: `plan_id` is keyed on IR content hash, so ANY edit (even additive) is a brand-new deploy, never an incremental add

**Class:** dead-end
**Scope:** `axrange/planner` (`derive_plan_id`), `axrange/twinconfig/` (expander), any workflow that
edits a twin-config or hand-authored IR expecting to extend an already-deployed twin
**Consumers:** code-writer, tester, live-verifier, doc-writer (runbook/exercise-authoring framing)
**Rule:** `derive_plan_id(scenario_id, ir_hash)` keys plan/deploy identity on the **content hash** of
the expanded IR — there is no notion of "the same twin, plus one node." Editing a twin-config (or any
IR) that has already been deployed and re-running `deploy` against it does **not** resume/extend the
standing deploy; it computes a different `plan_id` and would launch an entirely separate, full deploy
alongside (not instead of) the standing one. This is a genuine dead-end today, not a bug to route
around in the caller: don't attempt "add a node and redeploy" as a lightweight operation. Until
plan-diff/re-planning (`docs/PLANNER.md` §9) lands, the only two honest options are (a) accept a full
fresh redeploy of the whole twin, or (b) hand-provision the missing capability directly onto an
already-deployed host (QGA exec / in-guest commands) — which is what the twin-model live-verify did
for its Stage-5 attacker node. If you author twin-config content for a future capability, say so
explicitly in a comment/doc — the config and what was actually deployed can legitimately diverge.
**Origin:** CHANGELOG 2026-07-25 (Twin-model arc: dev45 live-verify Stage 5 (`exercise_tier` capstone)
PASSED); GAP-040.

> **Partially addressed 2026-08-11 (`docs/WORK_ORDER_twin_incremental_update.md`, `axrange te deploy
> --update`; CHANGELOG 2026-08-11).** A REDUCED subset of `docs/PLANNER.md` §9 (plan-diff/
> re-planning) is now built: add/remove whole nodes against a standing deploy, with the standing
> deploy's `plan_id` PINNED so every unchanged resource keeps its identity. This lesson's core claim
> — "editing a twin-config and redeploying does not resume the standing deploy" — no longer applies
> to the flagged path (`--update`); it remains fully accurate for the UNFLAGGED path (an ordinary
> edited redeploy still mints a new, separate deploy, by design — this dead-end is now an opt-in
> choice rather than the only option). Two things `--update` deliberately does NOT do, so this
> lesson's "hand-provision around the model" workaround is still the only option for them: in-place
> mutation of a SURVIVING node's own fields (§9.2's redo-rules table — refused loudly, never redone)
> and sibling-tier (`tap`/`tfc`/`exr`) auto-reconciliation (warned about, not fixed). **Gated `dev45`
> live-verify has not yet run** — treat `--update` as unverified against real infrastructure until it
> does (see the work order §6).

## LES-127 — Nested-quoted PowerShell AD filters don't survive a bash→base64→PowerShell delivery path; prefer `-Identity`/plain double-quoted strings

**Class:** platform-quirk
**Scope:** `axrange/adapter_proxmox/guest_agent.py` (QGA command construction), any hand-authored or
generated PowerShell one-liner delivered through the base64-in-command path (LES-002) that scripts AD
cmdlets (`New-ADUser`, `Get-ADComputer`, `Get-ADUser -Filter`, etc.)
**Consumers:** code-writer, live-verifier
**Rule:** A PowerShell command built to run AD cmdlets with a `-Filter "… ''x''"`-style nested-single-
quote filter expression, then wrapped in bash quoting and/or base64-encoded for QGA delivery, has its
nested quotes silently mangled by the multiple quoting layers (bash → PowerShell string → embedded AD
filter) rather than failing loud — the command runs but matches nothing (or errors in a way that
doesn't point at quoting). When hand-provisioning AD objects/queries through this delivery path
(as the Stage-5 twin-model live-verify did for vulnerable-account creation over QGA), prefer
`-Identity <name>` or a plain double-quoted string with no embedded quote-escaping over a nested-quote
`-Filter` expression.
**Origin:** CHANGELOG 2026-07-25 (Twin-model arc: dev45 live-verify Stage 5 (`exercise_tier` capstone)
PASSED — hand-provisioning vulnerable AD accounts via QGA `New-ADUser` during the Stage-5 workaround).

## LES-128 — A "never raises" best-effort resolver must catch bare `OSError`, not just the adapter's own typed error classes — a read-phase timeout escapes unwrapped

**Class:** bug-class
**Scope:** `axrange/adapter_proxmox/resolve.py`; any best-effort/"never raises" helper that calls
`RestClient.request(...)` or another adapter transport method and is documented to degrade gracefully
rather than propagate
**Consumers:** code-writer, code-reviewer
**Rule:** `RestClient.request` only wraps CONNECT-phase failures (`urllib.error.URLError`/
`HTTPError`) into the adapter's typed `AdapterError`/`AdapterTimeout`. A READ-phase failure against a
reachable-but-hung host — `socket.timeout`, `TimeoutError`, `ConnectionResetError` during `resp.read()`
— is a bare `OSError` subclass that propagates UNWRAPPED. A resolver written to catch only
`(AdapterError, AdapterTimeout)` is not actually never-raising: it crashes the human-facing command
(`te info`/`o2 info`/`tap info`) it exists to keep alive, on exactly the failure mode ("cluster is
there but not responding") it was built for. Catch `OSError` alongside the typed classes in every
best-effort call site, and use a short caller-supplied timeout (`resolve.py`'s `_INFO_TIMEOUT_S=5`) so
the degrade happens quickly rather than hanging a terminal.
**Origin:** CHANGELOG 2026-07-25 (Access facts & endpoint resolution, code review SHOULD-FIX 1); `docs/WORK_ORDER_access_facts.md`.

## LES-129 — Best-effort human-facing lookups and correctness-critical adapter calls are two intentionally separate call styles — never let one collapse into the other

**Class:** reuse-rule
**Scope:** `axrange/adapter_proxmox/resolve.py` vs. `axrange/adapter_proxmox/rest.py` /
`axrange/planner/teardown.py`; any future helper that queries live Proxmox state for BOTH a
human-facing display and a decision the engine acts on
**Consumers:** code-writer, code-reviewer
**Rule:** `resolve.py`'s short-timeout (5s), never-raise, degrade-to-`None`-or-a-placeholder lookups
exist ONLY to keep an `info` command responsive and non-crashing for a human waiting on a terminal.
They must never be reused by a correctness-critical path — the teardown protect guard
(`planner/teardown.py`'s `_protected()`), a deploy-time op's check-then-act, or anything else whose
WRONG answer (a stale `None` from a timeout that was actually just slow) could mean destroying/leaving
up the wrong resource. Those paths keep the adapter's normal full-timeout, fail-loud client. When
adding a new "look up the live state of X" helper, decide up front which of the two call styles it is
and put it in the matching module — don't add a "just this once" fast-path shortcut to a
correctness-critical caller.
**Origin:** CHANGELOG 2026-07-25 (Access facts & endpoint resolution, code review SHOULD-FIX,
architectural separation of `resolve.py` from `rest.py`); `docs/WORK_ORDER_access_facts.md`.

## LES-130 — Enabling SSH password login + a well-known default password is a lab-wide posture change the moment the knob exists, even unset — document it AT the knob, and never show a default-password hint on a node that can't accept one

**Class:** security
**Scope:** `axrange/adapter_proxmox/config.py` `AdapterConfig.default_password` / `DEFAULT_PASSWORD`
(adapter.conf); `axrange/cli/main.py` `_fill_access_facts` (the `te info` access-hint renderer); any
future default-credential knob
**Consumers:** code-writer, code-reviewer, doc-writer
**Rule:** Because `DEFAULT_PASSWORD`'s built-in default (`Axr4nge-Lab!`) is non-empty, Linux SSH
password login (`ssh_pwauth: true` + `lock_passwd: false`) turns ON lab-wide the moment
`configure-cloud-init` runs — even on an adapter.conf that never mentions the key at all. This is a
deliberate lab-usability trade, not an accident, but it must be documented directly at the knob
(`adapter.conf.example`'s comment block, the dataclass field's own docstring) — a security-relevant
default should never be discoverable only by reading source. The companion half of this rule: a node
that has no `configure-cloud-init` op at all (a VyOS router — config is delivered over SSH `vbash`
instead, per `docs/SLICE_ROUTER.md`) must NOT be shown a default-password access hint it doesn't
actually accept — `_fill_access_facts` detects this via `h.ssh_user` being unset on a `kind ==
"router"` host and renders a `(key auth)` hint with no password field, rather than printing a
password that would simply fail. Never let a generic "show the effective password" code path assume
every node type has one.
**Origin:** CHANGELOG 2026-07-25 (Access facts & endpoint resolution, code review SHOULD-FIX 2 +
the Part-A security-posture note); `docs/WORK_ORDER_access_facts.md`.

## LES-131 — A new kwarg on a widely-mocked adapter method must default to a sentinel that's never forwarded unless the caller explicitly asks — or every hand-written fake in the suite breaks at once

**Class:** pattern
**Scope:** `axrange/adapter_proxmox/rest.py` `vm_status` (and any other adapter function/method with
many hand-written fake stand-ins across `tests/`); any future signature change to a function tests
mock by hand rather than via a shared fixture
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Adding `timeout_s: int | None = None` to `vm_status` and having it ALWAYS forward
`timeout_s=...` to `client.request(...)` would have broken every hand-written fake `RestClient.
request()`/`vm_status()` across the test suite that doesn't accept that kwarg — a signature-widening
change silently becomes a mass test-breakage the moment ANY caller (even ones that never asked for the
new behavior) hits the new code path. The fix pattern: default the new kwarg to `None` (a sentinel,
not the real default value) and only forward it to the underlying call when the caller explicitly
passed something other than `None` — every existing call site's invocation stays byte-for-byte
unchanged, and only the one new best-effort caller (`resolve.py`) that actually wants a short timeout
opts in. Generalizes beyond this case: when widening a widely-mocked method's signature, ask whether
the new parameter can be made invisible to every caller that doesn't use it, not just given a
harmless-looking default.
**Origin:** CHANGELOG 2026-07-25 (Access facts & endpoint resolution, `vm_status`/`RestClient.request`
`timeout_s` addition); `docs/WORK_ORDER_access_facts.md`.

## LES-132 — An exclusion built on a naming-PREFIX filter both over-excludes a genuinely-named match and makes the structural check that follows it unreachable dead code

**Class:** bug-class
**Scope:** any resolver/query that needs to exclude a structurally-different-but-same-tier row
(`axrange/o2control/taps.py`'s `resolve_target_deploy`, resolving a scenario name to its live `te`
deploy while excluding tap-plan playout deploys — themselves also `tier="te"`, LES-082's discriminator
problem); any future `deploys_matching(...)`-style query that layers a cheap name/prefix filter in
front of a structural discriminator on the SAME candidate set
**Consumers:** code-writer, code-reviewer
**Rule:** `list-deploys --tier te`'s `exclude_plan_id_prefix="tap-"` is a deliberately-imperfect,
cheap first-pass filter for "every genuine te, excluding tap-plan playouts" — a real te scenario
happening to be named `tap-*` is an accepted, rare false-negative there. Reusing that SAME
`exclude_plan_id_prefix="tap-"` filter inside `resolve_target_deploy` (which needs "the live te
deploy whose `scenario_id` equals this exact name," a narrower and different question) is wrong in
two compounding ways: (1) it over-excludes — a genuine, live `te` twin whose *scenario* happens to be
named `tap-*` (`derive_plan_id` mints `tap-<name>-<hash>` for it) gets filtered out by plan-id prefix
BEFORE `scenario_id == target` is ever checked, so a real target 404s; (2) it makes the structural
`is_tap_plan_deploy` check written immediately after it UNREACHABLE DEAD CODE — nothing with a
`tap-`-prefixed plan_id survives the SQL filter to reach that check, so the "real" discriminator never
actually discriminates anything in this code path. The fix: filter to `scenario_id == target` over
the FULL tier-scoped candidate set first, and let the structural discriminator (`is_tap_plan_deploy`,
LES-082) alone decide "is this actually a tap-plan playout" — never combine a same-tier structural
exclusion with a naming-prefix shortcut on the exclude side; the prefix shortcut is only valid when
the caller can tolerate the false-negative it introduces (as `list-deploys` can), not when the caller
needs an exact, correct answer for one specific name.
**Origin:** CHANGELOG 2026-07-25 (Predefinable tap plans, `docs/WORK_ORDER_tap_target_resolution.md`
review, must-fix)

## LES-133 — Authoring a present-tense "design of record" doc from a history-heavy CLAUDE.md/CHANGELOG source demands a mechanical grep self-check, not just careful drafting

**Class:** verification-idiom
**Scope:** `docs/design/*.md` (the canonical design-of-record set) and any future authoring pass
against it; the inverse direction of LES-047-style drift checks — here the risk is importing
history-language INTO a present-tense doc, not letting a present-tense doc go stale
**Consumers:** doc-writer
**Rule:** `CLAUDE.md` and the formal specs are written as an accreting narrative — full of
"ratified 2026-06-03", "superseded", "no longer", "formerly", "(fixed 2026-07-07)" — because that
narrative IS the point of those documents. `docs/design/*.md` has the opposite job: describe the
system as it is, publishable, with zero learning-what-it-used-to-be required. Drafting by ear from
a history-heavy source reliably leaks a few of those markers through (a sentence that summarizes a
CHANGELOG entry keeps its "was"/"now"/"landed" framing by default) even when the author knows the
banned-word list going in. The reliable fix is mechanical, not diligence: after drafting, run
`grep -nE '\b(was|formerly|previously|superseded|retired|used to|no longer|landed)\b|CHANGELOG|20\d\d-|LES-|GAP-|WORK_ORDER|wip/' <file>` and rewrite every hit before treating the doc as done — do
this as a standing last step for every `docs/design/*.md` edit, not only the initial authoring
pass, since a later edit that patches in a new capability is exactly where a history marker gets
reintroduced.
**Origin:** `docs/design/03-engine.md` + `docs/design/04-networking-capture.md` authoring
(design-of-record doc-set effort, 2026-07-26).

## LES-134 — A secrets-bearing GET must gate its reveal on a boolean predicate, never the write-token dependency itself — masking is the default-safe posture, the token only narrows further

**Class:** security-idiom
**Scope:** any read endpoint that can optionally return a secret (`axrange/api/app.py`'s
`?secrets=true` reveal on `/api/deploys/{id}/access` and `/api/o2/info`; any future secrets-bearing
GET in `axrange/api/`)
**Consumers:** code-writer, code-reviewer
**Rule:** `_require_write_token`'s FastAPI dependency is built to make a token **mandatory** the
moment it's configured — correct for writes, wrong for a read endpoint whose *default* response is
already safe (masked) and only needs the token for the *optional* unmasked variant. Reusing that
dependency on a secrets-bearing GET would force a token even for the masked default read, breaking
the "reads are open" posture the rest of the API relies on. The fix: factor the check into a bare
boolean predicate (`_is_authorized(settings, authorization)` — open when no token is configured,
else a `secrets.compare_digest` constant-time compare) and gate **inside the handler**, conditional
on the caller actually asking to reveal: `if reveal_secrets and not _is_authorized(): raise 401`.
Masking is the service-layer default (`_masked()` in `axrange/api/service.py` runs unconditionally
unless `reveal_secrets` is threaded all the way through), so a bug in the gate check fails **closed**
— the worst a broken `_is_authorized` can do is wrongly refuse a legitimate reveal, never wrongly
leak a real secret, because the unmasked value never even reaches the response builder unless
`reveal_secrets` was true. When adding a sibling field alongside a masked one (e.g. a new DTO field
that happens to carry the same underlying value), verify it routes through the same `_masked()`
call — a field that bypasses it silently re-leaks what the primary field correctly hides.
**Origin:** CHANGELOG 2026-07-26 (control-plane API parity — access facts + O2 endpoint
resolution), `axrange/api/{app,service}.py`, `docs/WORK_ORDER_access_facts.md`.

## LES-135 — To make a standing sensor's pcaps host-readable AND survive VM rebuild, run it as an EXTERNAL hand-built VM with a virtio-fs host share — not a synthesized deployable sensor

**Class:** architecture-pattern
**Scope:** the O2 capture plane — when an operator needs direct host access to a standing sensor's
pcaps and/or capture persistence across the sensor VM's lifecycle (`adapter.conf` `EXTERNAL_SENSORS`;
`scripts/o2-pcaps-*.sh`; `docs/design/06-taps-sensors.md`)
**Consumers:** code-writer, doc-writer
**Rule:** A synthesized deployable sensor writes pcaps to its own VM root disk, reachable only over
QGA and **destroyed with the VM** — so an `o2 destroy` + redeploy loses every capture (the
persistence side of GAP-025). Two mechanisms compose to fix both problems at **zero engine cost**:
(1) declare the sensor in `EXTERNAL_SENSORS` — the machinery already exists and is live-verified for
`XPress-SW` (auto teardown-protection by id→`PROTECTED_EXTERNAL_NAMES` and vmid→`PROTECTED_EXTERNAL_VMIDS`;
tap plans target it by `id`; `verify-capture` deliberately skipped since axRange never reaches into
an external node), so a hand-built tcpdump VM is "removed from the deployable sensors" simply by not
synthesizing one and declaring this instead; (2) put its capture dir on a **virtio-fs share mapped
from a Proxmox-host directory** — a *mapped host directory*, NOT an extra virtual disk (a VM disk is
a block device the host can't browse without loop/guestmount). The host dir (`/var/lib/axrange/o2-pcaps/<id>/`)
is then plain `ls`/`cp`/`rsync` target on the host, and pcaps outlive any VM destroy/rebuild. Order
the in-guest capture unit `After=` the virtio-fs mount so tcpdump never writes to an unmounted path.
Because `verify-capture` is (correctly) skipped for external sensors, confirm capture by listing the
host dir, not by a green deploy (LES-001). Key seam insight: "make X external" for a capture sensor
is a **config-only** change (`adapter.conf` + a hand-built VM), never an engine edit — resist the
literal "delete it from `SENSOR_TYPES`", which reverses ratified registry design and breaks the
validator/archetypes/tests for no benefit.
**Origin:** CHANGELOG 2026-07-26 (standing tcpdump sensor as an external VM with a virtio-fs host
share), `scripts/o2-pcaps-{list,fetch}.sh`, `adapter.conf.example`.

## LES-136 — `mapfile -t X < <(cmd)` (any process substitution) hides cmd's exit status from `set -euo pipefail` — a failed ssh then reads as "nothing matched", not "the call failed"

**Class:** silent-success trap
**Scope:** any bash reading a subprocess's output — especially over ssh — via `mapfile`/`readarray`
`< <(cmd)` or a process-substitution redirect (`scripts/o2-pcaps-{list,fetch}.sh`; any operator/
live-verify helper that shells out to the cluster)
**Consumers:** code-writer, code-reviewer
**Rule:** `$?` after `mapfile` is `mapfile`'s own status (always 0), and `pipefail` does NOT cover
`< <(…)` — so a failed `ssh` (unreachable host, auth denied, remote shell can't parse the command)
yields an empty array that the next line treats as "zero matches" and exits 0. An operator then
believes the sensor is empty when the connection actually failed (the LES-001/004 "assert, don't
assume" class, at the transport layer). Fix: capture into a var with **command substitution**
(`out=$(ssh …) || { echo err; exit $?; }`), which DOES propagate the exit code, and gate on an
explicit success sentinel emitted by the remote side (`echo __OK__` as the last line) so "ran fine,
zero matches" is distinguishable from "call died mid-output". A sibling gotcha in the same scripts:
`body=$(… | grep -v SENTINEL)` aborts under `set -e` when *every* line is the sentinel (grep -v
exits 1 on zero output) — append `|| true`. And always pass `-o ConnectTimeout=<n>` to `ssh`/`scp`/
`rsync -e` in a helper, or an unreachable host hangs on the multi-minute TCP timeout instead of
failing fast.
**Origin:** o2-pcaps-fetch.sh code review (CHANGELOG 2026-07-26) — a wrong `--host` reported "no
files match" and exit 0; a bad host also hung ~2 min for lack of `ConnectTimeout`.

## LES-137 — `shopt -s nullglob; arr=($PATTERN)` only strips words that CONTAIN a glob metachar — a literal non-existent filename survives as itself (count=1)

**Class:** bug-class (glob)
**Scope:** any bash using `nullglob` + array expansion to detect "no match" (`scripts/o2-pcaps-*.sh`;
any glob-driven file selection)
**Consumers:** code-writer, code-reviewer
**Rule:** `nullglob` removes a word from pathname-expansion output only when that word is a *pattern*
(contains `*`, `?`, or `[`). A metacharacter-free literal (`cap-20260726-143000.pcap`, a typo'd exact
name) is never subject to glob removal, so it survives expansion as itself — the array has one
element, the "empty ⇒ no match" check passes, and the name flows straight to `cp`/`rsync`/`ls`, which
then fails opaquely ("No such file"). After expansion, filter with `[ -e "$f" ] || continue` (or use
`failglob`) before trusting the count. A `cap-*` glob that matches nothing IS stripped by nullglob —
so this edge only shows up with an exact name, which glob-based smoke tests miss.
**Origin:** o2-pcaps-{list,fetch}.sh code review (CHANGELOG 2026-07-26).

## LES-138 — `ssh "$H" "bash -c $(printf %q "$SCRIPT")"` only works when the remote LOGIN shell is bash — send the snippet over `bash -s` stdin with params as argv instead

**Class:** portability hazard
**Scope:** bash helpers that ship a snippet to a remote host over ssh (`scripts/o2-pcaps-*.sh`; any
over-ssh operator tooling)
**Consumers:** code-writer, code-reviewer
**Rule:** `printf %q` on a multi-line string emits ANSI-C `$'…\n…'` quoting, which ssh hands to the
remote user's **login** shell; dash/POSIX sh (Debian's `/bin/sh`) can't parse `$'…'`, so the
round-trip silently only works for a bash-login user (e.g. root). Worse, interpolating `$CAPDIR`/
`$PATTERN` into the snippet *text* lets a path containing `$` or `"` get re-expanded/malformed by the
inner shell. Both go away by feeding the script on **stdin** to a forced `bash` and passing data as
**positional args**: `ssh "$H" bash -s -- "$dir" "$pat" <<<"$SNIPPET"` (inside, `dir=$1; pat=$2`). The
values are argv (data, never re-parsed as code) and `bash -s` is explicit regardless of the login
shell. Same snippet runs locally via `bash -s -- "$dir" "$pat" <<<"$SNIPPET"`, so one body serves
both paths.
**Origin:** o2-pcaps-{list,fetch}.sh code review (CHANGELOG 2026-07-26).

## LES-139 — `--images` is the dir CONTAINING `images/`, not `images/` itself — a wrong root silently loads ZERO bindings and every clone fails "template_vmid missing"; and resume can't fix a plan-time input error

**Class:** tool-gotcha
**Scope:** any `axrange {te,o2} deploy --images <root>` / `load_registry(root=…)` call
(`axrange/manifest/loader.py` walks `root/images/manifests/` + `root/images/bindings/<platform>/`)
**Consumers:** live-verifier, code-writer
**Rule:** `load_registry` appends `images/` to the root, so the correct `--images` value is the
directory that *contains* `images/` (e.g. `deploy-dev45`), NOT `deploy-dev45/images` (which makes it
scan `…/images/images/…` and load an EMPTY registry). An empty registry doesn't error — it just
resolves no binding, so `clone-vm` fails with the misleading `template_vmid missing — check image
binding` even though the binding file is correct and on disk. Two diagnostic tells: `load_registry`
returns `manifests: []` / `len(bindings)==0`, and the failure hits EVERY image (not just one). Second
trap layered on top: an `o2/te deploy` resume re-runs the failed op with the **plan-time inputs
persisted by the first attempt** — it does NOT re-plan — so once a deploy has failed with a bad
images root, re-running with the corrected `--images` still fails identically. You must `destroy` the
failed deploy and start a FRESH one so the plan is rebuilt with the right registry. (The default
`--images` for `o2 deploy` is not the live layout, so it must be passed explicitly on dev45.)
**Origin:** dev45 suricata-surface redeploy, CHANGELOG 2026-07-26 (external-tcpdump follow-on) —
three failed `o2 deploy`s (`template_vmid missing`) before root-causing the doubled `images/`.

## LES-140 — The tap tier is ONE PLAN PER TWIN (idempotent full-replace with de-scope), never additive — re-applying a partial plan silently tears down every binding not in it

**Class:** reversal-hazard
**Scope:** `axrange tap deploy <plan>` against an already-tapped twin (`axrange/o2control/taps.py`;
any operator adding/adjusting a sensor binding on a live twin)
**Consumers:** live-verifier, code-writer
**Rule:** A twin has exactly ONE tap deploy (`tap-<target>-<hash>`); `tap deploy` computes the FULL
desired binding set from the plan and **de-scopes (removes) every gretap/mirror not listed** — it is
not additive (LES-112/113, seen from the operator side). So "add sensor X to one segment" by applying
a plan that lists only that one binding will DROP all the twin's other sensor bindings (on dev45 this
silently removed the XPress-SW / XPress-SW-A / o2-suricata mirrors, exactly the "don't disturb XPress"
constraint). To change one binding, author the COMPLETE intended plan (all segments, all sensors,
including the unchanged ones) and apply that. Before touching a live twin's taps, capture the current
full binding set (`tap status <id>`) so you can reconstruct it; treat a tap re-apply like editing a
whole file, never appending a line.
**Origin:** dev45 corp-enterprise external-tcpdump verify, CHANGELOG 2026-07-26 — a single-binding
"additive" plan de-scoped the XPress mirrors; caught + restored from the captured `tap status`.

## LES-141 — An op that a playout composes with EMPTY desired content to REVERT a resource must treat empty as "clear," not raise — a "no inputs" guard silently breaks every narrowing re-apply

**Class:** bug-class
**Scope:** any adapter op whose desired-state input can legitimately be EMPTY as a revert signal
(`axrange/adapter_proxmox/ssh.py::_install_ovs_mirror`; any op a playout re-asserts to empty to drop
a resource)
**Consumers:** code-writer, code-reviewer
**Rule:** The tap playout (`o2control/taps.py::_compose_ops`) composes an `install-ovs-mirror[$host]`
op with EMPTY `tapped_nics` to actively REVERT a host dropped on a narrowing re-apply (segment
fan-out → boundary, in the live case). The adapter's `if not nic_gretaps: raise "no tapped NICs"`
guard — written as a fail-loud "planner wiring bug" check — could not tell that legitimate revert
apart from a genuine misconfig, so it FAILED every dropped host and left the whole tap deploy
`failed`, while the new bindings had resolved fine. When a higher layer's contract says "empty
desired state means remove/clear this resource" (the module docstring literally said "re-asserts
every affected host's install-ovs-mirror back to EMPTY tapped_nics"), the executing op MUST honor it
by performing the clear (here: write an empty no-op hook + `tc qdisc del clsact` on the VM's tap
ifaces), not reject empty as invalid input. Audit every `if not <desired>: raise` guard on an op that
participates in a re-assert/revert playout: distinguish "empty because the caller wants this cleared"
(valid, do the teardown) from "empty because a required input failed to resolve" (the mirror-wiring
case, which correctly still raises `mirror-wiring-missing` when a NON-empty `tapped_nics` names an
unresolved gre_port). The tell that this class of bug is live: a NARROWING re-apply fails on exactly
the dropped items while the added items succeed.
**Origin:** dev45 corp-enterprise `tap-xpress` re-apply, CHANGELOG 2026-07-26 — 28 dropped hosts
failed with `adapter-input: no tapped NICs` until the adapter learned to clear on empty.

## LES-142 — Changing a mirror's captured DIRECTION invalidates every mechanism that relied on a frame ARRIVING at the mirrored port — audit the verifiers, not just the capture claim

**Class:** bug-class
**Scope:** any change to which direction(s) a mirror/tap captures (`axrange/adapter_proxmox/ssh.py`
`install-ovs-mirror`; `axrange/o2control/taps.py` binding→op composition; `verify-capture` and any
other heartbeat/sentinel mechanism that reads the mirrored stream)
**Rule:** Moving a multi-port mirror from "both directions on every port" to "rx only" (the SPAN
de-dup fix, `docs/design/06-taps-sensors.md`) correctly stops duplicate frames — but it also
silently breaks any pre-existing mechanism that depended on a frame being *delivered to* the
mirrored port (the tx half now removed). The tap-scopes build's `verify-capture` heartbeat is the
concrete instance: it injected from a bystander leaf and relied on the sentinel arriving at (tx into)
the mirrored port, which stopped working the moment that port went rx-only. Whenever a capture
mechanism's direction/coverage changes, don't stop at re-verifying the capture-composition code
(`install-ovs-mirror`, the mirror render) — grep for every *consumer* that reads "a frame arrived at
this port" as a signal (heartbeats, sentinels, liveness checks) and re-derive whether their
assumption still holds. This is the flagship finding of the build: it turned a correctness
improvement into a deploy-failing regression that unit tests didn't catch (the mocked adapter never
modeled real tx/rx delivery).
**Origin:** `docs/WORK_ORDER_tap_scopes.md` build/review, CHANGELOG 2026-07-27 "Tap scopes BUILT".

## LES-143 — A node-shared bridge entering a per-`(bridge, sensor)`-keyed resource-naming function breaks an unstated twin-uniqueness invariant (LES-118's family, one scope level up)

**Class:** bug-class
**Scope:** `axrange/adapter_proxmox/naming.py` (`gre_port_for_bridge_sensor` / `gre_key_for_bridge_sensor` and any other function keyed on `(bridge, sensor)` or similar); any change that feeds a node-shared or cluster-wide bridge (`vmbr0`, any PVE default bridge) into a naming/keying function previously fed only twin-scoped bridges (`tebr_*`/`tev*`); `o2/console/src/edit/allocate.ts` (`segmentNamePrefill` over aximprint's `segment_name`); any derived default name or key prefilled into a namespace that already holds entries
**Rule:** A `(bridge, sensor)`-keyed naming function implicitly assumes `bridge` is unique per twin —
true for `tebr_*`/`tev*`, false for `vmbr0`, which every twin's router shares for WAN egress. The
moment a node-shared bridge enters that keyspace (here: `perimeter: wan` routing the WAN uplink
through the same naming function as any other tap), two DIFFERENT twins both tapping it to one
sensor collide on ONE device name, and either twin's teardown deletes the other's live capture.
Before reusing a per-`(X, Y)` naming/keying function for a new input, ask "is X unique per twin, or
shared across twins?" — a shared input needs an explicit twin-discriminator threaded into the key,
while inputs that were already twin-scoped must keep byte-identical naming (renaming an
already-deployed resource is a separate, much bigger hazard). Same family as LES-118 (a shared netdev
diffed at too coarse a dimension causes data loss) but one level up: LES-118 is about *diffing*
granularity, this is about *naming/identity* granularity.
**Extended 2026-09-15 (Console S5.1.a) — the same shape in a PREFILL, where the naming function belongs to another repo.** aximprint's `segment_name(cidr)` drops the prefix length (`10.80.20.0/24` → `seg-10-80-20-0`) — safe inside the producer, which only ever emits `/24`s, so the network address is unique there. The Console's add-segment form reused it to prefill a name for **any** CIDR, and a `/23` or `/25` at an observed network address **inherited that segment's name** — two segments of one name fail validation (STR-095) and make every join by name (conduits, router legs, the Console's own tiles) ambiguous. Restated for a derived default: **a value produced by a lossy function must be collision-checked in the namespace where it lands**, and disambiguated **only on a clash** — by the dropped component first (`-<prefix length>`), then a counter — so the conventional value survives wherever it is free. At submit, refuse a name held by a **different** network; the same network under its own name is not a collision.
**Extended 2026-09-16 (Console S5.1.b–c) — the namespace is the WHOLE document, every block of it.** The host allocator numbered a generated host from `.10` upward past every address the export carries and every `add: host` the file holds — and stopped there. An `address:` key the file **suppresses** (or overrides) is just as taken: the batch that lands on it is refused whole, and on the other side of the same coin a clone would have re-used a key an operator had deliberately removed. The rule generalizes past addresses: when you allocate into a keyspace a document shares, the "taken" set is **every key the document holds, in every block**, not the block you happen to be writing to — and the test that proves it must put the colliding key in a *different* block from the one under edit.
**Origin:** `docs/WORK_ORDER_tap_scopes.md` build/review (`vmbr0` entering
`gre_port_for_bridge_sensor` for `perimeter: wan`), CHANGELOG 2026-07-27. **Extended by Console S5.1.a fix round 1 (A4), 2026-09-15** — CHANGELOG 2026-09-15 "Console S5.1.a BUILT"; `docs/WORK_ORDER_console_canvas.md` §18. **Extended by the S5.1.c allocator, 2026-09-16** — CHANGELOG 2026-09-16 "Console S5.1.b–c BUILT"; `docs/WORK_ORDER_console_canvas.md` §19.

## LES-144 — Never re-key a safety predicate to answer a different (narrower) question, even when the two happen to coincide in every case tested so far

**Class:** bug-class
**Scope:** any predicate gating a safety-relevant decision — `axrange/planner/tappoints.py`,
`axrange/o2control/taps.py` (verify-capture injector eligibility); any refactor that substitutes one
boolean check for another because "they're the same in practice"
**Rule:** An in-flight change (reverted before the tap-scopes build, but caught again during review)
re-keyed a safety predicate meaning "is this host multi-homed" (`_is_multihomed`) to instead check
"is this host's `kind == 'router'`". The two coincide for every archetype in the test fleet — but a
dual-homed NON-router host is multi-homed without being a router, and the re-keyed predicate silently
reclassified it as a SAFE sentinel-injection point when it structurally isn't (a multi-homed non-router
host can still leak the sentinel out a different segment than the one being verified). When
refactoring a predicate for reuse or convenience, verify it against the actual invariant the call site
depends on, not against whether it happens to pass the existing test fixtures — a predicate rename
that changes semantics needs a fresh audit of every caller's assumption, not just a passing test run.
**Origin:** `docs/WORK_ORDER_tap_scopes.md` build/review, CHANGELOG 2026-07-27.

## LES-145 — A unicast sentinel needs a real, ARP-resolvable target; a broadcast sentinel does not — never document a unicast target as "need not be real or reachable"

**Class:** platform-quirk
**Scope:** `verify-capture` / any heartbeat-sentinel injection that picks a destination address
(`axrange/o2control/taps.py`, `axrange/config_exec/render.py`); any in-guest packet-injection verifier
**Rule:** Switching a sentinel's destination from broadcast to unicast (needed here to force a
directly-connected route to select one specific egress NIC under an rx-only, router-injected
heartbeat) changes a load-bearing assumption: a unicast frame requires the kernel to ARP-resolve the
destination MAC first, and if that target isn't a real, reachable host on the segment, the frame never
gets sent as intended (or gets stuck behind an ARP request). A broadcast target has no such
requirement — it needs no resolution at all. Never carry forward a broadcast-sentinel-era comment or
doc line saying the target "need not be real or reachable" once the mechanism moves to unicast; that
property was true only for broadcast, and a unicast sentinel demands a genuinely live target.
**Origin:** `docs/WORK_ORDER_tap_scopes.md` build (the direction-aware `verify-capture` fix),
CHANGELOG 2026-07-27.

## LES-146 — When topology FORCES the heartbeat injector to a specific node, prove that node can actually run the sentinel before relying on it

**Class:** bug-class
**Scope:** `verify-capture` injector selection (`axrange/o2control/taps.py`); any design where a
capture scope's construction (rx-only, router-only candidate set) forces the verification injector to
one particular node
**Rule:** Once `segment`/`perimeter` scopes are rx-only and their off-segment traffic only ever enters
via a router leg, the router itself becomes the only node that can inject a sentinel proving that leg
is captured — there is no longer a bystander leaf to use instead. Don't assume that forced injector
has the delivery channel the mechanism needs (here: QGA) just because every OTHER injector candidate
in the fleet has had it — a VyOS router has no QGA, so a design that silently assumes "the router can
always run a QGA-delivered sentinel" breaks specifically on VyOS twins. When topology forces the
injector, explicitly check (and document, if genuinely unavailable) whether that node has the required
execution channel before shipping the mechanism as unconditional.
**Origin:** `docs/WORK_ORDER_tap_scopes.md` build/review, CHANGELOG 2026-07-27.

## LES-147 — An empty `tc filter` action list is invalid syntax — omit the whole filter, never emit an action-less one

**Class:** tool-gotcha
**Scope:** `axrange/adapter_proxmox/ssh.py` (`install-ovs-mirror`'s per-direction `tc filter add`
composition); any `tc filter`/`mirred` action chain built from a list that can legitimately be empty
**Rule:** `tc filter add … action mirred egress mirror dev <gretap> [pipe …]` requires at least one
action clause; a chain composed from a gretap list that can now be empty (e.g. the "both"-direction
gretap set on a port narrowed to rx-only, M5) must OMIT the entire `tc filter add` for that direction
when the list is empty — emitting `tc filter add … action` with nothing after `action` is a shell/tc
SYNTAX ERROR, not a harmless no-op filter. This is the `tc`-composition-layer analog of LES-141 (an
op must treat empty desired state as "clear," not raise) — but one layer lower, inside a single op's
own command construction rather than at the op's `inputs` contract.
**Origin:** `docs/WORK_ORDER_tap_scopes.md` build (M5, "omit the filter entirely when none"),
CHANGELOG 2026-07-27.

## LES-148 — A "record instead of drop" change must audit DERIVED key domains, not just the direct field consumers it was written for

**Class:** bug-class
**Scope:** `axrange/planner/inventory.py` (`HostNet.external` / the `__wan__` uplink recording); any
change that stops silently dropping a value and starts recording it into a shared collection that
other code already iterates (backfills, derived sets, `all_net_ids`-style aggregates)
**Rule:** When a "drop X" behavior becomes "record X, marked," every piece of code that iterates the
containing collection inherits X as a new member — including code that has nothing to do with the
change and was written long before X existed. Concretely: recording the router's WAN uplink into
`hosts[].nets[]` (closing GAP-029's WAN half) meant a synthetic/external net silently ALSO showed up
in `all_net_ids`, which an unrelated backfill three branches away iterated to assign each net a bridge
— and that backfill, never designed with an external net in mind, assigned it the twin's VLAN
underlay bridge by falling through its default case. Fixed by making `all_net_ids` (and every other
derived set built by iterating `hosts[].nets[]`) explicitly filter or account for `external`. Whenever
a "stop dropping, start recording" change lands, grep for every DERIVED collection/backfill built by
iterating the collection being changed, not just the direct consumers the change was written for.
**Origin:** `docs/WORK_ORDER_tap_scopes.md` build/review (M2, `HostNet.external`), CHANGELOG
2026-07-27.

## LES-149 — A default-on formatting/hint decoration in a render helper SHARED between a CLI display path and a DTO/`--json` field silently leaks into the machine-readable field (extends LES-037)

**Class:** bug-class
**Scope:** `axrange/cli/main.py` render helpers shared with `axrange/api/{dtos,service}.py` or any
`--json`-capable output; any helper reused for both a human-readable string and a structured field
**Rule:** LES-037 established that CLI and API must share ONE plan-build/deploy helper rather than
duplicating logic — this build surfaced the flip side of that reuse discipline. A shared render
helper that adds a "helpful" default decoration (a direction annotation, an `(external)` suffix, any
static hint) for human-readable display can leak that decoration into a value a caller treats as
machine-readable — the `--json`/DTO path — if the helper doesn't clearly separate "format for display"
from "return the raw value." When a render helper serves both a CLI text path and a `--json`/DTO
field, keep decoration strictly on the display branch and pass the underlying value through unmodified
on the structured branch; verify with an explicit `--json` test, not just a human read of the CLI
output.
**Origin:** `docs/WORK_ORDER_tap_scopes.md` build/review (M6, direction rendering vs. `--json`
output), CHANGELOG 2026-07-27.

## LES-150 — A vocabulary/selector's resolution can silently widen when an UPSTREAM change enlarges the collection it iterates, with no code change of its own

**Class:** bug-class
**Scope:** `axrange/o2control/taps.py` (the legacy `tap: <host>` selector, resolved as "every net in
`hosts[].nets[]` for that host"); any "for X in `<collection>`" resolution rule whose input
collection's membership is controlled elsewhere
**Rule:** A rule defined as "every X in `<collection>`" is only as stable as `<collection>`'s
membership, which can be changed by code that has nothing to do with that rule. Recording the WAN
uplink into `hosts[].nets[]` (M2) meant the legacy `tap: <host>` selector — unchanged itself — started
resolving to include the router's WAN leg, which it had never covered before and was never intended
to widen when M2 was written. Whenever a change enlarges a shared collection's membership, explicitly
re-audit every OTHER feature/selector that iterates it "for free" rather than assuming an unrelated
rule is safe because its own code didn't change.
**Origin:** `docs/WORK_ORDER_tap_scopes.md` build/review (M2, legacy `tap: <host>` selector),
CHANGELOG 2026-07-27.

## LES-151 — When reconciling two overlapping sources of design prose, resolve to the NEWEST one in a supersession chain, not the one nominally owning the topic

**Class:** dead-end / process gotcha
**Scope:** `docs/design/*.md` (the design-of-record set); any future pass that reconciles a
`docs/design/*.md` file against another source describing the same topic (a `CHANGELOG` entry, a
retired secondary doc set, another `docs/design/*.md` file)
**Rule:** A numbered `docs/design/*.md` file that already looks current-tense and complete can
still be stale relative to a same-day sibling source that superseded the material it was
originally written from. Concretely, during the 2026-07-27 consolidation of a second, now-retired
design-doc set into `docs/design/`: `docs/design/06-taps-sensors.md` carried an older tap-scope
vocabulary (`segment`/`boundary`/`host`/`leg`) straight from one now-retired source, even though a
same-day sibling source had already superseded that vocabulary (`port`/`segment`/`perimeter`/
`boundary` with a derived capture direction) and the code was already built against the new one.
Before treating a `docs/design/*.md` file as "already covers this," check every source touching
the same topic for "Supersede"/"SUPERSEDED"/"supersedes-in-direction" pointers, and always resolve
to the end of the chain, never the source that happens to share a topic with the target file.
**Origin:** docs consolidation pass (parallel agent A/3), CHANGELOG 2026-07-27 "Docs
consolidation: fold twin-model/networking-capture/taps-sensors briefs into `docs/design/`"; the
sources named above (a design-brief file set) no longer exist as of the same day's later
consolidation pass — see `docs/design/README.md`'s retired-brief mapping table.

## LES-152 — Split mechanism vs. vocabulary content across cross-linked docs rather than duplicating the rationale in both

**Class:** pattern
**Scope:** `docs/design/*.md` files that declare an explicit split of concerns (e.g.
`04-networking-capture.md`'s "this document describes only the underlying mechanism" vs.
`06-taps-sensors.md`'s tap-point vocabulary); any pair of docs where the same underlying fact is
relevant to both a "how it works" doc and a "what it means / how you address it" doc
**Rule:** When one fact spans a mechanism doc and a vocabulary/model doc — here, "a `tc` mirror
can be installed rx-only or both directions" (mechanism) and "which scope gets which direction"
(vocabulary/resolution table) — state the mechanism-level cause once, briefly, in the mechanism
doc with a one-line pointer to the vocabulary doc for the resolution table, and put the full
table + rationale only in the vocabulary doc. Avoids re-deriving the same rationale twice (and
the two copies drifting out of sync on a later edit) while keeping each doc self-contained for
its own stated scope.
**Origin:** docs consolidation pass (parallel agent A/3), CHANGELOG 2026-07-27 "Docs
consolidation: fold twin-model/networking-capture/taps-sensors briefs into `docs/design/`".

## LES-153 — When retiring a doc-file class, grep the codebase's own source comments/docstrings too, not just `*.md` — and treat the caller's verify command as the real acceptance bar even where it exceeds the written task list

**Class:** verification idiom
**Scope:** any future doc-genre retirement, rename, or path change (a `docs/DESIGN_BRIEF_*`-class
sweep, a `docs/SLICE_*`/`docs/WORK_ORDER_*` rename, any `docs/*.md` move); the final `grep`
acceptance check a task specifies
**Rule:** The final cleanup pass of the 2026-07-27 design-brief retirement was scoped (by its own
numbered task list) to `docs/design/`, `CLAUDE.md`, `docs/CONVENTIONS.md`, and a named list of
`docs/*.md` files — but its own verification command was `grep -r "DESIGN_BRIEF_" --include=*.md
--include=*.py .`, which turned up roughly 280 additional hits inside `axrange/*.py` and
`tests/*.py` docstrings/comments citing the retired brief filenames by path (e.g. `axrange/
planner/inventory.py`'s module docstring citing `docs/DESIGN_BRIEF_twin_model.md` §4). These were
left untouched (flagged as out-of-scope for a docs-only pass, `docs/CHANGELOG.md` 2026-07-27) —
but the discovery itself is the lesson: **a task's own final verify command is the actual
acceptance bar, even when it's broader than the numbered checklist that precedes it.** Before
declaring a doc-file retirement complete, run the exact grep the task specifies (not a narrower
one scoped to the files you already know you touched) — `--include=*.py` (and any other source
extension in the repo) can and does surface doc-citation drift a `*.md`-only sweep misses
entirely, because docstrings routinely cite design docs by path as provenance.
**Origin:** docs consolidation final pass, CHANGELOG 2026-07-27 "Docs consolidation: fold
twin-model/networking-capture/taps-sensors briefs into `docs/design/`, retire all 19 design
briefs, repoint inbound references".

## LES-154 — A `git worktree` under the repo root is invisible to `git status`/`git grep` scoping conventions but NOT to a plain filesystem `grep -r`; exclude it explicitly in any repo-wide sweep

**Class:** tool-gotcha
**Scope:** any repo-wide `grep -r`/`find` sweep run from the repo root when a `.claude/worktrees/`
(or any other) linked git worktree is present; doc-retirement/rename sweeps specifically
**Rule:** `.claude/worktrees/o2-build` is a real, separate git worktree (`git worktree list` shows
it checked out on its own branch, `worktree-o2-build`) that is excluded from the MAIN worktree's
own tracked-file listing via `.git/info/exclude`, but that exclusion only affects `git`-aware
tooling (`git status`, `git grep`, `git add`) — a plain `grep -r "pattern" .` still walks into it
and returns dozens of hits from a frozen, pre-retirement snapshot of the same files being cleaned
up in the current branch. Before running a repo-wide sweep's final grep to confirm zero remaining
hits, exclude every linked worktree directory explicitly (`grep -v '^\./\.claude/worktrees'` or
equivalent) — otherwise a genuinely-clean current-branch state will look dirty, and worse, an
agent under pressure to "fix every hit" could be tempted to edit files on a DIFFERENT branch's
checkout by mistake.
**Origin:** docs consolidation final pass, CHANGELOG 2026-07-27 "Docs consolidation: fold
twin-model/networking-capture/taps-sensors briefs into `docs/design/`, retire all 19 design
briefs, repoint inbound references".

## LES-155 — A work order's own in-file claim to be a "permanent grep carve-out" is not authoritative over a LATER task's explicit acceptance criteria

**Class:** process gotcha
**Scope:** any `docs/WORK_ORDER_*.md` file (or similar living document) that declares itself
exempt from some future sweep/grep gate; any later consolidation/retirement task whose own
verify command doesn't recognize that self-declared exemption
**Rule:** `docs/WORK_ORDER_doc_audit.md` (a 2026-07-10 work order) contained its own genre-rule
table entry stating work orders are "Permanent grep carve-outs (they name retired terms by
design)" — but that self-declaration was written for THAT order's own 2026-07-10 sweep, not for
the 2026-07-27 design-brief retirement, whose acceptance grep explicitly required zero
`DESIGN_BRIEF_` hits outside `docs/CHANGELOG.md`, DONE-marked work orders, and the README mapping
table. `WORK_ORDER_doc_audit.md` itself carried no top-level `DONE` marker, so per that later
task's own rule it needed fixing despite its internal "leave me alone" text. When a later task's
explicit acceptance criteria conflict with an in-file self-exemption written by an earlier task,
the LATER task's criteria win — a stale self-declared carve-out doesn't survive changed
requirements just because it predates them.
**Origin:** docs consolidation final pass, CHANGELOG 2026-07-27 "Docs consolidation: fold
twin-model/networking-capture/taps-sensors briefs into `docs/design/`, retire all 19 design
briefs, repoint inbound references".

## LES-156 — A "check-then-act" adopt turns a name collision into cross-owner DELETION: idempotent-create must prove OWNERSHIP, not just existence

**Class:** bug-class / silent-success-trap
**Scope:** `axrange/adapter_proxmox/rest.py` `op_create_vnet` (and every Model-C
`if self._X_exists(name): return {...}` adopt); `axrange/adapter_proxmox/naming.py`;
`axrange/planner/teardown.py` (outputs-driven deletion)
**Consumers:** code-writer, code-reviewer, tester, live-verifier

**Rule:** Model C's "check-then-act" makes create idempotent by returning success when the resource
already exists. That is correct only if the name is **unique to this owner**. If the name is not, the
adopt is silent theft: deploy B records deploy A's resource in its own op outputs, and because
teardown deletes from recorded outputs, **B's destroy deletes A's live resource** — reporting
`0 not cleared` while doing it, because by its own records it was right.

Two independent things must hold, and neither is sufficient alone:
1. the name must carry an owner discriminator (see LES-143), and
2. an idempotent create that adopts an existing resource should verify it actually belongs to this
   deploy before claiming it — existence is not ownership.

**How it bit:** `vnet_id_for_net` hashed only `net_id` while the zone id computed on the *very next
line* was plan-scoped. Two twins sharing a network name collided; destroying a throwaway twin
deleted the standing twin's vnet and took four VMs (including its router) off the network. The
tier-tag blast-radius guard was no help — both twins were `te`, so this was *same-tier* collateral
damage, a case no protect matrix was built to catch.

**Test that would have caught it, and was missing:** every naming test asserted one plan at a time.
Assert the cross-owner property directly — *two different plan ids, same net id, different names* —
because "names are unique per owner" is the invariant, and an invariant nothing asserts is one you
merely hope for.

**Origin:** dev45 live incident 2026-07-27 (`docs/PRODUCT_GAPS.md` GAP-043) — caused while tearing
down a failed live-verify twin next to a standing 32-host twin.

**Recurrence (2026-09-30):** the Unified Console's multi-store targets (fix round 2) made
`POST /api/deploys` search only the PRIMARY store before minting a row. With a standing twin
recorded in an extra store of the same cluster (`svc-engines-24131435` in
`/home/rocky/axr-svcengines.state.db`), a fresh `{config: "svc-engines"}` would have minted a second
row with the SAME `plan_id`; the deterministic `vmid_for_op`/`vm_name_for_host` formulas then adopt
the standing VMs as its own, protection never runs (it looks in the wrong store), and a later
`DELETE` of the new row destroys the standing twin. Caught in review before any live write. Fixed by
searching every store of the target for a non-destroyed deploy of that scenario/plan and running the
request in the store that holds it (`api/service.py` `_route_to_owning_store`; two stores → 409).
Same lesson, new door: "does this already exist?" must be asked of EVERY record that can own the
name, not the one the code happened to open.

## LES-157 — `INSERT OR IGNORE` for resume-safety silently freezes op INPUTS at first apply; any verb that reads them back reports the first deploy, not the current one

**Class:** silent-success-trap / bug-class
**Scope:** `axrange/planner/state.py` (op-row persistence), `axrange/planner/executor.py`; any
read-back verb sourcing from `inputs_resolved_json` (`axrange/o2control/taps.py`
`_bindings_summary_from_ops`, and the `tfc`/`exr` playouts' equivalents)
**Consumers:** code-writer, code-reviewer, tester, live-verifier

**Rule:** `INSERT OR IGNORE` on op rows is right for what it was chosen for — a resume must not
clobber op status/timestamps. But it also means **inputs are written once and never updated**, so a
re-applied deploy executes from the fresh in-memory plan while the database keeps the *first* apply's
inputs indefinitely. The wire is correct and the record is wrong, which is the worst combination:
every read-back verb confidently reports state that has not been true for days.

Compounding it, a well-intentioned safe default makes the staleness invisible. Reading a
field that predates a schema addition and defaulting it (`direction` absent ⇒ `"both"`, LES-096)
is correct for genuinely-old data — but applied to a **stale** row it manufactures a confident wrong
answer instead of an obviously-missing one. A safe default over stale data is worse than no data.

Two consequences to design against:
1. Persist inputs **when an op actually executes**, not when the plan is inserted. A blanket upsert
   is equally wrong in the other direction: it would claim new inputs are live for an op that a
   resume legitimately skipped.
2. When two verbs disagree, **prove whether they share a source before theorizing about freshness**.
   Both `tap status` and `tap info` read the *same* persisted rows here; `tap info` was **equally
   stale** and merely scattered the defect across itemized rows instead of collapsing it into one
   confident aggregate token. Two successive wrong explanations ("info re-resolves", then "info
   isolates the fresh row") were believed before anyone ran both verbs against a pre-fix tree. Grep
   to the column; do not infer from a verb's name or its richer output. See LES-158.

**Test that would have caught it, and was missing:** every test builds a deploy once. Assert the
re-apply path directly — apply, change the plan, re-apply, then read back and compare against the
new plan. For the tap tier especially, re-apply *is* the normal operating mode, not an edge case.

**Origin:** dev45 live incident 2026-07-27 (`docs/PRODUCT_GAPS.md` GAP-044) — found by cross-checking
`tap status` against `tap info` and `tc filter show` after the tap-scopes rx-only re-apply.

## LES-158 — An aggregating read-back verb collapses a stale minority into a confident token; when two verbs disagree, prove they don't share a source BEFORE theorizing about freshness

**Class:** silent-success-trap / verification-idiom
**Scope:** any pair of read-only verbs over the same persisted op rows where one aggregates and one
itemizes — `axrange/o2control/taps.py`'s `_bindings_summary_from_ops` (net-level union) vs.
`tap_info_from_deploy` (per-`(host, net, nic)`); `o2control/traffic.py`'s
`_assignments_summary_from_ops` vs. its info peer; any future `status`/`info` verb pair
**Consumers:** code-reviewer, live-verifier, tester

**Rule:** when a `status` verb and an `info` verb disagree, the first question is **not** "which one
is live?" — it is **"do they read the same source?"** Grep both to the column. Do not infer from the
verb's name, its richer output, or how current it *feels*.

In the GAP-044 incident both read the identical `install-ovs-mirror.inputs_resolved_json`. **Two
successive explanations were believed and both were wrong** — first "`tap info` re-resolves, `tap
status` is cached", then "`tap info` isolates whichever row is fresh" — before anyone simply ran both
verbs against a pre-fix tree:

```
STATUS: corp -> both+rx          <- the live symptom
INFO:   h-a    corp 0 -> both    <- ALSO STALE
INFO:   r-edge corp 0 -> rx      <- fresh
```

`tap info` was equally stale. What made it *look* correct is **aggregation**. A union over a set
(`"+".join(...)` across every host on a net) turns one stale member into a single confident-looking
token (`both+rx`) that reads as a coverage **claim**; the itemized peer scatters the same defect
across rows, where a stale value reads as *a different binding* rather than an error — and the fresh
row gets mistaken for confirmation.

**An aggregate is never the place to notice staleness, and the fact that it renders one clean value
is not evidence its inputs agree.** Reviewer corollary: when a verb unions a per-entity field, ask
what a single stale or failed entity does to the output token. If the answer is "widens it", the verb
over-reports by construction.

**Test that would have caught it, and was missing:** assert the two verbs **against each other** on
the same deploy after a re-apply that changes one entity — not each against its own expected literal.

**Origin:** dev45 live incident 2026-07-27 (`docs/PRODUCT_GAPS.md` GAP-044); diagnosis corrected
twice during code review of the fix.

## LES-159 — An op row carries DESIRED inputs from plan-insert onward, whether or not they were ever applied; refreshing them at attempt-start makes a consumer status filter mandatory, not optional

**Class:** bug-class / silent-success-trap
**Scope:** `axrange/planner/executor.py` `_run_one`'s `set_op_running` inputs refresh;
`axrange/planner/state.py` `set_op_running`; every `planner/opview.py` `op_fields` consumer that
presents "what is currently live" — `o2control/{taps,traffic,exercise,control}.py`,
`planner/{access,inventory}.py`
**Consumers:** code-writer, code-reviewer, tester

**Rule:** `inputs_resolved_json` is written at plan-insert with **desired** inputs and (since GAP-044)
refreshed again at attempt-start. **Neither write point means "applied."** Worse, `_run_one` stamps
the refresh **before** lock acquisition, so an op that fails `correctness_lock_keys` resolution or
times out acquiring a lock records new inputs with `attempts=0` — the adapter was never called.

Any surface rendering these rows as live state must therefore filter positively to
`status == COMPLETED` — never "not destroyed", which is LES-113, whose scope this extends from
`traffic.py` to every playout.

Attempt-start is still the **right** write point: `op_attempts` carries no inputs column, so the op
row is the only forensic record of what an attempt used, and moving the write to on-success would
make a partially-applied failed op report the *previous* apply's inputs as though current (and
`install-ovs-mirror` regenerates its whole hook script, so "keep the last successful inputs" is not
more truthful there — just differently wrong). **Pick attempt-start AND the consumer filter; neither
alone is correct.**

**Origin:** code review of the GAP-044 fix, 2026-07-27 — `traffic.py` had already adopted the filter
under LES-113; `taps.py` had not, and the inputs refresh made the omission newly reachable.

## LES-160 — A deliberately UNCONDITIONAL branch is a reversal hazard: if the only record of "why not gated" is a docstring, the next refactor gates it and every test stays green

**Class:** reversal hazard / verification-idiom
**Scope:** any review-driven decision of the form "do X for EVERY item, including the items where the
adjacent action is skipped" — `axrange/o2control/taps.py`'s `_delete_dropped_gretaps` (mark every
dropped pair's op `destroyed` even where its gretap DEVICE is kept under VLAN-zone aliasing);
`axrange/o2control/traffic.py`'s `_stop_dropped_instances`; any future de-scope path separating "is
this binding still desired" from "is the shared resource still needed"
**Consumers:** code-reviewer, tester, code-writer

**Rule:** when a review concludes an action must be unconditional *precisely where it looks like it
should be conditional*, the conditional variant is the shape a future "cleanup" will reintroduce — it
reads as the safer, tighter code. A docstring paragraph explaining the distinction does not survive
that refactor. A test does.

Add a test whose **only** failing condition is the gated variant. Here: seed a real deploy and real
op rows, then assert `status == DESTROYED` for every dropped pair **while** `teardown.calls == []`.

**Reviewer corollary — this is the transferable part:** to check whether a decision is actually
pinned, *apply the plausible wrong variant and run the suite*. If it stays green, the decision lives
only in prose. That check found this gap: moving `mark_op_destroyed` into the device-delete success
branch passed **all 131 tap tests** while silently reintroducing GAP-045 for the exact VLAN-zone
aliasing configuration that produced the LES-118 data-loss bug.

**How the gap hid:** the existing unit callers passed a bare `StateStore` with no deploy and no op
rows, so `mark_op_destroyed` was a 0-row UPDATE that nothing observed. A call that cannot fail is not
coverage. When a test constructs a store/DB purely to satisfy a signature, treat every assertion
about writes through it as vacuous until proven otherwise.

**Origin:** code review of the GAP-045 fix, 2026-07-27.

## LES-161 — A version-locked recognized-value-set widening must update the ENFORCEMENT gate in the SAME change as the doc/docstring; add a positive test for the new value

**Class:** bug-class
**Scope:** `axrange/validator/checks/structural.py` (any `STR-020`-style "is this declared version
in the recognized set" check); any version-locked schema/enum/closed-set widening —
`scenario_schema_version`, an op-registry closed op-name set, a validator closed binding-kind set
**Consumers:** code-writer, code-reviewer, tester

**Rule:** Widening a recognized-value set (a schema version, an enum, a closed kind set) is not
done once documentation says the new value is accepted — it is done when the code path that
actually REJECTS out-of-set values is updated in the SAME change. `docs/IR_SCHEMA.md`'s prose and
the Pydantic model's own docstring can both correctly say "`{1, 2, 3}` all validate" while the
validator's `STR-020` check still only recognizes `{1, 2}`, silently failing a real `scenario_
schema_version: 3` IR the docs just promised would pass. The doc + docstring are not the contract;
the rule that enforces the boundary is. Add a POSITIVE test asserting the NEW value validates
cleanly — a suite that only re-confirms old values still pass can stay green while the new value
silently fails, because nothing exercises the boundary case that actually moved.

**Origin:** `docs/WORK_ORDER_tap_eligibility.md` build, code-review round 1 (the single must-fix),
2026-07-28 — CHANGELOG "The twin substrate becomes tap-agnostic."

## LES-162 — Removing an IR field means sweeping every piece of code that GENERATES that field into YAML, not just checked-in content

**Class:** bug-class
**Scope:** any code that programmatically renders Scenario-IR YAML as text rather than through the
IR's own Pydantic model — `axrange/imagebuild/conformance.py`'s `build_smoke_scenario_yaml`
(f-string-templated), and by extension any future surface composer, demo emitter, or
test-fixture builder that does the same
**Consumers:** code-writer, code-reviewer

**Rule:** A field-removal content-migration pass naturally greps `target-environments/` and
`twin-configs/` for the literal key being removed — that's the checked-in content everyone
remembers to sweep. A string-templated YAML *generator* living in the engine's own source tree
(here, `build_smoke_scenario_yaml` literally emitted `"        tap: false\n"` as part of an
f-string) is invisible to that content grep; you have to grep the `axrange/` source tree itself for
the field name too. Once the removed field grows a hard-presence-rejection rule (`STR-200`-style —
reject the field's mere presence, any value), a missed generator line stops being a harmless no-op
and becomes a hard failure of its own consumer: `axrange image build` would have failed its own
smoke-test scenario on the very next run.

**Origin:** `docs/WORK_ORDER_tap_eligibility.md` build, code review, 2026-07-28 —
`axrange/imagebuild/conformance.py`'s stray `tap: false` line, caught before merge.

## LES-163 — Retiring an op (or the IR field that triggered it) must scrub `redo_on` keys and `description` prose on SIBLING ops too, not just delete the retired op's own catalog block

**Class:** bug-class
**Scope:** `axrange/ops/registry.py` (any op's `redo_on` list or prose `description` that names
another op or an IR field path belonging to it); `docs/OP_CATALOG.md`'s per-op signature blocks
**Consumers:** code-writer, code-reviewer

**Rule:** An op-catalog removal is not complete when the retired op's own YAML block is deleted —
every SIBLING op that references the retired op or its trigger field, by name or by `redo_on` path,
needs the same pass. Two concrete misses this surfaced: `install-ovs-mirror`'s `redo_on` still
carried `("nodes[*].tap", "nodes[*].nets", "networks[*].tap")` — IR field paths that no longer
exist once `tap:` is removed from the schema — and `attach-gre-tunnel`'s `description` named
"record-tap-intent, OP-MIR-005" as the origin of its `sensor_id` input, after that op was deleted.
Neither miss errors or fails a test on its own: a `redo_on` path pointing at a nonexistent IR field
is simply inert (never matches, never redoes), and stale prose in a `description` string is
never asserted against. Both are silent, not loud — which is exactly why the sweep has to be
deliberate rather than discovered by a failing test.

**Origin:** `docs/WORK_ORDER_tap_eligibility.md` build, `axrange/ops/registry.py`, 2026-07-28.

## LES-164 — A targeted field-removal pass must verify surrounding docstrings against CURRENT source, not just the lines the removal itself touches — prose can already be stale before your change, in an unrelated module

**Class:** verification-idiom
**Scope:** any docstring/design-digest prose anywhere in `axrange/` that names a mechanism by
identifier (an op name, an IR field) — grep the WHOLE repo for that identifier during a removal
pass, not just the files your ticket's field lives in
**Consumers:** code-writer, code-reviewer, doc-writer

**Rule:** A module's docstring can carry a "why we built it this way" note that references a
mechanism from a completely different, earlier change — and that note can already be stale by the
time an unrelated later change removes the mechanism it names. `axrange/planner/inventory.py`'s
`hosts[].role` docstring carried: *"An EARLIER revision hung this on `record-tap-intent` (a
TAP-gated op) additively; reverted for exactly the coupling reason above."* — a pre-existing note
about `role`'s source-of-facts design (unrelated to tap eligibility) that happened to name
`record-tap-intent` by identifier. Once that op was removed, the note became doubly confusing: a
historical aside about a mechanism that no longer exists, sitting in a module nobody was
otherwise touching for this ticket's stated purpose. It was caught and reworded (*"An EARLIER
revision hung this on a now-retired tap-gated op instead"*) only because the sweep grepped the
whole repository for the identifier being retired, not just the fields/files the change's own
scope named. Treat "does anything ELSE in the repo mention this by name" as a mandatory search
step on any removal, independent of which modules the removal's own diff touches.

**Origin:** `docs/WORK_ORDER_tap_eligibility.md` build, `axrange/planner/inventory.py`, 2026-07-28.

## LES-165 — A fixture carrying a removed field goes silently inert under Pydantic `extra="allow"`; re-derive the fixture's STATED PREMISE, don't rely on the suite staying green

**Class:** silent-success-trap
**Scope:** `tests/fixtures/ir/**/*.yaml` (any fixture whose header comment/description narrates
WHY it's shaped the way it is); any IR field removal, given the IR model's
`model_config = ConfigDict(extra="allow", ...)` (`axrange/ir/schema.py`)
**Consumers:** code-writer, code-reviewer, tester

**Rule:** Because the IR's Pydantic model is `extra="allow"`, a fixture that still carries a
just-removed field (e.g. a stray `tap: true`) does not error on parse — the field is silently
accepted as an unknown extra key and ignored. Only a dedicated presence-rejecting validator rule
(`STR-200`) catches it, and only if that fixture is actually run through the validator, not just
the parser. Worse than the stray field itself: `tests/fixtures/ir/valid_activity/scenario.yaml`
and `tests/fixtures/ir/valid_slice/scenario.yaml` both had header comments whose STATED PREMISE
described the fixture as exercising tapped/capture behavior ("Two tapped Linux hosts…", "…so
capture exists and start-activity gates on mirror-ready") — prose that stayed readable and
plausible even after the `tap: true` flags it described were removed, because nothing forces a
fixture's English description to agree with its YAML body. A fixture whose stated premise has
quietly stopped matching what it exercises is not caught by the suite staying green; it has to be
re-derived by a human reading the premise against current behavior, fixture by fixture.

**Origin:** `docs/WORK_ORDER_tap_eligibility.md` build, `tests/fixtures/ir/{valid_activity,
valid_slice}/scenario.yaml`, 2026-07-28.

## LES-166 — `git checkout -- <file>` without stashing first destroys that file's uncommitted work; stash before any discard-style git recovery during a pipeline run

**Class:** tool-gotcha
**Scope:** any subagent (tester, code-reviewer) recovering a working tree mid-pipeline-run with a
large uncommitted change already present — `git checkout --`/`git restore`/`git reset --hard`
against a file that has BOTH the in-flight change and pre-existing uncommitted edits from earlier
in the same session
**Consumers:** tester, code-reviewer, code-writer

**Rule:** During a pipeline run with a large uncommitted change already sitting in the tree
(the normal state mid-work-order, since nothing is committed until the doc-writer's stage
completes), `git checkout -- <file>` or `git restore <file>` does not selectively undo the mistake
you're trying to fix — it discards ALL uncommitted changes to that file, including the target
work-order's own edits that had nothing to do with the problem being corrected. `git stash push
-- <file>` (or a full `git stash`) first, confirm the recovery actually fixes what you intended,
then decide whether to drop or pop the stash — never reach for a bare discard command against a
file you know carries uncommitted, un-backed-up work. This project's git policy already permits
`git stash`; it does not need to be a destructive `checkout`/`restore` in the first place.

**Origin:** `docs/WORK_ORDER_tap_eligibility.md` build — a tester recovered a file via
`git checkout -- <file>` without stashing, destroying in-flight uncommitted work that had to be
hand-reconstructed; 2026-07-28.

## LES-167 — A predecessor pattern referencing a scope var the barrier's own scope doesn't carry silently resolves to ZERO edges — no warning, no error, since the founding commit

**Class:** silent-success-trap
**Scope:** `axrange/ops/registry.py` (any op/barrier's `predecessors=(...)` pattern),
`axrange/planner/pass3_ordering.py::_resolve_pattern`
**Consumers:** code-writer, code-reviewer, tester

**Rule:** When authoring or reviewing an op or barrier's `predecessors` pattern, verify every scope
var it references (`$scenario`, `$net-id`, etc.) is one the declaring op's OWN `scope=` actually
carries. `_resolve_pattern` silently drops any pattern referencing an absent var, producing zero DAG
edges with no signal at plan-build or at runtime — a completed deploy proves nothing here.
`OP-BAR-010 network-ready` (`scope="net-id"`) declared `predecessors=("apply-sdn-config[$scenario]",)`
since the original op-registry commit (`47c86b0`) and had never once actually gated on it; the gap
was caught only by explicit in-process DAG inspection (`plan.predecessors(...)`/
`plan.descendants(...)`) during a live-verify, not by the pytest suite, because nothing in the suite
asserts a barrier's expected in-edges are non-empty. Add a fail-loud check (warn or raise) in
`_resolve_pattern` when a declared predecessor resolves to zero ops, and/or a DAG-shape test per
barrier.

**Origin:** `docs/PRODUCT_GAPS.md` GAP-047, `docs/WORK_ORDER_tap_eligibility.md` gated `dev45`
live-verify, 2026-07-28.

## LES-168 — An op's trailing housekeeping pass must be scoped to THIS deploy's own resources, not every resource on the node — an unscoped scan's cost grows with unrelated lab occupancy

**Class:** perf-cost
**Scope:** `axrange/adapter_proxmox/rest.py` (`op_apply_sdn_config` and any op with a trailing SSH
housekeeping/re-assert step), any op that mixes "do the real work" with "re-verify everything else
on the node"
**Consumers:** code-writer, code-reviewer, live-verifier

**Rule:** An op's trailing housekeeping step (e.g. re-asserting tap ports on every `tebr_*` bridge
for every RUNNING VM on the node) must be scoped to the resources THIS deploy touched, not the whole
node. An unscoped scan's cost is O(bridges × all-running-VMs), which grows silently as a standing
lab (the O2 surface, other twins) accumulates VMs, and a hardcoded fixed timeout
(`run_ssh(..., timeout_s=60)`) will eventually fail a deploy that has nothing wrong with it. This is
load-driven, not transient — retries don't help. Caught live on dev45 with 41 running VMs (32 from a
standing `corp-enterprise` twin): `apply-sdn-config`'s real SDN work (`PUT /cluster/sdn`) succeeded
both times; only the unscoped node-wide scan timed out.

**Origin:** `docs/PRODUCT_GAPS.md` GAP-046, `docs/WORK_ORDER_tap_eligibility.md` gated `dev45`
live-verify, 2026-07-28.


## LES-169 — A "v1 shape-only" driver deferral can silently cap the whole product surface it feeds; check what the deferral actually bounds before authoring content on top of it

**Class:** design-deferral
**Scope:** `axrange/personas/drivers/scripted.py`, any driver/renderer documented as a deliberate
v1 simplification that downstream content is authored against
**Consumers:** code-writer, code-reviewer, doc-writer

**Rule:** When a driver is documented as a deliberate v1 simplification ("a generic,
protocol-agnostic connectivity-shape emitter... layering real per-protocol clients on top is the
Traffic tier's job"), work out the *numeric* ceiling that deferral imposes before authoring catalog
content against it. Here the scripted persona driver's ceiling was ~5 tiny TCP connections/second
per persona INSTANCE — and the binding constraint was not the documented one. Three separate
constants combined: `_VOLUME_MESSAGES` topped out at 24, `fire()` hardcoded a 0.2 s inter-message
gap, and the client looped over all emits **single-threaded**, so the gap bounded the whole persona
rather than one emit. Consequently `volume:` controlled connection COUNT only and never byte size,
making "a 2 GB nightly backup" and "a DNS query" literally the same traffic — a limit invisible in
the catalog YAML, in the PSN rules, and in every render-assertion unit test, all of which happily
described traffic the driver could not produce. The general form: a shape-only renderer under a
declarative schema lets authors express intent the mechanism cannot honor, and nothing in the
schema, the validator, or render-level tests will contradict them. Two defenses: (1) put at least
one **functional** test behind such a driver (render it, run it against a local sink, count bytes
and packets) rather than only asserting on rendered text — that is what quantified this ceiling;
(2) when a knob is named for a physical quantity (`volume`), make it actually resolve that quantity,
or rename it.

**Origin:** CHANGELOG 2026-07-28 (persona volume lever + UDP/broadcast/fan-out transports).


## LES-170 — Fan-out peer resolution must exclude the instance's own host, or a mesh persona talks to itself and produces uncapturable traffic

**Class:** correctness
**Scope:** `axrange/o2control/traffic.py` (`_resolve_peer_sets`), any role-based fan-out that
resolves "every host with role X" for an instance that itself holds role X
**Consumers:** code-writer, code-reviewer

**Rule:** When resolving a role to the FULL set of hosts carrying it (rather than one round-robin
peer), exclude the requesting instance's own host. In a mesh persona every host shares the role, so
an unfiltered fan-out has each node send to itself — traffic that loops back through the local
stack, never crosses a bridge, and therefore never reaches a tap point or a mirror. It inflates
every message/byte counter in the guest log while producing **zero** capturable frames, which is
the worst failure shape: it looks like success at exactly the layer an operator checks first. The
single-peer path (`_resolve_peers`, round-robin by instance index) has the same latent hazard for a
self-referential role and is worth knowing about, but only the fan-out path can hit it on every
instance at once.

**Origin:** CHANGELOG 2026-07-28 (persona volume lever — `p2p-update-peer`, `voip-softphone`).

## LES-171 — A destroy that cascades into dependent tiers must run those cascades BEFORE its own sweep; best-effort cascade steps turn a wrong order into a green result under a wall of warnings

**Class:** correctness (ordering) + observability
**Scope:** `axrange/o2control/cascade.py`, `axrange/cli/main.py` (`cmd_destroy`),
`axrange/api/service.py` (`destroy_deploy`); any teardown that cascades into deploys whose cleanup
reaches INTO the resources the parent is about to remove
**Consumers:** code-writer, code-reviewer, tester

**Rule:** Teardown order is the reverse of stand-up order, and the test is not "does the parent own
these resources" but "does the dependent's cleanup have to *touch* the parent's resources while they
still exist." A twin (`te`) destroy cascades into `exr`/`tfc`/`tap`, and every one of those tiers
cleans up *through* the twin's live VMs — the tap tier's de-scope re-asserts a per-VM mirror
hookscript (`qm set <vmid> --hookscript`), the traffic tier's persona stop is a guest-agent exec
*into* the guest. Sequencing the cascades after the parent's own VM purge means they can only ever
fail. Run them first: `exr` → `tfc` → `tap` → `te`.

The reason this survived build, review, and five prior live-verifies is the second half of the
lesson: **both of those cleanup loops are deliberately best-effort** — they log a warning and do not
count into `TeardownResult.errors` — so the inverted order did not fail anything. It reported
`Result: DESTROYED`, exit 0, under 55 red `!` lines (live-repro'd on dev45 2026-07-29, a 32-host
`corp-enterprise` twin: 30 `mirror-descope` + 25 `persona stop failed`, all
`Configuration file 'nodes/dev45/qemu-server/<vmid>.conf' does not exist`). The end state happened to
be correct because the VMs were already gone, which is exactly what made it invisible. That is the
LES-001 failure shape inverted: not a false green, but a true green so noisy that a *genuinely* stuck
de-scope is indistinguishable from a clean teardown. When a cascade step is best-effort by design,
ordering bugs in it are silent by construction — so the order must be pinned by a test that asserts
the SEQUENCE (a shared recording backend across both phases), never inferred from a passing destroy.

Corollary (LES-037, re-confirmed the hard way): the inverted order was duplicated verbatim in both
transports, so fixing one would have left the other wrong. Cross-tier cascade ordering is
orchestration sequencing — it belongs in one shared helper (`o2control/cascade.py`, with the order
exported as data and test-pinned), not re-implemented per entry point.

**Origin:** CHANGELOG 2026-07-29 (twin-destroy cascade ordering); dev45 operator report on
`corp-enterprise-f525eed4`.

---

## LES-172 — Audit an authoring vocabulary by tracing every field to a consumer; "inert" and "harmful" are different, and same-named fields are the trap

**Class:** correctness (false affordance) + maintainability
**Scope:** `axrange/twinconfig/schema.py`, `axrange/twinconfig/expand.py`, `axrange/ir/schema.py`,
`axrange/personas/schema.py`, `axrange/sensors/`; any schema module defining a vocabulary a human
authors by hand
**Consumers:** code-writer, code-reviewer, doc-writer

**Rule:** A field that is parsed but read by nothing is not merely dead weight — it is a **false
affordance**.
An author who writes it is expressing intent, gets no error, and gets no effect. This project has now
retired the same class four times: `tap_points[].labels` (GAP-030, dissolved), the `tap:` IR flags
(`docs/WORK_ORDER_tap_eligibility.md`), `twin_config.nodes[].persona` (the trigger — read by exactly
one catalog typo-warning and nothing else), and the five swept alongside it (CHANGELOG 2026-08-04).

**The audit method that works** (grep-for-the-name does NOT):

1. For each field, grep for its *consumers*, then read each hit — do not count them. Three of the
   twin-config fields looked consumed because a **same-named, different field** existed elsewhere:
   `install[].role: adds` and `routing.role` are not `node.role`; `overlays.adversary`/`agent_kind=
   adversary` is not `Activity.adversary`; `identity.users[].groups` **is** wired while the top-level
   `identity.groups` is not. A same-name collision is the single most likely way to mark a dead field
   live, or a live field dead.
2. Prove inertness rather than asserting it: expand every shipped config before and after the removal
   and diff the derived `plan_id`/`ir_hash`. Byte-identical means the field genuinely reached nothing
   — and, because `plan_id` is keyed on `ir_hash` (LES-126), it also proves no standing deploy was
   orphaned by the cleanup.

**"Inert" is not the same as "harmful."** `purpose:` reached nothing, but it *read* as documentation
and *was* documentation — nobody expected it to configure anything. `persona:` reached nothing while
reading as configuration; that one actively misleads. Both can be removed, but only the second is a
bug. When removing a documentation-shaped field, migrate the text (here: 18 `purpose:` strings became
YAML comments) instead of deleting it — the annotation was the only thing of value in it.

**Removal is only half the fix.** A permissive model (`extra="allow"`) will silently re-absorb the key
on the next copy-paste (LES-111). Pair every removal with a loud rejection whose message names where
the intent belongs now, and keep those rejections in a **table** rather than a one-off `if` — the
fourth removal should be a table entry, not another hand-written branch (LES-037).

**Origin:** CHANGELOG 2026-08-04 (inert-field sweep); user-requested audit of the `te` config specs,
prompted by discovering `nodes[].persona` did nothing.

---

## LES-173 — Rebuild a dataclass with `dataclasses.replace()`, never a hand-listed constructor, at a resolution/override site — a later-added field is silently dropped otherwise

**Class:** correctness (silent field loss)
**Scope:** `axrange/o2control/traffic.py`, `axrange/o2control/taps.py`, `axrange/cli/main.py`; any site that reconstructs a spec/config dataclass to override one field
**Consumers:** code-writer, code-reviewer

**Rule:** When code rebuilds a dataclass to change one field — the classic case is resolving a
name/id target: `spec = TrafficProfileSpec(target=concrete, assignments=spec.assignments,
replay=spec.replay)` — every field it does NOT re-list is silently reset to its default. That is a
time bomb: the day someone adds a field to the dataclass (W1 added `roles` to `TrafficProfileSpec`),
every hand-listed rebuild that predates it drops the new field to its default (`[]`) with no error,
no warning, no test failure. W1's dev45 live-verify hit exactly this — a name-target apply discarded
the profile's whole `roles:` block because the rebuild at the name-resolution branch was copied from
the 2026-07-25 pattern that had only `target`/`bindings`. Use `dataclasses.replace(spec,
target=concrete)` instead: it copies EVERY field and overrides only the named one, so it is immune to
the next field added. This is the LES-037 "fix the class, not the instance" discipline applied to
struct reconstruction: grep every `<DataclassName>(` construction that is a REBUILD (not the parser /
source-of-truth construction, which SHOULD list all fields) and convert it. Invisible to the unit
suite for a specific reason worth internalizing: the tests that exercised the surrounding function all
used a literal target that skipped the rebuild branch entirely, so the branch with the bug had zero
coverage while the function looked well-tested.

**Origin:** CHANGELOG 2026-08-04 (W1 dev45 live-verify, bug 1 — 6 rebuild sites across traffic/taps/cli).

---

## LES-174 — A launched daemon's output directory must be created before the unit redirects into it; don't assume a generic log dir exists on a plain host

**Class:** correctness (silent launch failure) + environment assumption
**Scope:** `axrange/config_exec/render.py` (`systemd_daemon_start_block` and any daemon-launch renderer), `axrange/personas/drivers/`
**Consumers:** code-writer, code-reviewer, live-verifier

**Rule:** A rendered launch of the form `bash -c '<cmd> >/some/dir/x.log 2>&1'` fails instantly with
`No such file or directory` — and the process never runs — if `/some/dir` does not exist. Do NOT
assume a conventional directory (`/var/log/<product>`) is present: on a minimal host nothing may have
created it. The persona drivers launched through `systemd_daemon_start_block`, which redirected into
`/var/log/axrange` that only sensor-VM rendering / `provision-app` apt-logging ever created as a side
effect — so a deliberately minimal twin (no apps, no sensor) was the FIRST to expose the driver's
implicit, undeclared dependency, even though the identical Loki-shipper block already did the right
thing (`mkdir -p $(dirname <log>)`). Fix at the shared launch primitive so every consumer gets it
once (LES-037), and `mkdir -p $(dirname <log>)` immediately before the redirect. General principle:
a component that WRITES to a path owns ensuring its directory exists; never inherit that from an
unrelated component's side effect. This is a member of the LES-001 family — a green `drive-persona`
op said nothing; only a real launch on a bare host revealed it.

**Origin:** CHANGELOG 2026-08-04 (W1 dev45 live-verify, bug 2); GAP-020 (the same class, fixed there
for the Loki shipper and never generalized).

---

## LES-175 — Name→id resolution added to an APPLY path must be mirrored on the DESTROY path; lifecycle verbs are symmetric and must resolve their target identically

**Class:** correctness (asymmetric lifecycle) + completeness
**Scope:** `axrange/o2control/taps.py`, `axrange/o2control/traffic.py`, `axrange/o2control/exercise.py`; any tier with separate apply/destroy verbs that accept a target
**Consumers:** code-writer, code-reviewer

**Rule:** When you teach an APPLY/deploy verb to accept a friendly target (a scenario/twin NAME
resolved to a concrete deploy id at run time), you MUST teach the matching DESTROY verb the same
resolution — they address the same thing and a user will pass the same value to both. The tap tier
(2026-07-25) and then the tfc tier (W1) both added name resolution to apply but left destroy building
its lookup key from the raw target (`find_deploy_for_plan(f"tfc-{target}")`), so `destroy --target
<name>` could NEVER find a deploy that apply had created by name — the exact round trip the feature
exists for. When adding target resolution, grep for every verb that consumes `target` (deploy, apply,
destroy, status, info) and confirm each resolves identically; a concrete-id passthrough keeps the
cascade/programmatic callers byte-identical, and a graceful fallback to the raw target preserves the
clear "no deploy found" error for a stale name. Symmetric lifecycle verbs that resolve their target
differently is a bug class, not a corner case.

**Origin:** CHANGELOG 2026-08-04 (W1 dev45 live-verify, bug 3 — both `destroy_traffic_profile` and
`destroy_taps`).

---

## LES-176 — A refactor that MOVES code (adds the correct version) must DELETE the old location in the same change, or you get silent duplicate execution the suite won't catch

**Class:** correctness (duplicate work) + refactor hygiene
**Scope:** any move/reorder refactor, especially teardown/cascade sequencing; `axrange/cli/main.py`
**Consumers:** code-writer, code-reviewer

**Rule:** When a fix "moves" a step (e.g. LES-171 moved the sibling-tier cascades to run BEFORE the
twin's own sweep), the change is only half-done until the OLD copy is deleted. The 2026-07-29 LES-171
fix added the correct before-sweep `cascade_destroy_siblings_for_te` call but left the pre-existing
after-sweep tap/tfc/exr cascade block in `cmd_destroy` — so every `te destroy` ran the cascade TWICE:
the correct pass, then a duplicate against already-purged VMs that reproduced the exact
`Configuration file … does not exist` wall LES-171 was written to eliminate. It passed every unit
test (the helpers are idempotent; the order test used `.index()` on the first occurrence) and was
only visible as runtime output on a real destroy — the same "green suite, wrong behavior on the
cluster" shape as its three sibling findings. When reviewing a move/reorder: confirm the old site is
gone, not merely that the new site is correct; and be doubly suspicious when a diff ADDS a
correctly-placed call without a corresponding deletion. Compounds LES-037 (the CLI ended up with BOTH
the shared-helper call AND the duplicate, the precise drift the shared helper was meant to prevent —
the API had it right, one call).

**Origin:** CHANGELOG 2026-08-04 (W1 dev45 live-verify, finding 4); extends LES-171/LES-037.

---

## LES-177 — "Static validation OK" must not imply referenced resources EXIST; verify external ids against the live inventory, or scope the wording to "shape only"

**Class:** correctness (false confidence) + validation completeness
**Scope:** `axrange/o2control/taps.py` (`tap validate` static mode), any validate path that reports OK on content naming an external resource (sensor ids, image refs, host ids, external-sink ids)
**Consumers:** code-writer, code-reviewer, live-verifier

**Rule:** A "static OK" from a validate command that only checks *shape* (the field is present, the
selector parses) says nothing about whether the resource it *names* actually exists. `tap validate`
printed "static OK" for a tap plan whose `sensors: [o2-tcpdump]` named a sensor decommissioned two
weeks earlier — and even echoed it as a "resolved target" — because sensor-id existence is only
checked at `tap deploy` time against the live sensor registry. A whole work order's "re-confirmed to
resolve" step was satisfied by that output and shipped the stale reference; the live-verify preflight
caught it minutes before a hard deploy failure. Two obligations follow: (1) when a validate has a
reachable inventory (a `--db`/adapter.conf, the two-mode posture tap/tfc validate already have),
CHECK existence of every named external resource and warn on an unknown one — the same discipline
`REF-120` applies to image ids against the manifest; (2) if existence genuinely can't be checked
(no live target), the wording must say "shape only — existence unchecked," never bare "OK," so a
downstream "re-confirmed to resolve" reviewer isn't given false confidence. Generalizes past sensors
to any id a plan/spec references but validation doesn't resolve. This is the LES-001 family for
validation: a green static check is not proof the thing it points at is real.

**Origin:** CHANGELOG 2026-08-05 (corp-enterprise bundle live-verify — the `o2-tcpdump` stale sensor
caught at preflight); `docs/PRODUCT_GAPS.md` GAP-052.

---

## LES-178 — Planner-internal bookkeeping a downstream PASS needs at build time must never ride in `inputs_resolved`; give it a dedicated non-persisted field

**Class:** bug-class
**Scope:** `axrange/planner/dag.py` (`OpInstance.inputs_resolved` vs. `planner_hints`), `axrange/planner/pass1_emit.py` (anywhere an op-input dict like `cr_inputs` is built), any future planner pass that stashes cross-cutting bookkeeping into an op's resolved inputs
**Consumers:** code-writer, code-reviewer, tester
**Rule:** `inputs_resolved` is not "the op's private scratch space" — it is a PERSISTED, diffed record (`state.op_to_row`, and now `plandiff.diff_plan`'s Mutated fingerprint). A field placed there that (a) is a plan-WIDE roster rather than the op's own entity's data, and (b) changes whenever ANYTHING ELSE in that roster's scope is added/removed, will fingerprint-diff as Mutated on every unrelated add/remove even though the op's OWN behavior never changed — `configure-router`'s `served_hosts` roster (every non-router host sharing one of the router's LAN nets) hard-refused `--update` on 100% of shipped router-bearing content before this was caught. If a later pass needs plan-build-time-only bookkeeping that no adapter ever reads and that must never be compared for "did this op change," carry it in a dedicated, explicitly non-persisted field (`OpInstance.planner_hints`) instead — never smuggle it into the same dict that simultaneously answers "what does this op need to run" and "did this op change since last time."
**Origin:** CHANGELOG 2026-08-11 (W2 — incremental twin update, round 2 M1, `docs/WORK_ORDER_twin_incremental_update.md`)

## LES-179 — A plan-diff (or any "did this change" comparison) must re-evaluate aggregate COMMIT ops explicitly, not just barriers — an op whose own inputs never mention what it commits stays falsely Unchanged forever

**Class:** silent-success-trap
**Scope:** `axrange/planner/plandiff.py` (`ops_to_reexecute`), any catalog op whose `after:` clause carries a bare `<name>[*]` (not `[$var@*]`) pattern — `apply-sdn-config`, `create-sdn-zone`, `verify-capture`; any future "commits everything staged elsewhere in the plan" op
**Consumers:** code-writer, code-reviewer, tester
**Rule:** An aggregate op like `apply-sdn-config` (`PUT /cluster/sdn`, committing every staged zone/vnet/subnet in the WHOLE plan) takes an input (`zone_id`) that never enumerates what it commits — so a field-level diff of its OWN `inputs_resolved` never changes when a sibling `create-vnet`/`create-subnet` is added elsewhere, and it classifies "Unchanged" forever. Two measured failure shapes: LOUD (a new network's vnet stays staged-but-uncommitted; the new host's `start-vm` fails "bridge does not exist") and SILENT (flipping `egress: true` on an already-gatewayed network adds only `create-subnet`; every op reports COMPLETED on a segment with no gateway and no SNAT). The fix generalizes past this one op: detect the catalog's OWN bare-star `after:` shape (`.endswith("[*]")`, distinct from the far-more-common bound `[$var@*]` tail) and force-reexecute any such op whose predecessor set gained a newly-Added id — computed from the live catalog (`list_actions_and_waits()`), not hand-maintained, so a FUTURE op with this shape is automatically covered. Restrict this to `IdempotencyClass.INFRA`/`CONFIG` + `CleanupStrategy.REDO` ops (re-running is documented-safe) — never sweep in a non-idempotent `[$var@*]`-shaped op like `run-script` this way.
**Origin:** CHANGELOG 2026-08-11 (W2, round 3 MUST-FIX 1, `docs/WORK_ORDER_twin_incremental_update.md` §3.3)

## LES-180 — A diff/resume query over a table with tombstone rows must EXCLUDE the tombstoned status, or a removed entity gets rediscovered as "removed" forever

**Class:** bug-class
**Scope:** `axrange/planner/plandiff.py` (`diff_plan`'s persisted-row filter), `axrange/planner/state.py` (`reset_incomplete_ops`), any future query over the `ops` table that doesn't explicitly filter `OpStatus.DESTROYED`
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Once an `--update`'s removal sweep marks an op row `destroyed`, that row does not disappear from the `ops` table — it becomes a tombstone. A diff that naively compares "op ids in the new plan" against "every persisted op id" (with no status filter) finds the tombstoned id absent from the new plan, same as it was the update that removed it, and reclassifies it `removed` on EVERY subsequent diff, forever: an identical re-run refuses without `--allow-destroy` indefinitely, and WITH the flag, re-issues real teardown calls against cluster resources that are already gone. The fix excludes `status == DESTROYED` rows before computing the diff (`diff_plan`) AND teaches the general-purpose incomplete-op resetter (`reset_incomplete_ops`) to leave `destroyed` rows alone too (it used to reset every non-`completed` row to `pending`, which would resurrect a tombstone into a state nothing in the current plan can ever complete). Generalize: any "what changed" or "what needs work" query over a table that uses soft-delete/tombstone status values needs that status in its WHERE clause explicitly — it is never safe to assume "absent from the new set" and "already torn down" produce the same downstream classification.
**Origin:** CHANGELOG 2026-08-11 (W2, round 3 M2, `docs/WORK_ORDER_twin_incremental_update.md`)

## LES-181 — A destructive tail that runs AFTER the executor reports success must own its own exit code and persisted status; the executor's own green result is not the last word

**Class:** silent-success-trap
**Scope:** `axrange/api/service.py` (`apply_update_tail`, `UpdateTailResult`, `_proxmox_runner`'s `post_run` hook), `axrange/cli/main.py` (`cmd_deploy`'s post-executor tail), any future "do more work after the executor finishes" orchestration step
**Consumers:** code-writer, code-reviewer, tester
**Rule:** `axrange te deploy --update` runs the executor (added/retried ops) and THEN a separate scoped-removal-sweep tail. Before this was caught, the tail returned nothing the caller checked — so a non-clean removal (the protect guard refusing a VM whose PVE name looked like a standing `o2_` resource; any resource that simply wouldn't clear) still reported `rc=0` and left the persisted deploy row `completed`, because the EXECUTOR's own successful added-ops pass had already written that status before the tail ever ran. A later `te status`/`GET /api/deploys/{id}` would report a green twin while a protect-guard-refused VM stayed running. Fix: the tail returns an explicit clean/dirty result (`UpdateTailResult.clean`) that BOTH callers (the CLI's inline call, and `_proxmox_runner`'s `post_run` hook for the API's async path) check — downgrading the persisted status to `FAILED` and the CLI exit code to non-zero with an "INCOMPLETE" message on a dirty tail, mirroring `te destroy`'s own "Result: INCOMPLETE" posture. Generalize: any step that runs after "the main work succeeded" and can itself fail must propagate its OWN outcome all the way to the terminal exit code and the persisted record — never let an earlier success silently absorb a later failure.
**Origin:** CHANGELOG 2026-08-11 (W2, round 3 M3, `docs/WORK_ORDER_twin_incremental_update.md`)

## LES-182 — An identity-drift auto-heal must test the SPECIFIC signal that identity actually drifted, not a proxy that is also true in an unrelated case

**Class:** bug-class
**Scope:** `axrange/api/service.py` (`resolve_deploy_plan`'s same-generation auto-heal branch), any future "silently rebind onto a pinned/moved identity" heuristic
**Consumers:** code-writer, code-reviewer, tester
**Rule:** After an `--update` pins a deploy's `plan_id` away from what a fresh `derive_plan_id(scenario_id, ir_hash)` would produce, an UNFLAGGED redeploy of byte-identical content needs to "heal" onto that pinned plan_id rather than mint a spurious new one — detected by `ir_hash` equality between the freshly-computed plan and the standing row. But `ir_hash` equality ALONE is also trivially true for a completely different case: an OLDER, already-`destroyed`-superseded deploy that happens to share the exact same `plan_id` as a fresh, unedited computation (reachable via `deploy` → `--fresh` → `destroy` the newer one, both deploys of unedited content). Without ALSO requiring `standing.plan_id != plan.plan_id` (proof that identity genuinely drifted, not just that content matches), the heal would silently revive a deploy_id that `--fresh`/`destroy` had deliberately moved past. When writing an auto-heal/reconciliation heuristic, name the exact condition that makes the heal SAFE (here: "plan_id actually moved") and assert it explicitly — a content-equality proxy that is ALSO true in a case the heal must not fire for is not a substitute, even when it is true in the case you're trying to fix.
**Origin:** CHANGELOG 2026-08-11 (W2, round 3 M4, `docs/WORK_ORDER_twin_incremental_update.md`)

## LES-183 — A destructive operation's "find the one standing target" lookup must refuse + name every candidate on ambiguity, and any confirmation prompt for it must echo the target id FIRST

**Class:** bug-class
**Scope:** `axrange/api/service.py` (`resolve_deploy_plan`'s `--update` standing-deploy lookup), `axrange/cli/main.py` (`_print_update_diff`'s prompt ordering), any destructive CLI/API operation that resolves a single target then asks for confirmation
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Two things, both needed for a destructive action to be safe to confirm: (1) when more than one candidate could plausibly be "the" target (two standing, non-destroyed `te` deploys of one scenario — trivially reachable via `--fresh` while a prior deploy is still live, a `failed` partial, or an unflagged edit deployed without `--update`), refuse rather than silently picking the newest by `updated_at`, and list every candidate `deploy_id` in the refusal so the operator can choose — guessing which live twin to run a destructive removal against is worse than refusing. (2) Once a single target IS resolved, any interactive confirmation prompt gating the destructive part must show WHICH target (the `deploy_id`) in its own output BEFORE the prompt, not only after the operator answers — `--update`'s diff header prints `deploy_id` first, so "Apply? [y/N]" is always answered with the target already known, never discovered after the fact.
**Origin:** CHANGELOG 2026-08-11 (W2, round 3 M5 + M5-i, `docs/WORK_ORDER_twin_incremental_update.md`)

## LES-184 — A refusal guard belongs inside the conditional branch whose semantics it protects, not hoisted above it where it can catch callers that should never see it

**Class:** bug-class
**Scope:** any function serving multiple call shapes (a flagged/destructive path AND a plain/ordinary path) that share a common prefix of code before branching — `axrange/api/service.py::resolve_deploy_plan` is the concrete example
**Consumers:** code-writer, code-reviewer
**Rule:** `resolve_deploy_plan`'s "more than one standing deploy — refuse" check (LES-183) was first written to run unconditionally, ABOVE the `if update:` branch that actually needs single-target certainty — so it also caught an ORDINARY, non-destructive deploy call that happened to reach the same ambiguous-store shape (reachable via the documented LES-126 dead-end: two edits deployed back to back without `--update`), telling the operator to destroy two LIVE twins just to mint an unrelated third. The guard's OWN advice was actively wrong for the caller it was accidentally also blocking. The fix was purely structural: move the check inside the branch whose semantics require it. When adding a refusal/validation guard to a function with more than one call shape, place it inside the narrowest branch that actually needs it — never above an `if` that determines whether the guard should apply at all, even if hoisting looks like harmless deduplication. Complements LES-098 (which is about auditing every LATER use of an already-validated value); this is about not applying a check to callers who were never in scope for it in the first place.
**Origin:** CHANGELOG 2026-08-11 (W2, round 3 MUST-FIX 2, `docs/WORK_ORDER_twin_incremental_update.md`)

## LES-185 — Validate a plan-diff/reconciliation feature against SHIPPED content, never only hand-written fixtures; a green unit suite can certify a build that refuses every real scenario

**Class:** verification-idiom
**Scope:** any feature whose correctness depends on the SHAPE of real, already-authored content (plan-diff, validators, expanders) — `tests/test_twin_incremental_update.py`'s `target-environments/fabric-verify/scenario.yaml` regression is the pattern to copy
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Round 1 of `--update` shipped with 1802 green tests — and hard-refused `--update` on every one of the 5 shipped twin-configs and 6 archetypes, because none of the hand-written unit fixtures happened to include a `kind: router` node, and the bug (`served_hosts` fingerprinting as Mutated) only manifests on router-bearing content. A synthetic fixture designed to exercise "add a host" cannot also prove "add a host on content shaped like what we actually ship" unless it deliberately mirrors that shape. When a feature's job is to operate correctly against a repo's own real, checked-in content (not just arbitrary valid IR), add at least one regression test that loads a REAL shipped file (`target-environments/*/scenario.yaml`, a `twin-configs/*` config, ...) end-to-end through the real code path — a passing hand-fixture suite is not evidence the feature works on what actually ships.
**Origin:** CHANGELOG 2026-08-11 (W2, round 2 code review requiring the `fabric-verify` regression test, `docs/WORK_ORDER_twin_incremental_update.md`)

## LES-186 — A diff exclusion for a field RETIRED from persistence must be asymmetric (old-present + new-absent only), never an unconditional both-sides skip

**Class:** bug-class
**Scope:** `axrange/planner/plandiff.py` (`_RETIRED_PERSISTED_INPUT_KEYS`, `_diff_fields`), any future migration of a field OUT of a persisted/compared dict
**Consumers:** code-writer, code-reviewer
**Rule:** When a field that used to live in a persisted, diffed dict (`inputs_resolved`) is migrated to a non-persisted location (LES-178's `planner_hints`), a pre-migration deploy's persisted row still carries the old key — comparing it against a freshly-built op (which never carries the key at all) must not report a false Mutated field. The correct exclusion is narrow: tolerate ONLY the migration direction (`key in old and key not in new`). An unconditional both-sides skip (ignore the key regardless of which side has it) looks like the safe/simple fix but silently defeats the diff's OWN purpose for that key going forward: if the key is ever RE-introduced on both sides (a genuinely different future field reusing the name, or a bug that resumes persisting it), an unconditional skip makes that reintroduction permanently invisible to Mutated detection. Keep the retired-key exclusion set small, document why each entry is there, and make it directional.
**Origin:** CHANGELOG 2026-08-11 (W2 code review SHOULD-FIX 4, `axrange/planner/plandiff.py`)

## LES-187 — `f(X) == f(Y)` after already asserting `X == Y` is a circular test; snapshot the durable record independently and compare THAT

**Class:** verification-idiom
**Scope:** any regression test asserting "two derived values match" where one side's INPUT was itself just asserted equal moments earlier — `tests/test_twin_incremental_update.py`'s pinned-plan_id VMID check is the fixed example
**Consumers:** tester, code-reviewer
**Rule:** The first cut of "prove every surviving op's VMID is unchanged after `--update`" computed `vmid_for_op(plan_id_before, op)` and compared it against `vmid_for_op(after.plan_id, op)` — but the SAME test had already asserted `after.plan_id == plan_id_before` a few lines above, so the comparison was guaranteed to pass regardless of whether `vmid_for_op` even computes anything meaningful; it could not have caught a regression. The fix snapshots an INDEPENDENT ground truth BEFORE the operation under test — the value the FAKE ADAPTER actually recorded as having been built (`row.outputs_json`'s persisted `vmid`, or a name captured via a snapshot call before the update runs) — and compares the live post-update recompute against THAT, not against a second application of the same formula fed by an already-asserted-equal input. When a test's "expected" and "actual" sides both derive from a value the test just asserted equal, it is testing the formula's self-consistency, not the system's behavior — capture a real, independently-observed record instead.
**Origin:** CHANGELOG 2026-08-11 (W2 code review §6.2 rewrite, `tests/test_twin_incremental_update.py::test_update_add_node_pins_plan_id_and_leaves_existing_ops_untouched`)

## LES-188 — A lesson naming a behaviour ("exclude the instance's own host") must be checked against EVERY code path that resolves the same thing, not just the one path the fix touched

**Class:** bug-class
**Scope:** `axrange/o2control/traffic.py` (`_resolve_peers` and `_resolve_peer_sets`, or any future near-duplicate resolver pair); any module with two functions that answer a structurally similar question (single-target resolution vs. set/fan-out resolution) from the same underlying data
**Consumers:** code-writer, code-reviewer, tester
**Rule:** LES-170 recorded the rule "fan-out peer resolution must exclude the instance's own host" after fixing exactly one function, `_resolve_peer_sets` (the `peer_scope: all` path). `_resolve_peers` (the `peer_scope: one`/single-peer path) resolves the SAME underlying question — "who else holds this role" — from the same candidate data, and never got the same exclusion, so a persona whose peer role is one it ALSO holds (any mesh) resolved to its own IP and talked to itself over loopback for months, completely silently (`fails=0`, healthy-looking logs, zero frames on any tap) — and it silently defeated shipped content (`domain-controller`'s DC-to-DC replication emit, whose own comment states the intended single-DC skip, never fired because the candidate list always contained the DC itself). When a lesson is recorded as a rule about a BEHAVIOUR ("X must exclude Y"), grep for every OTHER function in the codebase that resolves the same kind of thing and verify the rule was actually applied there too — fixing the first instance found is not the same as fixing the class of bug the lesson describes.
**Origin:** CHANGELOG 2026-08-14 ("Single-peer resolution excludes the sender's own host: a mesh persona was talking to itself over loopback"); extends LES-170.

## LES-189 — A "last successful update" timestamp is not proof the thing it guards actually exists; a timestamp-only optimization can silently no-op against an emptied/missing resource

**Class:** silent-success-trap
**Scope:** any Ansible/idempotency shortcut keyed on a freshness TIMESTAMP rather than a presence check — `cache_valid_time` on `ansible.builtin.apt` is the concrete trap; generalizes to any "skip if recently done" cache/state check anywhere in `axrange/config_exec/`
**Consumers:** code-writer, code-reviewer, live-verifier
**Rule:** The first cut of the apt-cache-refresh fix set `cache_valid_time: 3600` alongside `update_cache: true`, intending a free optimization (skip a redundant refresh within the hour). It is actively wrong: `cache_valid_time` trusts apt's update-SUCCESS timestamp, not whether `/var/lib/apt/lists` actually still has the index in it. A live A/B with the lists directory deliberately emptied but a recent stamp left behind reproduced the exact original failure — Ansible reported the refresh task `ok` without doing anything, and the install then failed on the empty index exactly as before the "fix." **Only a live test caught this** — the emptied-cache scenario is not something a unit test against a mocked Ansible run would think to construct. When an idempotency/freshness shortcut is keyed on "how long since we last succeeded" rather than "does the guarded resource currently exist/is currently valid," treat it as unverified until tested against the resource's ABSENCE despite a fresh-looking timestamp — a green re-run is not evidence the shortcut is safe.
**Origin:** CHANGELOG 2026-08-14 ("`provision-app` refreshes the apt index before installing", "the fix that didn't work" section)

## LES-190 — A newly-created netdev inherits the node's DEFAULT qdisc, which can be actively wrong for the netdev's actual purpose; assert the qdisc you need explicitly, in the idempotent/adopt path too

**Class:** platform-quirk
**Scope:** `axrange/adapter_proxmox/ssh.py` (`attach_gre_tunnel`), any op that creates a Linux netdev (`ip link add ...`) without explicitly setting its queueing discipline
**Consumers:** code-writer, code-reviewer, live-verifier
**Rule:** `ip link add ... type gretap` (or any netdev creation) inherits whatever qdisc the node defaults to — on PVE 9.x that is `fq_codel`, an Active Queue Management discipline whose entire purpose is to DROP packets to hold latency down on a congested link. That is correct for a real uplink and actively wrong for a mirror/capture path: the mirrored copy is fire-and-forget (no sender to signal or back off), so every drop is lost evidence, and nothing in the chain reports it as an error — the frames simply never arrive (LES-001's "never trust a green deploy for capture" family). Measured: replacing `fq_codel` with a deep `pfifo` FIFO was worth a ~60% increase in lossless mirror throughput on dev45. Assert the qdisc your netdev actually needs explicitly and unconditionally — and place the assertion in the op's IDEMPOTENT/adopt branch, not only its create branch, since Model C check-then-act means a pre-existing netdev (created before the fix, or reset by an operator) is adopted, not recreated, on every later run; without the assertion running there too, an adopted netdev keeps the wrong qdisc forever.
**Origin:** CHANGELOG 2026-08-14 ("Mirror gretap gets a deep FIFO: the node-default qdisc was silently dropping captured frames")

## LES-191 — A capture/loss measurement is only as trustworthy as its WINDOW LENGTH; marginal rates can pass short windows clean and then lose real traffic over a longer one

**Class:** verification-idiom
**Scope:** any capture-rate/drop-accounting measurement (`twin-configs/*/measure.py`-style harnesses), live-verify runbooks that assert "no drops" or "clean" from a capture path
**Consumers:** live-verifier, tester
**Rule:** During the 2026-08-13 dev45 max-capture-rate run, one rung (R4b, ~2.37 Gbit/s) passed two 25-second measurement windows and a 90-second window completely clean, then lost 5,965 packets over a subsequent 120-second window at the same offered rate. A marginal rate — one close to whatever the current bottleneck's true ceiling is — can look lossless in a short window purely because the loss event (a queue overflow burst, a transient contention spike) didn't happen to occur during that particular sampling interval. Any "no drops" or "clean" claim from a capture-path measurement needs a window long enough to plausibly catch bursty loss, not just enough to get a stable-looking pps average; prefer the longest window practical (the run's own eventual standard was 240 s) over repeating short windows and trusting a lucky pass.
**Origin:** CHANGELOG 2026-08-13 (corp-enterprise max-capture-rate run, `twin-configs/corp-enterprise-maxrate/README.md` "Gotchas worth keeping")

## LES-192 — Counters read over separate SSH round-trips cannot be safely differenced at high packet rates; treat a computed "deficit" between two independently-sampled counters as advisory, not ground truth

**Class:** tool-gotcha
**Scope:** any drop-accounting/measurement harness that reads counters from two different hosts (or two different commands) over separate connections and subtracts them — `twin-configs/*/measure.py`-style harnesses; any future capture-rate live-verify tooling
**Consumers:** live-verifier, tester
**Rule:** A harness differencing tc's mirrored-packet count against the gretap's transmitted count — each read via its own SSH round-trip — produced a NEGATIVE "missing frames" figure at one rung: proof the two samples were taken far enough apart in wall-clock time that packet-rate skew alone exceeded the true difference (at ~230k pps, a fifth of a second of inter-sample skew is worth ~46,000 phantom packets in either direction). A negative deficit is impossible and is the tell that the metric is unreliable at this pps, not that there's a bug worth chasing. When two counters must be compared to detect loss, either read them as close to atomically as possible (a single connection, back-to-back reads, or a script executed ON one host that reads both), or treat the derived deficit as advisory and gate real "loss detected" conclusions on a counter that IS safe to trust in isolation (here, `tc mirred overlimits`, a single counter on a single host) rather than a cross-host subtraction.
**Origin:** CHANGELOG 2026-08-13 (corp-enterprise max-capture-rate run, `twin-configs/corp-enterprise-maxrate/README.md` "A counter that lies")

## LES-193 — "Validated offline" for a live-verify fixture does not include peer/role resolution against the target twin; bind it before the gated run depends on it

**Class:** verification-idiom
**Scope:** `deploy-dev45/*.yaml`; any live-verify fixture; `axrange/o2control/traffic.py`; `axrange/personas/catalog/*.yaml`; whoever authors a tfc/tap fixture for a gated run
**Consumers:** live-verifier, code-writer, whoever stages fixtures for a gated live-verify
**Rule:** A traffic-profile fixture that parses, validates and references only real persona ids can still be **unbindable against the twin it targets**, because a persona's HARD (non-optional) `peer_role`s must each resolve to a host carrying that role — and a deliberately minimal verify twin carries almost no roles. Caught on the 2026-08-14 W2 gated run: `tfc-w2-incr-verify.yaml` assigned the `corp-workstation` persona to a 4-node twin (gw + 3 workstations); that persona has hard peers `domain-controller`, `file-server`, `egress-proxy` and `mail-server`, so `tfc deploy` would have fail-louded (LES-098/LES-001, correct behaviour) in the middle of a gated destructive run, for a reason wholly unrelated to the feature under test. The fixture had been authored and recorded as "validated offline"; that validation covered schema and references, not peer resolution. **Two rules follow.** (1) When staging a fixture for a gated run, compute the twin's role set and check every persona's hard peers against it — `{e.peer_role for e in persona.emits if not e.optional and e.peer_role not in RESERVED_PEER_ROLES} <= roles_present` — or simply bind it once on a throwaway twin first. (2) For a minimal verify twin, prefer a persona whose only hard peer is a role the twin's own hosts hold (here `bastion-jump` → `corp-workstation`), which both resolves and exercises the self-exclusion path. Generalises beyond personas: "validated offline" is a claim about the artifact, never about the artifact *against the specific live target*, and a gated run is the worst place to discover the difference.
**Origin:** CHANGELOG 2026-08-14 (W2 live-verify on dev45 — fixture defect found on first real use)

## LES-194 — In an AD attack chain, `DCSync ✅ / AS-REP-roast ✅ / Kerberoast ❌ (KRB_AP_ERR_SKEW)` is the signature of attacker↔DC clock skew, not a broken exploit; fix by syncing the ATTACKER to the DC

**Class:** platform-quirk / verification-idiom
**Scope:** `twin-configs/*/exr-*.yaml`; `target-environments/ad-domain-lab`; `axrange/o2control/exercise.py`; any AD kill-chain activity; whoever debugs a Kerberoast/AS-REP/DCSync step that "half works"
**Consumers:** live-verifier, whoever runs or authors an `exr` AD kill-chain, code-reviewer (don't accept "the Kerberoast exploit is broken" without checking clock skew first)
**Rule:** When an AD kill chain shows **DCSync succeeding, AS-REP roasting succeeding, but Kerberoasting failing with `KRB_AP_ERR_SKEW(Clock skew too great)`** (or an authenticated Kerberos step failing while NTLM/no-preauth steps pass), the exploit is fine — the **attacker's clock is skewed from the DC's**, and the failure is purely which auth primitive each step uses. The three steps stress different clocks: **DCSync** authenticates over **NTLM/DRSUAPI** (no Kerberos timestamp check → immune to skew); **AS-REP roast** sends an AS-REQ with **no pre-authentication** and just captures the KDC's reply (no client timestamp validated → immune); **Kerberoast** must first obtain a **TGT with a pre-auth timestamp** the KDC validates against its own clock (±5 min by default → fails on any real skew). Observed live 2026-08-14: the `corp-enterprise` DC ran **6h slow** (GAP-037) — the *domain* was internally consistent because members sync from the DC, so only the **non-domain-joined attacker** saw the gap. **Fix like a real operator: sync the ATTACKER to the DC, never "fix" the DC** — match the target's clock (`date -u <DC-time>` on the attack box, or point it at the DC as NTP), then the Kerberos step completes immediately (the run then returned a real `$krb5tgs$23$*svc-sql$…`). Diagnostic shortcut: read the DC's `(Get-Date).ToUniversalTime()` and the attacker's `date -u` and compare BEFORE assuming an exploit or credential is wrong. Belongs as an implicit or explicit step-0 in any AD kill-chain activity: a red-team playbook syncs to the DC before Kerberos work, so a robust exr spec should too — DONE 2026-08-14: `twin-configs/corp-enterprise/exr-ad-killchain.yaml` now opens with a `set-ntp false` + anon-RootDSE-`currentTime` + `date -u -s` step 0 and closes with an NTP-restore step 8, both `technique:`-less so they emit no ATT&CK marker. Live-verified: a clean replay returned a real `$krb5tgs$` with NO manual clock fix. Note `set-ntp false` FIRST is load-bearing — without it systemd-timesyncd reverts the `date -u -s` within ~2s.
**Origin:** CHANGELOG 2026-08-14 (exr on corp-enterprise — DCSync succeeded, Kerberoast blocked on the GAP-037 6h DC skew, fixed by syncing the attacker)

## LES-195 — A directory/file catalog reader must publish the id ITS OWN LOOKUP resolves by, and cross-check any content-declared id against it — never trust a sidecar/embedded id alone

**Class:** bug-class
**Scope:** `axrange/api/imprints.py`, and any future directory/file catalog reader that resolves an entry by NAME (a path segment, a filename stem) but ALSO has content that carries its own id-like field (a `twin_config.id`, a sidecar's own `imprint_id`/similar); kin **LES-093** (a deterministic-name producer must round-trip its own predicate on degenerate input) and **LES-065**/**LES-068** (round-trip any producer/consumer contract across a serialization boundary, don't trust that two sides "obviously" agree)
**Consumers:** code-writer, code-reviewer, tester
**Rule:** When a catalog reader resolves an entry by directory/file NAME (e.g. `GET /api/imprints/{id}` walks to `fixtures/imprints/<id>/`) but the entry's own content also carries an id-like field, two independent things must both hold. (1) The value published back to callers as `id` must be the NAME the lookup actually resolved by, never a value re-parsed out of the content — otherwise a `list` result's `id` can silently fail to round-trip on the matching `get`, because the content id and the addressing id are two different strings that merely happen to usually agree. (2) Any such content-declared id must be CROSS-CHECKED against the resolving name, and the read must fail loud (the module's own typed data error, never a bare exception or a silent pass-through) on any disagreement — silently preferring one of the two, or not checking at all, means a renamed or copy-pasted directory can serve one entry's data (or, worse, one entry's provenance/audit trail) under another entry's URL with no error raised anywhere. Caught in code review before it shipped (`axrange/api/imprints.py`'s M2 fix, 2026-08-25): the original draft derived `id` from `twin_config.id` and never checked it against the sidecar's own `imprint_id`, which would have broken `list`→`get` for a renamed fixture and let a stale sidecar be served silently, correct-looking, under the wrong id.
**Origin:** CHANGELOG 2026-08-25 (aximprint integration — Track C slice 1, `axrange/api/imprints.py` code-review fix M2)

## LES-196 — A read-only graph endpoint's "which catalog resolves this id" and "which id drives the status overlay" are two separate concerns; conflating them 404s any handoff that pins a non-catalog id

**Class:** bug-class / silent-success-trap
**Scope:** `axrange/api/service.py` (`topology`, `_resolve_read_ir_path`, `_parse_ir_only`, and by extension any future sibling read endpoint built the same way); `o2/grafana/plugins/axellio-axrange-control-panel/src/components/AxrangeControlPanel.tsx` (any deploy→Topology handoff); any future "deploy `<X>` as twin" surface that jumps to the topology screen after a write
**Consumers:** code-writer, code-reviewer, tester
**Rule:** `GET /api/scenarios/{id}/topology` builds its base node/edge graph ONLY by resolving `scenario` against the scenarios catalog (`_resolve_scenario_path`); the `deploy` query param drives ONLY the per-node STATUS overlay on top of that graph — it never contributes to, or substitutes for, base-graph resolution. Any UI handoff that pins a NON-scenario-catalog id (an imprint id, a twin-config id) as the topology `scenarioId` — even alongside a valid, live `deployId` — 404s the base graph and renders a **blank canvas**, while the deploy op-tree (keyed by `deploy_id` alone, with no dependency on the base graph resolving) renders fine right next to it — a half-broken screen that looks like partial success rather than a clear failure. **Fix:** make the read-only graph endpoint resolve `<X>`'s id against the SAME catalog the deploy actually sourced it from (done here via `service._resolve_read_ir_path`, a fallback that `_parse_ir_only` now uses for both `topology` and node-detail — base-graph-only: no param overrides, no provenance/sidecar load, so the fallback never depends on anything beyond the minimum a canvas render needs), or reconstruct the base graph from the deploy's own persisted plan instead of re-resolving the source id at all. Any new deploy source added to `create_deploy` (mirroring the `scenario`/`config`/`imprint` three-way split) needs the identical fallback wired into `_resolve_read_ir_path`, or its own "deploy as twin"-style UI handoff will silently blank the canvas the same way.
**Origin:** code review, aximprint Track C slice 2 (`axrange/api/service.py` `_resolve_read_ir_path`), CHANGELOG 2026-08-25 "aximprint integration — Track C slices 2+3"

---

## LES-197 — A half-committed feature is worse than an uncommitted one: never `git add -A`/`commit -a` in a shared, long-lived working tree

**Class:** silent-success-trap
**Scope:** any `git commit`/`git add` in a working tree that has OTHER uncommitted or in-progress work sitting alongside the change actually being committed (phrase scope — not one file)
**Consumers:** code-writer, code-reviewer, live-verifier
**Rule:** A broad stage (`git add -A`) or commit (`git commit -a`) issued in a tree that also has unrelated, still-in-progress files present will sweep in PART of that other change — whatever happens to match the broad pattern — while leaving the rest uncommitted. The commit SUCCEEDS with no error, so the danger is invisible until someone checks out HEAD fresh: here, an unrelated fix commit (`6b340504`, 2026-08-28) swept in `api/service.py`'s new callers and `cli/main.py`'s new `--twin-configs-dir` flag from a different, code-complete-but-uncommitted Console feature, without their supporting files (`serve.py`, `dtos.py`, `state.py`, `registry.py`, `topology_export.py`) — so HEAD raised a `TypeError` on `axrange serve`'s unknown keyword for over a week, undetected because the live `:8080` instance predated the sweep and nobody restarted it against HEAD. An uncommitted change is honestly incomplete; a half-committed one lies — HEAD looks plausible and isn't. Stage files explicitly, by name, especially when a tree is known to carry other WIP; and after any broad-add commit, sanity-check HEAD's actual entry points (start the real service/CLI command), not just the test suite, since the missing files may not be covered by the tests the half-swept caller exercises.
**Origin:** CHANGELOG 2026-09-02 "Content-only reapply for `--update`..." and the "Golden-twin fidelity" entry (2026-09-03); commit `fa0520d4`'s own message; the original sweep, commit `6b340504` (2026-08-28).

---

## LES-198 — A multi-catalog id fallback chain must let the caller PIN which catalog; trying catalogs in order and returning the first hit silently resolves the wrong content on a collision

**Class:** silent-success-trap
**Scope:** `axrange/api/service.py` (`_resolve_read_ir_path`, and any multi-catalog id resolution: scenario/twin-config/imprint); any picker/dropdown UI that lists ids sourced from more than one catalog; kin **LES-195** (a catalog reader must publish/cross-check the id it resolves by) and **LES-196** (a read endpoint's base-graph catalog and its status-overlay param are separate concerns)
**Consumers:** code-writer, code-reviewer, tester
**Rule:** When two independently-authored catalogs can validly contain the SAME id (a shipped twin-config named `corp-enterprise` and an unrelated imprint fixture also named `corp-enterprise` — a real collision in this repo's own test fixtures), a fallback chain that tries catalogs in a fixed order and returns the first hit will SILENTLY resolve the wrong one whenever both exist, with no error and no 404 — a worse failure than LES-196's blank canvas, because a blank canvas is at least honestly broken and this looks like success. Fix: give the caller an explicit discriminator (`?source=scenario|twin-config|imprint`) that PINS resolution to exactly one catalog, falling back to the historical chain only when the discriminator is omitted (back-compat); and make any UI picker that lists ids across catalogs tag each option with its source (`kind:id`, never a bare id), so the identical collision can't recur client-side. This is the third installment of the id-ambiguity family LES-195/196 started — audit any NEW multi-catalog id surface against all three before shipping it.
**Origin:** CHANGELOG 2026-08-27 "Logical/Physical semantics restored..." (finding M1), committed 2026-09-02 (`fa0520d4`).

---

## LES-199 — A temp-file-materialize-then-parse read path must delete its file in a `finally`, gated on an explicit "did I create this" flag

**Class:** bug-class
**Scope:** any read path that materializes a parameterized/templated document into a temp file purely to hand it to an unmodified parser (`axrange/twinconfig/registry.py` `materialize_twin_config`; `axrange/api/service.py` `_parse_ir_only`/`_resolve_read_ir_path`); any `tempfile`-based render-then-parse pattern
**Consumers:** code-writer, code-reviewer
**Rule:** A read path that expands a twin-config/imprint into a plain Scenario-IR YAML temp file so the unchanged validator/planner can parse it must delete that file in a `finally` block, gated on a flag saying THIS call is the one that created it (`is_temp=True`) — never unconditionally, since a REAL, non-temp catalog file resolved on a sibling branch must never be deleted, and never left for the OS to eventually reap. Every unguarded call leaked ~13 KB / ~150 ms of work forever; on a read-heavy endpoint hit repeatedly by a UI (topology, node-detail, topology-export) that is an unbounded disk leak, not a one-off. General shape: any function returning `(content, is_temp)` or equivalent has exactly one caller responsible for the temp branch's cleanup, and that cleanup must run on both the success and failure path.
**Origin:** CHANGELOG 2026-08-27 "Logical/Physical semantics restored..." (finding S2), committed 2026-09-02 (`fa0520d4`).

---

## LES-200 — A shared helper's docstring claiming a consumer it doesn't actually have hides duplicate, drifted logic from review

**Class:** convention
**Scope:** any shared frontend/backend helper whose docstring/comment names a specific caller ("used by X"); `o2/grafana/plugins/axellio-axrange-control-panel/src/io/sources.ts`; code review generally
**Consumers:** code-writer, code-reviewer
**Rule:** A shared helper's docstring claiming a consumer it does NOT actually have (`io/sources.ts:listAllSources` claimed `ControlToolbar` as a consumer while `ControlToolbar` actually hand-rolled its own duplicate two-catalog fetch) is not a harmless inaccuracy — it is an active trap. A reviewer or future author who trusts the docstring assumes reuse is already happening and stops looking for the duplicate, so the drift (here: the duplicate fetch never gained imprints when the real helper did) survives review undetected. When adding or reviewing a shared helper, either wire the claimed consumer in the SAME change, or correct the docstring to describe reality — never leave an aspirational "used by" comment standing in for actual reuse.
**Origin:** CHANGELOG 2026-08-27 "Logical/Physical semantics restored..." (finding M2), committed 2026-09-02 (`fa0520d4`).

---

## LES-201 — An argv containment guard that only inspects its own rendered tokens is blind to ambient tool configuration; neutralize the channel, don't just check the tokens

**Class:** security
**Scope:** `axrange/personas/drivers/command.py` (`_check_containment`); any driver/renderer that asserts "this command can't reach an unintended address" by inspecting only the literal tokens it emits
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A containment guard that checks only the argv tokens it renders is blind to ambient configuration that changes a tool's behavior WITHOUT appearing in the argv — `curl` honors `http_proxy`/`https_proxy` environment variables and a user's `~/.curlrc` by default, either of which could re-route a "contained" request past whatever address check the guard performed. Checking tokens alone is not enough: the guard must actively NEUTRALIZE every such ambient channel (`curl -q` skips `~/.curlrc`, `--noproxy '*'` ignores proxy env vars) and assert the neutralizing flags are present — and, symmetrically, assert that nothing re-opens a channel the design relies on staying closed (no `-L` redirect-follow flag, since a redirect response could re-target the request past the address the guard checked). A containment guard's job is "prove nothing OUTSIDE the checked tokens can influence where this command goes," not merely "the tokens I see look fine."
**Origin:** CHANGELOG 2026-09-02 "`drive: command`..." (review round, commit `a0f6bb67`).

---

## LES-202 — A peer-consistency check must resolve through the SAME escape-hatch binding table the containment guard uses, never compare the raw address token

**Class:** bug-class
**Scope:** `axrange/personas/drivers/command.py` (`_effective_destinations`, `_bound_hosts`); any check verifying "does this rendered command address the peer it claims to" where the render path has a legitimate escape hatch for binding a hostname to a literal address (`curl --resolve`/`--connect-to`)
**Consumers:** code-writer, code-reviewer, tester
**Rule:** When a render path has two ways to express "this address is safe" — a bare IP literal, or a hostname explicitly bound to one via an escape hatch — any DOWNSTREAM check that also needs to know which peer an argv actually addresses must resolve THROUGH that same binding table, not compare the raw address token. Treating the bound-hosts set as a flat set of hostnames (instead of a `{host: literal}` map) makes a peer-consistency assert false-positive on the legitimate escape-hatch case, because the address token in argv is a hostname, not the literal peer IP, by design — exactly the case the escape hatch exists to allow. Two checks over the same rendered artifact that both care about "what address does this really mean" must share one resolution function; each reimplementing its own assumption about the artifact's shape is how they end up disagreeing.
**Origin:** CHANGELOG 2026-09-02 "`drive: command`..." (review round, commit `a0f6bb67`).

---

## LES-203 — A new consumer implementing only a subset of a shared schema's knobs must honour or loudly refuse every field an author can set, never silently ignore one

**Class:** silent-success-trap
**Scope:** any new driver/backend that implements only some of an already-validated shared schema's fields (`axrange/personas/drivers/command.py` vs. the full `emits[]` schema in `axrange/personas/schema.py`); generally, a second implementation of an interface the first implementation didn't exhaust
**Consumers:** code-writer, code-reviewer
**Rule:** Schema validity and driver support are two different contracts — a field the validator accepts (because a SIBLING driver honours it) does not mean every driver honours it, and that gap is invisible until someone actually authors the combination. When a new consumer's v1 scope covers only some of a schema's allowed fields (`command`'s v1 table covers proto/ports/peer_role/cadence/volume but not every `transport:` override), every field the schema permits must still be explicitly HONOURED or explicitly, loudly REFUSED at render/start time — never silently dropped. An authored `emits[].transport: tcp` a template's tool cannot actually drive (no TCP client in the v1 `ntp` template; no transport concept for `icmp` at all) must fail loud naming the field and why, not vanish while the persona still validates and deploys "successfully."
**Origin:** CHANGELOG 2026-09-02 "`drive: command`..." (review round, commit `a0f6bb67` — `explicit_transports` wiring).

---

## LES-204 — Never parse a login shell's stdout as pure data; tag the probe's own output and filter on the tag

**Class:** platform-quirk
**Scope:** any probe/check run via a login shell (`sh -lc`, `bash -l`) over QGA/SSH whose stdout is read as structured data; `axrange/personas/drivers/command.py` (the in-guest tool-availability probe)
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Running a probe command through a LOGIN shell can prepend arbitrary banner/profile text to stdout (`/etc/profile`, `/etc/profile.d/*`) ahead of the probe's own output, so a probe that treats "whatever came back on stdout" as the answer risks misreading a banner line as if it were the probe's own report. Fix: never trust raw stdout from a login-shell-wrapped command as data — have the probe TAG its own real output lines with a distinctive marker (`MISSING:<tool>`) and filter strictly on that tag, discarding everything else regardless of what it looks like. Generalizes to any QGA/SSH exec that wraps a command in a login shell for PATH/env reasons.
**Origin:** CHANGELOG 2026-09-02 "`drive: command`..." (review round, commit `a0f6bb67`).

---

## LES-205 — Keep a narrow eligibility gate and the refusal message that explains it in ONE function, never two independently-maintained implementations

**Class:** pattern
**Scope:** `axrange/planner/plandiff.py` (`reapplyable_ops`/`reapply_ineligibility_reason`); any narrow, opt-in carve-out from a broader refusal ("except in this one case")
**Consumers:** code-writer, code-reviewer
**Rule:** When adding a narrow, opt-in exception to a broad refusal (`--reapply` carving one op/field combination out of `--update`'s Mutated hard-refusal), implement the "is this covered?" predicate and the "why not, if it isn't" message as ONE function (or a tight pair sharing the same underlying check) — never as two independently-maintained implementations. Two separate copies of the same eligibility logic WILL drift the first time either is edited alone: the gate silently starts accepting or rejecting cases its own printed explanation disagrees with, and nothing catches that except a human noticing the message is wrong. General form: don't duplicate a predicate, sharpened for the case where one copy is a boolean gate and the other is the human-facing explanation of that same boolean.
**Origin:** CHANGELOG 2026-09-02 "Content-only reapply for `--update`..." (design note, commit `4c2b7679`).

---

## LES-206 — A diff/render helper that may carry a large or sensitive payload needs its elision threaded through EVERY surface that prints it, not just the first one built

**Class:** security
**Scope:** `axrange/planner/plandiff.py` (`render_value`, `content_digest`); `axrange/api/service.py` (`_mutation_refusal_message`); `axrange/cli/main.py` (the `--update` diff printer); any diff/render helper whose input may carry large or sensitive content (script text, credentials) and that has more than one caller/surface
**Consumers:** code-writer, code-reviewer
**Rule:** A shared diff/render helper that can carry a large or sensitive payload (an entire `run-script` `script_content`, potentially embedding secrets) needs its elision behavior (a length cap, a digest-instead-of-value mode) threaded through EVERY surface that prints its output — CLI stderr, API JSON error bodies, log lines — not just the surface built first. Fixing elision on one printer and leaving a sibling to dump the raw value verbatim is not "mostly fixed," it is a leak with one patched exit and one open one. When a helper gains an opt-in elision parameter, audit every call site across every surface before calling the feature complete.
**Origin:** CHANGELOG 2026-09-02 "Content-only reapply for `--update`..." (`render_value`/`content_digest`, commit `4c2b7679`).

---

## LES-207 — Force-resetting a COMPLETED producer op to re-run it is only safe because the executor never re-validates a completed successor's predecessors; that invariant is load-bearing and must be explicitly preserved

**Class:** reversal-hazard
**Scope:** `axrange/planner/executor.py` (the resume/skip logic for `completed` ops); any feature that force-resets an already-`completed` op's status to `pending` so it re-runs (`--reapply`'s `force_reset_ops`; `--update`'s barrier/aggregate-op reset)
**Consumers:** code-writer, code-reviewer
**Rule:** Force-resetting a completed op back to `pending` (as `--reapply` does for a mutated `run-script`) is safe TODAY only because the executor's resume short-circuit does not re-validate a completed SUCCESSOR op's predecessors before skipping it — a downstream op that already ran against the old content stays `completed` and is never re-examined merely because an upstream producer it depended on was force-reset and changed. This is load-bearing for `--reapply`'s narrow safety story, and easy to break by accident: any future move toward a "live-readiness" scheduler (one that re-checks a completed op's inputs/predecessors before trusting `completed`) would silently start cascading re-runs through a force-reset op's whole downstream subtree — a far bigger blast radius than intended. Anyone touching resume/skip logic must explicitly re-review this invariant, not just re-run the existing suite (which pins today's behavior but doesn't explain why it must hold).
**Origin:** CHANGELOG 2026-09-02 "Content-only reapply for `--update`..." (design note, commit `4c2b7679`).

---

## LES-208 — A unit test must never resolve a gitignored config file via the process's cwd; inject every backend seam the code offers, not just the obvious ones

**Class:** tool-gotcha
**Scope:** `tests/test_o2_taps.py` (`apply_tap_plan`/`_delete_dropped_gretaps`); any test that must fake EVERY backend role the code under test can reach, not just the ones its assertions obviously touch
**Consumers:** tester, code-writer, code-reviewer
**Rule:** A unit test must never resolve a gitignored config file via the process's current working directory — `load_config(None)` falling back to `./adapter.conf` means a test's pass/fail (and, here, its RUNTIME: an ~11 s SSH timeout) depends on which directory pytest happens to run from and what happens to be sitting there, not on the code under test. `test_apply_tap_plan_reshard_surfaces_dropped_gretap_in_warnings` injected fakes for `adapter`/`config_backend` but missed `teardown_backend`, so `_delete_dropped_gretaps` silently built a REAL `ProxmoxTeardown` and reached for a real SSH target from whatever `adapter.conf` happened to be on disk. When writing or reviewing a test against code with multiple injectable backend roles, enumerate ALL of them from the function signature/constructor before writing fakes, not just the ones the test's assertions obviously exercise.
**Origin:** CHANGELOG 2026-09-02 (commit `0b38f46b`, surfaced by the `drive: command` code review's isolated-worktree run — the failure did not reproduce in every checkout, only ones missing the repo-root `adapter.conf`).

---

## LES-209 — A repo-resolution helper used by a hook/gate must trust the invoking tree (`git rev-parse --show-toplevel` from cwd) over an ambient environment variable

**Class:** tool-gotcha
**Scope:** `scripts/hooks.py` (`_resolve_repo`); `scripts/doc-stats.py` (`_resolve_repo`, same shape); any hook/gate that must know "which repo is this operation about" and has both an ambient env var and a filesystem-derivable answer available
**Consumers:** code-writer, code-reviewer (whenever a `/dev` pipeline stage runs inside an isolated git worktree)
**Rule:** A repo-resolution helper that checks `CLAUDE_PROJECT_DIR` (a harness-injected environment variable) BEFORE the invoking process's own `git rev-parse --show-toplevel` resolves to the WRONG tree whenever the two disagree. Concretely: a `code-writer` subagent working in an isolated worktree (`.../scratchpad/wt/<branch>/`) inherits `CLAUDE_PROJECT_DIR` pointing at the MAIN checkout, so `scripts/doc-stats.py --check` (and the commit-guard hook that calls it) polices the worktree's commit against the MAIN tree's doc counters and source state — not the worktree's own. A worktree whose own tree is internally consistent can be hard-blocked by drift that exists only in a DIFFERENT tree, with an error message ("CLAUDE.md claims 1956 pytest tests, source says 2021") that gives no hint the tree being checked isn't the tree being committed. **Fix direction (not yet applied):** resolve via `git rev-parse --show-toplevel` from cwd FIRST when cwd is inside a git tree, falling back to `CLAUDE_PROJECT_DIR` only when it is not (e.g. a hook invoked outside any git tree). General rule: a gate operating "on the repo" must resolve which repo from the OPERATION's own location, not from an ambient variable that may have been set for a different purpose (identifying the user's overall project to the harness) than the one the gate is using it for (identifying which tree's state to check).
**Origin:** coordinator finding, 2026-09-03 — empirically reproduced against `.../scratchpad/wt/option-a-reapply` and confirmed by re-running `doc-stats.py --check` from `/home/rocky/axRange` directly (`docs/PRODUCT_GAPS.md` GAP-058).

## LES-210 — A structural exemption for one entity kind disarms every derived rule that reads that kind as evidence; re-derive each rule against the SHIPPED topology (extends LES-185)

**Class:** bug-class
**Scope:** `axrange/twinconfig/expand.py`, `axrange/twinconfig/gate.py`, `axrange/planner/plandiff.py`; any gate/filter/cascade that exempts one entity kind (`kind: router`, a barrier op, a protected tier) while a *derived* rule downstream counts entities of that same kind
**Consumers:** code-writer, code-reviewer, tester
**Rule:** When a feature exempts an entity kind from a selection rule ("routers are never gated out"), enumerate every DERIVED rule downstream that counts or reads that kind — occupancy tests, emptiness cascades, "is anything still attached?" guards, reference cross-checks — and decide for each one whether the exempt kind is evidence or plumbing. An exemption plus a derived rule that still counts the exempt entity is a silent no-op or a false error, and its severity is topology-dependent, so it is invisible in a fixture that does not reproduce the real shape. Concretely: the deploy gate exempted `kind: router` nodes, but the segment-emptiness cascade counted router legs as occupancy and the "gated out from under an emitted node" hard error counted routers as emitted nodes. A cast emits ONE `gw` with a leg on every one of its 27–33 segments, so every segment was permanently "occupied" (the cascade could never fire) and every below-bar segment became a hard error — **every real cast refused**, with a hand-built fixture suite fully green. The fix is one sentence stated once and applied everywhere: *the exempt kind FOLLOWS the thing it is attached to* — its legs are pruned with their segment, it is dropped when stranded, and it is never evidence. Build the fixture to reproduce the real topology's degenerate shape (one router touching everything), not a tidier one.
**Origin:** CHANGELOG 2026-09-04 "Deploy gate BUILT" ruling 1; `docs/WORK_ORDER_deploy_gate.md` §11.2; found independently by the aximprint deployment-scope build and by code review. Extends LES-185.

## LES-211 — A printed remedy must name the clause that fired AND be a value that still works when pasted back verbatim; test it by extracting the command from the message

**Class:** bug-class
**Scope:** any user-facing refusal/warning that prints a `--param`/`--flag <value>` remedy — `axrange/twinconfig/gate.py` (`exact_param_value`), `axrange/twinconfig/expand.py`, `axrange/cli/main.py`, `axrange/o2control/*`
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Two independent failure modes, both of which produce a message that *looks* helpful. (1) **Wrong clause:** when a predicate has more than one clause (occupancy AND role-confidence, size AND count), the refusal must carry which clause actually fired and offer the remedy for THAT one — telling an operator to lower `DEPLOY_MIN_OCCUPANCY` when the role-confidence clause removed their host is an actively wrong instruction. Return a typed verdict (`(metric, reason)`), never a bare bool or a pre-formatted string. (2) **Un-pasteable value:** a threshold rendered for prose (`%g`, `round()`, `:.2f`) is usually *below* the exact value it names, so re-running at it refuses the entity again — `8/11` prints as `0.727273` while the producer carried `0.7272727272727273`, and the comparison epsilon (`1e-9`) is far tighter than the display rounding. Render remedy values with `repr(float(x))` (exact by construction: `float(repr(x)) == x`) and keep the prose rendering separate. Test it the only way that proves it: **extract the value from the printed message and re-run with it**, asserting the entity is now emitted.
**Origin:** CHANGELOG 2026-09-04 "Deploy gate BUILT" ruling 6 (review defects M2/M3); `axrange/twinconfig/gate.py::exact_param_value`.

## LES-212 — A predicate shared by two entity kinds must take applicability per kind as a REQUIRED argument, never a default

**Class:** bug-class
**Scope:** `axrange/twinconfig/gate.py` (`gate_verdict(..., role_confidence_applies=)`), `axrange/planner/tappoints.py`, `axrange/planner/teardown.py`; any predicate/validator called for more than one entity kind where a clause is meaningful for only some of them
**Consumers:** code-writer, code-reviewer
**Rule:** When one predicate serves two entity kinds and a clause of it applies to only one, make the caller state the applicability as a **required keyword argument**. A default is the bug: it silently picks one kind's semantics for both, and the wrong answer is a *selection* (something quietly missing), not an exception. The deploy gate's single predicate applied its role-confidence clause to segments as well as nodes; a segment has no role, so raising `DEPLOY_MIN_ROLE_CONFIDENCE` above zero gated out every observed segment — and then hard-errored for having gated a segment out from under its own emitted nodes. Pair the required argument with a **producer-side refusal**: reject the inapplicable field where it is authored (`check_observation` refuses `role_confidence` on a segment block) rather than parsing and ignoring it, which is the inert-field shape LES-172 has retired four times.
**Origin:** CHANGELOG 2026-09-04 "Deploy gate BUILT" ruling 3 (code-writer deviation 1 from `docs/WORK_ORDER_deploy_gate.md` §4.2).

## LES-213 — Scope a new derived-drop/cascade rule to "the feature actually ran," or it moves the content hash of every config that never opted in

**Class:** bug-class
**Scope:** `axrange/twinconfig/expand.py`, `axrange/twinconfig/gate.py` (`DeployGateReport.active`); any new expansion-time rule that DROPS or ADDS IR content — cascades, pruning, auto-synthesis, defaulting
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A new rule that removes (or adds) IR content must be gated on evidence that the feature was opted into at all — "≥1 `observation:` block exists anywhere in this config" — not applied unconditionally. Otherwise it fires on hand-authored content that never asked for it and changes that content's `ir_hash`, hence its `plan_id`, hence its VMIDs, VM names, zone, vnets and underlay bridge, orphaning every standing deploy of it (LES-126). The same scoping also makes the regression pin *provable*: with the rule inactive, expansion is byte-identical by construction rather than by luck. Emit the feature's own metadata (`scenario.labels`) under the same condition, for the same reason. Pin it with three assertions, not one: the `plan_id` value, `"labels" not in scenario` (the mechanism, so a regression says WHY the hash moved), and byte-identity between the default and the strictest possible threshold.
**Origin:** CHANGELOG 2026-09-04 "Deploy gate BUILT" ruling 5; `tests/test_twinconfig_catalog.py` (six-config pin).

## LES-214 — Test a confirm-by-hash helper against the RE-PARSED persisted artifact, never the producer's own in-memory dict (extends LES-187)

**Class:** bug-class
**Scope:** `axrange/twinconfig/gate.py` (`confirm_deploy_gate`), `axrange/planner/dag.py` (`hash_ir`/`derive_plan_id`), `axrange/o2control/*` (`playout_hash`); any "recompute and compare to a recorded hash" confirmation
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A helper whose contract is "re-derive this artifact and confirm it reproduces a recorded hash" must be tested by re-loading the artifact from where it actually persists — the config file on disk, the state-store row, the deploy record — and hashing THAT. Feeding it the producer's own in-memory dict asserts `f(X) == f(X)`, which is true no matter how badly serialization, key ordering, float formatting or YAML round-tripping drift; it certifies nothing about the confirmation the scorecard depends on. Assert all three outcomes explicitly: confirms an unchanged config, **un**confirms after an edit (returns `None`, rendered as `UNCONFIRMED`, never a number nobody can stand behind), and confirms a `--param` override that was actually used. Corollary: normalize any value that joins the hash through ONE helper and pin its bytes (`str(0.1 + 0.7)` is `'0.7999999999999999'` and would mint a different `plan_id` than a literal `0.8` for the same intended threshold).
**Origin:** CHANGELOG 2026-09-04 "Deploy gate BUILT" (`confirm_deploy_gate`, work order §6). Extends LES-187.

## LES-215 — An emptiness cascade must distinguish "the feature emptied it" from "it never had occupants"; router-only transit and uplink segments have none by construction

**Class:** bug-class
**Scope:** `axrange/twinconfig/expand.py` (`occupied_before`/`occupied_after`), `axrange/twinconfig/gate.py`; any "drop the container when nothing is left in it" rule — segments, vnets, zones, groups, plans
**Consumers:** code-writer, code-reviewer, tester
**Rule:** "Nothing is in it now" and "the feature took everything out of it" are different predicates, and only the second justifies a drop. Compare the occupant set **before** and **after** the operation and drop iff before was non-empty and after is empty; never test emptiness outright. Structurally-empty containers are normal and load-bearing: a router↔router transit link and a WAN uplink segment have no host occupant *by construction* (the IP allocator already models exactly this case), so a bare emptiness test removed them at **every** threshold including 0.0, stranded their routers, and turned a threshold change into the topology break the router exemption exists to prevent. It was found only against shipped, hand-authored content — `dmz-perimeter` lost its `internet` uplink and `datacenter --param FABRIC=true` lost `fab-link` plus both fabric routers, from a single annotated host that gated nothing out — so re-test any new cascade against every shipped config, not just the fixture that motivated it.
**Origin:** the deploy-gate re-test, 2026-09-04 (tester finding); CHANGELOG 2026-09-04 "Deploy gate BUILT" ruling 2; `docs/WORK_ORDER_deploy_gate.md` §4.3 step 3 as amended.

## LES-216 — Reusing a node-selection helper's SHAPE means reusing its capability filter too; a payload change (ping → a Python script) silently invalidates the eligibility it encoded

**Class:** bug-class
**Scope:** `axrange/fidelity/runner.py` (probe peer selection), `axrange/o2control/taps.py` (`_select_verify_injector`); any "pick a guest on this segment and run something in it" selection reused for a new payload
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A selection helper's candidate rules (on the target's segment, prefer a leaf, know when the router is the only option) are only half its contract — the other half is the implicit "and this node can run the payload," which is invisible because the original payload was universally available. `_select_verify_injector` picks any guest with QGA because its payload is `ping`; the fidelity probe's payload is a stdlib **Python 3** script, so the same shape reused verbatim happily selected Windows guests — **28 of 45 targets** on the standing CorpAPCN twin — and would have aborted the whole run on the first missing interpreter. When you copy a selection shape, re-derive its capability filter against YOUR payload (here: filter to peers whose recorded `wait-os-ready` `os_family` is Linux) and state the filter in the helper's own signature or docstring so the next copy inherits it. Second half of the same rule: scope the resulting refusal to the **peer**, not the run — put the interpreter check and the render inside the per-peer `try` so one unusable guest produces `unmeasured` facts instead of destroying a document that was otherwise computable (LES-089's loud refusal, narrowed to the unit it actually applies to).
**Origin:** code-reviewer must-fix 1, fidelity-scorecard build; CHANGELOG 2026-09-05 "Fidelity scorecard BUILT"; `docs/WORK_ORDER_fidelity_scorecard.md` §3.3/§9.7. Extends LES-121 and LES-146 (both about the injector a topology forces on you) to the payload the injector must run.

## LES-217 — A deployment-scope exclusion must be applied to EVERY check that resolves against the deployable set, or the excluded entity reads as a reproduction failure — the exact inversion of the ruling

**Class:** bug-class
**Scope:** `axrange/fidelity/rows.py` (`observed_listen_facts`, rows 1/2/3/5), `axrange/twinconfig/gate.py` (`confirm_deploy_gate`); any scoring/diff/reconcile pass over a set some upstream gate deliberately narrowed
**Consumers:** code-writer, code-reviewer, tester
**Rule:** When a gate declares part of the reference population **out of deployment scope** ("this host is below the occupancy threshold, so we chose not to deploy it"), every downstream check that resolves reference facts against the deployed set must know about that gate — not just the one or two rows the ruling was written next to. A check that skips the filter resolves an out-of-scope entity to *no candidate* and reports **absence**, which is semantically "the twin failed to reproduce it" when the truth is "we deliberately did not ask it to." Row 5 was written against the observed listen set with no threshold filter while rows 1 and 2 had one, so a gated-out host's listen fact FAILED the twin. Enumerate the rows that touch the deployable set when the scope rule lands, and make the excluded-but-reported state (`below deploy threshold`) a value the row publishes rather than a branch it silently takes. Corollary: an **unconfirmed** threshold (`None`) applies *no* filter — a guessed threshold is worse than none.
**Origin:** code-reviewer must-fix 2, fidelity-scorecard build; CHANGELOG 2026-09-05 "Fidelity scorecard BUILT"; `docs/design/13-golden-twin-fidelity.md` §3.4; `docs/WORK_ORDER_fidelity_scorecard.md` §3.2/§9.5/§9.7.

## LES-218 — A gating check whose denominator can be zero must return `not-computed` WITH the denominator, never `pass`; and an unevaluable gate must never collapse into a failure verdict either

**Class:** silent-success-trap
**Scope:** `axrange/fidelity/rows.py` + `axrange/fidelity/schema.py` (`rollup_verdict`, `not-computed`); any pass/fail rule of the form "100% of the observed X are reproduced", any certification/coverage/scoring rollup
**Consumers:** code-writer, code-reviewer, tester
**Rule:** "100% of 0" is the classic vacuous pass, and it is most expensive in a *certification* artifact, where a green row is a claim someone will act on. Every ratio-shaped check must (a) publish its denominator beside the ratio, and (b) return a **`not-computed` verdict carrying a mandatory reason** when the denominator is empty — make the dataclass refuse construction without one, so the state cannot be produced by accident. Two corollaries that are easy to get backwards: `not-computed` is **not** a failure — roll it up to a distinct `incomplete` verdict, because "we could not evaluate this gate" and "this gate failed" prompt different actions, and no build may be certified while any gate is uncomputed; and the *filters* that shrink a denominator must be published with their reasons and counts too — an unexplained exclusion is as dishonest as a vacuous pass, and is how a denominator quietly becomes empty in the first place.
**Origin:** code-reviewer must-fix 3, fidelity-scorecard build; CHANGELOG 2026-09-05 "Fidelity scorecard BUILT"; `docs/WORK_ORDER_fidelity_scorecard.md` §4.1/§9.7. Extends LES-101 (a degenerate filter must never degrade to "match everything") to the scoring side: a degenerate denominator must never degrade to "pass".

## LES-219 — Attach a "this value was re-derived" warning to the DERIVATION, not to the absence of the artifact it substitutes for

**Class:** bug-class
**Scope:** `axrange/fidelity/reference.py` (`evidence_source`, the re-derivation warning); any fallback that computes a value an authoritative producer is *supposed* to supply
**Consumers:** code-writer, code-reviewer
**Rule:** When an authoritative artifact is missing and you fall back to computing its values yourself, it is tempting to emit the caveat from the "artifact missing" branch — one place, obviously correct today. It is wrong the day the artifact half-lands: the branch stops firing, the warning disappears, and the values still being re-derived (facts the artifact does not cover, fields it omits) are now published as if they were read from the producer — imputed-as-observed, the exact fabrication an evidence-class discipline exists to prevent. Emit the caveat from the code path that *performs* the derivation, keyed to the facts it produced, and let the artifact's presence be a separate, independently-reported field (`evidence_source: "sidecar fields" | "re-derived from topology export"`). Same shape as any "degraded mode" banner: bind it to the degradation, never to its cause.
**Origin:** code-reviewer must-fix 4, fidelity-scorecard build; CHANGELOG 2026-09-05 "Fidelity scorecard BUILT"; `docs/WORK_ORDER_fidelity_scorecard.md` §8.1/§9.7.

## LES-220 — A "below threshold" / "excluded" state name must be sourced from the mechanism that actually produced it; one label for two different situations is a lie a reader cannot see

**Class:** convention
**Scope:** `axrange/fidelity/rows.py` (`BELOW_THRESHOLD` vs `not_evaluated_below_bar`), `axrange/twinconfig/gate.py`; any published state naming why an entity was left out; `o2/console/src/edit/dependents.ts` (`removalConsequences`); any confirmation or summary that states one outcome for records of several kinds
**Consumers:** code-writer, code-reviewer
**Rule:** "This fact was not scored because it is below the bar" has (at least) two distinct causes that must not share a name: **a gate really ran and excluded it** at a recorded, confirmable threshold, versus **no gate exists on this subject at all** and the code applied the spec's default bar itself. Collapsing them into one string makes a reader believe a threshold was recorded when none was, which is precisely the guess `confirm_deploy_gate` was built to eliminate. Name each state after the mechanism that produced it (`below deploy threshold` when the gate is confirmed, `not_evaluated_below_bar` when the subject carries no gate), keep the strings as module constants so the document's vocabulary is greppable, and make the distinction visible in the published document rather than only in the code path.
**Extended 2026-09-15 (Console S5.1.a) — a per-KIND consequence.** Removing an added segment that is the only home of records staged on it first warned that the apply *would fail* for all of them. It would not: the compiler **refuses** a host whose segment is neither observed nor added (the apply fails), but reports a conduit to a segment the twin does not carry **ineffective and drops it** (the apply succeeds, without the conduit). One sentence for two outcomes is this entry's lie again — in a confirmation, where it shapes what the operator agrees to. When a message summarizes consequences across record kinds, state **each kind's own outcome, sourced from the mechanism that produces it**, one sentence per kind, and title the test as the contrast (*"a host FAILS the apply; a conduit is reported ineffective and dropped — never 'the apply would fail'"*).
**Origin:** code-writer deviation, fidelity-scorecard build; CHANGELOG 2026-09-05 "Fidelity scorecard BUILT"; `docs/WORK_ORDER_fidelity_scorecard.md` §9.7 ruling 3. **Extended by Console S5.1.a fix round 2 (SF2), 2026-09-15** — CHANGELOG 2026-09-15 "Console S5.1.a BUILT"; `docs/WORK_ORDER_console_canvas.md` §18.

## LES-221 — A hand-rolled binary protocol probe must be wire-tested against a canned responder; asserting on the rendered bytes proves only that the renderer agrees with itself

**Class:** pattern
**Scope:** `axrange/fidelity/probe.py` (dns/krb/ldap/cldap/smb/rdp/nbns/dhcp/dcerpc), `tests/test_fidelity_probe.py`; any hand-assembled protocol request (`struct.pack`-shaped) whose reply must be parsed
**Consumers:** code-writer, tester, code-reviewer
**Rule:** A `struct.pack`-built AS-REQ, SMB2 NEGOTIATE, X.224 CR, DHCP INFORM or EPM bind is exactly the code where a one-byte length field, an endianness flip or a missing NetBIOS session header is both easy to write and invisible to a render assertion — the test and the bug read the same spec incorrectly together. Stand up a **canned responder** (a local socket serving a recorded/synthesized valid reply, plus at least one malformed or wrong-protocol reply) and drive the real send/receive/parse path against it; assert that a valid reply parses to `pass`, a wrong-protocol reply to `fail`, and an unparseable one to `inconclusive` — never `pass`. Render-only assertions still belong in the suite for **containment** properties (every peer address a literal, no hostname reaching the guest), which is a different claim than "this handshake works".
**Origin:** code-writer note, fidelity-scorecard build; CHANGELOG 2026-09-05 "Fidelity scorecard BUILT"; `docs/WORK_ORDER_fidelity_scorecard.md` §3.3/§9.7.

## LES-222 — On a cross-repo file seam exactly ONE id-like field is the addressing id; publish the others and never resolve, compare, or cross-check against them

**Class:** convention
**Scope:** `axrange/fidelity/reference.py` (`bundle_id` vs `pit_id`), `axrange/api/imprints.py`; any reader of a producer-written bundle carrying several id-shaped fields
**Consumers:** code-writer, code-reviewer
**Rule:** A producer's bundle typically carries several id-shaped fields — a directory/file stem, a content-declared id, a point-in-time or run id, a code fingerprint — and only one of them addresses the artifact. Decide which, in the reader, and write it down: here `bundle_id` is the **directory name this lookup resolved by**, cross-checked against the content-declared `twin_config.id` (LES-195) and used for every derived filename; `pit_id` is **published and never cross-checked**, because by the producer's design it changes every capture and is not an address. Cross-checking a non-addressing id turns an ordinary re-imprint into a hard failure; treating it as the address makes the reader's output un-diffable across runs. Say which is which in the dataclass docstring, or the next reader will guess.
**Origin:** code-writer note, fidelity-scorecard build; CHANGELOG 2026-09-05 "Fidelity scorecard BUILT"; `docs/WORK_ORDER_fidelity_scorecard.md` §9.7 ruling 3. Companion to LES-195 (cross-check the content-declared id against the one the lookup resolved by) and LES-198 (pin the catalog, never a first-hit chain).

## LES-223 — A parametrized test's parameter list must be DERIVED from the artifact it covers, never hardcoded; a hardcoded list stays green while the artifact grows

**Class:** silent-success-trap
**Scope:** `tests/test_fidelity_probe.py` (`_all_methods()`, which reads the dispatch table out of `probe._SCRIPT_TEMPLATE`); any `@pytest.mark.parametrize` over a registry, catalog, enum, op set, rule set or driver table the product also enumerates
**Consumers:** tester, code-writer, code-reviewer
**Rule:** When the product owns a registry (`METHODS`, `_DRIVERS`, the op catalog, the rule catalog), parametrize over **the registry object itself**, not over a literal list that mirrors it. A mirrored list is correct exactly once: four new probe methods were added to `METHODS` and the hardcoded parametrization kept passing at full green while none of the four was ever exercised — the suite reported coverage it did not have, which is worse than a red test. Derive the ids from the artifact itself — here `_all_methods()` parses the `METHODS` dispatch table out of the rendered script template, so the parametrization tracks what the guest will actually run — and add a companion assertion pinning the registry's **key set** (every port-mapped method is dispatched; the only undispatched names are the two declared fallbacks), so a *removal* or a rename is caught too.
**Origin:** tester finding, fidelity-scorecard build; CHANGELOG 2026-09-05 "Fidelity scorecard BUILT"; `docs/WORK_ORDER_fidelity_scorecard.md` §9.7.

## LES-224 — A new per-host input on an EXISTING op must be emitted conditionally, or it retro-classifies every standing deploy's op as Mutated; and a fact that must change at runtime needs its own op, not a passenger seat

**Class:** bug-class
**Scope:** `axrange/planner/pass1_emit.py` (`clone-vm`'s `inputs_resolved`, `node_listen`/`node_role`/`node_kind`/`image_id`), `axrange/planner/plandiff.py` (`_diff_fields`), `axrange/planner/inventory.py`; any new key added to an op's persisted inputs on a product with a resume/`--update` diff
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Riding a new read-only fact on an existing per-host op is the cheapest way to publish it (the op is unconditional, so the inventory read covers the whole roster — LES-095), and it has a trap the suite cannot see: a standing deploy's persisted rows have **no such key**, and **ABSENT vs `None` is a difference** to a field diff. Emit the key unconditionally and every one of those ops classifies **Mutated**, so `te deploy --update` hard-refuses on 100% of already-deployed content — a feature that adds a field breaks the feature that adds a node. **Emit it only when it has a value**, and read it back defensively (`.get(...)` degrading to `None`), because a resumed deploy's `completed` op never re-runs and so never gains the key (LES-096). Then **state the residual cost at the line that creates it**, in the emitter's own comment and in the work order: with the conditional carrier, *changing* the fact on a standing twin still classifies Mutated, so the fact is a **deploy-time** one. That is acceptable for a declaration authored once (a listen set); it is not acceptable for anything an operator expects to swap on a running twin — which is precisely why `role` got its own op (`OP-TFC-020 assign-role`) instead of staying a `clone-vm` passenger. Decide which kind you have **before** choosing the seat.
**Origin:** `docs/WORK_ORDER_protocol_faithful_services.md` S1 build (`App.listen` → `node_listen`), 2026-09-05; CHANGELOG 2026-09-05 "Protocol-faithful services BUILT", work order §10.2 item 1. Same failure shape as LES-185, one build later, on the other side of the diff.

## LES-225 — A DECLARED fact published beside a MEASURED one must be barred from the verdict *and* from reading as a finding when the measured side is absent; handle all four present/absent cells explicitly

**Class:** bug-class
**Scope:** `axrange/fidelity/rows.py` (`declared_vs_observed_listen`, `Reproduced.declared_listen`, rows 4/5); any report that publishes a self-declared set beside an independently-measured one — declared vs discovered inventory, expected vs actual, config vs runtime
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Two rules, and the second is the one that gets missed. **(1) The declaration may not decide the verdict.** In a certification artifact the whole value is that the measurement is independent; letting "the config says it serves 53" satisfy "something answers on 53" reinstates exactly the substitution the measurement exists to remove. Publish it in the `measured`/informational block, never in a clause, and **pin it with a strip-and-compare test**: remove the declared keys from the output and assert every verdict is byte-identical. **(2) An absent input must be reported as an absence, never differenced into a finding.** Enumerate the (declared × measured) present/absent matrix — all four cells — and give each its own branch with a note naming *which* side is missing. The dangerous cell is *declared present, measured absent*: a naive set difference emits every declared item as "declared but never observed", a manufactured finding **that scales with how thoroughly the artifact was authored**, so the more careful the author, the worse the twin looks. Make every branch publish the **same key set** (empty lists where there is nothing to list) so the document stays diffable across runs.
**Origin:** `docs/WORK_ORDER_protocol_faithful_services.md` S1 build, 2026-09-05; CHANGELOG 2026-09-05 "Protocol-faithful services BUILT", work order §10.2 item 4. Extends LES-089 (name the absence after the mechanism) and LES-220 (one label for two situations is a lie) to the *comparison* case.

## LES-226 — `None` vs `[]` is a real contract only if `[]` is producible AND acts differently; otherwise refuse the empty value where it is written, or drop the distinction

**Class:** convention
**Scope:** `axrange/twinconfig/expand.py` (`_check_listen`, the `tap_points: []` precedent), `axrange/planner/inventory.py` (`HostInfo.listen`), `axrange/api/dtos.py`; any optional list/map field on an author-facing schema
**Consumers:** code-writer, code-reviewer
**Rule:** An `Optional[list[X]]` invites three states — absent, empty, populated — but a design usually only has two meanings: *"makes no claim"* and *"claims these"*. If no consumer behaves differently for `[]` than for absent, the empty value is not a third state, it is a **silent alias for the first**, and it will arrive from a half-finished edit or a generator that emitted a key before it had content. Pick one of two honest resolutions: **refuse `[]` at the author** with a message that says to omit the key (chosen here, following the `tap_points: []` precedent), or **normalize it to absent at the boundary** and document that there is no empty state. What you must not do is carry both and let downstream code test truthiness in some places and `is not None` in others. Say which value means what in the field's own docstring — "`None` when this host declares none, never `[]`" — because the next reader's default assumption will be that an empty list is meaningful.
**Origin:** `docs/WORK_ORDER_protocol_faithful_services.md` S1 build (review S6), 2026-09-05; CHANGELOG 2026-09-05 "Protocol-faithful services BUILT", work order §10.2 item 2.

## LES-227 — A generated artifact keyed COARSER than the fact it carries must over- or under-claim for some members; key it on the fact's own granularity

**Class:** bug-class
**Scope:** aximprint `src/aximprint/synth/services.py` (`serve-<node>.sh`, one per node group, not per role) and `synth/responder.py`; on this side `axrange/config_exec/render.py` and `axrange/personas/drivers/scripted.py`; any per-role / per-class / per-template artifact generated from per-instance evidence — provisioning scripts, config files, dashboards, rulesets
**Consumers:** code-writer, code-reviewer
**Rule:** Generating one artifact per *role* (or per class, template, group) from evidence collected per *instance* forces a choice no aggregation can escape: take the union and every member claims facts only some had, or take the intersection and some members lose facts they really had. Both **fabricate** — the union loudest: four hosts inferred as one role were observed answering on 1, 2 and 9 ports respectively, so a role-keyed server script would have made three of them serve six services nothing ever saw them serve, manufacturing exactly the evidence the work was removing. Key the artifact at the granularity the *fact* was observed at, and let the coarser grouping stay a naming/reuse convenience only. When the coarser key is forced on you (a template with one output), publish the per-member divergence as a caveat rather than smoothing it.
**Origin:** aximprint's half of `docs/WORK_ORDER_protocol_faithful_services.md` S2, 2026-09-05; CHANGELOG 2026-09-05 "Protocol-faithful services BUILT" (recorded in axRange because the same rule governs any per-role render here — `axrange/config_exec/render.py`, `axrange/personas/drivers/`).

## LES-228 — Resolve every requested field of a cross-repo seam against the PRODUCER's actual schema before agreeing to it; a spec sketch is a request, not an inventory

**Class:** dead-end
**Scope:** `docs/WORK_ORDER_protocol_faithful_services.md` §6.1 (`services[].windows_seen`, `observations.dns`), `axrange/fidelity/reference.py`, `axrange/fidelity/rows.py`, `axrange/api/imprints.py`; any consumer-authored JSON/YAML seam sketch a sibling repo or team is expected to fill; `o2/console/src/edit/allocate.ts` (`claimMessage`, `KEEPS_ONLY_WITH_A_HOST`), `docs/design/14-operator-assertions.md` §3's worked examples; any UI copy, doc or remedy string that promises what a sibling producer DOES with a record
**Consumers:** code-writer, code-reviewer, doc-writer
**Rule:** A consumer drafting a seam writes the fields it *wishes* existed, in a shape that reads exactly like the fields that *do* — and both sides then plan against it. Before the sketch is agreed, walk each field back to the producer's real schema and answer two questions: *does a column/table hold this today*, and *is its meaning the one the name implies at this granularity*. Two failures found in one sketch: a per-listen `windows_seen` that could not exist (the store holds a **host's** occupancy, not a **port's** — publishing it under the per-listen name would attach a plausible number to a fact nothing measured), and DNS rows specified `observed` when the capture saw a *name in an answer* and the honest class is `referenced`. Both had already been promised to a downstream consumer: a whole scorecard row's per-listen occupancy had **no producer**, discovered only when someone tried to build it. When a field turns out not to exist, **strike it in the sketch in place** and state where the consequence lands, rather than quietly building around it — the next reader will otherwise plan against the same fiction. Corollary: a producer must report the correction back rather than emitting a best-guess value under the requested name.
**Extended 2026-09-15 (Console S5.1.a) — the BEHAVIOUR half: promise a producer's effect only after running the producer.** A field sketch is not an inventory, and a design note's worked example or a producer's own remedy string is not an account of what the producer does. The Console's first note for an observed segment the cast does not declare said an `add: segment` *"restores that segment"* — resting on `14` §3's example (*"pruned by the cast … it is the OT lab"*). The reviewer ran aximprint's real `compile_twin`: an add of an observed CIDR only sets that segment's name and zone type, and the compiler keeps the segment only when a host is placed on it — which aximprint's reference-ingest S2 had already recorded ("confirms rather than creates"). The compiler's own conduit remedy (*"add them with `kind: segment`"*) makes the same wrong promise (GAP-093). Before copy, a doc or a remedy states what a record will *do*, run the producer on a case that exercises it; and when an illustration turns out wrong, correct it with a **dated note** rather than a silent rewrite, because other readers already planned against it.
**Origin:** `docs/WORK_ORDER_protocol_faithful_services.md` §6.1's two corrections, 2026-09-05; CHANGELOG 2026-09-05 "Protocol-faithful services BUILT", work order §10.2 item 8. Companion to LES-222 (decide which id on a seam is the addressing one) and LES-143 (join two sides in one vocabulary). **Extended by Console S5.1.a review (call 1), 2026-09-15** — CHANGELOG 2026-09-15 "Console S5.1.a BUILT"; `docs/WORK_ORDER_console_canvas.md` §18. Companion to LES-242 (run the real thing once).

## LES-229 — An open port is not a reproduced service; only a protocol-aware probe distinguishes them, and a connect-check will flatter the number

**Class:** silent-success-trap
**Scope:** `axrange/fidelity/probe.py`, `axrange/fidelity/rows.py` (row 5), `axrange/fidelity/runner.py`; aximprint `synth/responder.py` (the echo responder); any "is this service present / reproduced / healthy" check — a TCP connect, a UDP send with no ICMP unreachable, a port scan, an `ss -lnH` listen check, a `--healthcheck` exit code
**Consumers:** code-writer, code-reviewer, tester, live-verifier
**Rule:** A socket that accepts your bytes proves a **listener**, not a **service**. Score reproduction only on a **protocol-valid response you parsed**, and label every non-protocol-aware fallback (`connect`, `udp-connect`) as exactly that, structurally incapable of counting as a pass. Measured on scorecard run 1: the twin's echo responder answered on `123/udp` and `389/tcp+udp` by returning the probe's own bytes, and the probe correctly scored **zero** for all three because it read the reply — an NTP **mode 3** (the client's own request, not a server's mode 4) and an LDAP **`protocolOp` tag 0x63** (`searchRequest`, not `searchResEntry`). A connect-based check would have counted those three as reproduced and reported **8 of 31** instead of the honest **5 of 31** — a 60% inflation of the platform's headline fidelity row, produced entirely by asking a question whose answer cannot distinguish the two cases. The same asymmetry runs the other way and must be kept visible: a probe that could not *ask* (no peer, no interpreter, a timeout inside the harness) is `unmeasured`, which blocks a pass but never asserts absence. Corollary for producers: an echo responder standing in for a real daemon does not merely fail to help, it **manufactures the appearance of reproduction** for any weaker check downstream — which is why it now survives only where no real server exists and is labelled `compiler`.
**Origin:** scorecard run 1 (`dev45`, READ-ONLY, PASSED), 2026-09-05; CHANGELOG 2026-09-05 (later) "Scorecard run 1 RAN on `dev45`"; `docs/WORK_ORDER_fidelity_scorecard.md` §7.1. Same family as LES-001 (never trust a green op for capture) and LES-221 (wire-test a probe against a canned responder, don't assert on its render).

## LES-230 — A cross-repo file contract must be VENDORED and derived from, never restated by hand on both sides; and your own policy stays out of the vendored copy

**Class:** convention
**Scope:** `axrange/api/schemas/axr-assertions-1.schema.json`, `axrange/api/console_files.py` (the import-time closed-set derivation), `axrange/fidelity/reference.py`; any file format a sibling repo/team also parses — a schema, a manifest, an export, a bundle sidecar
**Consumers:** code-writer, code-reviewer, tester
**Rule:** When two repos read and write one file, the format has exactly one owner and everyone else has a **copy**. Restating the record kinds, the per-kind field sets and the enum values as literals in your validator makes the two definitions drift **silently and immediately** — the writer keeps passing its own tests while the reader refuses every file it produces. Do three things instead: **vendor** the owner's published artifact byte-identical into your tree; **derive** every closed set from it at import time so a stale copy cannot even load; and **arm a drift test** that resolves the sibling checkout (an env var with a conventional relative fallback) and **skips cleanly when it is absent**, so the check runs for whoever has both repos and never blocks whoever has one. Split the test in two: a **hard** one comparing the derived sets (a semantic break) and a **soft** one comparing bytes (a cosmetic re-publish). **Corollary, equally load-bearing:** rules that are *yours* — extra requirements the owner does not impose — live in **your code with their reason attached**, never edited into the vendored file, or the byte-compare stops meaning anything and the next person cannot tell the contract from your preference. Here three axRange-only rules (`imprint` required; `segment` and `name` required on an `add`) sit in `console_files.py` with a comment saying the compiler fails them at compile time anyway. And none of this substitutes for a **live round trip against the real reader** — that is what found the mismatch this rule exists to prevent (LES-242).
**Origin:** Console S1 build, 2026-09-05/06; CHANGELOG 2026-09-06 "Console S1 BUILT" ruling R1/R2; `docs/WORK_ORDER_console_canvas.md` §12. Round 1's hand-restated vocabulary wrote a `kind` key on every record and the compiler refused the file (`suppress[0]: unknown key(s) ['kind']`) — a break no unit test on either side could see.

## LES-231 — Two layers each applying "drop the nulls" with one exception between them lose the exception; a GET's output must survive its own PUT

**Class:** bug-class
**Scope:** `axrange/api/console_files.py` (the assertions serializer vs the DTO layer, `value: null` on an `override`); any read/write pair where a model layer and a serializer both prune empty values
**Consumers:** code-writer, code-reviewer, tester
**Rule:** "Omit `None` so the document stays clean" is a reasonable rule in a DTO layer and a reasonable rule in a YAML serializer, and applying it in **both** deletes any field whose *meaning is the null* — here an `override` whose authored `value` is deliberately `null`. The bug is invisible from either layer's tests, because each is individually correct. Two defenses, and use both: enumerate the fields whose null is **semantic** and exempt them **in one place** (the serializer, closest to the bytes), and pin the whole surface with a **round-trip test — GET a document, PUT it back unchanged, assert the file is byte-identical**. That single test catches every future instance of this shape (a dropped empty list, a coerced timestamp, a stripped key) without anyone having to predict which field it will be.
**Origin:** Console S1 build (review round 1 must-fix), 2026-09-05/06; CHANGELOG 2026-09-06 "Console S1 BUILT". Companion to LES-226 (`None` vs `[]` is a contract only if both are producible and act differently).

## LES-232 — A change/success signal over an artifact you did not produce must be a CONTENT identity you compute; a filename or the producer's own hash will lie in opposite directions

**Class:** silent-success-trap
**Scope:** `axrange/api/recompile.py` (`code_fingerprint` vs the sha256 content fingerprint; the post-apply bundle check), `axrange/fidelity/reference.py`; any "did this run change anything / did it work" check over a directory, bundle, export or generated tree
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Two cheap identities are both wrong, and they fail in **opposite** directions. **(1) Existence of an expected filename** reports success for a stage that did nothing: the apply's first stage writes *generic stems*, only the install stage names them `<id>.*`, and the pre-existing bundle already had `<id>.yaml` from an earlier install — so a name-keyed guard passed while the bundle was never updated. **(2) The producer's own fingerprint** reports *no change* forever: `code_fingerprint` is the compiler's **build** hash, identical for two different casts of the same code, so `changed` was permanently false and a keep-5 snapshot history was keep-1. Compute your own **content** identity — a hash over the named set of files whose bytes define the artifact — and use it for both the "did it change" answer and any history key. Publish the producer's fingerprint beside it (it answers a different question: *which compiler build*), and say in the field's docstring which is which (LES-222).
**Origin:** Console S1 build (tester's live integration, round 1), 2026-09-05/06; CHANGELOG 2026-09-06 "Console S1 BUILT" ruling R4; `docs/WORK_ORDER_console_canvas.md` §12.

## LES-233 — A test that globs a shared temp directory is asserting about the whole machine, not about its own run

**Class:** bug-class
**Scope:** `tests/test_api_console.py`, any test using `/tmp`, `glob("/tmp/axr-*")`, a fixed scratch path, or a shared cache directory to find files a code path wrote
**Consumers:** tester, code-writer, code-reviewer
**Rule:** Asserting on the contents of a shared directory couples a test to every other process, every previous run and every other developer on the box: it passes in isolation, fails in CI beside a sibling test, and — the worse direction — **passes for the wrong reason** by finding an artifact something else left behind. Give each test its own `tmp_path`, point the code under test at it explicitly (a settings field, an argument, a monkeypatched constant), and assert on **that directory only**. If the code path hardcodes a shared location, that is the finding: make the location injectable rather than teaching the test to glob.
**Origin:** Console S1 build (review round 2), 2026-09-05/06; CHANGELOG 2026-09-06 "Console S1 BUILT".

## LES-234 — A PID-named temp file is not unique across threads, and `mkstemp` + `replace` silently re-permissions the file it replaces

**Class:** bug-class
**Scope:** `axrange/api/console_files.py` (the atomic writer for `<id>.assertions.yaml` / `layout.json`); any atomic-write helper — `open(f"{path}.{os.getpid()}.tmp")`, `NamedTemporaryFile` + `os.replace`, config/state/snapshot writers
**Consumers:** code-writer, code-reviewer
**Rule:** Two independent traps in the same six lines. **(1) `os.getpid()` is not a unique token.** A sync FastAPI `def` runs in a threadpool, so two concurrent requests in one process pick the **same** temp name: one gets an `OSError` → 500, the other **silently overwrites the first's content** — the dangerous half, because it looks like a successful write. Use `tempfile.mkstemp(dir=<same directory>)`, which is unique per call and lands on the same filesystem so the `replace` stays atomic. **(2) `mkstemp` creates at 0600 by design**, and `os.replace` carries that mode onto the destination, so an atomic "write" **narrows an existing 0644 file to 0600** and the next reader — a different user, a service account, the compiler — gets a permission error on a file nobody edited. **`os.stat` the target first and re-apply its mode** (falling back to your documented default, 0644, when the file is new), and assert the mode in a test on both paths: new file and existing file.
**Origin:** Console S1 build (review round 1 must-fix and round 3 must-fix — the same helper, twice), 2026-09-05/06; CHANGELOG 2026-09-06 "Console S1 BUILT".

## LES-235 — A tolerant reader must publish what it skipped and why; one empty return standing for N distinguishable failures is a silence, not a default

**Class:** silent-success-trap
**Scope:** `axrange/api/imprints.py` (`list_imprints`' `skipped: [{id, reason}]`), `axrange/api/recompile.py` (`sensors_from_tap_plan`); any loader/lister/resolver that "degrades gracefully" — catalog scans, plan resolvers, optional-file readers
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Tolerance is a feature; **untraceable** tolerance is a defect wearing a feature's clothes. Two shapes of the same failure showed up in one build. **(1) A lister that skips unloadable entries** must publish a `skipped: [{id, reason}]` alongside the results — otherwise "the Console shows four bundles" and "there are four bundles" become indistinguishable, and a typo'd directory is reported as an absence. **(2) A resolver returning an empty collection for every degraded state** — file missing, unparseable, key absent, wrong type, empty list — collapses five situations an operator would act on differently into one silent success. Distinguish them: raise where the caller can act, or return a value that carries the reason. The test for both is the same question: *given this output, could the operator tell which of the possible worlds they are in?*
**Origin:** Console S1 build (D3(a)'s widening; review round 3 must-fix on `sensors_from_tap_plan`), 2026-09-05/06; CHANGELOG 2026-09-06 "Console S1 BUILT". Same family as LES-089 (name an absence after the mechanism) and LES-220 (one label for two situations is a lie).

## LES-236 — A path-traversal guard must be tested against its PERCENT-ENCODED form, or it is tested against the one input an attacker will not send

**Class:** security
**Scope:** `axrange/api/imprints.py` (`resolve_imprint_dir`, the one id regex `[A-Za-z0-9][A-Za-z0-9._-]*`), any route with a filesystem-resolving path parameter — `{imprint_id}`, `{deploy_id}`, `{config_id}`, `{image}`
**Consumers:** code-writer, code-reviewer, tester
**Rule:** `../` in a URL path is often normalized away before your handler ever sees it, so a guard tested only with `../../etc/passwd` may pass while `%2e%2e%2f` — and here plain `%2e`, which decodes to `.` — arrives at the handler intact and reaches the filesystem. Two rules together: state the guard as a **positive** id pattern (an allow-list regex, `[A-Za-z0-9][A-Za-z0-9._-]*`, anchored) rather than a blocklist of bad sequences, and put it in **exactly one** function every route resolves through — a second copy is a second thing to forget. Then test it **percent-encoded**: `%2e`, `%2e%2e%2f`, a double-encoded `%252e`, a bare `.`, an absolute path, and a name with a NUL. Here `%2e` reached a `PUT` and would have written the operator's file **into the catalog root**.
**Origin:** Console S1 build (review round 1 must-fix), 2026-09-05/06; CHANGELOG 2026-09-06 "Console S1 BUILT".

## LES-237 — A build's own invocation is part of its identity; record it in the artifact and refuse BY NAME when it is missing

**Class:** pattern
**Scope:** `axrange/api/recompile.py` (the `cast_invocation` block under `bundle_schema: axr-topology/1.5`, its 422); any generated artifact a later process must **regenerate** — casts, image builds, exports, reports
**Consumers:** code-writer, code-reviewer, doc-writer
**Rule:** If anything downstream will ever re-run the producer, the flags that produced the artifact are **part of the artifact**, not tribal knowledge in a runbook or a work order. Without them the re-runner must guess, and the guess is silently wrong in the most damaging way: omitting `--relocate 10.200.0.0/16` recompiles a bundle into a **different address space** while every file name and every count still looks right. Have the producer record its own argv (or the semantic subset of it) in the artifact, have the consumer read it from there, and when the block is **absent** return a **loud, named refusal** — "this bundle carries no `cast_invocation`; it predates the block and cannot be recompiled safely" — never a defaulted invocation. A guessed default here is worse than no feature.
**Origin:** Console S1 build, 2026-09-05/06; CHANGELOG 2026-09-06 "Console S1 BUILT" ruling R3; the producer half in aximprint (`axr-topology/1.5`).

## LES-238 — Bound an output stream AFTER you parse it; keep the head of a document and the tail of a log, and measure the cap against a real artifact

**Class:** bug-class
**Scope:** `axrange/api/recompile.py` (stdout parsed before bounding, head-kept; stderr tail-bounded); any subprocess/HTTP/log capture that truncates before handing the text on
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Truncating a subprocess's output before parsing it turns a *successful* run into a *reported failure* whose message points at the wrong thing: the real compiler prints **8356 characters** of JSON against an 8000-character cap, tail-kept, so the parser saw a fragment and emitted a plausible, entirely false "stdout was not JSON" warning. Three parts to the rule. **Parse first, bound second** — the cap exists to protect an HTTP response and a log line, not the parser. **Direction matters and differs by kind**: a *document* is head-anchored (its opening brace and its schema key are at the front), a *log* is tail-anchored (the error is at the end) — pick per stream and say which in a comment. And **measure the cap against a real artifact** before choosing it: any limit picked from a fixture is a limit picked from the smallest input the code will ever see.
**Origin:** Console S1 build (tester's live finding, round 3), 2026-09-05/06; CHANGELOG 2026-09-06 "Console S1 BUILT".

## LES-239 — A diff over one population cannot see an edit that moves the other; snapshot both sides, and never let the code explain a silence it cannot know the cause of

**Class:** bug-class
**Scope:** `axrange/api/recompile.py` (the `.history/<fp>.json` snapshot carrying BOTH the observed population and the twin's emitted population), `axrange/api/service.py` (`GET /api/imprints/{id}/diff`); any before/after comparison over a pipeline with an input population and an output population
**Consumers:** code-writer, code-reviewer, tester
**Rule:** When a change can move *either* of two populations — the observed input or the compiled output — a snapshot of one is structurally blind to half of all edits. A `suppress` record removes a host from the **twin**, leaving the **observed** side untouched, so an observed-only snapshot reported "no change" for an edit that had worked perfectly. Snapshot **both sides**, report them **separately** (an operator needs to know *which* population moved), and add a boolean like `twin_config_changed` so a consumer never has to infer it. The second half of the rule is subtler and was the actual review finding: the first fix emitted a warning **asserting the kind of change it could not know** ("the observed population is unchanged, so this is a compiler-side edit"). Code may report **what it compared and what it found**; it may not narrate a cause it has no evidence for. An honest `null` section plus "this side was not snapshotted" beats a confident sentence that is right half the time.
**Origin:** Console S1 build (tester's live finding, round 3), 2026-09-05/06; CHANGELOG 2026-09-06 "Console S1 BUILT"; the residual for the Console's S2 slice is recorded in `docs/WORK_ORDER_console_canvas.md` §12.

## LES-240 — A snapshot a later release will diff against needs a schema version from day one, and "cannot compare" must serialize as null, never as empty

**Class:** bug-class
**Scope:** `axrange/api/recompile.py` (`SNAPSHOT_SCHEMA = "axr-bundle-snapshot/1"`, `.history/<fp>.json`); any persisted state a **future** version of the same code reads — caches, histories, checkpoint files, `.state` sidecars
**Consumers:** code-writer, code-reviewer
**Rule:** A snapshot file is a wire format with one peer: your own next release. Unversioned, an older snapshot missing a section that a newer diff expects reads as **"that section was empty then"**, which the diff faithfully renders as *"everything in it was added"* — a fabricated change set presented with full confidence, and the operator's only clue is that it looks like far too much happened. Stamp `schema` in the file from the very first write, refuse or degrade explicitly on an unknown one, and make the degraded answer **`null` with a stated reason** — "this snapshot predates the twin-side section; it cannot be compared" — because `[]` is a *claim* about content and `null` is an *absence* of one. The same distinction the scorecard's verdict vocabulary rests on (LES-218), applied to a file.
**Origin:** Console S1 build (review round 3 must-fix), 2026-09-05/06; CHANGELOG 2026-09-06 "Console S1 BUILT".

## LES-241 — A count named like a collection is still a count; join on it and you are off by exactly what you did not know it excluded

**Class:** convention
**Scope:** the cast provenance sidecar's `population.emitted_hosts` (a number, 156) vs the twin config's node instances (157); `axrange/fidelity/reference.py`, `axrange/api/imprints.py`; any cross-repo field whose plural name suggests a list
**Consumers:** code-writer, code-reviewer, doc-writer
**Rule:** `emitted_hosts: 156` reads like a roster and is a **tally**, and the tally and the roster disagree — by one, the synthetic `gw` router the compiler adds and does not count as an emitted *host*. Two consequences worth internalizing: a plural field name is **not** evidence of a collection (check the type before writing the consumer), and a count published by the producer answers **the producer's question**, whose inclusion rules you do not have — so never cross-check it against a number you derived yourself unless the producer has documented what it counts. Name your own fields for what they hold (`emitted_host_count`), and when you must consume someone else's, record the observed discrepancy and its cause in the reader's docstring instead of "fixing" it with an off-by-one.
**Origin:** Console S1 build, 2026-09-05/06; CHANGELOG 2026-09-06 "Console S1 BUILT". Companion to LES-222 (decide which id on a seam addresses the artifact).

## LES-242 — A fake backend that reproduces the fix cannot detect its absence; put the injection seam BELOW the code under test, and run the real thing once

**Class:** silent-success-trap
**Scope:** `axrange/api/recompile.py` (`RecompileBackend` — the seam must sit at the *process* boundary, not at the two-stage orchestration), `tests/test_api_console.py`; any protocol/ABC seam standing in for an external tool, service or cluster
**Consumers:** tester, code-writer, code-reviewer
**Rule:** A test double placed **above** the logic you are testing *implements* that logic, so the suite verifies the double. Round 1's fake recompile backend returned an updated bundle — which is precisely what the real two-stage invocation was **failing** to do, because a cast alone writes generic stems and only the install stage names them. **81 unit tests were green against a fake that had silently performed the missing stage.** Place the seam at the lowest boundary that is genuinely external — here `subprocess` / the binary, not "the recompile" — so everything you actually own runs in the test. And because no placement is provably low enough, pair the suite with **one live round trip against the real tool** before calling a cross-process feature done: that run, not the 81 tests, found all four round-1 defects.
**Origin:** Console S1 build (tester's live integration vs. round 1's green unit suite), 2026-09-05/06; CHANGELOG 2026-09-06 "Console S1 BUILT". Same family as LES-001 (never trust a green op for capture) and LES-208 (inject every backend seam — this is the rule for *where*).

## LES-243 — "Check that a lock is held" and "take the lock" are different guarantees; write a test for each

**Class:** pattern
**Scope:** `axrange/api/recompile.py` / `axrange/api/app.py` (the per-imprint apply lock: `POST …/apply` **takes** it → 409 on a concurrent apply; `PUT …/assertions` **checks** it → 409 while an apply runs); any resource with a mutual-exclusion guard — leases, resource locks, per-deploy gates
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Two callers relate to one lock in two different ways and each needs its own test, because each fails silently in a different direction. The **taker** must be proven to *serialize*: two concurrent applies, one succeeds and one gets a named 409 — a test that merely calls apply twice sequentially proves nothing. The **checker** must be proven to *refuse while held and proceed when free*: a test that only covers "refused while held" leaves a permanently-stuck lock undetectable. Say in the code which role each caller has, and state the lock's **scope** as a limit where an operator will read it — this one is **process-local**, correct for a single `axrange serve` and silently insufficient for two (contrast the engine's DB-backed `resource_locks`, which are cross-process on purpose).
**Origin:** Console S1 build (review round 3), 2026-09-05/06; CHANGELOG 2026-09-06 "Console S1 BUILT" (the apply deviation from `docs/WORK_ORDER_console_canvas.md`'s `apply_id`+poll shape).

## LES-244 — A probe's expected output must be written against the real server's SECURITY POSTURE, not against the protocol's textbook reply

**Class:** bug-class
**Scope:** `twin-configs/svc-verify/README.md` (step 3's `smbclient -L` criterion), `axrange/fidelity/probe.py`, any runbook or scorecard row asserting on a real daemon's reply — SMB share enumeration, LDAP anonymous bind, SNMP public, HTTP directory listing
**Consumers:** live-verifier, tester, code-writer
**Rule:** A protocol's documented reply and a hardened server's reply are different documents. `smbclient -L //dc -N` against Windows Server 2022 returns an **accepted anonymous session with an empty share table** — anonymous enumeration is denied by default — so a criterion written as "a share list including NETLOGON and SYSVOL" reads as a FAIL on a perfectly working DC, and the operator's only ways out are to weaken the twin or to fudge the record. Write the criterion at the layer the probe actually proves — *"the anonymous session is accepted"*, with the authenticated listing as the separate, stronger check — and state the posture the expectation assumes. The same trap sits in every "the service should return X" line: name the server version and configuration the X came from.
**Origin:** services §9.2 live-verify (`dev45`, PASSED), 2026-09-05/06; CHANGELOG 2026-09-06 "Services §9.2 gated `dev45` live-verify PASSED". Companion to LES-229 (an open port is not a reproduced service — this is its inverse: a hardened service is not a broken one).

## LES-245 — The failure counter you added for diagnosis is also the positive control; read it on a GREEN run, and design it so zero is a claim

**Class:** pattern
**Scope:** `axrange/personas/drivers/` (the guest log's `ok=` / `fails=` / `missing_tool=` counters), `axrange/config_exec/render.py`; any generated in-guest agent, persona, activity or probe that logs its own outcomes
**Consumers:** live-verifier, tester, code-writer
**Rule:** A counter added so a failing run can be diagnosed earns its keep on the **succeeding** run, where it is the only thing separating *"the persona did 32 things correctly"* from *"the persona did nothing and nothing complained"* — the failure mode this whole platform's capture path is built to catch (LES-001). Its first real use here was as a **clean positive control**: `32 invocations across four cycles, fails=0 missing_tool=0`, which is a *measurement*, where a bare "the unit is active" is an assumption. So: emit an attempt count as well as a failure count (zero failures out of zero attempts is not success); name each distinguishable failure separately (`fails` vs `missing_tool` — a refused connection and an absent binary need different fixes); write the counters where a live-verifier will find them without shell archaeology; and put them in the runbook's expected output so reading them is a step, not a habit.
**Origin:** services §9.2 live-verify (`dev45`, PASSED) step 5, 2026-09-05/06; CHANGELOG 2026-09-06 "Services §9.2 gated `dev45` live-verify PASSED".

## LES-246 — Verifying one dependency does not establish independence; a claim that a step "does not need X" must enumerate its preconditions

**Class:** convention
**Scope:** `axrange/o2control/taps.py` (a tap plan needs the sensor's **state-store row**, not merely a live `gre-cap`), `docs/RUNBOOK_*`, `twin-configs/*/README.md`; any runbook step or work order asserting a component is optional or independent
**Consumers:** live-verifier, doc-writer, code-writer
**Rule:** "The tap does not need the standing surface — the sensor is already up" was true about the *wire* and false about the *state store*: `tap deploy` resolves the sensor from the **o2 deploy row in the same state DB**, so a live, capturing sensor whose row lives in another DB fails at resolve. Independence is not established by verifying the dependency you thought of; it is established by **enumerating the preconditions and checking each**. When a doc or a work order says "this step is independent of Y", make it list what it *does* require — the resource, the state row, the credential, the network path — and have the live-verify confirm each one, because the one nobody listed is the one that stops the run.
**Origin:** services §9.2 live-verify (`dev45`, PASSED) — the tap-resolve failure that opened the sensor saga, 2026-09-05/06; CHANGELOG 2026-09-06; `docs/PRODUCT_GAPS.md` GAP-069. Extends LES-177 (static validation OK never means the referenced resource exists).

## LES-247 — For a sensor, "exists" is not the check: **exists, running, and fresh** is — and a status verb that reports the VM cannot tell a dead capture from a live one

**Class:** silent-success-trap
**Scope:** `axrange/o2control/` (`o2 status`), `axrange/config_exec/render.py` (`deploy-sensor`'s per-type launch), any standing long-lived component whose value is a *stream* — sensors, shippers, exporters, watchdogs
**Consumers:** live-verifier, code-writer, code-reviewer, tester
**Rule:** The second clause to LES-177. For a standing sensor, three things must be true and a status verb that checks only the first is worse than none, because it is **believed**: the VM **exists**, the capture process is **running**, and its output is **fresh**. On `dev45` the standing `o2-suricata` was up and green for **40 days** with Suricata dead since 2026-07-27 — launched as a bare `-D` daemon rather than a transient systemd unit, so nothing restarted it and nothing noticed; `eve.json` was frozen **to the nanosecond** and only the Loki shipper still ran. Make the freshness check the cheap one everyone runs: newest-record timestamp, or a byte counter that must advance across two reads. And on the producing side, every long-lived in-guest process launches as a **named transient systemd unit with a fail-loud start check** — the 2026-07-17 fix did exactly this for tcpdump and zeek and **skipped Suricata**, which is how a per-tool launch path becomes a per-tool gap (`docs/PRODUCT_GAPS.md` GAP-067).
**Origin:** services §9.2 live-verify (`dev45`, PASSED) preflight, 2026-09-05/06; CHANGELOG 2026-09-06 "Services §9.2 gated `dev45` live-verify PASSED"; CHANGELOG 2026-07-17 (the supervision fix that never covered Suricata). Second clause to LES-177; same family as LES-001 and LES-079/LES-086.

## LES-248 — A flag verified against one image set is not verified for another: image roots are checked **per image**, never per root

**Class:** bug-class
**Scope:** `--images` on every deploy verb, `axrange/manifest/`, `axrange/adapter_proxmox/rest.py` (`clone-vm`'s `template_vmid`); any run that reuses a working invocation against different content
**Consumers:** live-verifier, code-writer, code-reviewer
**Rule:** LES-139 says `--images` is the directory *containing* `images/`. This is the layer above: a root that resolves **your twin's** images does not necessarily carry **the standing surface's** — the `deploy-dev45` root has every twin binding and no `suricata-sensor`, while the `axr-product` root the surface was actually deployed from has it. Reusing the invocation that worked ten minutes ago failed at `clone-vm` with `template_vmid missing`, **after five SDN ops had already run** and created real cluster state. Before pointing an existing `--images` root at a *different* spec, check that **each image that spec names** resolves under it (`ls <root>/images/bindings/proxmox/`), and treat a root as scoped to the content it was verified with, not as a machine-wide setting. Structurally, this is why an unresolvable binding must fail at **plan** time (`docs/PRODUCT_GAPS.md` GAP-072) — a per-image precondition checked only on the cluster is checked after the damage.
**Origin:** services §9.2 live-verify (`dev45`), the failed surface re-assert, 2026-09-05/06; CHANGELOG 2026-09-06; `docs/PRODUCT_GAPS.md` GAP-070/GAP-071/GAP-072. One layer up from LES-139.

## LES-249 — A baseline must be read from the store, not from a display that paginates; a default row limit will silently define your "before"

**Class:** silent-success-trap
**Scope:** `axrange/cli/main.py` (`list-deploys`, a 20-row default view), any CLI/API listing used to establish a before/after comparison — deploys, VMs, resources, op rows
**Consumers:** live-verifier, tester, doc-writer
**Rule:** A blast-radius or teardown proof is a **difference between two censuses**, and a census taken through a paginated display is a census of the page. `list-deploys` shows the newest 20 rows by default, so a baseline captured with it silently excludes everything older — and the teardown check that follows compares two windows rather than two populations, which is exactly wrong in the direction that hides a leak. Take the baseline from the **store** (a query, `--json`, or the cluster itself — `qm list | wc -l`), record the absolute number in the run log, and when a runbook prescribes a listing command, prescribe the unbounded form.
**Origin:** services §9.2 live-verify (`dev45`, PASSED) step 6, 2026-09-05/06; CHANGELOG 2026-09-06 "Services §9.2 gated `dev45` live-verify PASSED".

## LES-250 — An image-supplied listener belongs to the IMAGE, not to the twin; a listen-set exclusivity criterion must be stated against the image baseline

**Class:** convention
**Scope:** `docs/design/13-golden-twin-fidelity.md` rows 4/5, `axrange/fidelity/rows.py`, `docs/WORK_ORDER_protocol_faithful_services.md` §9.2 step 2, `twin-configs/*/README.md`; any "the host serves exactly X" check
**Consumers:** live-verifier, code-writer, code-reviewer, doc-writer
**Rule:** "The non-loopback listener set contains nothing beyond the declared set plus `tcp/22`" is unachievable on a stock cloud image, and the failure is not the twin's: Debian's `systemd-resolved` binds **LLMNR on `5355/tcp+udp` on the wildcard**, so a host that serves exactly what it declared still fails an exclusivity test. Exclusivity is only meaningful **relative to a baseline**: measure the image's own listener set once (the image build is the natural place), publish it as the baseline, and state the criterion as *declared ∪ image-baseline*. The two honest fixes are an allow-list carried with the image or a bake that disables the service; what does not work is hand-maintaining exceptions per twin, because the next image update moves them. Corollary for a fidelity row: never score a twin against a service it did not choose to run.
**Origin:** services §9.2 live-verify (`dev45`, PASSED) step 2, 2026-09-05/06; CHANGELOG 2026-09-06 "Services §9.2 gated `dev45` live-verify PASSED"; `docs/PRODUCT_GAPS.md` GAP-075.

## LES-251 — An etag learned from a REFUSAL is for a reload, never a retry; adopt it and the concurrency guard inverts

**Class:** silent-success-trap
**Scope:** `o2/console/src/edit/store.ts`, `axrange/api/console_files.py` (the content-hash etag), `axrange/api/service.py`; any optimistic-concurrency client — `If-Match`, a version column, a CAS loop
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A 412 hands back the server's current etag as **evidence of what the other writer wrote**. Storing it as the client's own etag and re-issuing the save is the one tempting move that turns **last-writer-refused** into **last-writer-wins** — the retry succeeds, the other writer's change is gone, and every concurrency test still passes, because the guard is still *there*, just satisfied with a value the client never read. The rule is *report, never adopt, reload before retry*: keep the pending edit intact, record the server's etag in the conflict for display only, and set a `mustReload` flag that makes a re-issued save impossible until a real re-read has happened. Write the test as "after a 412, a second save attempt is refused **by the client**" — a test that only asserts the 412 arrived proves nothing about what happens next.
**Origin:** Console S2 build (review round 2, must-fix 1), 2026-09-08; CHANGELOG 2026-09-08 "Console S2 BUILT"; `docs/WORK_ORDER_console_canvas.md` §13.4; `docs/design/15-console-canvas.md` §8. Companion to LES-243 (two callers, two relationships to one lock, two tests).

## LES-252 — A build tool's env PREFIX is a shared namespace: a variable the dev server reads may also be compiled into the client

**Class:** tool-gotcha
**Scope:** `o2/console/vite.config.ts`, `o2/console/src/api/client.ts`; any `VITE_` / `REACT_APP_` / `NEXT_PUBLIC_` variable; any build tool that lifts prefixed environment into a bundle
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Vite's `loadEnv` lifts **every** `VITE_`-prefixed process variable into `import.meta.env`, so one variable meant to configure the **dev server** (`VITE_AXRANGE_API`, the proxy's target) was also compiled into the **client** as its base URL — and every dev request then went cross-origin, past the very proxy the variable existed to configure, to an API that installs no CORS. Give the two roles two variables and say which is which where an operator will read it: an **unprefixed** name for config-time values that must never reach the browser (`AXRANGE_DEV_API`), the prefixed name only for what you intend to compile in. Then prove the negative — a test that **greps the built bundle** for what must not be in it (here the write token, which is kept in a module variable for the life of the tab and never in storage). A secret leaked this way is silent; a proxy bypassed this way is merely confusing, and both come from the same namespace.
**Origin:** Console S2 build (review round 2, must-fix 2), 2026-09-08; CHANGELOG 2026-09-08 "Console S2 BUILT"; `o2/console/README.md` "Configuration".

## LES-253 — A nullable data prop is THREE worlds — loading, failed, absent — and only the third is a fact about the artifact

**Class:** bug-class
**Scope:** `o2/console/src/views/`, `o2/console/src/api/hooks.ts`; any UI, report or CLI rendering an optional value fetched over a network; any `Optional[...]` DTO field turned into prose; **any error rendered inside a collapsible / hidden region** (a collapsed summary bar, a closed pane, a drawer)
**Consumers:** code-writer, code-reviewer, tester
**Rule:** `data == null` collapses *"not yet"*, *"the read failed"* and *"the server says there is none"* into one branch, and whatever sentence you write there is wrong in two of the three cases. Here a **failed** provenance read rendered as **"no sidecar on this bundle"** — an error presented as a measurement of the artifact, which is exactly the class of claim this project's fidelity work exists to keep honest. Model the three states explicitly, render the server's own reason in the failure state, and let **only** the absent state say anything about the thing being described. The wire half of this is LES-240 (`null` ≠ empty, with the reason in `warnings`); this is the render half, and they must agree.
**Extended 2026-09-12 (Console level-0 R1) — the DISCLOSURE case.** The three worlds are only as visible as the region they render in. Two rules follow, and the second is the one that bites: **(1)** when an error renders inside a collapsible region, **check both modes** — a failure state that is correct when expanded and invisible when collapsed is still a silent failure, and "collapsed by default" (R1 made the summary bar exactly that) turns the default into the broken case. **(2) Never gate an error's render on a different read's data** — R1's first cut drew the failure notice only when the *cast summary* had loaded, so the one situation where the notice matters most (both reads failing) rendered nothing at all. An error's visibility must depend on that error alone. This was the must-fix R1 closed.
**Origin:** Console S2 build (review round 2, must-fix 3), 2026-09-08; CHANGELOG 2026-09-08 "Console S2 BUILT". **Scope + disclosure case extended by Console level-0 R1, 2026-09-12** (CHANGELOG 2026-09-12).

## LES-254 — `Number()` is not a parser for an operator-facing value: parse strictly, and REPORT what you rejected

**Class:** silent-success-trap
**Scope:** `o2/console/src/model/zoom.ts` (the `?ghosts=`/`?class=`/`?occupancy_lt=` filters); any query-string, env-var or CLI value coerced with `Number()`/`parseInt`/`float()`; any filter whose fallback is "no filter"
**Consumers:** code-writer, code-reviewer, tester
**Rule:** `Number("+")` is `0`, `Number("")` is `0`, `Number("0x10")` is `16`, `Number(" 1 ")` is `1` — so a typo'd `?occupancy_lt=+` became a **silent zero threshold**, a filter that quietly changed what the operator was looking at and never said so. Use a strict parse that distinguishes **absent** from **present and unusable**, and surface every rejected value in a `problems` channel the operator can actually see instead of falling back to a default. Decide the neighbouring cases explicitly at the same time, because they fail the same silent way: a **duplicated** query key and a filter supplied at the **wrong level** were both being ignored without a word.
**Origin:** Console S2 build (tester round 3), 2026-09-08; CHANGELOG 2026-09-08 "Console S2 BUILT"; `docs/WORK_ORDER_console_canvas.md` §13.4.

## LES-255 — When the upstream artifact does not carry the identity, publish a SET DELTA and refuse a per-item column

**Class:** convention
**Scope:** `axrange/api/dtos.py` (`ImprintDiffDTO.suppressed_keys`), `axrange/api/recompile.py` (`suppressed_key_delta`); any diff that must join a consumer's **authored** id to a producer's **minted** id
**Consumers:** code-writer, code-reviewer, doc-writer
**Rule:** A column named like a join is read as a join. When the producer's rows do not carry the correspondence — the cast sidecar's suppressed rows are `{key, note, placed, matched}` and **never name the twin node a suppression removed**, and the twin's ids are minted by the *other* repo's grouping rule — a per-item `authored_key` column can only be filled by correlating what changed in the same interval, which is a guess wearing a schema, and it is wrong precisely when it matters (two suppressions and an unrelated removal in one apply). Publish the granularity the data actually supports (a **set delta** of the authored keys), keep the durable per-item authority where it needs no comparison at all (`population.suppressed[]` on the provenance route), **disclose the capability given up**, and distinguish *"the record matched nothing"* (`matched: false`) from *"the record removed something"* so an ineffective edit is never reported as applied. Reimplementing the other side's id-minting rule to recover the join is the tempting alternative and is worse: it is a second, silently divergent copy of someone else's contract.
**Origin:** Console S2 build, the §12.6 ruling (Authority: implementation (Claude Code)), 2026-09-08; CHANGELOG 2026-09-08 "Console S2 BUILT"; `docs/WORK_ORDER_console_canvas.md` §13.2. Same family as LES-241 (a count is not a collection) and LES-222 (which id addresses the artifact on a seam).

## LES-256 — A hashed-asset build states its cache policy in TWO lines, or a rebuild serves a blank page from cache

**Class:** convention
**Scope:** `axrange/api/app.py` (`_spa_cache_control`), `o2/console/`; any static mount or nginx site serving a Vite/webpack content-hashed build
**Consumers:** code-writer, code-reviewer, live-verifier
**Rule:** Content-hashed assets change name whenever their bytes change, so they may be cached forever (`public, max-age=31536000, immutable`); the **entry HTML names those hashes** and must never be (`Cache-Control: no-cache`, which means *revalidate*, not *do not store* — the 304 is still free). Write both lines in the same commit: with only the asset line, a browser holding yesterday's `index.html` requests a file the new build deleted and the app fails to boot with a **blank page and no server-side error**, which reads as "the deploy broke" for as long as it takes someone to open devtools. Two mechanical details that bite: the **directory-index** requests (`/`, any trailing slash) do not arrive as the literal string `index.html` and will miss a policy keyed on that name — which is exactly the request the policy exists for; and set the header only on a successful response, so a 404 keeps its own semantics.
**Origin:** Console S2 build, 2026-09-08; CHANGELOG 2026-09-08 "Console S2 BUILT"; `o2/console/README.md` "Known behaviors of the co-serve".

## LES-257 — A history fallback makes every miss look like a 200: exclude the API prefix on the DECODED path, allow-list asset extensions, and prove a refusal by asserting ABSENCE

**Class:** silent-success-trap
**Scope:** `axrange/api/app.py` (`_SpaFiles`, `_spa_wants_index`, `_SPA_ASSET_SUFFIXES`); any SPA `try_files $uri /index.html` mount sharing an origin with an API
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A history fallback is required the moment the URL is the application's state (a bookmark or a reload lands on a path that exists only in the client's router), and it converts *every* miss into `index.html` with a **200**. Three consequences must each be handled deliberately. **(1)** An unknown or unauthorized **API** path would answer HTML that the caller parses as JSON and misreports as something unrelated — exclude the API prefix, and test the exclusion on the **decoded** path, because `%61pi/...` decodes to `api/...`. **(2)** A **missing asset** would answer HTML with a 200 whose only symptom is a MIME-type error in the browser console — use an **extension allow-list**, never a "the last segment contains a dot" heuristic, because data-derived routes are full of dots (`…/segments/10.80.20.0-24` would 404 on every reload under that heuristic). **(3)** A **traversal test cannot be written as "expect 404"** — a refused `../../secret` and a served file both return 200 — so the only assertion that distinguishes them is that the **sibling file's content is absent from the body**. Finally, state the residuals rather than hiding them: an extension-less asset falls back, and a route segment ending in an asset extension does not.
**Origin:** Console S2 build, 2026-09-08; CHANGELOG 2026-09-08 "Console S2 BUILT"; `docs/design/15-console-canvas.md` §9.

## LES-258 — A hand-written cross-language mirror needs a field-set guard on the side that runs in CI — and its newest field is its least-verified

**Class:** pattern
**Scope:** `o2/console/src/api/types.ts` ↔ `axrange/api/dtos.py`, `tests/test_api_console.py`; any hand-written DTO mirror, vendored schema or constant duplicated across two languages or two repos
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Two artifacts describing one contract drift the instant either side is edited alone, and no type checker spans them. Pin the **field sets** in a test on the side that **runs in CI** (Python here, since a TypeScript suite may not run at all in a backend-only change), and make the failure message name the **other** file, so whoever breaks it is told where the fix goes rather than being handed a mysterious set difference. Then know what that guard does *not* prove: names, not values, and only the fields that existed when it was written. Pair it with a **live** test against a real server — opt-in and skipped without it, so a checkout without the server is not a failure — and treat the field you added today as the least-verified thing in the contract until that live run has exercised it.
**Origin:** Console S2 build, 2026-09-08; CHANGELOG 2026-09-08 "Console S2 BUILT". Companion to LES-230 (the vendored schema whose closed sets are derived at import, never restated) and LES-242 (run the real thing once).

## LES-259 — Isolation that lives in a framework key is untested by construction; pin it at the unit you own

**Class:** pattern
**Scope:** `o2/console/src/edit/store.ts`, `o2/console/src/App.tsx`; any React `key=`-driven remount used as a reset; any per-instance or per-tenant isolation delegated to a framework, `o2/console/src/api/hooks.ts` (`useResource`), `o2/console/src/views/useGraph.ts` (the graph hold); any data hook or cache whose value outlives a change of subject
**Consumers:** code-writer, code-reviewer, tester
**Rule:** *"A different imprint gets a fresh store because the component is keyed on the id"* is a claim about the framework's remount behavior, not about your code, and **no unit test of the store can exercise it** — under that design the store never sees a second id, so the property has no test and no defence against a refactor that moves, renames or drops the key. Restate the invariant as a precondition the owned unit **enforces**: the store checks the loaded document's `imprint_id` and refuses one that belongs elsewhere. Now the guarantee is a two-line test, it survives the key being dropped, and it also catches the case the key never could — a stale response arriving after the operator navigated away. General form: if an isolation property can only be demonstrated by the framework, move it into the unit whose tests you control.
**Extended 2026-09-15 (Console S5.1.a, MF1 — the must-fix) — isolation at the DATA hook, and a hold that made a one-render leak permanent.** The workspace was keyed by imprint, so the edit *store* was isolated — the data beneath it was not. `useResource` dropped the previous subject's value in an **effect**, and an effect runs after the render: the first render for imprint B still returned A's topology and cast, so a graph built from them under B's id existed for that render. Fix round 1 then added a hold (keep the last graph while an apply re-reads the cast) keyed on the **imprint id label** — the label matched, and the one-render mix became persistent: **A's tiles drawn under B's heading, and a gesture staging A's segments into B's file.** Two rules. **Bind a fetched value to the subject it was read for, at render time** — store `{subject, data}` and return `data` only when the current deps equal the stored subject; clearing in an effect always leaves one mixed render. **Key a hold or cache on the input objects it was derived from, never on an id label** a different subject can share. The owned-unit proof: render the hook for A, switch to B with B's read held open (LES-323), and assert that nothing of A is returned on B's **first** render.
**Origin:** Console S2 build (review round 2, should-fix; tester round 3 closed the load-side check), 2026-09-08; CHANGELOG 2026-09-08 "Console S2 BUILT". **Extended by Console S5.1.a fix round 2 (MF1), 2026-09-15** — CHANGELOG 2026-09-15 "Console S5.1.a BUILT"; `docs/WORK_ORDER_console_canvas.md` §18.

## LES-260 — jsdom hands a non-`file://` `import.meta.url`, so a filesystem test must opt into the node environment

**Class:** tool-gotcha
**Scope:** `o2/console/test/`, `o2/console/vitest.config.ts`; any Vitest/Jest file under a jsdom (or other browser) test environment that does `fileURLToPath(import.meta.url)`, `new URL('.', import.meta.url)`, or otherwise reads its own path off `import.meta.url`
**Consumers:** code-writer, tester
**Rule:** Under the jsdom test environment `import.meta.url` is **not** a `file://` URL (jsdom sets it to the document base, an `http://localhost/`-style value), so `fileURLToPath(import.meta.url)` throws `ERR_INVALID_URL_SCHEME` the instant the test does real filesystem work — reading a golden, resolving a fixture, globbing a directory. The whole file must opt into the node environment with a top-of-file `// @vitest-environment node` docblock comment (Vitest) — it is the *file* that switches, not one test — or the config must route these files to a node environment by path. A DOM-rendering test and a filesystem test therefore cannot share one file when the suite default is jsdom; split them. The failure is loud and immediate, not silent, but it reads as a URL bug rather than an environment one, which is where the time goes.
**Origin:** Console S3 build, 2026-09-09; CHANGELOG 2026-09-09 "Console S3 BUILT". Companion to LES-259 (Console S2 test discipline).

## LES-261 — A deterministic ordered-column layout is local only for COLUMN-PRESERVING edits; a column-count change reflows, and pins are the escape hatch

**Class:** pattern
**Scope:** `o2/console/src/layout/`; any deterministic, identity-keyed layout that packs elements into ordered columns/rows/lanes and claims an edit moves "only the edited element" (`docs/design/15-console-canvas.md` §4, `docs/WORK_ORDER_console_canvas.md` §5.2)
**Consumers:** code-writer, code-reviewer, tester
**Rule:** "The same input yields the same coordinates, and adding one element moves only that element" holds **only** while the edit is **column-preserving** — it adds/removes within an existing row grid and does not change the column (or row) *count*. An edit that adds a column (a new/promoted grouping key) or a row changes the total extent, so any width-centered or otherwise globally-anchored chrome recenters and every downstream column shifts — the locality claim is then an **overclaim**, and it will pass a golden test that only ever adds a tile to an existing row. State the boundary explicitly in the contract, decide the chrome anchoring deliberately (width-centered accepts the churn as *pins* territory; hard-left avoids it but looks broken over a wide row — pick and say which), and **golden-test a column-COUNT-changing edit as its own case**, not just a within-row add, so the reflow is a reviewed baseline rather than a surprise. The escape hatch for holding a layout stable across such an edit is user pins, not a stronger promise from the pure function.
**Origin:** Console S3 review (should-fix B), 2026-09-09; CHANGELOG 2026-09-09 "Console S3 BUILT"; `docs/WORK_ORDER_console_canvas.md` §14.4. Same family as LES-185 (the collapse band the layout must survive).

## LES-262 — Order an evidence/priority-class mix by the DEFINED precedence, never alphabetically — and test with ≥2 distinct classes

**Class:** bug-class
**Scope:** `o2/console/src/model/` (`evidence.ts`, the mix ring/dot); any place that reduces a multi-member set of a *ranked* enum (evidence classes, severities, log levels, tiers) to a single "dominant"/"worst"/"highest" representative
**Consumers:** code-writer, code-reviewer, tester
**Rule:** When an enum has a **defined precedence** that is not its alphabetical order, sorting or `min`/`max`-ing its string values picks the wrong representative — here the six evidence classes rank `asserted > imported > observed > referenced > imputed > compiler`, but alphabetically `imputed` sorts before `observed`, so a multi-class mix named the wrong "dominant" class in the swatch. Reduce through the **explicit** comparator (`precedenceOf`), never `[...set].sort()[0]` on the labels. And the test must use **≥2 distinct classes**: a mono-class fixture (everything `observed`, the common real-bundle case) never runs the comparison, so the bug ships green — pick the fixture to force the ordering path, exactly the class of gap a "typical data" fixture hides.
**Origin:** Console S3 review (should-fix A), 2026-09-09; CHANGELOG 2026-09-09 "Console S3 BUILT"; `docs/WORK_ORDER_console_canvas.md` §14.3. Same family as LES-255 (a set delta ordered by authored key) and the "mono-class fixture hides the sort" pattern in LES-259's neighbourhood.

## LES-263 — The id that drives a route/fetch/store-key must be the CANONICAL id threaded from the caller, never a producer-baked id

**Class:** bug-class
**Scope:** `o2/console/src/model/graph.ts` (`buildGraph`), `o2/console/src/views/` navigation (Level0/Level1 drill, outline links, search); any join/model layer that carries an id later used to build a route, a fetch URL, or a store key
**Consumers:** code-writer, code-reviewer, tester
**Rule:** The id that drives a route, a fetch, or a store-key must be the **canonical** id — the one the API/router is keyed on (here the bundle directory name = `twin_config.id` = the id in the page URL) — threaded in **from the caller** as a REQUIRED field with **no fallback** to a producer-baked id (a topology export's `imprint_id`/`pit_id`, a sidecar's embedded id). A baked id is display/provenance data only and must drive nothing. The trap: when the baked id and the canonical id happen to agree — the common self-consistent case — a fixture built from one bundle exercises neither path and the bug ships green; here two of three imprints had baked ≠ canonical and every drill-down 400/404'd, while the self-consistent goldens passed. **Test with a fixture where canonical ≠ baked**, and assert the built route/hit **contains the canonical id and NOT the baked one**. Same family as LES-262/LES-259 (a mono-value fixture certifies a bug green) and LES-195 (a catalog reader must publish the id ITS OWN lookup resolves by).
**Origin:** Console S3 post-ship drill-down id fix, 2026-09-09; CHANGELOG 2026-09-09 "Console S3 drill-down id fix"; `docs/WORK_ORDER_console_canvas.md` §14.6.

## LES-264 — A plain-locale `grep` over a UTF-8 file with multibyte comment glyphs silently returns ZERO matches — a symbol looks absent when present

**Class:** tool-gotcha
**Scope:** any `grep` over `o2/console/src/` (or any source tree) whose files carry multibyte comment glyphs such as `§`; `file(1)` classification of source files
**Consumers:** code-writer, code-reviewer, tester, live-verifier
**Rule:** A source file that is valid UTF-8 but contains a multibyte glyph in a comment (e.g. `§`) is classified by `file(1)` as `data`, and under a UTF-8 locale a plain `grep <pattern> <file>` treats it as binary — returning **zero matches with NO "Binary file … matches" line**, so a token that is present looks absent. Use `LC_ALL=C grep -a <pattern> <file>`, pipe the file in (`cat f | grep …`), or read it with the Read tool. This bit during the drill-down id fix: it hid the original `imprintId` assignment in `o2/console/src/model/graph.ts` from investigation. **Never conclude "the token isn't there" from a single plain-locale grep of a file that may hold non-ASCII content** — confirm with `LC_ALL=C grep -a` or a direct read first.
**Origin:** Console S3 drill-down id fix investigation, 2026-09-09; confirmed on `o2/console/src/model/graph.ts` (pre-existing condition).

## LES-265 — A stale Vite/Vitest cache can serve pre-fix source across a revert boundary and mask a regression

**Class:** tool-gotcha
**Scope:** `o2/console/` (Vite 8 / Vitest); any test run that must observe a source change across a pre-fix ⇄ post-fix boundary (a revert-proof of a regression test)
**Consumers:** tester, code-reviewer, live-verifier
**Rule:** A stale `node_modules/.vite` / `.vitest` transform cache can serve the **pre-fix** source across a pre/post-fix boundary, so a "revert-proven" regression test can pass against code that is no longer on disk — masking a real regression instead of catching it. When proving a fix by revert (run against pre-fix source → see it fail; restore → see it pass), **clear the cache between the two runs**, or ensure the runner's cache key is a source hash, so each run reflects the source on disk rather than a cached transform. Applies to any watch-mode/transform-cached JS test runner, not only Vitest.
**Origin:** Console S3 drill-down id fix review (environment observation), 2026-09-09.

## LES-266 — Re-derive a moved element's connectors by re-running the same pure layout primitive over the moved coordinates, not by translating cached geometry

**Class:** reuse-pattern
**Scope:** `o2/console/src/layout/` (`applyPins` re-anchoring lanes via `layoutLanes`); any deterministic layout where an override (a pin, a drag, a manual reposition) moves an element and connectors/annotations anchored to it must follow
**Consumers:** code-writer, code-reviewer, tester
**Rule:** When an override moves an element, the things anchored to it (a conduit lane's endpoint, a tie-line, a label leader) must move too — and the faithful way is to **re-run the same pure layout primitive that produced them, over the moved coordinates**, holding any independent axis fixed (here `layoutLanes(conduits, movedTileBoxes, laneBandTop)` — the moved tile boxes but the *same* band top), rather than translating the cached polylines the first pass emitted. Two properties fall out for free: a connector whose endpoint moved tracks it exactly (same routing rule, new input), and **a connector neither endpoint touched recomputes byte-identical** (same inputs → same output for a deterministic primitive), so locality is preserved and the no-override goldens are unchanged without a special case. Translating cached geometry instead would drift the moved connector (a translate is not a re-route) and risk perturbing untouched ones. General form: for a pure, deterministic layout, prefer *recompute from moved inputs* over *transform stored outputs* — the recompute is the reuse the module boundary already pays for.
**Origin:** Console S4 (pins) build, 2026-09-10; CHANGELOG 2026-09-10 "Console S4 BUILT"; `docs/WORK_ORDER_console_canvas.md` §15.2. Applies the reuse-direction principle of LES-025 to a specific layout-recompute case.

## LES-267 — Map SVG client→user space via `getScreenCTM().inverse()` and capture the scale ONCE at gesture start — a viewBox that grows mid-drag rescales a live CTM and the element accelerates

**Class:** bug-class
**Scope:** `o2/console/src/views/usePinDrag.ts` (`layoutScaleOf`, `draggedTopLeft`); any pointer-drag on an SVG whose element is `max-width:100%`/`preserveAspectRatio`-scaled, or whose viewBox can change during the gesture
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A pointer drag on a scaled SVG must convert the client-pixel delta into **user-space (viewBox) units**, and the conversion factor is the screen CTM (`getScreenCTM()`, inverted — `1/a`, `1/d` for the axis-aligned case; a boundingRect ÷ viewBox ratio as a fallback when no CTM is available under jsdom or a not-yet-laid-out SVG; identity as a last resort so the drag degrades rather than throwing). **Capture that scale ONCE at gesture start and reuse it for every delta.** Re-reading a live CTM per move drifts whenever the SVG's on-screen size or viewBox changes during the gesture — e.g. a `max-width:100%` element whose viewBox grows to contain the element being dragged: the CTM shrinks, the same client delta maps to a larger user delta, and the element **accelerates** away from the cursor. Corollary (LES-268): frame the viewBox to *committed* state, never to the live drag, so the growth that would feed this loop cannot happen mid-gesture. Make `draggedTopLeft(origin, down, client, scale)` and the scale reader pure and exported, so "the delta stays 1:1 in user units under a non-identity scale" is unit-tested without a browser.
**Origin:** Console S4 (pins) build (review should-fix S2), 2026-09-10; CHANGELOG 2026-09-10 "Console S4 BUILT"; `docs/WORK_ORDER_console_canvas.md` §15.3.

## LES-268 — Compute canvas/viewBox growth in the VIEW, never in the pure layout — so the pure output stays golden and the empty-override case is an exact no-op

**Class:** pattern
**Scope:** `o2/console/src/views/Level0.tsx` (`frameFor`) vs `o2/console/src/layout/`; any split between a pure, golden-tested layout function and a view that must occasionally enlarge the drawing surface (a viewBox, a canvas, a scroll region) to contain an overridden/pinned/dragged element
**Consumers:** code-writer, code-reviewer, tester
**Rule:** When a pure layout function is golden-tested and an override (pin/drag) can push an element past the pure canvas bounds, compute the enlarged frame in the **view**, off the layout's output, and **never fold it into the pure function** — otherwise the golden coordinates shift whenever the frame does, and the "no overrides == the pure function" invariant (which makes *reset* byte-identical and keeps the goldens valid) breaks. Frame to **committed** state (saved pins), not the in-progress gesture, both to keep the frame stable and to avoid the mid-gesture rescale of LES-267; accept that an element dragged past the current edge clips until the next commit grows the frame — a deliberate trade for a drag that never rescales itself. The pure layout returns identity-keyed coordinates; the view decides how much surface to show them on.
**Origin:** Console S4 (pins) build, 2026-09-10; CHANGELOG 2026-09-10 "Console S4 BUILT"; `docs/WORK_ORDER_console_canvas.md` §15.2. Companion to LES-267 (the mid-drag rescale this prevents) and LES-261 (keep the pure layout pure so pins are the only override channel).

## LES-269 — A post-drag click suppressor must be TIME-bounded, not "expected-click"-bounded — pointer capture routes the compat click to the capturing element, so a match-and-clear flag stays armed

**Class:** bug-class
**Scope:** `o2/console/src/views/usePinDrag.ts` (`isSuppressedClick`, the `SUPPRESS_WINDOW_MS` window); any drag gesture that uses `setPointerCapture` and must stop its own trailing synthetic `click` from also firing the element's click handler
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A drag that must not also fire a click (here: dragging a tile to pin it must not also drill into it) arms a suppressor on `pointerup`. Do **not** clear that suppressor on "the next click arrived" — because a drag uses `setPointerCapture`, and after `pointerup` the browser's **compatibility `click` is dispatched to the element that held the capture** (the `<svg>`), not the tile, in several browsers, so the tile's own click handler never runs and never clears the flag. A match-and-clear scheme therefore leaves the flag **armed indefinitely**, and it swallows a **later, genuine** click on that element. Bound the suppression by **time** instead (a short window comfortably covering the synthetic click, well under a deliberate second click), so it self-expires whether or not the expected click ever reaches the handler. The general trap: any "consume the next N events" guard that assumes the event will be delivered to the same node that armed it is wrong under pointer/focus capture — bound it by time or by an explicit end-of-gesture signal, not by the awaited follow-up.
**Origin:** Console S4 (pins) build (review should-fix S1), 2026-09-10; CHANGELOG 2026-09-10 "Console S4 BUILT"; `docs/WORK_ORDER_console_canvas.md` §15.3.

## LES-270 — A `*/` that falls out of markdown emphasis, a glob, or a `[*]` inside a `/* … */` block comment silently terminates the comment

**Class:** tool-gotcha
**Scope:** `o2/console/` (TS/JS/CSS, any `/* … */` block comment); any C-family block comment that quotes prose or code containing an adjacent `*` and `/` — markdown emphasis (`*saved*/*applied*`), a path glob (`src/*/x`), a regex, or a `[*]` index written next to a slash
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A `/* … */` block comment ends at the **first** `*/` byte pair anywhere inside it, and that pair is easy to introduce by accident when the comment quotes prose or code: markdown emphasis around a slash (`*saved*/*applied*` contains `*/`), a glob (`foo/*/bar` → `*/`), a `[*]` written before a `/`, or a regex fragment. The comment closes early, and the **tail of the intended comment becomes live source** — here `*saved*/*applied*` closed a doc comment and turned the rest into parse errors. The failure is loud (a compile/parse error) but points at the *tail*, not at the stray `*/`, so the time goes into reading the wrong line. Break the sequence deliberately: write `* /` with a space, wrap the fragment in a backtick or quotes, use a `//` line comment for prose that mentions slashes-next-to-stars, or spell emphasis without adjacent slashes. General trap: never let author-controlled prose or a quoted path land verbatim inside a block comment without scanning it for an accidental `*/`.
**Origin:** Console S5 (editing and save) build, 2026-09-10; CHANGELOG 2026-09-10 "Console S5 BUILT".

## LES-271 — A hand-mirrored client validator may be LOOSER than its server, never STRICTER — the divergence direction is the rule

**Class:** bug-class
**Scope:** `o2/console/src/edit/` (`records.ts`'s client pre-flight mirroring `axrange/api/console_files.py`); any client-side validator that hand-mirrors a server's accept/refuse rules to give fast feedback before a round trip
**Consumers:** code-writer, code-reviewer, tester
**Rule:** When a client hand-mirrors a server's validation to pre-flight an edit, the two rule sets **will** drift — and the *direction* of the drift decides whether the drift is recoverable. A client that is **looser** than the server sends a record the server refuses with its own message: annoying, but visible and recoverable. A client that is **stricter** than the server blocks a **legal** edit with **no round trip at all** — a silent UX loss the operator cannot diagnose, because the server that would have accepted it is never asked. So every client rule must be provably **≤** the server's (accept everything the server accepts; you may additionally reject some things the server also rejects, never something it accepts). Prove it with a **shared-input parity fixture** — one table of inputs run through both the client predicate and the server's, asserting the client never refuses what the server accepts. This complements LES-258 (the field-set of a hand-written DTO mirror drifts) with the **direction** rule for validation logic. (One deliberate, UI-unreachable exception is acceptable only when the form structurally cannot produce the stricter-refused input — S5's `{cpu: ""}`, N2 — and it must be named as the exception it is.)
**Origin:** Console S5 (editing and save) build, 2026-09-10; CHANGELOG 2026-09-10 "Console S5 BUILT"; `docs/WORK_ORDER_console_canvas.md` §16.1/§16.4. Complements LES-258 (DTO field-set drift).

## LES-272 — A "collect every entry" serializer turns a SCOPED write into a whole-store commit unless the caller filters by state first

**Class:** bug-class
**Scope:** `o2/console/src/edit/` (`documentFrom`, the removal path); any code that builds a persisted document by serializing "all entries in the store" and then PUTs/writes it for a scoped operation (a single removal, a single-field save)
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A serializer that walks the **whole** store (`documentFrom` → every entry) is correct for a full save but **wrong** for a scoped write: routing a scoped operation (removing one saved record) through it silently commits **every** entry the store holds — including *pending*, browser-only edits that were never meant to be persisted — collapsing the pending/saved boundary. The write boundary lives at the **call site**, not in the serializer: the caller must filter to exactly the entries the operation is allowed to persist (here, **saved + applied** only, excluding *pending*) before handing them to the serializer. A general rule: a "give me the document for X" helper that ignores X and returns the document for *everything* is a scope leak waiting for a caller that isn't a full save. Test the scoped path directly — stage a pending edit, do the scoped write, assert the pending edit did **not** land in the written document.
**Origin:** Console S5 (editing and save) build (review should-fix S1), 2026-09-10; CHANGELOG 2026-09-10 "Console S5 BUILT"; `docs/WORK_ORDER_console_canvas.md` §16.3. Same "pending = browser only" boundary as `docs/design/15-console-canvas.md` §8.

## LES-273 — A "could not compare" signal must reach the outcome-rendering branch, or "don't know" collapses into "no"

**Class:** silent-success-trap
**Scope:** `o2/console/src/` (the apply-outcome render consuming `useApply`/`getDiff`); any consumer that turns an already-fetched "did X change" result into a highlight-or-not / act-or-not decision; any producer that can report "changed" without being able to say what changed
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A producer that can say "changed: true" while being unable to say **what** changed (here: `changed: true` paired with `history_written: false` in the response's own `warnings`, or a downstream diff read that itself fails) is emitting a **third** outcome — not "no change" and not "here is the change" — and the qualifying signal almost always lives on a field the happy-path render never reads. A consumer that reads only that happy-path field collapses "I could not tell you" into the exact same silent, no-highlight render as a genuine no-op — the operator sees nothing move either way and cannot tell a real no-op from a check the tool simply could not perform. Read the qualifying field explicitly and render a third, honest outcome (carrying the producer's own reason) everywhere a "compared, no difference" outcome is rendered. This is the same family as LES-240 (the wire contract: `null` ≠ empty, with the reason in `warnings`) and LES-253 (the render contract: loading / failed / absent are three worlds) — surfacing a third time, at the point an already-fetched comparison result becomes a UI decision. Any time you write "if (changed) highlight() else nothing()", ask what a `changed: true, diff: unknown` value would do.
**Origin:** Console S6 (apply and refresh) build (review must-fix), 2026-09-10; CHANGELOG 2026-09-10 "Console S6 BUILT"; `docs/WORK_ORDER_console_canvas.md` §17.2. Same family as LES-240, LES-253. **Example:** GAP-087's `compiler_changed` render needed a guard for exactly this: an absent field (`undefined`, an older server that predates the verdict) falls through to "compiler changed" unless it is explicitly folded into the same branch as `false` — fixed by testing for `=== true` alone and treating `undefined`/`false` as one "nothing to say" branch (CHANGELOG 2026-09-19).

## LES-274 — An async write-flow's stale-continuation guard must gate only its side-effecting tail call, not the whole continuation

**Class:** bug-class
**Scope:** `o2/console/src/` (the apply continuation's `go(...)` navigation); any SPA mutation flow with no `AbortSignal` that performs a shell-level side effect (navigation, writing a global store) after an `await`, where the operator can act again before the await resolves
**Consumers:** code-writer, code-reviewer, tester
**Rule:** An async write hook typically has no `AbortSignal` to cancel its in-flight request, so "the operator moved on before this resolved" cannot be handled by aborting the request — it has to be handled by not *acting* on a stale result once it arrives. The guard belongs on the **side-effecting tail call only** (here, the trailing `go(...)` navigation), never wrapped around the whole continuation: a state refresh (reloading cast/provenance/assertions/diff) is harmless and should still land wherever the operator now is, while force-navigating them back to where they issued the mutation from is the actual bug. Build the guard as a **captured-at-start vs read-live** comparison — a ref snapshotting the identity that must stay current (e.g. the path the operator was on when the action started), checked immediately before the one side-effecting call — never a boolean flag that suppresses the entire continuation, which throws away the harmless half along with the disruptive half. Scope the guard to the one call whose effect is visible and disruptive if stale; let everything else in the continuation run unconditionally.
**Origin:** Console S6 (apply and refresh) build (review should-fix), 2026-09-10; CHANGELOG 2026-09-10 "Console S6 BUILT"; `docs/WORK_ORDER_console_canvas.md` §17.3.

## LES-275 — A shared preparation step fixed in ONE caller is not fixed: resolve a materialized document's relative paths at one shared helper, or the second caller keeps the bug

**Class:** bug-class
**Scope:** `axrange/twinconfig/registry.py` (`twin_config_source_dir` / `resolve_twin_config_path`), `axrange/api/service.py` (the `config:` and `imprint:` resolvers), `axrange/cli/main.py`; any pipeline that **materializes** a document (expand a template, render a temp file) and then resolves that document's own **relative** references
**Consumers:** code-writer, code-reviewer, tester, live-verifier
**Rule:** When a document is expanded into a temp file before use, every relative path inside it (`scripts/bootstrap.sh`, `personas/`, an include) is silently re-based on the **temp directory**, where nothing exists. The fix — resolve those paths against the document's **source** directory — is one step, and the trap is that it gets made **at the first caller that hits it** and stays there: axRange's CLI had set `scenario_source_dir` from the catalog path for months while **both** API resolvers re-derived the path from the tempfile, so `POST /api/deploys {"config": …}` and the imprint "deploy as twin" action 400'd at plan build on **every real cast bundle** — the same class as LES-037 (a fix applied on one path of a two-path feature). Two rules follow: (1) the resolution belongs in **one exported helper** (`twin_config_source_dir(config_id)` → the resolved, symlink-followed parent of the file the loader reads) that every caller calls, never a line copied into each; (2) the test that pins it must run the **whole** step (build a plan from real content that actually carries a relative reference), because a unit test on synthetic content with no relative paths passes under both behaviours. General form: when you find a caller doing a preparation step locally, look for the *other* callers of the same pipeline before calling it fixed.
**Origin:** Console level-0 R5+R7 build (review must-fix M2), 2026-09-11; CHANGELOG 2026-09-11 "Console level-0 R5 + R7 BUILT". Same family as LES-037.

## LES-276 — Lifting a refusal exposes whatever sat behind it — prove the newly reachable path on REAL content, in the same slice

**Class:** verification-idiom
**Scope:** any change that relaxes a guard, tolerates a previously-refused input, or flips a feature on (`axrange/api/imprints.py` R7's canonical-id tolerance); any "unblocking only" slice
**Consumers:** code-writer, code-reviewer, tester, live-verifier
**Rule:** A refusal is a **cover**: while it holds, everything downstream of it is unexecuted, so its defects are unobserved and its tests are unwritten. The moment the refusal is relaxed the downstream becomes reachable — and the first real request is what finds the next failure, not the suite. R7 lifted a 400 on `GET /api/imprints/{id}` and immediately exposed a plan-build failure (LES-275) that had made "deploy as twin" dead on every real cast bundle, invisible for as long as the id check refused first. So an unblocking change is **not done when the refusal is gone**: walk the newly reachable path end to end against **real, shipped content** (here: a plan preview of all three real bundles — 695 / 1077 / 1227 ops — plus the ordinary `config:` path at 72), and pin those numbers. A synthetic fixture will not do it, because a fixture is usually built to the shape the refusal allowed. Corollary for reviewers: when a diff removes or weakens a check, ask "what runs now that never ran?" and require evidence for it.
**Origin:** Console level-0 R7 (GAP-082) build, 2026-09-11; CHANGELOG 2026-09-11 "Console level-0 R5 + R7 BUILT".

## LES-277 — A placeholder plus a qualifier field is honest only to consumers that READ the qualifier — enumerate the route's consumers before you publish a zero

**Class:** silent-success-trap
**Scope:** `axrange/api/imprints.py` (the summary/provenance routes' zero-valued fields beside `sidecar_shape`/`evidence_source`/`warnings`), `o2/grafana/plugins/` (the Track C imprints screen); any API that replaces a refusal with a tolerant default plus a "here is what I actually measured" qualifier
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Turning a refusal into "a default value **plus** a qualifier field" is the right wire contract (LES-240) — and it is honest **only** to a consumer that reads the qualifier. Every consumer that renders the value alone now shows an **unmeasured** quantity as a **measured** one: after the GAP-082 relaxation the Grafana imprints screen renders Services/Personas/Out-of-vocab as a confident `0` and Mean confidence as `0.00` for bundles whose sidecar is absent, canonical or unrecognized — strictly **worse** than the error it used to show, because an error is legible and a zero is a claim. So before publishing a tolerant default, **enumerate the route's consumers** (every SPA, plugin, CLI and test that reads the field) and either update each to read the qualifier, or keep the refusal for that shape, or register the regression as a gap with an end date. A zero with no reader for its qualifier is a lie with a footnote.
**Origin:** Console level-0 R7 build, 2026-09-11; `docs/PRODUCT_GAPS.md` GAP-083; CHANGELOG 2026-09-11. Same family as LES-240 / LES-253 (don't-know must not render as no). **Mirror: LES-342** (a placeholder in an *identifier* field, which a picker turns into write-path data) — and GAP-083 itself was closed 2026-09-16 by deleting the screen (Console S7, decision D5).

## LES-278 — Classify every id-shaped field by ONE question — may a legitimate producer write a value that differs from the addressing id? — then refuse the "no" set and warn on the "yes" set

**Class:** pattern
**Scope:** `axrange/api/imprints.py` (`_check_addressing_id`, `_sidecar_id_check`, `_export_id_warnings`); any reader of a multi-file bundle whose artifacts each carry their own id (a cast bundle's twin config + provenance sidecar + topology export)
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A bundle assembled by several producers carries several id-shaped fields, and treating them uniformly is wrong in both directions: refuse them all and a legitimate producer difference 400s every real bundle (GAP-082 — a cast export bakes the **observed capture's** id, which is legitimately not the directory name); tolerate them all and a copy-pasted sidecar serves one imprint's rows under another's URL (LES-195's original case). Classify each field by exactly one question — *can any legitimate producer write a value different from the addressing id?* — and let the answer decide: **no → refuse** (the directory name IS the addressing id; `twin_config.id` must equal it; a **normalized** sidecar's `imprint_id` must equal it, and a **missing** one only warns because it contradicts nothing); **yes → publish and warn, never compare** (the export's `imprint_id`, any `pit_id`). Write the classification **in the module docstring, field by field, with the reason** — it is the artifact the next reader needs, and it reconciles LES-195 with LES-222 instead of leaving them looking contradictory.
**Origin:** Console level-0 R7 (GAP-082, decision D3 "tolerate on read", Authority: user), 2026-09-11; `axrange/api/imprints.py` module docstring; CHANGELOG 2026-09-11. Reconciles LES-195 and LES-222.

## LES-279 — `UnicodeDecodeError` is a `ValueError`, not a `JSONDecodeError` — and a JSON parse that SUCCEEDS can still hand you `Infinity` or a 400-digit integer

**Class:** platform-gotcha
**Scope:** any `json.load`/`json.loads` boundary that maps a parse failure to a typed refusal (`axrange/api/imprints.py`, `axrange/api/topology_export.py`, `axrange/fidelity/reference.py`, `axrange/jsonnum.py`)
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Two assumptions fail at the same boundary. (1) **Catching `json.JSONDecodeError` is not catching "this file is not JSON"** — a file that is not valid UTF-8 raises `UnicodeDecodeError` *before* the parser runs, and it is a `ValueError`, not a `JSONDecodeError`, so a reader that catches only the latter turns a corrupt artifact into a 500. Catch `ValueError` (or `(UnicodeDecodeError, json.JSONDecodeError)` explicitly) at the read boundary, and say which file. (2) **A successful parse is not a safe value** — Python's `json` accepts `NaN`, `Infinity` and `-Infinity` by default, and parses integer literals of arbitrary precision, so `1e400` becomes an infinite float (which re-serializes as `null`) and a 400-digit literal becomes an exact `int` a browser rounds. Validate every number you re-publish (LES-280), and remember that the shape check and the value check are two different jobs at one line of code.
**Origin:** Console level-0 R5+R7 build (review round 2), 2026-09-11; `axrange/jsonnum.py` module docstring; CHANGELOG 2026-09-11.

## LES-280 — Bound a republished number to the CONSUMER's range and REFUSE what falls outside, naming the field — never clamp

**Class:** pattern
**Scope:** `axrange/jsonnum.py` (`whole_number` / `optional_whole_number` / `optional_finite_number`, `MAX_WHOLE = 2**53 - 1`); any value read from an external artifact and republished over JSON to a JavaScript consumer
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A number a reader takes from someone else's artifact and publishes must satisfy the **consumer's** range, not Python's: a whole number is a JSON number in `0 .. 2**53 - 1` (JavaScript's `Number.MAX_SAFE_INTEGER`) and a float is finite. Outside that, **refuse and name the field** — never clamp to the bound, never parse a number out of a string, never silently coerce. Clamping invents a fact the artifact does not contain and, worse, invents a *plausible* one: a `windows` clamped to `2**53-1` reads as a real measurement, while an `Infinity` at least announces itself (as the Console drawing every host as a ghost, which is how this was found). What the refusal **becomes** is the caller's call, and both answers are legitimate: a strict reader returns 400 naming the field; a tolerant one publishes `null` and states the problem. Put the rule in **one module** — three drifting copies of it (one accepting any-size ints, one any `float()`-able string, none with an upper bound) is what preceded this.
**Origin:** Console level-0 R5+R7 build (review round 2, SF2), 2026-09-11; CHANGELOG 2026-09-11. Companion to LES-279 (why an unbounded value arrives at all) and LES-240 (`null` + a stated reason).

## LES-281 — Detect a document's SHAPE first, then enforce that shape's preconditions — a precondition applied to the wrong shape refuses every valid document

**Class:** bug-class
**Scope:** `axrange/api/imprints.py` (`sidecar_shape`: canonical / normalized / **unrecognized** / absent, and the per-shape checks behind it); any reader of a file format with more than one legal shape (a versioned schema, a producer-vs-fixture variant)
**Consumers:** code-writer, code-reviewer, tester
**Rule:** When one reader serves several legal document shapes, the order is **detect, then enforce** — and getting it backwards produces the most confusing failure class there is: a precondition that belongs to shape B applied to a shape-A document, refusing **100 % of valid A documents** with a message about a field A never had. GAP-082 was exactly this: the normalized sidecar's `imprint_id` check ran against **canonical** sidecars, which declare no imprint id, so every real cast bundle 400'd with `provenance.imprint_id=None`. Detect the shape from a field the shapes do not share (the `schema` string), publish which shape was read (`sidecar_shape`) so a consumer never infers it from the presence of a warning, and give "present but not one I consume" its **own** name (`unrecognized`) distinct from "absent" — those are different operator situations and collapsing them hides a producer upgrade.
**Origin:** Console level-0 R7 (GAP-082) build, 2026-09-11; CHANGELOG 2026-09-11. The `unrecognized`-vs-`absent` half is the LES-219 family (say what you measured, don't make the consumer infer it).

## LES-282 — One guard on every route multiplies its cost — cache the parsed artifact at its SINGLE loader, and make the guard return what it parsed instead of just a path

**Class:** perf-characteristic
**Scope:** `axrange/api/imprints.py` (`_load_twin_config`'s parse cache; `_addressable_bundle` / `check_imprint_addressable`); any per-request precondition that reads and parses a file on a route family that includes hot routes
**Consumers:** code-writer, code-reviewer, tester, live-verifier
**Rule:** A correctness guard placed on *every* route of a family inherits the **hottest** route's call rate, so a cost that is negligible once per page load becomes the page's dominant cost: parsing the pit bundle's twin config is **215 ms**, and adding the addressing guard to `/cast`, `/provenance`, `/topology`, `/assertions`, `/layout`, apply and diff meant paying it on **every pin drop**. Two fixes, both structural: (1) **cache the parse at the one loader** every path goes through (`GET /layout` 235.6 ms → **3.29 ms** warm, ~70×; opening the Console 2522 ms / 8 parses → **601 ms / 0**; the cold first parse per bundle per process is unchanged at ~316 ms, and saying so is part of the report); (2) **have the guard return the artifact it parsed**, not merely a validated path — a guard that returns a `Path` forces every caller to re-open and re-parse the same file, which is how the cost got multiplied in the first place. Measure before and after on the **real** bundle: a guard's cost is invisible on a 20-node fixture.
**Origin:** Console level-0 R7 build (review round 2, performance), 2026-09-11; CHANGELOG 2026-09-11.

## LES-283 — A file-backed cache key is (resolved path, inode, size, mtime_ns, ctime_ns) with the stat taken BEFORE the read — cache no failures, and name the residual you cannot close

**Class:** pattern
**Scope:** `axrange/api/imprints.py` (`_load_twin_config`'s cache); any memoized read of a file an operator or another process can rewrite under a long-lived server
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A cache over a file that a different process writes (an operator's editor, a recompile, `install-cast`) must key on **identity + every cheap change signal**: the **resolved** path (so two symlinks to one file share an entry and a moved symlink does not), plus `st_ino`, `st_size`, `st_mtime_ns` **and** `st_ctime_ns` (ctime catches an in-place rewrite that restores mtime). **Stat before the read**, never after, or a write landing between read and stat is cached as current forever. **Never cache a failure** — a parse error must be re-attempted after the operator fixes the file. Bound the map and lock it (sync FastAPI routes run on a threadpool, so the cache is concurrent whether you planned for it or not). And **name the residual**: a rewrite that preserves inode, size, mtime and ctime — a same-nanosecond in-place overwrite — is undetectable by this key, which is acceptable for a read cache but must be written down rather than implied away by "the cache is invalidated on change".
**Origin:** Console level-0 R7 build (review round 2), 2026-09-11; CHANGELOG 2026-09-11. Companion to LES-282 (why the cache exists at all).

## LES-284 — A cache that hands out a shared MUTABLE model must hand out an immutable one — freeze it, or copy per call

**Class:** bug-class
**Scope:** `axrange/api/imprints.py` (the cached `TwinConfig` model); any memoized parse whose value is a mutable model, dict or list handed to arbitrary callers
**Consumers:** code-writer, code-reviewer, tester
**Rule:** The moment a parse is cached, its value stops being "this caller's object" and becomes **process state shared by every later request** — so one caller mutating it (sorting a list in place, filling a default, stashing a derived field) silently corrupts every subsequent read, in a way no single request can reproduce. Either make the cached value **immutable** (a frozen model / `MappingProxyType` / a tuple) or **copy on hand-out** (a deep copy per call, paying the copy instead of the parse). Decide deliberately and write down which: copy costs a little and tolerates careless callers; freeze costs nothing and makes a careless caller fail loudly at the mutation. The tell that this is unhandled is the cache's own happy-path test — "the same object comes back twice" is exactly the property that makes the aliasing bug possible, so pair it with a test asserting a caller **cannot** mutate what it received.
**Check two things before reaching for `frozen=True`.** (1) **Grep for in-place writers** — one is enough to take the option away: `axrange/twinconfig/expand.py:677` assigns `leg.ip` during IP allocation, a working path freezing would refuse. (2) **`frozen` blocks attribute assignment only** — it would not stop `tc.nodes.pop()` or a nested model's field being rewritten, so even where it *is* available it is a partial guarantee. A **deep copy** covers both, and here costs 4.3 ms on a 109-node config against ~210 ms to parse it (~2 %). Copy on the **hit** path as well as the miss (LES-287), and write the trade down where the cache lives: "callers must not mutate this" in a docstring is not a guarantee.
**Origin:** Console level-0 R5+R7 build (final code round), 2026-09-11; `axrange/api/imprints.py` `_load_twin_config`; CHANGELOG 2026-09-11. Companion to LES-283 (the key) and LES-282 (why the cache exists).

## LES-285 — Typing ONE field fixes one crash; the defect class is EVERY sibling field the same reader takes from the same untrusted document

**Class:** bug-class
**Scope:** `axrange/fidelity/reference.py` (the passthrough fields read from a provenance sidecar / topology export), `axrange/api/topology_export.py`, `axrange/api/imprints.py`
**Consumers:** code-writer, code-reviewer, tester
**Rule:** When a defect reads "field X arrives from an external document untyped and reaches a consumer (or crashes the reader)", the fix is **not** X. The class is *every* field the same reader takes from the same document by the same untyped path — they share the producer, the trust level and the failure mode, and fixing only the one the bug report named leaves the next N reports queued behind it. So: enumerate the reader's passthrough fields, type them **all** in the same pass (here five further fields followed the first), and add the enumeration itself to the test — a field-set assertion that fails when a new untyped passthrough appears, because the next one will be added by someone who never read this lesson. The same move applies to any "sanitize on the way out" boundary: the unit of work is the boundary, not the field.
**Origin:** Console level-0 R5+R7 build (final code round), 2026-09-11; CHANGELOG 2026-09-11. Same shape as LES-224 (an input rule that must hold for every sibling input) and the LES-037 family.

## LES-286 — Two readers of one value need ONE classifier — a per-row rule and an aggregate rule that drift will disagree about the same datum

**Class:** duplicate-logic
**Scope:** `axrange/api/imprints.py` (a provenance row's `confidence` and the published **mean** confidence over those rows); any value that is both listed per-row and summarized; `o2/console/src/views/ElementActions.tsx` (`AddSegmentForm`'s live claim note vs its Stage check), `o2/console/src/model/keys.ts` (`parseCidr`); any live validation note shown beside a submit-time check
**Consumers:** code-writer, code-reviewer, tester
**Rule:** When one value is consumed twice — once per row and once by an aggregate over the rows — the two consumers grow **two** notions of "is this value usable", and they drift: here the per-row path tolerated a non-numeric `confidence` while the mean's path refused it, so a single malformed row turned `GET …/provenance` into a typed 400 for a document the row renderer was perfectly happy to display. Write **one** classifier (`is this a usable confidence?`) and have both the row and the aggregate call it: a non-numeric confidence is **absent**, absent rows are **counted** as absent, and the mean is taken over the usable ones with the denominator published. The rule generalizes: any "filter + summarize" pair over the same records must share the predicate, or the summary describes a different population than the list beside it.
**Extended 2026-09-15 (Console S5.1.a, B1) — a LIVE note and a SUBMIT check are two readers of one value.** The add-segment form's live claim note read the typed CIDR one way while Stage's refusal read it another, and `Number("")` is `0`, so a half-typed `10.80.31.0/` became a `/0` — a network over the whole address space that "overlapped" every segment — and the note warned about a network Stage would never consider. Derive **one canonical value** (`strictCidr(cidr)`) and have the note, the prefills, the collision checks and the refusal all read it; a value that is not a network CIDR gets no note at all. Test the pair **at every prefix of a typed input** — `1`, `10.`, `10.80.31.0/`, `10.80.31.0/2`, `10.80.31.0/24` — asserting the note says exactly what Stage would, because the drift lives in the half-typed states no single fixture value visits (LES-305's sweep, applied to keystrokes; LES-254's strict parse).
**Origin:** Console level-0 R5+R7 build (review round 2), 2026-09-11; CHANGELOG 2026-09-11. Same family as LES-025 (one implementation, reused) applied to a validation predicate. **Extended by Console S5.1.a fix round 1 (B1), 2026-09-15** — CHANGELOG 2026-09-15 "Console S5.1.a BUILT"; `docs/WORK_ORDER_console_canvas.md` §18.

## LES-287 — A cache-isolation test must mutate the object returned by a cache HIT, not by the miss — the miss path usually copies anyway, so the hole passes

**Class:** verification-idiom
**Scope:** `axrange/api/imprints.py` (`_load_twin_config`'s isolation test); any test asserting that a memoized loader cannot leak shared mutable state to its callers
**Consumers:** tester, code-writer, code-reviewer
**Rule:** The bug a cache-isolation test exists to catch is "a **hit** returns the stored instance", so the test must take the object from a **hit** — call the loader twice, mutate the **second** result, then call a third time and assert the third is unaffected. Mutating the object the **miss** returned proves nothing: the miss path commonly builds or copies a fresh object anyway, so the assertion passes whether or not the hit path is isolated, and the test reads as coverage while testing the wrong path. This build's first cut of the test had exactly that hole, and it surfaced only because the fix was **revert-proven against the hit path specifically** (revert the copy on the hit branch alone and watch the test fail — if it still passes, the test is aimed at the miss). Mutate in more than one way, too: an attribute assignment, a **list** mutation (`obj.items.pop()`) and a **nested** model's field, because the cheap "make it immutable" answers cover only the first (LES-284). General form: a test for "X cannot leak" must obtain its object from the code path that would leak, not from the one that happens to be safe.
**Origin:** Console level-0 R5+R7 build (final code round), 2026-09-11; CHANGELOG 2026-09-11. Companion to LES-284 (the copy this proves) and the same "revert-prove the *specific* branch" discipline as LES-265.

## LES-288 — A VIEW flag that rides the filter type is enrolled in every consumer that reads that type as a WHOLE — enumerate them, and derive the taxonomy once

**Class:** bug-class
**Scope:** `o2/console/src/model/zoom.ts` (`Filters`, `anyDimmingFilter`, `clearFilters`), `o2/console/src/model/graph.ts`, `o2/console/src/views/Filters.tsx`; any struct that accumulates fields of different KINDS and is consumed by predicates over the whole struct, `o2/console/src/App.tsx` (the in-pane selection reset, now keyed on `location.pathname` only); any state reset or cache invalidation keyed on a route, URL or query object
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Adding a field to a shared type is never just an addition: every consumer that reads the type **as a whole** — "is any filter active?", "what does *clear* produce?", "should the clear chip show?", "did the scope change?" — silently enrolls the new field in its own notion of what the type means. R1 added `?summary=` (chrome: is the summary bar open) to a `Filters` struct whose other members select a subset, and by default it would have dimmed every tile as though a filter were active, been wiped by *clear*, and reset the operator's open drawer on every toggle, because each of those behaviours was an **open-coded membership list at its own call site**. The fix is not to patch the three sites: **state the taxonomy once, in the type's own module** — here dimming filters / the emphasis flag / view flags — export a predicate per category (`anyDimmingFilter`, `clearFilters`), and have every consumer derive from them. Then the next flag's author chooses a category instead of remembering three lists. Before adding the field, **enumerate the whole-struct consumers** (grep the type name, not the field name — the field does not exist yet), because that list *is* the change's blast radius.
**Extended 2026-09-15 (Console S5.1.a, B2) — a RESET key is a whole-struct consumer too, and its worst member is one the app's own SUCCESS path sets.** The in-pane selection reset whenever a scope string built from the pathname **plus the filters** changed. A view flag re-budgets the map and a dimming filter emphasizes a subset — neither moves the page to another place — yet each dropped the selection *and whatever was typed under it*. Worst of all, an apply that changed anything sets `?changed=1`, so **every successful apply** wiped the operator's in-progress form. A reset key states *identity*: include only what changes **which place** the page is (here, the pathname), and audit it against the flags the app sets on its own success paths, not only the ones a user toggles.
**Origin:** Console level-0 R1 build, 2026-09-12; `o2/console/src/model/zoom.ts` "The filter taxonomy, stated once"; CHANGELOG 2026-09-12 "Console level-0 R1 BUILT". Same family as LES-025 (one implementation, reused) and LES-224 (a rule that must hold for every sibling). **Extended by Console S5.1.a fix round 1 (B2), 2026-09-15** — CHANGELOG 2026-09-15 "Console S5.1.a BUILT"; `docs/WORK_ORDER_console_canvas.md` §18.

## LES-289 — A persisted ABSOLUTE-coordinate override is stamped to a layout EPOCH — and the accept/migrate/stamp decision belongs to the SERIES of layout changes, not one slice

**Class:** reversal-hazard
**Scope:** `axrange/api/console_files.py` (`axr-layout/1`, the pins document), `o2/console/src/layout/pins.ts`, `o2/console/src/layout/level0.ts`; any stored user override expressed as a position, offset or index into a computed artifact
**Consumers:** code-writer, code-reviewer, tester, doc-writer
**Rule:** A pin stored as `{x, y}` is a position on a plane the document does not identify, so **any** change to the plane's origin re-points every stored override *without changing a byte of it* — the file still validates, still loads, and now means something else. R1 shifted level 0's content origin right by 128 units for the tier rails; every stored pin silently moved 128 units left relative to its tile. The durable rule: **stamp the override document with the plane/epoch it was authored against** (a layout version, or the origin it assumed), so a later client can offset it, refuse it, or tell the operator their pins are stale — a stored absolute coordinate without a frame of reference is an undetectable silent corruption. And decide **when** deliberately: R1's blast radius was genuinely zero (the only `layout.json` in the tree held `"pins": {}`), while R2, R3, R4 and R6 each move the plane again and R6 changes which tiles exist — so the honest call was to **accept and publish the limit now and put the stamp in the slice that has the whole series in view**, not to migrate four times. Write that reasoning down where the next slice will read it; "we'll fix it later" that is not attached to a named slice is how it becomes never.
**Extended 2026-09-16 (Console S5.1.b) — a persisted override also needs an owner for its subject's DEATH.** A clone's placement is committed as a pin keyed on the new segment's CIDR. Remove the `add: segment` records again and the pin stays in `layout.json`, naming a segment nothing adds: it validates, it loads, it draws nothing, and it will silently re-position the next segment that ever takes that CIDR. The stamp answers *"which plane was this authored against"*; it does not answer *"does its subject still exist"*. So for any persisted override keyed on a record's identity, decide the lifecycle explicitly and write the decision down: delete it with the record, or **keep it and disclose it** — this slice keeps it (deleting a pin behind a `PUT` the operator did not ask for is its own surprise) and names it in the pin bar as naming no segment in play, with the cleanup assigned to a named slice. An override whose subject can be deleted and which has neither an owner nor a disclosure is an orphan the file will eventually re-apply to a stranger.
**Origin:** Console level-0 R1 build, 2026-09-12 (honest limit 1, decision scoped to R2); `docs/WORK_ORDER_console_level0_rendering.md` §8/§10.7; CHANGELOG 2026-09-12. **Extended by Console S5.1.b's clone pins, 2026-09-16** — CHANGELOG 2026-09-16 "Console S5.1.b–c BUILT"; `docs/WORK_ORDER_console_canvas.md` §19.

## LES-290 — A TypeScript mirror of CSS layout constants needs a test that READS the CSS — and that pins the unit assumption, not just the numbers

**Class:** duplicate-logic
**Scope:** `o2/console/src/layout/viewport.ts` (`estimateMapWidth`), `o2/console/src/theme/shell.css`; any code that reproduces a stylesheet's geometry in another language (a jsdom fallback, a server-side render, a PDF exporter)
**Consumers:** code-writer, code-reviewer, tester
**Rule:** When code must know a stylesheet's numbers — R1's jsdom/first-paint fallback mirrors `shell.css`'s `minmax()` grid minimums and `.pane` padding, because in jsdom every element measures 0 — the copy is a duplicate that drifts silently: CSS has no consumer that fails when it changes. Close it with a test that **reads the CSS file** and asserts the mirrored values, not with a comment. Critically, **pin the UNIT assumption too**: the mirror converts `rem` to px at an assumed root font size, so moving a `font-size` from `body` to `html` rescales *every* `rem` in the file at once and breaks the mirror without touching a single number it copied — which is precisely the edit R1 made elsewhere in the same stylesheet. The test therefore asserts both the declarations it mirrors **and** that no `font-size` is set on `html`. General form: when you duplicate a value across a language boundary, test the boundary's *assumptions*, because those are what the other side can change without appearing to.
**Origin:** Console level-0 R1 build (reviewer), 2026-09-12; `o2/console/test/viewport.test.ts`, `o2/console/src/theme/shell.css` header comment; CHANGELOG 2026-09-12.

## LES-291 — `window.resize` does not fire for an IN-PAGE layout change — observe the element, not the viewport

**Class:** platform-gotcha
**Scope:** `o2/console/src/views/Level0.tsx` (the map pane measurement); any code that reacts to "how wide am I now?"
**Consumers:** code-writer, code-reviewer, tester
**Rule:** `window.addEventListener("resize", …)` fires when the **viewport** changes, and for nothing else. An element that changes width because the page rearranged itself — a collapsing sidebar, a grid-template change, a font load, a scrollbar appearing, a pane the operator dragged — fires **no** resize event, so a fit-to-width computed on the window listener alone goes stale in exactly the situation it matters most: R1's *fit width* would have used the width the map no longer had the moment the outline pane collapsed. **Observe the element** (`ResizeObserver` on the measured node), and keep the window listener only as the cheap extra (and, in jsdom, the only one available). Guard the observer's existence — it is undefined in some test environments — and measure once on mount, because the observer's first callback is not guaranteed before paint.
**Origin:** Console level-0 R1 build (reviewer), 2026-09-12; CHANGELOG 2026-09-12.

## LES-292 — `pkill -f "<full command string>"` run from a shell whose OWN command line contains that string kills the shell — and every step after it silently never runs

**Class:** tool-gotcha
**Scope:** any agent/CI bash step that cleans up a background process with `pkill -f` / `killall` on a command-line pattern (dev servers, `vite preview`, `axrange serve`, a tailed log)
**Consumers:** code-writer, tester, live-verifier
**Rule:** `pkill -f` matches against **every** process's full command line — including the shell running the `pkill` itself, whose argv contains the pattern as a literal argument. So `pkill -f "npm run dev -- --port 5174"` from a compound command that mentions that string terminates its own shell: the intended target dies, the shell dies with it, and **every subsequent command in that step never runs** while the step reports no error of its own — the output simply stops, which reads as "finished" in a transcript. Kill by **PID** captured when you started the process (`$!`), or scope the pattern so it cannot match the caller (`pgrep -f … | grep -v $$`, a `--session` guard, or a pattern matching only the child's argv0). If a verification step's output ends abruptly with its later assertions missing, suspect this before suspecting the assertions.
**Origin:** Console level-0 R1 build (code-writer), 2026-09-12; CHANGELOG 2026-09-12. Same "the tool matched more than you meant" family as LES-264.

## LES-293 — A test fixture that REJECTS the underlying read renders no chrome — so assert a control where the control actually draws

**Class:** verification-idiom
**Scope:** `o2/console/test/views.test.tsx`, `o2/console/test/shell.test.tsx`; any component test whose fixture stubs a data source with a failure or an empty result
**Consumers:** tester, code-writer, code-reviewer
**Rule:** A component test's fixture decides which of the component's branches even exists in the DOM. A fixture whose `getTopology` rejects renders the **error** branch — which draws no map, and therefore no filter bar, no scale control and no legend — so an assertion about a filter chip placed in that test is asserting against a tree that structurally cannot contain it, and it fails (or worse, passes vacuously on a `queryBy…` null check) for a reason that has nothing to do with the behaviour under test. Put each assertion in the test whose fixture renders the branch that draws it, and when a control assertion fails, **check what the fixture made the component render** before changing the component. The corollary is a positive one: keep one fixture per branch (loaded / failed / empty) and name them so the choice is deliberate rather than inherited by copy-paste.
**Origin:** Console level-0 R1 build (code-writer), 2026-09-12; CHANGELOG 2026-09-12. Companion to LES-253 (the three worlds those fixtures represent).

## LES-294 — `git diff -- <repo-relative path>` after `cd`-ing into a subdirectory matches NOTHING and reads as "no changes"

**Class:** tool-gotcha
**Scope:** any agent/CI step that runs `git diff`/`git status`/`git add` with a pathspec after changing directory (this repo's `cd o2/console && …` npm steps)
**Consumers:** code-writer, tester, code-reviewer, live-verifier
**Rule:** A git **pathspec is relative to the current working directory**, not to the repository root (unless it starts with `:/`). So `cd o2/console && git diff -- o2/console/test/layout/` looks for `o2/console/o2/console/test/layout/`, matches nothing, exits **0** with empty output — which reads exactly like "this path is unchanged", and nearly hid a golden regeneration in this slice. Empty output from a filtered `git diff` is only evidence if you know the filter matched something: use `:/o2/console/test/layout` (root-relative), run the command from the repo root, or verify the pathspec first (`git ls-files -- <spec> | head`). The same trap applies to `git add <path>` in a subdirectory step, where it silently stages nothing.
**Origin:** Console level-0 R1 build (code-writer), 2026-09-12; CHANGELOG 2026-09-12.

## LES-295 — A file containing a raw NUL byte is classified BINARY, so `grep` skips it silently and every grep-driven audit of it comes back clean

**Class:** tool-gotcha
**Scope:** `o2/console/src/model/graph.ts` (a map-key separator, now written as `\x00`); any source file that embeds a control character as a literal; any audit, review or refactor driven by `grep`/`rg` over a tree
**Consumers:** code-writer, code-reviewer, tester, doc-writer
**Rule:** GNU grep treats a file containing a NUL byte as **binary** and, by default, prints `Binary file … matches` or nothing at all rather than the matching lines — so a source file with a literal control character in a string becomes **invisible to every grep-driven audit of the tree**, and the audit reports clean. `file` will call the module `data`, which is the tell. Three consequences worth holding: a grep that finds *no* occurrences of a symbol is not proof the symbol is unused; a "which modules consume this type?" sweep can miss the one module that matters (it did here, costing this slice a grep); and the fix is cheap and permanent — **write control characters as escapes** (`"\x00"`, `"\x00"`), never as literals, so the file stays text. Add `grep -c ""`/`file` to a tree audit when the result seems too clean, or pass `-a` to force text treatment.
**Origin:** Console level-0 R1 build (tester), 2026-09-12; CHANGELOG 2026-09-12. Same "the tool silently declined to look" family as LES-264 (a multibyte-`§` locale grep) and LES-294.

## LES-296 — A validity stamp that certifies a SET advances only when the WHOLE set is re-authored — and "re-authored" must be identified by VALUE, not by key

**Class:** bug-class
**Scope:** `o2/console/src/layout/plane.ts` (`planeForSave` / `pinPlaneStatus`), `o2/console/src/App.tsx` (the per-session authored-pin record in `ImprintWorkspace`), `axrange/api/console_files.py` (`axr-layout/1`'s `plane` field); any provenance, epoch, `verified-against` or `as-of` stamp written alongside a **whole-collection** write
**Consumers:** code-writer, code-reviewer, tester
**Rule:** LES-289 says stamp a stored absolute override with the plane it was authored against. The **write** half is where that guard destroys itself: a gesture that edits one member usually **commits the whole collection**, so stamping the document "current" on every save re-labels N inherited members as freshly authored off a single edit — the warning disappears for ever and N−1 members are silently wrong, which is exactly the corruption the stamp exists to prevent, re-entering through its own mechanism. Two rules, both needed: (1) **the stamp advances only when the set is WHOLLY re-authored** — a reset (the empty set, vacuously) or every surviving member re-authored since the document was loaded; otherwise the document keeps the stamp it arrived with, so a legacy file stays legacy and a stale one stays stale until the operator finishes the job, and a caller that cannot say what it authored gets the same conservative answer. (2) **Identify re-authorship by VALUE, not by key.** A set of keys says "we touched A at some point", which stops being true the moment the document underneath is replaced — and a well-behaved client builds that path deliberately: an etag 412 rolls the save back and the reload swaps in the other writer's values (LES-251). Key-only, `stale {A,B} → edit A → 412 → reload someone else's A → edit B` certifies a member nobody re-authored. Record **what this session wrote** and count a key only while the stored value still equals it (an exact match — both sides are values you computed, so there is no tolerance to tune). Two corollaries: the session record must live beside the **document** it describes, not in a component that unmounts on navigation (ours died on every drill to a sub-view until it moved); and the on-screen advice ("reset, or re-place every one") and the rule that clears the flag must be **the same function**, or the progress counter and the guard will drift. The tell that you are in this territory at all: any path that deliberately rolls back and re-reads.
**Origin:** Console level-0 R2 build (review must-fix M1, then final-round S1), 2026-09-14; CHANGELOG 2026-09-14 "Console level-0 R2 BUILT"; `docs/WORK_ORDER_console_level0_rendering.md` §11.4. Extends LES-289; same lost-update family as LES-251. Folds in the build's own candidate: *provenance for a whole-collection write must be keyed by value.*

## LES-297 — Normalise a magnitude→visual-channel scale over the DRAWN set's own range, clamp both ends, keep zero out of band — and measure the discrimination on real data before a second consumer inherits it

**Class:** pattern
**Scope:** `o2/console/src/layout/scale.ts` (`volumeRange` / `normalizedLog10` / `strokeForBytes`), consumed by `o2/console/src/layout/trunk.ts` and (next) the east-west arcs; any magnitude → stroke width / radius / opacity / saturation mapping over real-world data spanning orders of magnitude
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Two independent choices hide inside "width by volume", and only the first is obvious. **Log, not linear**, because real traffic spans four orders of magnitude and a linear map draws everything but the busiest as the same hairline. **And normalise over the DRAWN set's own `[min .. max]`, not against an absolute zero** — dividing by `log10(1+max)` alone spends most of the band on magnitudes the data never contains, which is how twelve elements land within **0.66 px** of each other (the same twelve span **2.06 px** against their own range) and a stated "magnitude is visible before you read a label" property silently fails. State the cost rather than hiding it: **width becomes relative within one drawing**, so the thinnest non-zero element is always the minimum width and the same element is drawn differently when the drawn set changes (a top-K view versus "show all") — record that next to wherever the K/budget is chosen, or an operator reads a width change as a data change. Clamp at **both** ends so a caller passing a stale range draws a full-width or minimum-width element rather than one off the scale; make the degenerate inputs explicit (a single distinct magnitude is the maximum, not a division by zero; a non-finite or empty range yields the mark that claims least). **Keep zero out of band**: a zero-magnitude element still exists, so give it a distinct out-of-band mark — "nothing was observed here" and "this is not connected" are different statements — and be honest that a sub-pixel width is a *claim* the stylesheet has to make visible (ours renders `0.5` as a dashed 1 px, so a test asserting the width must assert the class beside it). Finally: **measure on the real bundles before a second consumer inherits the module**, because the second consumer is usually the one with forty elements, and by then the scale is load-bearing.
**Origin:** Console level-0 R2 build (review S2/S4), 2026-09-14; CHANGELOG 2026-09-14; `docs/WORK_ORDER_console_level0_rendering.md` §11.3 and §5 D2.

## LES-298 — A geometric invariant proven under the DEFAULT layout must be re-proven at the OVERRIDE channel's extremes — pin what survives as a measured limit, and guard the guard

**Class:** verification-idiom
**Scope:** `o2/console/test/layout/helpers.ts` (`noConnectorCrossesATile`), `o2/console/test/r2.trunk.test.ts`, `o2/console/src/layout/pins.ts` (`applyPins`); any computed-layout invariant in a system that also lets a user override placement (pins, manual coordinates, a stored offset)
**Consumers:** code-writer, code-reviewer, tester
**Rule:** An invariant like "no connector crosses a tile" is trivially true under the layout the pure function produces, because the function was written to make it true. The interesting question is what happens at the **override channel's extremes** — an absolute pin with **no reflow** re-routes nothing, so a group dragged far enough can put a box across a line the layout took for granted. Enumerate the extremes deliberately (above, below, and *straddling* the structural line), run the invariant at each, and then: fix what is fixable in the layout (approach a box from the side it is actually on), and **pin what is not as a measured limit** — a test asserting the violation count is `> 0` at named coordinates, whose failure message tells the next reader to delete the test and the doc note if the limit ever empties. A limit recorded with coordinates is a statement; a limit recorded in prose is a rumour. **And guard the guard:** an invariant expressed as `collection.every(…)` is **vacuously true on an empty collection**, so the assertion passes loudest exactly when the thing under test was never built — assert the collection is non-empty in the same test. **Corollary on assertion shape:** when the property is about an extent (a bus, a bounding box, a band), *"the span grows"* is the wrong assertion, because the moved element can land **inside** the existing span and the test then proves nothing while looking like it proves something. Assert **containment** (the extent covers every member, wherever they are) as the general property, and prove growth separately with an element deliberately placed out of range.
**Origin:** Console level-0 R2 build (review S1 + the tester's bus-extent assertion), 2026-09-14; CHANGELOG 2026-09-14; `docs/WORK_ORDER_console_level0_rendering.md` §8 (limit 1) and §11.5. Same "the test certifies less than it appears to" family as LES-287.

## LES-299 — Widening a drawing/emission rule from a NECESSARY CONDITION to a disjunction invalidates every test that encoded the old one — restate them, do not delete them

**Class:** pattern
**Scope:** `o2/console/src/layout/level0.ts` (the WAN box, keyed on `graph.wanEdge` rather than on published routers), `o2/console/src/model/graph.ts`, `o2/console/test/layout.golden.test.ts` (the rails test that empties a tier); any predicate that decides whether an element is drawn/emitted and is later widened to "A **or** B"
**Consumers:** code-writer, code-reviewer, tester
**Rule:** When an element's existence condition goes from "A" to "A or B", every test that produced the **absence** case by removing A stops testing absence — it now silently exercises the **B** branch and still passes, which is the worst possible outcome: green, and no longer about what its name says. The mechanical fix is to restate the setup so it removes **every** disjunct (our rails test had to drop the non-local population and its edges as well as the routers), and to leave an in-test comment saying *why both*, because the next reader's instinct is that dropping the routers is enough. **Do not delete the old tests**: they are the record of the previous contract, and rewriting them in place with a comment naming the widening is what lets a reviewer see that the absence case was considered rather than lost. Give the newly-reachable branch (here, the *inferred* case) its own coverage in the same commit, or the widening is tested only by the tests it broke.
**Origin:** Console level-0 R2 build, 2026-09-14; CHANGELOG 2026-09-14; `docs/WORK_ORDER_console_level0_rendering.md` §11.1.

## LES-300 — Which goldens a model-layer field moves is decided by what the layouts EMBED, not by which layout you edited

**Class:** verification-idiom
**Scope:** `o2/console/test/layout/*.golden.json`, `o2/console/src/model/graph.ts` (`GraphSegment`), `o2/console/src/layout/level1.ts`; any snapshot/golden suite whose snapshots embed a shared domain object rather than only the coordinates the layout under test computed
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Adding a field to a **shared model type** moves every golden whose layout **embeds that type**, not just the goldens of the layout you were editing — R2 added `GraphSegment.northSouth` for level 0 and three **level-1** goldens grew by two leaves each, because `layout1` embeds the whole `GraphSegment` in its output. Predict the blast radius from the *embedding*, not the edit: before regenerating, ask which layouts carry the type by value, and state the expected per-file delta up front so a surprise is visible. Then verify by **machine-walking** the trees — "0 leaves changed, 0 removed, N added" is a far stronger claim than "the diff looked additive", and it is the claim that distinguishes a new field from an accidental relayout. (The structural alternative — snapshot only the coordinates and join the model in at read time — is worth considering when a golden suite starts changing for reasons unrelated to geometry.)
**Origin:** Console level-0 R2 build (tester), 2026-09-14; CHANGELOG 2026-09-14; `docs/WORK_ORDER_console_level0_rendering.md` §11.5. Companion to LES-268 (keep view concerns out of the pure layout so goldens stay valid).

## LES-301 — An `Edit` with `replace_all` matches only occurrences at the anchor's exact indentation, while reporting that all occurrences were replaced

**Class:** tool-gotcha
**Scope:** any agent-driven `Edit`/search-and-replace with `replace_all` over a source or documentation tree — especially a repeated code line, a list item, or a counter quoted at several nesting depths
**Consumers:** code-writer, code-reviewer, doc-writer, tester
**Rule:** `replace_all` replaces every **exact** occurrence of `old_string` — and if the anchor was copied from a read with its leading whitespace attached, occurrences at any *other* indentation are not occurrences. The tool then reports **"all occurrences were successfully replaced"**, which is true of the ones that matched and reads as "all the ones you meant", so a partially-applied rename or counter bump looks finished. Two habits close it: choose an anchor that **cannot carry indentation** (start it at the first non-whitespace character, or pick a substring unique enough to stand alone — `3101 pytest tests`, not `  - **3101 pytest tests** passing.`); and **count before and after** (`grep -c`) rather than trusting the success message, since the count is the only evidence of how many sites actually changed. The same trap hits a code edit where one call site is nested one level deeper than the rest.
**Origin:** Console level-0 R2 doc pass and build rounds, 2026-09-14. Same "the tool reported success for what it did, not for what you asked" family as LES-294 and LES-295.

## LES-302 — `0 * Infinity` is `NaN`, so an "unbounded capacity" sentinel silently poisons the grid arithmetic that consumes it

**Class:** bug-class
**Scope:** `o2/console/src/layout/infra.ts` (`devicesAcross` / `infraBandRows` and the row grid they feed); any capacity, budget, quota or column-count computation that uses `Infinity` (or a very large number) to mean *unbounded*, and any arithmetic downstream of it
**Consumers:** code-writer, code-reviewer, tester
**Rule:** `Infinity` is the natural way to say "no limit" — and it is arithmetic, not a flag, so the next expression that multiplies, subtracts or `%`-es it inherits the whole IEEE-754 rulebook. The specific trap: **`0 * Infinity === NaN`**, and `NaN` then propagates silently through every coordinate it touches (it is not falsy, it does not throw, and `NaN === NaN` is false, so even an equality guard downstream lets it through). Here an unbounded devices-per-row capacity multiplied by a zero row index gave **every unplaced device an `x` of `NaN`** — a drawing with no elements on it and no error anywhere. Three habits: (1) **decide whether the sentinel is ever consumed by arithmetic**, and if it is, clamp it to a real bound at the boundary (`Math.min(cap, count)`) instead of carrying `Infinity` inward; (2) **the branch that produces the sentinel needs its own fixture** — the shipped content usually exercises only the bounded branch, so the unbounded one is written, type-checked, reviewed and never run (LES-298's family); (3) assert **`Number.isFinite`** on computed coordinates in the layout's own invariant helper, since that is the one place a `NaN` is cheap to catch and expensive everywhere else.
**Origin:** Console level-0 R3 build, 2026-09-14; CHANGELOG 2026-09-14 "Console level-0 R3 BUILT". Same "the degenerate input is the one no real data produces" family as LES-297's degenerate-range clause.

## LES-303 — When a work order's RULE and its own ACCEPTANCE conflict on real data, the data is the tie-breaker — and the conflict must be RECORDED, not silently resolved

**Class:** pattern
**Scope:** `docs/WORK_ORDER_*.md` slice text versus its own acceptance clauses; `o2/console/src/model/graph.ts` (`roleBadgesOf`); any build implementing a written spec whose stated rule and stated expected outcome are both checkable
**Consumers:** code-writer, code-reviewer, doc-writer
**Rule:** A well-written slice states **a rule** ("up to three badges, highest count first") *and* **an acceptance** ("the `10.80.20.0/24` tile carries a `DC` badge with count 2"). On synthetic data both hold; on the data that ships they can contradict each other — here pure count ranking yields `EP 9 / WS 7 / NONE 5` and hides **both** domain controllers, so implementing the rule literally fails the acceptance, and satisfying the acceptance means departing from the rule. **Implement what the data says the author wanted** (the acceptance names the *outcome*; the rule was a guess at how to get there) — but the resolution is only half the job. **Write the conflict down where the rule lives**: correct the order's own wording, say which clause gave way and why, quantify the departure on real content (ours changes the badge set on 4 of 39 and 3 of 32 segments and displaces a meaningful role exactly once), and record the alternatives you refused with their arithmetic (widening to four badges was refused because `(140 − 2·4)/3 = 44` units becomes 32, where labels overflow). Otherwise the next reader finds code that disagrees with the spec and "fixes" it back. **The corollary that makes this more than bookkeeping:** the measurement is what turns a preference into a decision — without it, "reserve a slot for infrastructure roles" is taste, and with it, it is a one-line cost the reviewer can rule on.
**Origin:** Console level-0 R3 build + review (badge ranking, reviewer ruled KEEP), 2026-09-14; CHANGELOG 2026-09-14; `docs/WORK_ORDER_console_level0_rendering.md` §3 R3 correction blockquote and §12.2.

## LES-304 — A connector family the VIEW derives cannot be covered by a layout-level invariant; publish the geometry from the layout, or it is unchecked

**Class:** bug-class
**Scope:** `o2/console/src/layout/level0.ts` (`Level0Layout.infraStubs`), `o2/console/test/layout/helpers.ts` (`noConnectorCrossesATile`); any pure-layout invariant ("nothing crosses a tile", "no two elements overlap") in a system where some drawn geometry is computed in the view instead
**Consumers:** code-writer, code-reviewer, tester
**Rule:** An invariant helper can only inspect what the layout **publishes**. A connector the *view* derives — ours drew a `<line>` from a device box to a tile box at render time — is invisible to it, so the helper reports clean while that family crosses tiles on every bundle, and it does so **quietly for as long as the family stays in the view** (two slices, here). The tell is a helper whose documentation has to say *which* connectors it covers: if that list is shorter than what the screen shows, the gap is the bug. Two rules: **publish the geometry from the pure layer** (a stub is `{from, to}` in `Level0Layout`, not a pair of box lookups in JSX) so it joins the invariant automatically and the goldens pin it; and when a slice deliberately leaves a family out, **name it in the helper's docstring with the slice that will fix it**, so "covered" is never assumed from "passes". The deeper principle is the one that keeps goldens meaningful (LES-268): the view decides *appearance*, the layout decides *position* — a connector's endpoints are position.
**Origin:** Console level-0 R3 build, 2026-09-14 (R2 had deliberately excluded the tie-lines for exactly this reason); CHANGELOG 2026-09-14; `docs/WORK_ORDER_console_level0_rendering.md` §11.5 and §12.1.

## LES-305 — A single adversarial probe can land in a LATTICE GAP and report "clean": sweep the parameter, do not sample it, and pin both a violating and a clean coordinate

**Class:** verification-idiom
**Scope:** `o2/console/test/r3.infra.test.ts`, `o2/console/src/layout/infra.ts`; any test that probes a geometric or scheduling limit at a chosen parameter value, where the system under test has two or more periodic pitches (tile pitch vs device pitch, poll interval vs timeout, page size vs record size)
**Consumers:** tester, code-writer, code-reviewer
**Rule:** When two structures in a layout have **different pitches**, the set of parameter values at which they conflict is itself periodic — and so is the set at which they happen not to. A single adversarial probe is therefore evidence about *that coordinate*, never about the property: ours passed at `x=900` and read as "the stub never crosses a tile", when the truth is that device pitch 168 against tile pitch 172 leaves an ~8-unit clean window that **slides** as the pin moves — a sweep of `x ∈ [140, 200]` violates at **13 of 16** positions. The same arithmetic bites from the other side: an attempt to *construct* a violation can miss for the same reason, and has to be **aimed** rather than guessed (pin the single-device column to x=710 → its stub lands at 972, inside a tile spanning 894–1050). So: **sweep the parameter across at least one full period of the coarser pitch and report the violation count**, treat a clean sample as a hypothesis until the sweep agrees, and **pin both a violating and a clean coordinate** as tests — the pair is what stops either from being read as the rule. Then state the mechanism (the two pitches and their difference) in the module's own docstring, because a reader who knows *why* the clean window exists will not re-derive the limit as a bug. Folds in the build's own independently-arrived-at candidate: *a single passing adversarial case is not evidence of a property when the geometry is periodic.*
**Origin:** Console level-0 R3 build + review (the widened crossing assertion) and the tester's aimed construction, 2026-09-14; CHANGELOG 2026-09-14; `docs/WORK_ORDER_console_level0_rendering.md` §8 (R3 limit 1). Sharpens LES-298 (prove the invariant at the override channel's extremes) with *how many* extremes to try.

## LES-306 — A headroom claim in a decision record must be COMPUTED from the same constants the code uses and PINNED by a test, or it degrades silently as the data grows

**Class:** verification-idiom
**Scope:** `o2/console/test/layout.real.test.ts` (`max(perColumn) === devicesAcross(columnW)`), `docs/WORK_ORDER_*.md` "honest limits" blocks; any doc sentence of the form "there is room for N more before X happens"
**Consumers:** doc-writer, code-writer, tester, code-reviewer
**Rule:** "Four fit across and the busiest column holds three, so there is one spare" is the kind of sentence that makes a risky change (an epoch bump, a budget, a cache size) sound harmless — and it is exactly the kind that is **wrong by one** when written from memory rather than computed, as ours was: three fit across, the busiest real column holds three, and the true headroom is **zero spare slots**. Two rules. **Compute the claim from the same constants the code uses** — call the function (`devicesAcross(COLUMN_W)`), do not restate its arithmetic in prose, or the doc and the code diverge the first time a constant moves. **Pin it as a relation over the real content**, not as a literal (`max(perColumn) === devicesAcross(columnW)`, not `=== 3`), so the test fails the day a bundle grows a fourth device in one column — which is the same day the consequence the claim was guarding lands. A headroom figure with no test behind it decays into a rumour at the speed the content grows, and it is load-bearing precisely because someone used it to decide the change was safe.
**Origin:** Console level-0 R3 review, 2026-09-14; CHANGELOG 2026-09-14; `docs/WORK_ORDER_console_level0_rendering.md` §8 (R3 limit 2) and §12.3. Companion to LES-298's "a limit recorded with coordinates is a statement; a limit recorded in prose is a rumour."

## LES-307 — A top-N projection must say "N of M" everywhere it is rendered — INCLUDING in the accessible name — or it reads as the complete set

**Class:** pattern
**Scope:** `o2/console/src/views/mapParts.tsx` (`badgeRowTitle`, the tile `aria-label`); every budgeted or top-K projection on this surface — R3's role badges, R4's top-K east-west arcs, R6's per-group tile budget, the level-1 rung collapse; any UI, report or API response that shows the first N of M; `o2/console/test/` (role/name queries under a disclosed projection)
**Consumers:** code-writer, code-reviewer, tester, doc-writer
**Rule:** A projection that does not disclose itself is indistinguishable from a complete answer, and the reader has no way to know which they are looking at — three role badges read as "this segment has three roles", twelve arcs read as "there are twelve links". **State N of M wherever the projection is rendered**, and treat three surfaces as one requirement: the visible affordance (a hover title, a `+N more` chip), the **accessible name** (an `aria-label`/`alt`, which is the *only* channel for a screen-reader user and is the one routinely forgotten — especially where the decorative element itself is `aria-hidden`), and any exported/`--json` form. **Generate all of them from one helper** so the wording and the numbers cannot drift; a second phrasing is a second thing to keep true. Two sharpeners: a projection that is deliberately **not a ranking** (ours reserves a slot for infrastructure roles, LES-303) needs the disclosure *more*, not less, because "the top three" is not even an available reading; and the disclosure must name **where the complete record is** ("+2 more in the inspector"), or the reader is told something is hidden and given nowhere to go.
**Extended 2026-09-15 (Console S5.1.a) — the TEST consequence of honouring this rule.** Once the `+N more` tile names the segments it hides in its accessible name, an unscoped `getByRole("button", {name: /10\.80\.31\.0\/24/})` matches that tile **as well as** the element under test (the outline row) — and either throws on the second match or, written as `getAllBy…[0]`, silently exercises the wrong path. **Scope role/name queries to the pane that owns the path under test** (`within(screen.getByRole("navigation", {name: "Imprints"}))`), and pin in the same test *that* the disclosure names the element, so the scoping reads as explained rather than arbitrary.
**Origin:** Console level-0 R3 review (should-fix S2), 2026-09-14; CHANGELOG 2026-09-14; `docs/WORK_ORDER_console_level0_rendering.md` §12.2. Stated as a rule for the whole surface because R4's top-K arcs and R6's tile budget are the next two instances. **Extended by Console S5.1.a, 2026-09-15** — CHANGELOG 2026-09-15 "Console S5.1.a BUILT"; `docs/WORK_ORDER_console_canvas.md` §18.

## LES-308 — A cubic Bézier reaches only THREE QUARTERS of the way toward its control points, so "the curve passes below everything" is false whenever the endpoints sit above the clearance zone

**Class:** bug-class
**Scope:** `o2/console/src/layout/arcs.ts`; any clearance, collision or containment claim argued from **control-point** coordinates — Bézier/spline connectors in a diagram, easing curves asserted to stay within a range, a path router that "dips below" an obstacle
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A control point is an **attractor, not a waypoint**: a symmetric cubic's extreme is `0.125·(y₀+y₃) + 0.75·y_c`, so with both controls at depth `D` the curve only reaches **¾ of the way** from its endpoints toward `D` — and the shortfall is proportional to how far the *endpoints* sit from it. Our arcs were specified as "a cubic from one group's bottom edge to the other's, with control depth in the band", which reads as *the curve is in the band*; it is not, and on real content the gap bit exactly where the data is irregular — site columns differ in height, so an arc between two **short** columns sagged only three quarters of the way down and passed through the lowest tiles of a **tall** column between them (1 crossing at K=12, **6** with the budget lifted). Two rules. **Never argue clearance from where the control points are** — either sample the curve (t-sweep) and assert against the real obstacle set, or make the geometry structural. **Prefer structural:** route the path *into* the clear zone first (here, drop vertically to the band top, inside the endpoint's own column x-span where nothing else lives, and curve from there), so the property holds by construction rather than by the arithmetic happening to work out on today's heights. A corollary for the depth itself: measure it from `max(zone, both endpoints)`, never from the zone alone, or an endpoint dragged *past* the zone puts both controls above it and bulges the curve back through the obstacles it was meant to clear.
**Origin:** Console level-0 R4 build, 2026-09-15; CHANGELOG 2026-09-15; `docs/WORK_ORDER_console_level0_rendering.md` §8 (R4 limits 1–2) and §13.2. The measured before/after (1 and 6 crossings versus 0 and 0) was reproduced independently by the tester on both geometries.

## LES-309 — A budget that bounds ONE of two overlapping families is inert whenever the other family mirrors it — measure the budget's effect on real content, not just its code path

**Class:** pattern
**Scope:** `o2/console/src/layout/arcs.ts` (`ARC_TOP_K`), `o2/console/src/layout/level0.ts`; any top-N / collapse / sampling budget on a surface that draws a second, unbudgeted family over the same elements (asserted vs observed, pinned vs computed, alerting vs informational)
**Consumers:** code-writer, code-reviewer, doc-writer, tester
**Rule:** A budget's effectiveness is a property of the **content**, not of the code that implements it — and it goes to zero when a second, deliberately unbudgeted family covers the same elements. Ours bounds the *observed* east-west pairs at twelve while drawing **every** *asserted* conduit by ruling, and both shipped casts declare a conduit for essentially every pair they observed (40 for 40; 22 against 31) — so the map draws 40 arcs, `hiddenPairs` is 0, and the *show all* control never renders. Three things follow. **Measure the budget against the real bundles before claiming it bounds anything**, and publish the measurement beside the constant. **Do not "fix" inertness by budgeting the other family too** unless you have checked what the budget would drop first: here a declared-but-silent conduit carries zero bytes, sorts last under a volume budget, and would be hidden **first** — precisely the element the whole distinction exists to show — and a second budget is also a second control and a second projection over the twin's *declared* structure. **Decide on the cost, and say which cost it is:** an inert budget over a drawing whose size is bounded by *other* means (here the band's height is clamped by construction) costs **density, not layout**, which is usually acceptable — and accepting it explicitly is what stops the next reader filing "the budget doesn't work" as a bug. Record the consequence plainly: any control reachable only when the budget fires is, on this content, **unreachable**, and any measurement taken in that view describes a screen nobody can open.
**Origin:** Console level-0 R4 build + review, 2026-09-15 (ruled ACCEPT, Authority: implementation (Claude Code), on the reviewer's recommendation); CHANGELOG 2026-09-15; `docs/WORK_ORDER_console_level0_rendering.md` §5 D2 and §13.3.

## LES-310 — Shell variable assignments chained with `&&` before a backgrounded job are swallowed into that job, leaving the rest of the line with EMPTY variables and silently no-op'd commands

**Class:** platform-gotcha
**Scope:** any `Bash` tool invocation or shell script of the form `VAR=x && cmd &` / `A=1 && B=2 && long_cmd & …`; agent-driven one-liners that set up variables and background a build, test run or server
**Consumers:** code-writer, tester, code-reviewer
**Rule:** `&` binds **the entire preceding `&&` list**, not just the last command — so `A=1 && B=2 && server &` puts the *assignments* in the background subshell too, and every later command on the line (or in the same tool call) sees `A` and `B` **empty**. The damage is silent and shaped like success: `grep "$PAT" file` with an empty `PAT` matches everything, `rm -rf "$DIR"/tmp` becomes `/tmp`, `curl "$BASE/api"` hits a relative path, and the exit codes are all zero. Two habits: **never background an `&&` chain** — set variables on their own lines (or `export` them) and background only the single command that needs it, `{ ...; } &` if a group really must detach; and **echo the variables after the backgrounded line** in any script an agent writes unattended, because an empty-variable no-op leaves no other trace. Same family as LES-292 (a `pkill -f` pattern that matches the shell running it): a shell one-liner's *control* semantics, not its commands, are what silently changes the meaning of everything downstream.
**Origin:** Console level-0 R4 build (code-writer's measurement harness), 2026-09-15; CHANGELOG 2026-09-15.

## LES-311 — A test over REAL data is not a test of the REAL configuration: assemble the object through the seam the app uses, or name the branch you are exercising and the live shape you are not

**Class:** verification-idiom
**Scope:** `o2/console/test/layout.real.test.ts`, `o2/console/test/r4.*.test.ts`; any test that builds its subject from a real bundle/fixture by hand rather than through the production assembly path (`buildGraph`, a loader, a DI container, an API client); `o2/console/src/model/graph.ts` (`CastInput`'s fixture-only `deployedCidrByName`/`zoneTypeByName`, superseded by `segmentRoster`), `o2/console/src/views/ElementActions.tsx` (`SegmentFacts`); any view that renders `x ?? <fallback>` as a fact; `o2/console/test/fixtures/realBundles.ts`, `o2/console/test/placement.test.ts` (a layout probe through `realGraph` + `level0OptionsFor`)
**Consumers:** tester, code-writer, code-reviewer
**Rule:** "It runs against the real bundle" is a claim about the **data**, and it is routinely mistaken for a claim about the **configuration**. Our top-K budget tests read the genuine pit bundle and asserted `hiddenPairs > 0` — while constructing the graph **without the conduit roster the API actually publishes**, a shape that cannot occur in the app: live, every observed pair is also a declared conduit, so nothing is ever hidden and the assertion describes a screen no operator can open. The test was not wrong (the budget branch is real and worth covering) — its **name and its silence** were. So: **build the subject through the same seam the application uses** whenever you can, and when you deliberately bypass it to reach a branch, (a) **say so in the test name** (`[no-roster construction]`), (b) **comment the bypass at the construction site**, not in a header far away, and (c) **add a sibling test over the live shape** asserting what the operator actually sees — here: 40 conduits for 40 observed pairs, 40 asserted arcs, `hiddenPairs === 0`, the budget toggle a complete no-op down to every stroke width. Without (c) the suite is green in a world the product is not in. Companion to LES-298's "prove it at the override channel's extremes": that one is about untested *values*, this one about an untested *assembly*.
**Extended 2026-09-15 (Console S5.1.a, A1) — the RENDER consequence: a fixture-only input reads as a finding on every real element.** The segment inspector rendered `deployedCidrByName[name] ?? "not declared"`, and the same for the zone type. Only the golden fixture ever filled those maps — no API field carried them — so on **every real segment of every real bundle** the inspector stated "not declared": an absence of *input* presented as a fact about the *twin*, invisible to a suite whose fixture filled the maps. **Before rendering `x ?? <fallback>` as a fact, trace `x` to the production path that fills it.** Where none does, publish it (here `segment_roster` on the cast summary) or render the fallback as *not published by this server* — never as a statement about the artifact (LES-253). Once a real field exists, make it the **one** source and demote the fixture's direct input to a fixture-only seam, so the live app cannot read two.
**Extended 2026-09-16 (Console S5.1.b) — a geometry PROBE is the same claim, and it decided a disagreement.** A reviewer measured a clone's proposed placement crossing six asserted arcs; the code-writer measured 38 units of clearance. Both had run the real bundle; only one had built the map **as the app builds it** (the graph through `castInputFrom` with the published rosters, and the layout through the same options the view passes, budgets and filters included). Five of six site columns carry a `+N more` box that reserves a grid row, so their boxes end lower; the one group without it ends 112 units higher, and the proposal leaves it. **A probe that assembles its own graph or its own layout options is measuring a map no operator can open** — so build the subject through the live seam, keep one shared fixture that does it (`realBundles.ts`), and when two measurements of the same geometry disagree, check which map each was taken on before deciding who is right.
**Origin:** Console level-0 R4 review, 2026-09-15; CHANGELOG 2026-09-15; `docs/WORK_ORDER_console_level0_rendering.md` §13.4. **Extended by Console S5.1.a fix round 1 (A1), 2026-09-15** — CHANGELOG 2026-09-15 "Console S5.1.a BUILT"; `docs/WORK_ORDER_console_canvas.md` §18. **Extended by Console S5.1.b's placement probe, 2026-09-16** — CHANGELOG 2026-09-16 "Console S5.1.b–c BUILT"; `docs/WORK_ORDER_console_canvas.md` §19.

## LES-312 — A count sentence beside a drawing must count what is DRAWN, not what one input family contributed — and its test fixture must make the families differ in size

**Class:** verification-idiom
**Scope:** `o2/console/src/views/Level0.tsx` (`eastWestNote`), `o2/console/test/r4.ui.test.tsx`; any "showing N of M" / "N hidden" / "N results" disclosure computed over a surface fed by more than one source
**Consumers:** code-writer, tester, code-reviewer, doc-writer
**Rule:** When a drawing is fed by two families (observed + asserted, cached + live, filtered + pinned), the count beside it is trivially written against **the family the developer was thinking about** — and it then contradicts the pixels for every reader. The fix is mechanical: derive the sentence from **the rendered collection**, after every merge and de-duplication, never from an input list or a producer's tally. The verification half is the part that gets skipped: **the fixture must make the families differ in size**, because a test bundle where "observed pairs" and "asserted conduits" happen to be the same set passes with either implementation and certifies nothing — ours coincide at 40 on the pit bundle, so the coincidence *is* the live content and the discriminating case had to be constructed. Assert the wording, the numbers **and** the pair's internal consistency (shown + hidden = total drawn) on a fixture where the families differ, and pin the degenerate phrasings too (one item, zero hidden, all-silent). Extends **LES-307**: that lesson says *disclose the projection everywhere it is rendered*; this one says *make sure the number you disclose is the number the reader can count*.
**Origin:** Console level-0 R4 review (should-fix S2), 2026-09-15; CHANGELOG 2026-09-15.

## LES-313 — When a property is defended at SEVERAL steps, a single-line revert under-reports the fix's necessity — mutate the property, not one line

**Class:** verification-idiom
**Scope:** `o2/console/src/model/roles.ts` (`roleBadgeLabel` and its trim sites), `o2/console/test/`; any revert proof for a fix applied at more than one point (a normalization done on input, in a derived slice, and at the final render; a guard repeated at each layer)
**Consumers:** tester, code-writer, code-reviewer
**Rule:** The standard revert proof — undo the fix, watch the test go red — quietly assumes the fix is **one edit**. When a property is defended at several steps (the filter, the derived slice, the final label each trimmed independently), undoing any *single* step leaves the others covering it, the test stays **green**, and the proof reports "this line was unnecessary" — which is exactly the reasoning that makes a one-line fallback look sufficient while the real defect (the untrimmed value reaching *some* consumer) survives at the sites nobody reverted. Reverting **all three** sites was what turned our proof red. So: **revert the property, not the line** — enumerate every site that enforces it (grep the predicate, not the diff), undo them together, and state in the proof how many sites had to go. If reverting one site legitimately cannot fail, that is worth knowing too: either the site is redundant and should be removed, or the coverage is missing for the path only that site protects.
**Origin:** Owed from Console level-0 R3's `roleBadgeLabel` whitespace fix (iteration 4), recorded with the R4 batch, 2026-09-15; CHANGELOG 2026-09-15. Companion to LES-305 (sweep the parameter, do not sample it) — both are about a proof whose scope is narrower than the property it claims.

## LES-314 — "Assert the bound on real data" is not "assert the CONSTANT does the bounding": drive the input past the knee, or the clamp can be deleted and the test stays green

**Class:** verification-idiom
**Scope:** `o2/console/test/r4.arcs.test.ts` (the band-height bound, `ARC_DEPTH_MAX`); any test of a clamp, cap, timeout, retry ceiling, rate limit or saturation constant whose engagement threshold is far outside the real data's range
**Consumers:** tester, code-writer, code-reviewer
**Rule:** A clamp is only exercised **past its knee**, and real content usually sits far below it — so a test that asserts "the band is bounded" over the shipped bundle proves the *arithmetic before* the clamp, not the clamp. Ours passed with `ARC_DEPTH_MAX` **removed entirely**, because the clamp only engages past a horizontal span of ~1,840 units and no real pair comes close; its own revert proof is what caught it. Two rules: **compute the knee** from the same constants the code uses (`(MAX − MIN)/PER_DX`) and **construct a synthetic input beyond it**, keeping the real-data case beside it as the *representative* assertion — the pair is the test, one alone is not; and **revert-prove the constant specifically**, not the feature, because a feature-level proof goes red for the wrong reason or not at all. Same family as **LES-298** (prove the invariant at the override channel's extremes) and **LES-305** (sweep, do not sample): the property under test lives at the edge of the parameter space, and the shipped content is nowhere near it.
**Origin:** Console level-0 R4 testing (the tester's finding about its own first band-bound test), 2026-09-15; CHANGELOG 2026-09-15; `docs/WORK_ORDER_console_level0_rendering.md` §13.6.

## LES-315 — A screenshot proves nothing until you prove the SERVER IS YOURS: a stale listener from an earlier iteration serves the old build to every shot you take

**Class:** verification-idiom
**Scope:** `o2/console` (`npm run dev` / `npm run preview` / a co-served `axrange serve --spa-dir`); any agent-driven visual, HTTP or browser verification against a server the same agent (or a previous iteration) started on a fixed port
**Consumers:** code-writer, tester, live-verifier, code-reviewer
**Rule:** A port is a **rendezvous, not an identity**. A dev/preview server left running by an earlier iteration keeps the port, so the new one either fails to bind (and the tooling's "port in use, trying 5174" line scrolls past) or is never started at all — and every screenshot, `curl` and browser assertion afterwards describes **the previous build** while looking exactly like success. This slice's first R6 screenshots were of pre-R6 pixels for precisely that reason. Before believing anything served on a port you did not just verify: **assert the listening PID is the one you started** (`ss -ltnp 'sport = :5173'` / `lsof -ti:5173` compared against the child pid you captured), and prefer a **fresh port per iteration** so a stale listener cannot be mistaken for yours. Two corollaries: kill by **PID**, never by `pkill -f <pattern>` (LES-292 — the pattern matches the shell running it); and when the artifact is a built bundle, check the served `index.html`'s content hash against `dist/`, because a correct server serving a stale `dist/` is the same failure one layer down. Same family as LES-247 (a component reported healthy for 40 days because nothing asserted the thing itself was alive).
**Origin:** Console level-0 R6 build (code-writer), 2026-09-15; CHANGELOG 2026-09-15.

## LES-316 — A budget that changes WHICH ELEMENTS EXIST silently deletes every connector anchored to them; measure the loss on real content, on every bundle, before shipping it

**Class:** bug-class
**Scope:** `o2/console/src/layout/{columns,level0,arcs}.ts`, `src/model/budgets.ts`; any top-N, collapse, pagination or sampling projection over a graph drawing whose **edges** anchor to the nodes the projection removes (also: a filtered table whose totals row sums the unfiltered set, a zoom level that drops nodes an overlay still references)
**Consumers:** code-writer, code-reviewer, tester, doc-writer
**Rule:** A budget over **nodes** is implicitly a budget over **edges**, and nothing in the type system says so: the connector code asks each end for its box, a collapsed end has none, and the connector is dropped — **silently, and in the same shape as "there was no link"**. Ours collapsed a site group's tiles and would have taken 16 of 40 east-west pairs off the pit bundle's map and 15 of 31 off `corpapcn-twin`'s. Three rules. **(1) Measure the loss on real content before shipping**, in the projection's own units *and* in the quantity the drawing encodes — the two diverge hard: pit kept **98.9 %** of its east-west bytes (its dominant pair had both ends drawn) while `corpapcn-twin` kept **4.6 % and lost its widest arc entirely**, so a single-bundle check would have certified the defect. **(2) Give the collapsed set a STAND-IN ANCHOR and fold onto it** — merge the connectors that land there into one, carrying summed volume and a `pairs` count, rather than drawing N identical curves between the same two boxes. **(3) Count what remains undrawable** — a link whose two ends fold to the *same* anchor is not a picture and must be reported (`internalPairs`), never dropped in silence. Companion to LES-309 (a budget inert because another family mirrors it): both say the same thing from opposite ends — **a budget's real behaviour is a property of the content, and you only learn it by measuring on every bundle that ships.**
**Origin:** Console level-0 R6 build (code-writer), 2026-09-15; CHANGELOG 2026-09-15; `docs/WORK_ORDER_console_level0_rendering.md` §14.3.

## LES-317 — Plane-specific coordinates in tests are invalidated by the same change that bumps the plane epoch: treat the bump as the signal to re-sweep, and DERIVE the coordinate that must keep meaning the same thing while leaving literal the one that should fail loudly

**Class:** verification-idiom
**Scope:** `o2/console/test/{r3.infra,r4.arcs,r6.columns}.test.ts` (the pinned violating/clean pin coordinates), `o2/console/src/layout/plane.ts` (`PLANE_EPOCH`); any test whose literal coordinates, offsets, indices or window positions are meaningful only against a layout/schema/version the code also stamps
**Consumers:** tester, code-writer, code-reviewer
**Rule:** A test that pins a coordinate to demonstrate a behaviour ("this pin position drags a stub through another column's tiles") is pinned to a **plane**, not to a property — so the slice that bumps the plane epoch has, by definition, invalidated every one of them, and a coordinate that still *passes* afterwards may be passing for a different reason than it was written for. **Treat the epoch bump as the trigger to re-sweep and re-aim the whole set** (LES-305's sweep, re-run on the new plane), and then split them deliberately: **derive** the coordinate whose *meaning* must survive — compute it from the layout ("a point inside the neighbouring column's x-span") so it keeps demonstrating the limit through the next plane change — and **leave literal** the coordinate that is a *baseline* ("this position is clean"), so a future plane change falsifies it **loudly** instead of silently re-aiming it at a position that happens to still be clean. Both kinds are needed: all-derived tests never notice the plane moved; all-literal tests break on every slice and get deleted. R6 had to re-aim every pre-R6 coordinate for exactly this reason.
**Origin:** Console level-0 R6 build + fix round (code-writer), 2026-09-15; CHANGELOG 2026-09-15; `docs/WORK_ORDER_console_level0_rendering.md` §8 (R6 block). Extends LES-305 and LES-289.

## LES-318 — LES-264 recurs on MINIFIED bundles: a build escapes non-ASCII to `\uXXXX`, so grepping `dist/` for a character that is present returns empty and reads as "stale build"

**Class:** tool-gotcha
**Scope:** `o2/console/dist/assets/*.js` (Vite/esbuild output); any grep-driven check that a built, minified or transpiled artifact contains a literal string — especially UI separators and glyphs (`·`, `—`, `↔`, `≥`, arrows, box-drawing) and any non-ASCII label
**Consumers:** code-writer, tester, code-reviewer, live-verifier
**Rule:** esbuild (and therefore `vite build`) emits **ASCII-escaped** output by default, so a `·` in the source is `·` in the bundle and `grep -F '·' dist/assets/index-*.js` matches **nothing** on a bundle that contains it. The empty result is indistinguishable from the answer that matters — *"the build is stale / my change didn't land"* — and this slice spent a cycle chasing exactly that. Check for **both forms** (`grep -E '·|\\u00b7'`), or verify against a token that is ASCII by construction (a `data-testid`, a class name, a content hash from the build manifest), or decode the artifact before searching it. This is LES-264's rule (*an artifact you did not produce in this step may not contain what its source does*) in a new disguise, and a sibling of LES-295 (a NUL byte makes grep skip the file entirely): **an empty grep over a transformed artifact is not evidence of absence** — prove the pattern can match at all before drawing a conclusion from its failure.
**Origin:** Console level-0 R6 build (code-writer), 2026-09-15; CHANGELOG 2026-09-15.

## LES-319 — A projection ranked on ONE axis must disclose what it hides on the axes it is NOT ranked on; choosing the ranking key is the lesser question

**Class:** pattern
**Scope:** `o2/console/src/views/Level0.tsx` (`overflowTitle`/`overflowLabel`/`volumeClause`), `src/layout/columns.ts`; any top-N, collapse, "busiest", "recent" or "first N" projection over records that carry more than one magnitude (hosts *and* bytes; count *and* cost; rows *and* severity)
**Consumers:** code-reviewer, code-writer, doc-writer
**Rule:** Review energy goes to *"is the ranking key right?"*, and that is the **lesser** question — every key is defensible for some question, and the URL can offer the alternative (`?order=cidr`). The load-bearing question is *"what does this ranking hide on the axis it is not ranked on, and does the surface say so?"* Ours ranks tiles by **host count** and shows bytes **on the tile** — so a one-host, 1.77 GB segment collapses and the per-tile byte line structurally *cannot* report it, because a collapsed element has no tile. Measured before the fix: the collapsed sets held **75.1 %**, **65.5 %** and **99.9 %** of their groups' traffic. The rule is mechanical: **whatever magnitude the surviving elements display, the "+N more" affordance must display the same magnitude for the hidden set** — summed from the model, not from the layout (the layout no longer has them) — in every channel the count appears in (LES-307), and **omitted when the hidden set's value is zero**, since "0 B" is noise and its absence is not a claim. A projection that shows a tidy six while hiding three quarters of the volume is a *more* confident wrong picture than the unreadable full set it replaced.
**Origin:** Console level-0 R6 review, 2026-09-15; CHANGELOG 2026-09-15; `docs/WORK_ORDER_console_level0_rendering.md` §14.2. Extends LES-307 and LES-312.

## LES-320 — RE-KEY a golden diff by identity before believing its size: a reordering slice reports a rewrite, and the attribution must enumerate EVERY cause

**Class:** verification-idiom
**Scope:** `o2/console/test/layout/*.golden.json` and any regeneration accounting; any snapshot/golden containing **arrays whose order is part of the output** (layout tiles, query results, rendered rows, serialized sets)
**Consumers:** tester, code-writer, code-reviewer, doc-writer
**Rule:** A leaf-by-leaf diff of a golden is keyed by **array index**, so a slice that only **reorders** reports every element at every index as changed — and the raw figure then gets quoted as evidence of a rewrite. R6's raw diff read **366/374 changed leaves and 326 removed**; re-keyed by identity (tiles by CIDR, arcs by pair, devices by id) it was **69 changed and ZERO removed** — nothing was lost, and the 326 "removals" were embedded `hosts[]` arrays re-indexing under the new ranking. So: **re-key by a stable identity before drawing any conclusion about a golden's size**, and publish the re-keyed number as the headline with the raw one beside it. The second half is where accounts go wrong: **enumerate every cause and order them by size** — ours had three (a plane narrowing from a column-width rule, a band-top fix worth exactly `SITE_PAD`, and the permutation plus new keys), and **the largest was missing from the first account**, which is precisely the shape of an explanation that sounds complete. Finish with an invariant that is cheap to state and hard to fake ("every box that moved moved in `x` only; `y` is invariant at 348"). Companion to LES-300 (*which goldens a model field moves is decided by what the layouts embed*): that one predicts which files move, this one interprets the diff once they have.
**Origin:** Console level-0 R6 review, 2026-09-15; CHANGELOG 2026-09-15; `docs/WORK_ORDER_console_level0_rendering.md` §14.5.

## LES-321 — When a shared test fixture's helper changes its RETURN TYPE, audit every call site — the typecheck finds them only if the language is typed, and only the edited one is ever noticed by hand

**Class:** bug-class
**Scope:** `o2/console/test/fixtures/trimmed.ts` (`withCollapsedTraffic()`), any shared fixture/builder/factory in a test tree; any helper whose signature or return type changes while more than one test file calls it
**Consumers:** code-writer, tester, code-reviewer
**Rule:** Promoting a local helper into a shared fixture is a **signature change in disguise** — ours moved `withCollapsedTraffic()` from a local graph-returning helper into the shared fixture, where it returns a `TopologyExport`, and only the call site inside the test being edited was updated. The slice's own `npm run typecheck` caught it, which is the point worth keeping: **a strict typecheck over the test tree is the audit**, so run it (not just the test runner) after any fixture refactor — a green test run proves nothing about the files that no longer compile, and in an untyped tree the same change fails at runtime in a file nobody opened. The habit is LES-083's and LES-037's, restated for the direction it actually recurs in: **grep the helper's name across the whole tree and fix every caller in the same edit**, because the one under your cursor is the one you will remember and the others are the ones that ship. It is numbered separately from LES-083 rather than folded into it deliberately: LES-083's scope is `axrange/cli/main.py`'s `--json` quiet threading, so the injector would never surface it for a TypeScript fixture, and a rule that only reaches the reader who is already in the file it names is not a rule.
**Origin:** Console level-0 R6 fix round (code-writer's own typecheck, on a defect that round had just introduced), 2026-09-15; CHANGELOG 2026-09-15. Same family as LES-083 (audit every call site of a changed helper) and LES-037 (a shared step fixed at one caller and re-derived at the others).

## LES-322 — A value seeded into `useState` from props goes stale while mounted: hold typed input as `null`-until-touched, and never remount to refresh it

**Class:** bug-class
**Scope:** `o2/console/src/views/ElementActions.tsx` (`AddSegmentForm`: `typedCidr`/`typedName`/`typedZone`); any React (or other retained-mode) form control whose initial value is derived from a prop that can change while the control is mounted — a suggestion, an allocation, a default computed from store state
**Consumers:** code-writer, code-reviewer, tester
**Rule:** `useState(suggestion)` reads its argument **once, on mount**. A form prefilled that way keeps showing the value it mounted with after the suggestion moves — the add-segment form's "next free `/24`" moves whenever a staged add takes that `/24`, a removal frees it, or a re-read changes the claims — so the field ends up proposing a CIDR that is now taken. Both tempting fixes are wrong: syncing the prop into state in an effect **overwrites what the operator typed**, and remounting the form (`key={suggestion}`) **throws away the note and every other field** (and re-fires any mount-time focus, LES-324). Hold what the operator typed **separately, as `null` until the field is touched**, and derive the shown value as `typed ?? suggestion`: untouched, the field follows the suggestion in both directions; touched, nothing later overwrites it. A dependent prefill chains the same way (the name follows the CIDR until the name itself is typed). Test both directions — past a staged add *and back on its removal* — and that a typed value survives a changed suggestion.
**Extended 2026-09-16 (Console S5.1.b) — the other half: keeping a component MOUNTED to preserve state makes its identity your job.** The opposite move to remounting is just as sharp. The clone form is deliberately kept mounted across a re-placement, so a typed CIDR, a typed note and a ticked *mirror conduits* survive the operator moving the tile — which is right, and which means React will also keep it mounted when the *subject changes*: starting a clone of a **different** source reused the first source's form, with the first source's typed values. State that is deliberately preserved needs an explicit identity: **key the component by the subject the state belongs to** (the source segment, the selection object), so a new subject remounts and the same subject does not, and make sibling instances carry **distinct** keys for the same reason. The pair is the rule: `null`-until-touched for a value that must follow a changing input, an explicit key for state that must not follow a changing subject.
**Origin:** Console S5.1.a build, 2026-09-15; CHANGELOG 2026-09-15 "Console S5.1.a BUILT"; `docs/WORK_ORDER_console_canvas.md` §18. Pinned by `o2/console/test/edit.ui.test.tsx` › *an UNTOUCHED field follows the suggestion both ways* and *what the operator TYPED is never overwritten by a changed suggestion*. **Extended by Console S5.1.b's kept-mounted clone form, 2026-09-16** — CHANGELOG 2026-09-16 "Console S5.1.b–c BUILT"; `docs/WORK_ORDER_console_canvas.md` §19.

## LES-323 — The loading window is invisible to a test unless the test holds it open — and a placeholder that answers to the real frame's test id certifies arrival early

**Class:** verification-idiom
**Scope:** `o2/console/test/` (`fixround2.ui.test.tsx`'s held-open fakes), `o2/console/src/views/ElementActions.tsx` (`ActionsUnavailable`'s own `actions-unavailable` test id), `o2/console/src/views/useGraph.ts` (the hold across a cast re-read); any UI test of a property that holds only while a request is in flight — a loading state, a stale-data leak across a subject switch, a value held across a re-read
**Consumers:** tester, code-writer, code-reviewer
**Rule:** Two traps, one window. **(1) A fake API that resolves at once cannot test anything that happens while a read is in flight.** The in-flight render passes in a microtask the test never observes, so *"nothing of imprint A is drawn while B loads"* and *"the typed input survives the apply's re-read"* are green whether or not they hold. Make the fake **stateful and able to hold a read open** — hand back a promise the test resolves explicitly — assert **during** the window, then release it and assert after. The S5.1.a must-fix (imprint A's tiles drawn, and stageable, under imprint B) was caught that way and no other. **(2) A loading placeholder must not reuse the real frame's test id.** If the "loading…" section answers to `element-actions`, `await findByTestId("element-actions")` resolves on the placeholder, the test proceeds before the graph has arrived, and every later assertion runs against the wrong branch or passes vacuously. Give each world — loading, failed, absent, loaded (LES-253) — its **own** test id, so a wait for the loaded frame can be satisfied only by the loaded frame.
**Origin:** Console S5.1.a fix rounds 1–2, 2026-09-15; CHANGELOG 2026-09-15 "Console S5.1.a BUILT"; `docs/WORK_ORDER_console_canvas.md` §18.4. Companion to LES-253 (the three worlds) and LES-293 (which branch a fixture renders).

## LES-324 — Programmatic focus is a contract: take it once per GESTURE, give it back where it came from, and the panel you moved it into owns Escape — which must never discard work

**Class:** pattern
**Scope:** `o2/console/src/App.tsx` (the Escape handler, `openerRef`, `clearSelection`, the unsaved-edits guard), `o2/console/src/views/ElementActions.tsx` (`Frame`, `focusedGestures`); any mount-time `focus()` effect; any global keyboard shortcut in an app that holds unsaved state
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Moving keyboard focus into a panel after a select gesture is right — without it a keyboard user tabs through the whole map to reach the actions the gesture just opened — and it creates three obligations. **(1) Take focus once per gesture, never per mount.** A mount-time `focus()` effect fires again whenever the panel remounts — a graph re-read unmounts and remounts it with the *same* selection — and steals focus from whatever the operator is on, for a selection made minutes earlier. Key the effect to the **gesture's identity** (the selection object, remembered in a `WeakSet`), so neither a remount nor a keystroke re-fires it. **(2) Give it back.** Clearing the selection returns focus to the control that opened it or, when that control has unmounted, to a focusable pane — **never `body`**, which restarts a keyboard user at the top of the document; move focus *before* the re-render so it never falls through, and treat a gesture made with focus on `body` as having no opener. **(3) The panel you moved focus into owns Escape**, resolved in order: clear the explicit selection (unless it equals the URL's own subject, which should cost no press), then step out of the panel, then go up a level. **Escape must never be the gesture that discards work:** going up from the last level leaves the imprint and would drop its pending edits, so with unsaved edits it stays and says why in a live region. A keypress is cheap to make by accident; the deliberate exits — a link, Back, a reload — need a draft, not a keypress guard (GAP-094).
**Origin:** Console S5.1.a fix rounds 1–2 (B3, C, T5 and the nits), 2026-09-15; CHANGELOG 2026-09-15 "Console S5.1.a BUILT"; `docs/WORK_ORDER_console_canvas.md` §18.4.

## LES-325 — Decide "exists", "absent" and "depends on" in the CONSUMER's resolution set and key space — consult the records that re-key an entity, and never borrow a set from another address space

**Class:** bug-class
**Scope:** `o2/console/src/edit/dependents.ts` (`dependentsOf`, `orphanReasons`), `o2/console/src/edit/allocate.ts` (`castStandingOf`), `o2/console/src/model/graph.ts` (`castStatusOf`); any client that predicts how another system — a compiler, an expander, a planner — will resolve a record: orphan flags, dependency confirmations, "already exists" refusals
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Three findings in one slice, one rule. **(a) A join by an editable name breaks the moment the pipeline renames.** The Console tells whether the cast declares an observed segment by joining names, and the compiler applies an `add: segment`'s `name` to the live observed segment — so a segment renamed by a saved add read as "pruned". Before concluding *absent*, consult the records that change the key (here, this file's adds that set a name), and where a rename can still hide (an `override` of `name` hand-edited into the file), say *"renamed or pruned"* rather than choosing one. **(b) "Depends on" is decided by the consumer's resolution set.** Removing an `add: segment` asked to remove the hosts on it as well — even when the segment was **observed**, where the compiler resolves those hosts without the add (`known_cidrs` = observed ∪ added). A record depends on another only when that other is its **only home** in the set the consumer actually resolves against. **(c) Never borrow a published set from another address space.** Orphan flagging counted the installed cast's roster as a home, but the roster's CIDRs are **deployed, relocated** networks (`10.200.3.0/24` for an observed `10.80.20.0/24`), and the compiler never resolves a record against the previous cast — counting them only hid real orphans. So: name the consumer's resolution function, reproduce its set **and its key space** exactly, and test with a fixture whose key spaces **differ** (a relocated roster), because where they coincide the wrong set passes.
**Origin:** Console S5.1.a fix rounds 2–3 (SF1, SF2 and its correction), 2026-09-15; CHANGELOG 2026-09-15 "Console S5.1.a BUILT"; `docs/WORK_ORDER_console_canvas.md` §18.4. Same family as LES-263 (the canonical id) and LES-222 (one addressing id on a seam).

## LES-326 — A default for a closed-set field must be the schema's own default when one value flips a scenario-wide property — say where the default came from, and never re-default to the "richer" value

**Class:** reversal-hazard
**Scope:** `o2/console/src/edit/allocate.ts` (`defaultZoneType`), `axrange/twinconfig/schema.py` (`TwinConfigSegment.zone_type`: `None` → `simple`), `axrange/twinconfig/expand.py` (the scenario-wide VLAN rule); any form, generator or compiler fallback for an enum where a single value changes the behaviour of every sibling — a zone type, a network mode, a scheduler class
**Consumers:** code-writer, code-reviewer
**Rule:** The S5 add-segment form defaulted `zone_type` to **`vlan`**. That is not a per-segment choice in effect: **one** `vlan` segment makes the whole scenario a VLAN zone (`expand.py`: `is_vlan = any(... == "vlan")`), and a VLAN zone **rejects SNAT egress** — so an operator who added one segment without touching the select would silently re-zone every other segment and break the twin's egress. A default nobody chose must be the value that changes nothing else: **the schema's own default** (`simple`). Better, derive it from what the content already declares — the site's dominant declared zone, then the cast's, then the schema default (a tie goes to the schema default; a value outside the closed set is not counted) — and **say which source the default came from** beside the control, so an inherited `vlan` reads as inherited. Do not "improve" the fallback back to `vlan` because much shipped content is VLAN-zoned (the seven `target-environments/` archetypes): that is exactly where the derived chain already yields `vlan`, and the fallback exists for the case where nothing declared anything.
**Origin:** Console S5.1.a fix round 2 (ZT), 2026-09-15; CHANGELOG 2026-09-15 "Console S5.1.a BUILT"; `docs/WORK_ORDER_console_canvas.md` §18.2 call 4.

## LES-327 — A stylesheet `fill` beats an SVG presentation attribute, and jsdom applies NO stylesheet: pin the class the element renders with, and guard the rule that could override it

**Class:** bug-class
**Scope:** `o2/console/src/theme/shell.css` (`.tile` vs `.added-tile`), `o2/console/src/views/Level0.tsx` (the hatch pattern set as a `fill` attribute), `o2/console/test/placement.test.ts`; any SVG styled by BOTH presentation attributes and CSS, and any jsdom/Vitest/RTL suite asserting on appearance
**Consumers:** code-writer, code-reviewer, tester
**Rule:** An SVG presentation attribute (`fill="url(#hatch)"`) is the **weakest** source of a value: any CSS rule that sets the same property — even a plain class selector — overrides it. So a pattern fill set in the markup is silently flattened the moment the element also matches a stylesheet rule that sets `fill`, and **no jsdom test can see it**, because jsdom parses stylesheets but does not apply them: `getAttribute("fill")` is still the pattern, every assertion passes, and the browser draws a flat box. Ours did exactly that — the pending clone tile reused `.tile`, whose rule sets `fill`. Three habits: **(1)** give an element whose appearance comes from an attribute its **own class that sets no such property**, and say so in the stylesheet where the next editor will read it; **(2)** assert the **class the element renders with**, not only the attribute, so the test names the thing that decides; **(3)** add a test that **reads the stylesheet file** and fails if a rule matching that element sets the overriding property — and give that test its own not-vacuous check (assert it found the rules it parses), or a regex that stops matching turns the guard green and empty. The general form: when two styling mechanisms with different precedence can set one property, a unit test in a CSS-less DOM proves nothing about which one wins.
**Origin:** Console S5.1.b build (the review's must-fix, caught in headless Chromium — no unit test could have), 2026-09-16; CHANGELOG 2026-09-16 "Console S5.1.b–c BUILT"; `docs/WORK_ORDER_console_canvas.md` §19.4. Companion to LES-290 (a TS mirror of CSS constants needs a test that reads the CSS).

## LES-328 — A set/grouping function over two concatenated lists must dedupe by key first, or a value present in both changes the grouping

**Class:** bug-class
**Scope:** `o2/console/src/layout/added.ts` (observed segments + the file's added ones through `groupIntoSites`), `o2/console/src/edit/allocate.ts`; any call of the shape `group(f([...listA, ...listB]))` where the two lists can name the same entity
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Concatenating two lists and handing the result to a set function is only correct while the lists are disjoint, and the moment they overlap the **duplicate is counted twice** — which is not a cosmetic double-render but a different answer, because grouping rules read counts. Ours asks `groupIntoSites` where an added segment would land, over the observed segments **plus** the added ones; a segment added on an **observed** CIDR then appeared twice, and the singleton rule (a bucket of one folds into `misc`) saw a bucket of two and pulled it out — so the same segment was reported in a group the map does not draw it in. Dedupe by the entity's own key **before** the set function, not after, and write the regression as *"a CIDR named twice does not pull a singleton out of misc"*: a test that asserts only the member count will pass on the duplicate. Ask it wherever a "what is in play" list is assembled from a source of truth plus a set of pending edits — that pair overlaps by construction, since an edit may name something that already exists.
**Origin:** Console S5.1.b build (review), 2026-09-16; CHANGELOG 2026-09-16 "Console S5.1.b–c BUILT"; `docs/WORK_ORDER_console_canvas.md` §19.4.

## LES-329 — A generated default carries an assumption: export the assumption WITH the data, name the members it is false for, and guard it where it is generated

**Class:** pattern
**Scope:** `scripts/gen_role_listen.py` (`_DIALED_BY_A_SERVING_PERSONA`, `check_dialed_caveat`), `o2/console/src/model/roleListen.generated.ts` (`ROLES_WITH_DIALED_EMITS`), `o2/console/src/model/roles.ts` (`defaultListenFor`); any table, catalog or default set DERIVED from another artifact whose vocabulary does not carry everything the derivation needs
**Consumers:** code-writer, code-reviewer, doc-writer
**Rule:** A derivation is only as true as the field the source artifact does **not** have. Ours builds a per-role listen set from the persona catalog, where a `real-service` persona's emits are not split into what its daemon serves and what its host dials — so five roles' defaults hold a port the role *dials*. Three things make that honest instead of a quiet fabrication, and all three are required: **(1)** compute what you can and **name the exceptions in data** — a list of the members the assumption is false for, generated from the same pass, exported beside the table rather than written in a comment; **(2)** carry it **to the point of use** — the form that offers the prefill repeats the caveat and names the typed role first when it is one of them, because a caveat only in the generator's header reaches nobody who needs it (LES-307's channel rule, applied to a derivation rather than a projection); **(3)** **guard the exception list where it is generated** — a check that fails loudly when a named member stops matching the shape the caveat describes, so a catalog rename cannot leave the UI naming a role that no longer exists. And register the missing field as a gap with an owner (GAP-095) rather than inventing a heuristic: a derived default that quietly picks one interpretation is worse than one that says what it cannot know. Exclude, by name and with the reason, any source entry whose purpose makes it meaningless for the derivation (`loadgen-*`, which characterizes capture throughput).
**Origin:** Console S5.1.c build, 2026-09-16; CHANGELOG 2026-09-16 "Console S5.1.b–c BUILT", call 3; `docs/WORK_ORDER_console_canvas.md` §19.2; `docs/PRODUCT_GAPS.md` GAP-095. Companion to LES-228 (resolve a seam against the producer's real schema) and LES-307 (disclose in every channel).

## LES-330 — A side effect committed beside a reducer dispatch outlives a refused action: ask ONE pure question, then do both

**Class:** bug-class
**Scope:** `o2/console/src/App.tsx` (a clone's pin, session placement and selection beside `stageBatch`), `o2/console/src/edit/store.ts` (`batchRefusal`, exported for exactly this); any dispatch/mutation whose caller also performs an effect the store cannot undo — a persisted write, a navigation, a selection, a toast
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A reducer that can **refuse** an action leaves the caller's neighbouring side effects standing, and they are usually the ones with consequences outside the store: a clone's placement written to `layout.json`, a selection moved, a route changed. The tempting fix — re-check the same condition at the call site — is the actual bug, because two independent copies of a rule drift and the caller's copy is the one nobody tests. Instead **export the reducer's own decision as a pure function** and have both call it: the caller asks once, returns early on a refusal (the reducer will record the reason if the action is dispatched anyway), and otherwise dispatches *and* commits its effects. Keep the reducer's internal check too — the store's atomicity must not depend on every caller having asked first — and say in its docstring that the redundancy is defence in depth, not dead code, or a later reader will delete one of them. The test to write is the one that makes the refusal happen and then asserts the **effects**: no pin written, no selection moved, nothing navigated.
**Origin:** Console S5.1.b build (review should-fix S4), 2026-09-16; CHANGELOG 2026-09-16 "Console S5.1.b–c BUILT"; `docs/WORK_ORDER_console_canvas.md` §19.4. Same family as LES-272 (the write boundary belongs at the call site) and LES-274 (gate the side-effecting tail call).

## LES-331 — A clamp must test the boundary it is ABOUT, and a measurement must use the same reference the rule uses — when two measurements disagree, both may be right

**Class:** verification-idiom
**Scope:** `o2/console/src/layout/added.ts` (`proposedClonePlacement`'s clamp against the site column's box), `o2/console/test/placement.test.ts`; any clamp, bound or guard expressed against a nearby-but-different reference — a container edge vs. a neighbour's edge, a deadline vs. a timeout, a quota vs. a rate
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A clamp written against the wrong reference is right in most of the data and wrong exactly where the two references separate — and both a passing measurement and a failing one can then be correct. Ours proposed a clone's tile in the next slot of its source's column and clamped it against the **east-west rail**; the rule it was about is *"never leave the column"*. On five of six site columns the two agree, because a `+N more` box reserves a grid row and pushes the column's bottom to 680; on the sixth (exactly six segments, so no `+N more`) the column ends at **568**, the proposal falls 100 units below its own column into the corridor the arcs drop through, and the rail test still calls it clear at 668 < 706. A reviewer measured **six crossed arcs**; the author measured **38 units of clearance**; both were measuring real states of the real bundle. Three habits follow: **name the boundary the rule is about and clamp against that object's own geometry**, not a landmark that usually coincides with it; **take every measurement through the same seam and the same reference the rule uses** (LES-311), and when two disagree, reconcile the *inputs* before arguing the conclusion — the disagreement is data; and **sweep the whole population** (every group of every real bundle, at both budgets) rather than the one case that prompted the fix, because the separating case is by definition the unusual one.
**Origin:** Console S5.1.b build (the reviewer/code-writer measurement dispute, settled by re-measuring), 2026-09-16; CHANGELOG 2026-09-16 "Console S5.1.b–c BUILT"; `docs/WORK_ORDER_console_canvas.md` §19.4. Companion to LES-305 (sweep the parameter, do not sample it) and LES-311 (measure through the seam the app uses).

## LES-332 — A counter-minted id outlives its mount the moment anything PERSISTS it: mint ids unique to the session, and test the collision by restoring AND then creating a second

**Class:** bug-class
**Scope:** `o2/console/src/edit/gestures.ts` (`newGestureId`), `o2/console/src/edit/draft.ts`, `o2/console/src/App.tsx`; any id minted from a per-mount/per-process counter (`useRef(0)`, a module `seq++`) that is later written to storage, a file, a URL or an API — batch ids, correlation ids, temporary keys
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A counter is unique only within the lifetime that owns it, and **persisting the id silently widens that lifetime**. Gesture ids came from a `useRef(0)` that resets on every workspace remount; the draft then persisted each record's whole tag, so a clone staged, reloaded, and followed by a second clone produced **two batches under one id** — one change-list row labelled with the first, counting both, whose single undo removed **all 47 records and both pins**. The id also keyed the grouping, the expander state and a `data-` attribute, so one collision was wrong in four places at once. When a value is going to be stored, mint it **unique to the session or globally** (`crypto.randomUUID()`, with a random-prefixed counter as the fallback — never a bare counter, which is exactly the bug), and ask of every generated id: *what is the widest scope this value can reach, and is it unique there?* **The regression test must reproduce the SEQUENCE, not the state:** restore the persisted batch **and then create a second one**; a test that restores and stops passes happily on the collision, because the collision needs a second minting. Same family as LES-030 (an id stable within one plan is not unique across deploys of it).
**Origin:** Console S5.1.d build (must-fix M2, found independently by the tester and the reviewer), 2026-09-16; CHANGELOG 2026-09-16 "Console S5.1.d–e BUILT"; `docs/WORK_ORDER_console_canvas.md` §20.4.

## LES-333 — A comparison against a source that may not have ARRIVED has three outcomes, not two — and nothing may re-stamp the thing you compare against

**Class:** silent-success-trap
**Scope:** `o2/console/src/edit/draft.ts` (`DraftWorld.fingerprint`: a value, `null`, or `"unknown"`), `o2/console/src/App.tsx` (the draft write effect, and the stamp in `performSave`'s success continuation); any "has X changed since?" check whose baseline is fetched separately from the thing being checked
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Two independent reads resolve in either order, so a check that compares against "the current value" can run **before that value exists** — and `null` for *not here yet* is indistinguishable from `null` for *there genuinely is none*. The draft's recompile warning compared its stored `code_fingerprint` against a cast that had not resolved, so on the ordinary ordering the warning was skipped in silence, exactly as it would be for a bundle that publishes no fingerprint. **Wait for the baseline to settle and pass three outcomes** — a value, *there is none*, and *it could not be read* — rendering the third as its own honest sentence (LES-273). The second half is worse and easier to miss: **nothing may re-stamp the stored baseline except the operation that legitimately supersedes it.** A write effect keyed on the etag stamped the *new* fingerprint onto the draft, destroying the comparison for every later restore too — a bug that erases its own evidence. Advance such a stamp in exactly one place (here, a save's success continuation), and pin that with a **structural** test (no effect keyed on the etag may write the stamp), because a behavioural test of a race is decisive only in isolation (LES-337).
**Origin:** Console S5.1.d build (must-fix M3), 2026-09-16; CHANGELOG 2026-09-16 "Console S5.1.d–e BUILT"; `docs/WORK_ORDER_console_canvas.md` §20.4. Same family as LES-240 / LES-253 / LES-273 (don't-know is not no). **Example:** GAP-088 re-described the sidecar `code_fingerprint` as the PIT stamp but the draft's "recompiled since" check kept comparing against it, so it could never fire (fixed 2026-09-19 by stamping drafts with the bundle's separately-published content fingerprint instead — CHANGELOG 2026-09-19).

## LES-334 — "Clear of everything" and "what does this cover" must be derived from ONE object set, or the one box you always hit is the one you never name

**Class:** bug-class
**Scope:** `o2/console/src/layout/added.ts` (`drawnBoxes`, `laneX`, `coveredBy`); any placement that avoids a set of obstacles while a second routine reports collisions against a separately-assembled set — a layout lane, a collision map, a reserved-range allocator
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A placement rule and its disclosure are two consumers of one question — *what is drawn here?* — and the moment each assembles its own answer they disagree about exactly the elements one of them forgot. Ours parked an unplaceable tile "right of every column" while the coverage report walked the tiles and the overflow boxes: the parking lane landed squarely on the O7 unplaced-population tile, which was in neither list, and reported `covers: []`. **Build one set of drawn boxes, name it, and have both the avoidance and the report read it** — then a new element type is added once and both behaviours follow. Pair it with the containment rule that makes the report readable: **you do not cover the box you are inside** (an appended tile sits in its own column's box by construction, and reporting that as a collision would train the operator to ignore the message). And decide per placement kind whether overlap is *allowed and disclosed* (a proposal the operator is about to move) or *forbidden* (a tile drawn until they move it) — both are legitimate, and the difference must be stated where the rule lives.
**Origin:** Console S5.1.d build (must-fix M1), 2026-09-16; CHANGELOG 2026-09-16 "Console S5.1.d–e BUILT"; `docs/WORK_ORDER_console_canvas.md` §20.4. Same family as LES-025 (one implementation, reused) and LES-304 (publish the geometry rather than re-deriving it).

## LES-335 — A back door into your own state must pass the front door's refusals — restoring is reconciliation, never a replay

**Class:** bug-class
**Scope:** `o2/console/src/edit/draft.ts` (`reconcileDraft`'s `validRecord` + `crossRecordConflict` deps), `o2/console/src/edit/records.ts` (`invalidStoredRecord`); any rehydration path — a draft, a cache, an undo stack, an import, a session restore — that puts records into a store the normal path validates on the way in
**Consumers:** code-writer, code-reviewer, tester
**Rule:** Every validation a product performs sits on the *gesture* path, and a restore is by construction **not** that path: it puts records into the store having passed none of them. Ours could restore an `add: host X` into a file that had since gained `suppress: X` — a combination the writer refuses and the compiler calls an error — leaving both in the store and both in the next PUT. So run the **same** refusals on the way back in, against the world **and** against the records this restore has already accepted (a draft can conflict with itself), and report each drop with **the rule's own message** rather than a generic "could not restore". Two sharpeners. **A shape check is not a re-validation:** refuse what *this app could not have produced* (a hand-edited or corrupted document) and let everything else through to the server, which stays the authority — a stricter client silently blocks legal edits (LES-271). And **classify each refusal by whether it can change**: a world-dependent one (the file gained a conflicting record) should *keep* the stored copy so the operator can resolve the conflict and get their work back, while a permanent one should drop it — with a discard control offered either way, so nothing is stuck and nothing is lost silently.
**Origin:** Console S5.1.d build (review should-fixes S2/S3/S8, and fix-round D), 2026-09-16; CHANGELOG 2026-09-16 "Console S5.1.d–e BUILT"; `docs/WORK_ORDER_console_canvas.md` §20.1/§20.4. Companion to LES-251 (report, never adopt — a restore is not a merge).

## LES-336 — A confirmation prompt whose reason has gone must DISMISS itself, never act

**Class:** bug-class
**Scope:** `o2/console/src/App.tsx` (the leave guard: `leaveAnyway` vs the auto-dismiss effect); any modal, prompt or pending-confirmation whose precondition is live state the operator can change while it is open
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A prompt is a question about a **decision**, and only the operator's answer may make it. When the condition that raised it disappears — they undid the last edit, discarded the draft, someone else's save removed the conflict — the tempting implementation is "the reason is gone, so proceed with what they were doing". That is the app deciding on their behalf: our leave guard navigated away the moment the pending count hit zero, moving the operator somewhere they had never agreed to go. **Resolve the prompt by dismissing it and staying put**; keep the action behind the explicit control (and keep the destination it was going to, so *Leave anyway* goes exactly where the click was going, not to a re-derived route). The general shape: a guard may cancel itself, never complete itself. Its neighbour rule is about *where* the guard lives — one guard on the single function every navigation passes through, rather than one per link, so a new link cannot forget it — and about *which* keypresses get a one-click escape at all: a cheap-to-press key (Escape) refuses and explains, it does not offer "leave anyway" (LES-324).
**Origin:** Console S5.1.d build (review should-fix S4), 2026-09-16; CHANGELOG 2026-09-16 "Console S5.1.d–e BUILT"; `docs/WORK_ORDER_console_canvas.md` §20.4. Companion to LES-274 (gate the side-effecting tail call, not the whole continuation).

## LES-337 — `waitFor` certifies that a value BECAME right, not that it IS right: run every new or changed test alone, and prefer a structural guard for a race

**Class:** verification-idiom
**Scope:** `o2/console/test/` (any `waitFor`/`findBy*` assertion over state a later effect can rewrite), `o2/console/test/draft.test.ts` (the structural fingerprint guard); any async UI assertion in a suite that shares a module, a storage or a worker between tests
**Consumers:** tester, code-writer, code-reviewer
**Rule:** `waitFor(() => expect(x).toBe(y))` passes on the **first** tick where the predicate holds — so a value that is briefly right and then re-stamped by a later effect certifies green, and whether it does depends on scheduling that a different test order changes. A test that passes in its file and **fails alone** is not flaky: it is telling you it was relying on a coincidence. So **run every test you add or change alone as well as in its file** (`vitest run -t "<name>"`), and when a round has touched many, sweep them individually — ours swept all 44 in one file and found no further order dependence, which is a result worth recording either way. Two companions: assert the **settled** value (await the effect that writes it, or assert the absence of a later write) rather than the first acceptable one; and when the property is a **race**, add a **structural** guard that does not depend on timing at all — "no effect keyed on this value may write that stamp", asserted over the source — and say in the test's own name that the behavioural pair is decisive only in isolation, so a future reader does not over-read a green full-suite run. Same family as LES-313 (a proof whose scope is narrower than the property it claims).
**Origin:** Console S5.1.d build (the fix round's re-probe, and the fingerprint race behind M3), 2026-09-16; CHANGELOG 2026-09-16 "Console S5.1.d–e BUILT"; `docs/WORK_ORDER_console_canvas.md` §20.4/§20.5.

## LES-338 — A browser probe pointed at CHECKED-IN content writes into the repo: point probes at a scratch copy

**Class:** tool-gotcha
**Scope:** `twin-configs-cast/` (the tracked cast bundles, and their operator-owned `layout.json` / `*.assertions.yaml` siblings), `axrange serve --imprints-dir`, any headless-browser or `curl` probe driven against a server whose data root is inside the working tree
**Consumers:** tester, code-writer, live-verifier
**Rule:** A probe that *drives the product* also **writes what the product writes**. Ours dragged a tile in headless Chromium against a server pointed at the checked-in `twin-configs-cast/`, and the placement pin landed in `corpapcn-twin/layout.json` — a tracked bundle gaining an operator file nobody authored, caught only because a hygiene test asserts that reading the real bundles creates none of them (`tests/test_api_console.py::test_the_real_bundles_have_no_operator_files_and_reading_them_creates_none`). Two habits: **copy the data root to scratch and point the probe at the copy** (`--imprints-dir <scratch>/bundles`), and **check `git status` after any probe that exercised a write path**, because the artifact may be a file the suite does not police. Keep the hygiene test itself, and never "fix" it by adding the stray file to an ignore list — its whole job is to notice this. Same family as LES-315 (prove the server you are measuring is yours): both are about a probe's *side effects* on the environment it is measuring, one on the port, one on the tree.
**Origin:** Console S5.1.d build (a browser probe during the fix round; the stray file removed, a backup kept in the session's scratch), 2026-09-16; CHANGELOG 2026-09-16 "Console S5.1.d–e BUILT"; `docs/WORK_ORDER_console_canvas.md` §20.7.

## LES-339 — `jest -t` takes a REGEX, so a test name containing parentheses selects nothing — and with `--passWithNoTests` that is a green exit

**Class:** tool-gotcha
**Scope:** `o2/grafana/plugins/axellio-axrange-control-panel` (jest, `npm run test:ci`), and any `-t`/`--testNamePattern`/`-k`-style selector over a test name written in prose; any runner invoked with `--passWithNoTests`
**Consumers:** tester, code-writer, code-reviewer
**Rule:** `-t "…"` is a **regular expression**, not a substring: a test whose name contains `(GAP-083)`, `(a claim nobody measured)` or any other parenthesized aside is matched against a pattern in which those parentheses are a **capture group**, so the pattern does not match the literal name and the selector returns **zero** tests. With `--passWithNoTests` in the script — the `@grafana/create-plugin` default — that run **exits 0**, prints a suite summary with everything skipped, and looks exactly like a pass. Measured here: `-t "renders … (GAP-083)"` → **52 skipped, exit 0**; the same name with the parentheses escaped → **1 passed**. So: **escape the pattern** (or select by file plus a unique unparenthesized fragment), and **read the count, never the exit code** — a targeted run that does not print a visible `1 passed` (or the number you expected) selected nothing. The habit that catches it for free is LES-337's: run each new or changed case alone and confirm it actually ran. This bites hardest in a house style that writes prose test names, which is this repo's.
**Origin:** Console S7 build (verifying the GAP-083 no-measurement cases individually), 2026-09-16; CHANGELOG 2026-09-16 "Console S7 BUILT and D5 EXECUTED"; `docs/WORK_ORDER_console_canvas.md` §21.3. Companion to LES-337 (run it alone) and LES-264/LES-295 (a matcher that silently matches nothing).

## LES-340 — In a `@grafana/create-plugin` package a production build is a BUNDLING gate, not a correctness gate — and a tolerated baseline must be reported as "none of them are mine"

**Class:** tool-gotcha
**Scope:** `o2/grafana/plugins/axellio-axrange-control-panel` (`npm run build` vs `npm run dev`, `npm run typecheck`, `npm run test:ci`); any webpack config that registers `ESLintPlugin` / `ForkTsCheckerWebpackPlugin` behind an `--env development` branch
**Consumers:** code-writer, code-reviewer, tester
**Rule:** `@grafana/create-plugin`'s webpack config registers **ESLint and fork-ts-checker only in development mode**, so `npm run build` (production) compiles and bundles **without linting or type-checking anything**. A green production build therefore proves the bundle was produced — not that the code type-checks, and not that it lints. Run `npm run typecheck` and `npm run test:ci` explicitly, and treat the build as the packaging gate it is. The second half is how to report it **in a package that carries a tolerated baseline** (here LES-042's ~TS2686 noise): a bare error count is unreadable — "244 errors" looks like a catastrophe and "339 → 244" looks like an improvement nobody made. State three things: the **count before and after**, that **every one of them is the known baseline class**, and — the load-bearing one — that **none of the errors is in a file this change created or touched**, with the per-file counts of surviving files shown to be unmoved. That is a claim a reader can check; "typecheck is at its baseline" is not.
**Origin:** Console S7 build (the plugin gate: build 0, `test:ci` 68 passed, typecheck 339 → 244 all baseline), 2026-09-16; CHANGELOG 2026-09-16 "Console S7 BUILT and D5 EXECUTED"; `docs/WORK_ORDER_console_canvas.md` §21.6. Extends LES-042 (the baseline itself) with how to *use* and *report* it.

## LES-341 — A cross-origin iframe cannot report its own failure: never render a load claim, always give a new-tab escape, and check that each knob you name governs the DIRECTION you are embedding in

**Class:** platform-quirk
**Scope:** `o2/grafana/plugins/axellio-axrange-control-panel/src/components/console/ConsoleEmbed.tsx`; any cross-origin `<iframe>`, and any troubleshooting text that names a framing/embedding setting
**Consumers:** code-writer, code-reviewer, doc-writer
**Rule:** A cross-origin frame is opaque to its parent: **`onLoad` fires for a blocked frame as readily as for a rendered one**, `contentWindow` is unreadable, and a browser can refuse the load with nothing observable from the embedding document. So a panel must **claim no load state** — a "loaded" badge that is wrong half the time is worse than no badge — and must always offer a **new-tab link**, which is the one-click test the frame itself cannot perform. Then the note that lists what to check has to be *right*, because an operator will work the list top-down and stop when one entry seems to explain it. Two rules for that list. **Order by likelihood for the deployment shape you ship** — ours names the **browser's** own reachability of the embedded origin first (every other call in this plugin goes server-side through a proxy, so operators have been configuring hosts reachable from the *server*, and this frame is the exception), then **mixed content** (an https parent framing an http child, silently blocked and the exact shape of our example URL), then the parent's CSP `frame-src`, then the child's own `X-Frame-Options`/`frame-ancestors`, marked inert where the child sends neither. And **verify each named knob governs the direction you are embedding in**: Grafana's `[security] allow_embedding` sets `X-Frame-Options` on **Grafana's own** responses — it governs Grafana *being embedded*, never Grafana *embedding another origin*. Naming it would send an operator to flip a setting that changes nothing and then rule the real cause out. A wrong entry in a troubleshooting list is worse than a missing one.
**Origin:** Console S7 build (review round — the framing note's first cut named `allow_embedding`), 2026-09-16; CHANGELOG 2026-09-16 "Console S7 BUILT and D5 EXECUTED"; `docs/WORK_ORDER_console_canvas.md` §21.4. Same family as LES-253 (do not render a state you cannot know).

## LES-342 — A placeholder string in an IDENTIFIER field becomes write-path data the moment a picker offers it: refuse it in one helper every consumer shares

**Class:** bug-class
**Scope:** `o2/grafana/plugins/axellio-axrange-control-panel/src/io/targets.ts` (`GET /api/targets`'s `node: "(unset)"`), `axrange/api/service.py` (`list_targets`); any API that substitutes a human-readable placeholder into a field whose values are **identifiers** a UI will offer for selection
**Consumers:** code-writer, code-reviewer, tester
**Rule:** LES-277's trap is a tolerant default rendered as a measurement. This is its **mirror, and worse**: a placeholder in an **identifier** field is not merely displayed, it is **selectable**, and a selected value is sent back as data. `list_targets` emits `node = cfg.default_node or "(unset)"` for an adapter config that declares an API URL but no default node; rendered as an ordinary option, `(unset)` could be picked and sent verbatim as `{"target_node": "(unset)"}` on a **real deploy**, where it names no cluster node at all. So: when an API substitutes a placeholder into an identifier field, **decide once — in a shared helper — which value is a placeholder and what to say about it**, and have every picker render it **disabled with the reason** (showing it matters: the operator learns the adapter *is* configured, just nodeless) rather than hiding it or letting it through. Each consumer then maps that one decision onto its own widget, which is the only part they cannot share. The deeper fix is upstream — publish `node: null` plus a reason field rather than a magic string — but a client that offers a magic string as a choice is a defect regardless of what the server does.
**Origin:** Console S7 build (review), 2026-09-16; CHANGELOG 2026-09-16 "Console S7 BUILT and D5 EXECUTED"; `docs/WORK_ORDER_console_canvas.md` §21.1/§21.4. The mirror of LES-277 (a placeholder rendered as a measurement).

## LES-343 — Shrinking a doc that "accumulated" is a MOVE, not a REWRITE: verbatim-extract dated blocks to an archive, prove it with a line-set diff, and read the stitched seam

**Class:** pattern
**Scope:** `CLAUDE.md`, `docs/CHANGELOG.md`, `docs/SESSION_STATE.md`, and any doc that grows by appending dated/historical blocks over time (a "direction" blockquote, a build-progress paragraph, a per-session handoff section) until it is too large to load into every session's/agent's context
**Consumers:** doc-writer, code-reviewer
**Rule:** When a doc like this needs to shrink, the fix is never to summarize or drop the old content — this project's "record, don't rewrite history" discipline applies to size-driven moves exactly as it does to content ones, and a summary silently discards whatever the summarizer judged unimportant, a judgment nobody asked for. Instead: (1) find the content's own natural boundaries — a markdown blockquote is blank-line-bounded even when it contains bare `>` paragraph breaks inside it; a changelog/handoff entry is bounded by its next dated heading — and extract each unit whole, verbatim; (2) where a boundary falls *inside* one physical line (a long paragraph mixing a still-true sentence with a historical run), cut on a substring anchor instead of a line number, and assert the anchor appears **exactly once** before trusting it; (3) leave a short dated pointer list behind, newest first, one line per moved entry; (4) prove nothing was lost by diffing the *set* of non-empty stripped lines removed from the source against the set added to the archive — anything in neither is a real loss and blocks the change (a whole-line move needs no more than this; a substring-anchored cut needs the moved/kept substrings checked directly, since its "before" and "after" whole line will never match each other by construction, which the generic diff alone would misreport as loss). The one failure mode that check cannot catch: stitching two verbatim fragments together with nothing between them reads as a run-on ("**all wired** Only **Stage 6**..." — a missing full stop). After any substring-anchored cut, read the rendered result at the seam; don't stop at "the anchors matched."
**Origin:** `docs/DIRECTION_LOG.md` / `docs/changelog/2026-H1.md` / `docs/handoffs/2026-09.md` extraction, 2026-09-16; CHANGELOG 2026-09-16 "Doc archival restructure: CLAUDE.md / CHANGELOG.md / SESSION_STATE.md split for context budget".

## LES-344 — `doc-stats.py --check` polices the LIVE source of truth, not a frozen number: a doc-only pass can go stale from a SIBLING agent's concurrent commits, with nothing wrong in the docs you touched

**Class:** live-env-fact
**Scope:** `.claude-doc-stats.json` (the `files`/`counters`/`claims` this project polices — currently `CLAUDE.md`, `docs/OP_CATALOG.md`, `docs/VALIDATOR.md`), `scripts/doc-stats.py --check`; any pipeline stage that runs while a sibling agent is concurrently touching the source a counter is computed from (here: `tests/`, while another agent added tests during this same session)
**Consumers:** doc-writer, tester
**Rule:** `doc-stats.py` recomputes each counter from source (e.g. `pytest --collect-only`) at the moment `--check` runs — it has no memory of what was true when the session started. A pass that never edits code can still see `--check` fail, because a sibling agent's in-flight commits moved the real number out from under a claim that was correct an hour earlier: this session's baseline `--check` was clean (`3131` matched), and after a concurrent agent's work landed in `tests/`/`axrange/api/` the same check reported `CLAUDE.md:36/39/155 claims 3131 pytest tests, source says 3145` — with the op-type and catalog-rule counters unaffected, confirming it was specifically the test suite that grew. Two consequences: don't trust a clean `--check` from the *start* of a session to still hold at the *end* — re-run it as the last step, against whatever the tree looks like then; and when it fails, tell apart drift in a block you're *moving* (fix it in the new home; an archived historical entry is allowed to quote the number that was true when it was written) from drift in a claim you're *keeping* (fix the number in place) — the latter is a legitimate, expected doc-writer fix caused by someone else's work, not scope creep, because the counter is defined as "whatever the source says right now," not "whatever was true when I started."
**Origin:** Doc archival restructure session, 2026-09-16; CHANGELOG 2026-09-16 "Doc archival restructure: CLAUDE.md / CHANGELOG.md / SESSION_STATE.md split for context budget" — a concurrent agent's Console S1 API/tests work changed the collected pytest count mid-session.

## LES-345 — A recipe must install what provides every binary AND every dlopen'd mechanism it uses; a `command -v` census is structurally blind to the latter

**Class:** bug-class
**Scope:** any provisioning recipe/script that installs packages for a client tool (`config_exec` recipes, `twin-configs/*/scripts/`, `axrange/personas/drivers/command.py`'s `extra_packages`); any package-presence check built as a binary census
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A package name does not reliably ship the binary a client tool needs, and a binary's presence does not mean every runtime mechanism it can load is present either — three distinct ways a recipe can under-install and still look complete: (1) `samba` does not ship `smbclient` (that's a separate package); (2) `snmpd` does not ship `snmpget`; (3) `ldap-utils` pulls no SASL GSSAPI plugin (`libsasl2-modules-gssapi-mit`), and a **dlopen'd mechanism has no binary at all**, so a `command -v`/`which` census — the obvious way to verify "did the install work" — is structurally unable to see it. The GSSAPI gap presented as "the server refused GSSAPI," a server-side-shaped symptom with a client-side packaging cause. When a recipe declares what a client needs, enumerate every binary the tool execs AND every plugin/mechanism it can dlopen (SASL mechanisms, PAM modules, NSS modules are the recurring shape), and census a dlopen'd mechanism **by file path**, not by command.
**Origin:** CHANGELOG 2026-09-21 "Protocol engines" (finding 2); commit `193b4846` (`fix(engines): five findings from the live run`); `axrange/personas/drivers/command.py:725-730` (`extra_packages`); `twin-configs/svc-engines/scripts/prepare-engine-client.sh:89-97` (censuses `libgssapiv2.so` by file path).

## LES-346 — A fail-loud check that discards the tool's own stderr names a cause it never observed

**Class:** bug-class
**Scope:** any self-check/health-check that shells out to a real client/server tool and reports failure with a hand-written diagnosis rather than the tool's own output (`config_exec` provisioning self-checks, `run-script` fail-loud assertions, `deploy-sensor`/`verify-capture`-style checks)
**Consumers:** code-writer, code-reviewer
**Rule:** "Fail loud" means the operator learns the true cause, not merely that *something* failed. A check that runs a tool, discards its stderr, and substitutes an author's best-guess diagnosis can be **precisely as loud as a silent failure while pointing at the wrong thing** — measured twice in the same round: the file server's SMB self-check blamed `smb.conf` for a failure whose real cause was elsewhere entirely (the guest-access signing/encryption settings, not the share config), and separately `ss -lnt`/`-lnu` output was mis-parsed (see the companion finding below) in a check that reported "FAIL" without ever printing what `ss` actually returned. Both cost real debugging time precisely because the fail-loud message was confident and wrong. Rule: any check that shells out must **keep and surface the tool's own stdout/stderr** in the failure message, and a hand-written diagnosis is a hypothesis to offer alongside the raw output, never a replacement for it.
**Origin:** CHANGELOG 2026-09-21 "Protocol engines" (finding 3); commit `ffd20d43` (the SMB check fix) and `218fa719` (the `ss` column / snmp OID fixes) — both found live on dev45, twin `svc-engines`.
**Recurrence (2026-09-28):** `axrange/config_exec/backend.py` recorded a failed `run-script`/`provision-app`'s message as `(stdout or stderr)[:300]` — the first 300 characters of *stdout*, so a `FAIL` line written to *stderr* was silently dropped from the diagnosis during the corp-twin devlab live run 1. Fixed to keep the stderr tail (plus the stdout tail), not the stdout head. CHANGELOG 2026-09-28 "corp-twin devlab live run 1"; commit `e2fe96ac`.

## LES-347 — A verifier that reports process presence/exit-code but not task completion reproduces the very failure mode it was built to catch

**Class:** silent-success-trap
**Scope:** any driver/verifier that counts a client run as `ok` from exit status alone — browser/headless-client drivers (`axrange/personas/drivers/browser.py`), any `drive:` engine whose "did it work" signal is process exit rather than the outcome the process was supposed to produce
**Consumers:** code-writer, code-reviewer, live-verifier
**Rule:** Headless Chromium exits **0** having rendered a DNS-failure or connection-refused error page — a completely normal, successful browser session from the process's point of view, and a false success from the persona's. This round built `scripts/verify_wire_protocols.py` specifically to stop trusting shape over substance at the *sensor*, and then made the identical mistake one layer down at the *client*: the browser engine's own `ok=` counter was exit-code-only, so `ok=8` meant nothing about whether any page actually loaded. The general form: a verifier (or a driver's own success counter) that reports "the process that was supposed to do X ran and exited 0" instead of "X actually happened" is not a weaker version of the real check — it is a **false positive generator wearing the shape of a check**, and it is especially dangerous when it is the newest, most-trusted instrument in the room (this round's own protocol-fidelity tooling). Judge completion from the actual artifact of success (here: the DOM — an error page and an empty load are counted as distinct, real outcomes), never from the launcher's exit code alone.
**Origin:** CHANGELOG 2026-09-21 "Protocol engines" (finding 6); commit `193b4846` (`fix(engines): five findings from the live run`); `axrange/personas/drivers/browser.py`.
**Recurrence (2026-10-01, corp-twin capture-rate run):** the `scripted` driver's traffic read clean on the wire (SMB connection established, bytes flowing) while caddy/smbd reset the filler stream after the first window — `corp-file-share-client` logged 494,783 failed messages since 2026-09-28. Judged from the engine's own application-layer failed-message counters, not wire shape. `docs/reference/capture-rate/2026-10-01-corp-twin-ladder.md`; `docs/PRODUCT_GAPS.md` GAP-120.

## LES-348 — A verb that mutates live state and records a safety verdict must INVALIDATE the recorded verdict before acting, and every failure path must leave it invalidated

**Class:** ordering-invariant
**Scope:** any command/op that flips a live target's state and writes a safety/containment verdict derived from reading it back (`axrange/o2control/containment.py`'s `egress_live`/`te egress`; the tap/tfc containment gate; generalizes to any "flip X, then trust the recorded verdict that X is safe" op)
**Consumers:** code-writer, code-reviewer
**Rule:** A recorded safety verdict (here: `egress_phase` on a `configure-router` op row) is read by other code paths (`te info`, the containment gate) that have no way to know it might be stale or that an in-flight action could invalidate it mid-read. The fix is to write an `*-pending-unverified`/`*-unverified` sentinel to the SAME field **before** touching the guest, so any reader mid-action sees "unverified," never a stale "safe." Then: only a verified success (the engine's own `--healthcheck` asserting, and naming the SAME phase a separate `--show` read) overwrites the sentinel with a trusted value; **every** failure path — a non-zero exit, a QGA timeout, a transport exception, a corrupt persisted-outputs blob — must be caught and must leave the sentinel standing (never silently coerce to a default that could be read as safe). The rule generalizes past this one gate: whenever a verb both acts on live state and records a derived verdict about it, invalidate the verdict as the FIRST write of the verb, not the last.
**Origin:** `docs/WORK_ORDER_corp_twin.md` §10 P0 review round (commit `a59932c2`, "one-budget fail-closed read-back"); `axrange/o2control/containment.py` docstring "FAIL CLOSED".

## LES-349 — A rewritten line-oriented network config (ifupdown/netplan) must be reloaded AND validated through the tool's own path, unconditionally, in the same apply

**Class:** bug-class
**Scope:** any script that rewrites `/etc/network/interfaces*` or a netplan YAML and expects the change to take effect without invoking that tool's own reload/dry-run — `twins/*/scripts/serve-*.sh`, any `config_exec` recipe that touches guest network config
**Consumers:** code-writer, code-reviewer, live-verifier
**Rule:** Writing the file is necessary but not sufficient, in two ways found on the same script. **(1) The reload must be unconditional, not "only if the file changed."** Debian 12 renders netplan into systemd-networkd, which keeps an old static address in memory across a bridge teardown/rebuild until `networkctl reload` actually re-reads it — a bare `systemctl daemon-reload` only re-runs netplan's *generator*, not networkd's own reload, so a prior partial apply can leave the file byte-correct while the live state is still wrong, and a change-gated reload never fires to fix it. Rewrite every port first, THEN run one unconditional `netplan generate` + `networkctl reload` that fails loud, THEN flush/enslave ports. **(2) The rewrite must be validated by the tool's OWN parser/dry-run in the same apply.** ifupdown's stanza syntax has no end-of-line comments — a rewritten `iface … manual  # comment` parses with no error and silently misapplies, deferring the failure to the next boot/reload. Check every rewrite immediately with `ifquery -a --list` (or `ifup --no-act -a`; `netplan generate` before `networkctl reload` when netplan is present) and die on failure inside the same apply, not on the next one.
**Origin:** `docs/WORK_ORDER_corp_twin.md` §10 P0 review round 2 (commit `a59932c2`, the ifupdown parser check) and §11.D live run 1 (commit `e2fe96ac`, the networkd reload ordering fix); `twins/corp-twin/scripts/serve-l2switch.sh`.

## LES-350 — Proxmox's `ipconfigN` is positional against `netN`: an address-less leg must still occupy its slot, or every later NIC's address shifts

**Class:** platform-quirk
**Scope:** `axrange/adapter_proxmox/rest.py` (`_build_ipconfig_body`), `axrange/planner/pass1_emit.py` (multi-homed IP allocation, L2 legs); any code building a VM's `netN`/`ipconfigN` pairs for Proxmox
**Consumers:** code-writer, code-reviewer
**Rule:** PVE keys `ipconfigN` positionally against `netN` — there is no per-interface name or MAC binding, only index. Omitting `ipconfigN` entirely for an address-less NIC (instead of emitting an explicit "no address" placeholder AT that index) silently reassigns every later NIC's intended address to the wrong `ipconfigN` slot the moment an address-less leg sits ahead of an addressed one. An address-less leg must keep a positional placeholder (`{"ip": None}` in the planner's IR) so `ipconfigN` numbering stays aligned with `netN` regardless of which legs carry addresses — pinned by `pass1_emit.py`'s "an address-less leg ahead of an addressed one keeps a placeholder" rule and a test.
**Origin:** `docs/WORK_ORDER_corp_twin.md` §10 P0 (commit `54c2b046`), fidelity gap G16.

## LES-351 — An unquoted heredoc runs command substitution on backticks inside its own PROSE COMMENTS, not just its data lines

**Class:** bug-class
**Scope:** any shell recipe that writes a config file via an unquoted heredoc delimiter (`<<EOF`, not `<<'EOF'`) so `$VAR` expansion works inside it — provisioning recipes (`twins/*/scripts/*.sh`, `axrange/config_exec/` templates)
**Consumers:** code-writer, code-reviewer
**Rule:** An unquoted heredoc has no concept of "this is a comment explaining the file, not a command" — every backtick pair inside it runs command substitution, on every line, data or prose. A comment that quotes a command name for readability (`` `tcpdump` ``, `` `ip route get` ``) is executed as that command, with whatever side effects it has, as a side effect of writing an unrelated file — here, launching bare `tcpdump` forever, discovered only because it left the guest in promiscuous mode with AppArmor denials. If a heredoc needs `$VAR` expansion, escape every literal backtick that must NOT expand, or split the work into a quoted (`<<'EOF'`) template plus a separate substitution pass (`sed`/`envsubst`). A repo-wide test that refuses an unescaped backtick inside any unquoted heredoc in a recipe is cheap insurance once bitten.
**Origin:** `docs/WORK_ORDER_corp_twin.md` §11.D live run 1 (commit `e2fe96ac`; the bug shipped in `f20b9825`, 2026-09-23); `twins/_shared/scripts/serve-sink.sh`; `tests/test_twin_recipe_heredocs.py`.

## LES-352 — A test guarded by "skip if the checked-in fixture is missing" goes silently green forever once that content MOVES

**Class:** silent-success-trap
**Scope:** any test with a `skipif`/early-return guard keyed on a path's existence, over content that is checked into THIS repo and is supposed to always resolve — `tests/test_api_console.py`'s real-bundle tests, Console vitest `realBundles.ts`, any "skip if the corpus isn't there" guard; distinct from LES-133's drift test, which legitimately skips when an OPTIONAL sibling repo is absent
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A guard reading "skip this test if the directory/file is absent" is a correctness check only while that path stays put. The moment the content it exercises MOVES (a folder rename, a consolidation like `twin-configs/` → `twins/`), the guard silently degrades from "irrelevant on this checkout" to "green forever, checking nothing" — and nobody notices, because a skip and a pass render identically in a summary count. The fix: a guard over content that is supposed to always be present in this repo must FAIL, not skip, when it can't find its target, and at least one sibling test must assert the real content's presence outright with no skip path at all (`test_the_real_bundles_are_present`, pinned specifically so a future rename can't go unnoticed). When moving or renaming checked-in content, grep for every `skipif`/`if not path.exists(): return` that names the old path and either re-point it or convert it to a hard failure.
**Origin:** `docs/WORK_ORDER_twin_consolidation.md` Phase 2 record (commit `be76169d`, 2026-09-26): "the real-bundle tests do not skip... fail rather than let the `skipif` guards go green."

## LES-353 — `apt-daily-upgrade.timer` fires soon after a fresh boot (not just on schedule) and races provisioning: it holds the dpkg lock and can restart QGA mid-exec

**Class:** platform-quirk
**Scope:** any provisioning recipe running `apt-get`/`apt` in the first minutes after a fresh Debian/Ubuntu VM boots — `run-script`/`provision-app` steps, any cloud-init image with `apt-daily-upgrade.timer` enabled
**Consumers:** code-writer, code-reviewer, live-verifier
**Rule:** `apt-daily-upgrade.timer` is `Persistent=true`, so a run missed since the last boot (typical for a freshly imaged VM) fires within minutes of boot, not on its usual schedule — squarely inside a provisioning window. It races provisioning two ways: it holds the apt/dpkg lock (a lock-timeout retry is necessary but not sufficient), and if it upgrades `qemu-guest-agent` itself, the QGA restart **orphans** whatever exec was mid-flight over that channel — which reads as a hung/timed-out step, not as "the agent restarted under you," and cost real diagnosis time before the true cause was found. Quiesce it before the first package step: disable both `apt-daily.timer` and `apt-daily-upgrade.timer`, and wait (bounded) for any run already in flight to finish, THEN proceed. A lock-timeout retry alone papers over the lock contention but not the QGA-restart orphaning.
**Origin:** `docs/WORK_ORDER_corp_twin.md` §11.D live run 1 (commit `e2fe96ac`); `axr_apt_quiesce` in `twins/_shared/scripts/serve-sink.sh`.

## LES-354 — Every traffic driver renders for the persona's declared `os_family`, never the host's actual OS; nothing validated the two agree until asked to

**Class:** bug-class
**Scope:** `axrange/o2control/traffic.py` (`resolve_traffic_bindings`); any persona/cast content that assigns a catalog persona onto a host by role; any twin config's `tfc/*.yaml`
**Consumers:** code-writer, code-reviewer, content
**Rule:** A scripted/command traffic driver picks its client implementation (shell, PowerShell, `smbclient`, …) from the **persona's** `os_family` field, and the host it runs against is chosen by role binding elsewhere — the two are never cross-checked unless something does it explicitly. A persona shipped with the wrong `os_family` (here: a Windows-role variant mislabeled `linux`, predating the Windows scripted driver, when the bug was invisible because the Linux path ran regardless) fails every instance at execution time with an opaque QGA error (`Failed to execute child process`), not at validation. `tfc validate|plan|deploy` now refuse any assignment whose persona `os_family` disagrees with the host's actual OS (manifest OS when `--images` is given, else a trusted `wait-os-ready` recording — never a fallback default) — but this is a repo-wide guard added after the fact, not a schema-level guarantee; a NEW twin/cast can still ship the same mismatch and will only be caught by `tests/test_tfc_persona_os.py`'s strict sweep or a live `tfc validate` run. Any persona-assignment content should be validated against host OS as part of authoring, not discovered live.
**Origin:** `docs/WORK_ORDER_corp_twin.md` §11 "Live run 3 — full twin at gate 0.9" (2026-09-28); CHANGELOG 2026-09-28 "`tfc` refuses a persona whose `os_family` differs from its host's"; GAP-111.

## LES-355 — A FIB `ip route get … from <addr>` probe is invalid (martian source, EINVAL) when `<addr>` is the probing host's own address, regardless of the routing policy under test

**Class:** platform-quirk
**Scope:** any health-check/assertion script that uses `ip route get <dst> iif <leg> from <src>` (or an equivalent kernel FIB lookup) to prove a routing/policy decision live, sourced from one of the asserting host's OWN addresses — `axrange/adapter_proxmox/egress_render.py`'s `axr_assert_measure`; generalizes to any router/gateway self-test
**Consumers:** code-writer, code-reviewer
**Rule:** The Linux kernel treats a packet whose *source* address is local to the receiving host as martian on input (`EINVAL` with `accept_local=0`, the default) — a FIB lookup with `from <the router's own address>` fails this way **on every kernel**, independent of whatever policy routing is actually in effect, and reads exactly like "the assertion doesn't work on this kernel" rather than "the probe itself is malformed." This was live-verified as a false alarm: the underlying policy was correctly applied the whole time. The fix is structural, not a kernel-version workaround: probe with a **non-local host address** that legitimately belongs to the leg under test (the lowest qualifying host on the leg's connected prefix, excluding the router's own addresses and any known sink/self address) — never the asserting host's own interface address — when simulating traffic that would arrive FROM that leg.
**Origin:** `docs/WORK_ORDER_corp_twin.md` §11.D live run 2 (2026-09-28); CHANGELOG 2026-09-28 "Egress `measure` healthcheck probes every LAN leg from a foreign (non-router) source"; `docs/PRODUCT_GAPS.md` GAP-100.

## LES-356 — A fire-and-forget mirror/replication tunnel must not rely on PMTU discovery: there is no sender to receive the ICMP frag-needed, so an oversized encapsulated frame is dropped silently, not fragmented

**Class:** bug-class
**Scope:** any GRE/tunnel netdev carrying mirrored, replicated, or otherwise one-way fire-and-forget traffic across an MTU-constrained transport — `axrange/adapter_proxmox/ssh.py`'s `attach-gre-tunnel` (`_GRETAP_PMTU_OPTS`); any future replicated-traffic or anycast-style path
**Consumers:** code-writer, code-reviewer
**Rule:** Standard PMTU discovery assumes a sender that can react to an ICMP "fragmentation needed" by lowering its own segment size — a mirror has no such sender (it is a passive tap copy, not a connection endpoint), so with the kernel's default PMTU-discovery behavior on a gretap, an outer packet that exceeds the transport MTU is simply dropped (`tx_errors` for an inner-DF'd packet hitting `tnl_update_pmtu` E2BIG, `tx_dropped` for one that then fails `ip_fragment`) with **no signal anywhere** — not on the sender (none exists), not as a routing error, not in the mirror's own qdisc counters (which report 0 dropped, because the loss is inside the tunnel's xmit, past the queue). Reproduced deterministically in an isolated netns rig: 150/150 oversized frames lost with default PMTU discovery, 0/150 lost with `nopmtudisc ignore-df`. The fix for any such tunnel is to create it with `nopmtudisc ignore-df` (or the equivalent "always fragment, never rely on discovery" flag) up front, and to assert it on the ADOPT/idempotent path too (a changelink, not just at creation) — an existing netdev created before the fix, or reset by an operator, otherwise keeps the wrong behavior forever (same shape as LES-190's qdisc-assertion rule). This trades some throughput (fragmentation + reassembly cost) for correctness; raising the transport MTU end-to-end removes the cost but not the need for the flag as a floor.
**Origin:** `docs/WORK_ORDER_corp_twin.md` §11 "Live run 3 — mirror loss on the tap tier" (2026-09-28); CHANGELOG 2026-09-28 "Mirror gretaps carry outer DF off"; GAP-112.

## LES-357 — A `ContextVar` overlay set in the request thread does not carry into a background/worker thread; a test that injects a SYNCHRONOUS background runner cannot catch that it's missing

**Class:** silent-success-trap
**Scope:** `axrange/personas/registry.py` (`overlay`/`current_overlay`/`_OVERLAY`); any code that sets a `ContextVar` (or thread-local) inside a request handler and later runs work on a separately-spawned native thread — a detached/background job, a thread pool, a `threading.Thread` target; any test harness that swaps a "run this in the background" seam for a synchronous `lambda job: job()` stand-in
**Consumers:** code-writer, code-reviewer, tester
**Rule:** A `contextvars.ContextVar` is **not** automatically inherited by a new `threading.Thread` — a fresh thread starts with its own empty context, so a value set via `.set()` on the request thread is invisible to `.get()` calls made inside a spawned worker unless the worker explicitly re-establishes it (here: re-entering the same `with overlay(...):` block inside the thread's target function, using the value captured on the request thread before handoff — `axrange/o2control/traffic.py`'s `_job()`, "A thread does not inherit the request's persona overlay: re-enter it"). The trap is that this is exactly the kind of bug a test suite can miss **while staying green**: a test that injects a synchronous background runner (`app.state.tfc_host_background = lambda job: job()`) executes the "background" work on the very same thread as the request, so the ContextVar is trivially still set — the test proves nothing about the real threaded path and would pass whether or not the re-entry code exists. At least one test must exercise the REAL background mechanism (an actual separate thread, not a same-thread stand-in) to catch this class of bug; a synchronous test double is fine for testing everything else about the job, but not for testing context propagation.
**Origin:** Unified Console per-host persona apply (`docs/reference/ui-consolidation/00-design-ruling.md` §3.2, closing GAP-105 for the per-request path); `axrange/personas/registry.py:105-120`; `axrange/o2control/traffic.py` `_job()`; the synchronous test double at `tests/test_unified_console.py` (`app.state.tfc_host_background = lambda job: job()`).

## LES-358 — A ticking "now" clock can render an age younger than the data when a live answer arrives between ticks; take the later of the clock and the arrival time

**Class:** bug-class
**Scope:** `o2/console/src/api/runtimeHooks.ts` (`useNow`); any UI that computes a displayed age/staleness as `now - checked_at` where `now` comes from a periodically-ticking clock (`setInterval`/`useState` re-rendered every N seconds) rather than being read fresh on every render
**Consumers:** code-writer, code-reviewer
**Rule:** A `now` value held in state and only advanced every `intervalMs` (`useNow`, `docs/design/16-unified-console.md`'s recorded-vs-live labelling) is, between ticks, an **increasingly stale snapshot** of wall-clock time — ordinary and harmless for display purposes UNTIL it is compared against a live value's own `checked_at`, which can arrive (and be genuinely newer than the last tick) at any moment. Computing the shown age as `ago(checked_at, now)` with that stale `now` can then put `checked_at` AFTER `now`, rendering as an age that reads younger than the data actually is (in the worst case, negative) — the opposite of the honest-staleness rule `docs/design/16-unified-console.md` §3 rule 3 exists to satisfy. The fix is not a faster tick (that only narrows the window): compute the `now` fed into any freshness calculation as `Math.max(tickClock, receivedAt-or-generated_at)` — the later of "the last time we re-rendered the clock" and "the last time we actually received an answer" — so a fresh arrival can never appear to be from the future relative to the clock driving its own display.
**Origin:** Unified Console Range/Twin areas (`docs/design/16-unified-console.md` §3 rule 3, §6); `o2/console/src/areas/range/RangePage.tsx` (`const now = Math.max(tick, (range.data?.generated_at ?? 0) * 1000)`); `o2/console/src/areas/twin/TwinPage.tsx` (`const now = Math.max(tick, live.receivedAt ?? 0)`).

## LES-359 — Never open SQLite with `immutable=1` unless the file truly cannot change: "there is no `-wal` right now" proves nothing about the next millisecond

**Class:** bug-class / silent-success-trap
**Scope:** `axrange/planner/state.py` (`StateStore._open_readonly`, `readonly_opens`); any code that opens a SQLite database another process may be writing — the API's read routes against the live `state.db`, tooling that inspects a store while a deploy runs
**Consumers:** code-writer, code-reviewer, tester
**Rule:** `immutable=1` is a promise to SQLite that the file will not change for the life of the connection, so it takes **no locks and ignores the WAL/shared-memory entirely**. Using it to avoid leaving `-wal`/`-shm` files beside a store — gated on "no `-wal` exists, so no writer has it open" — is a check-then-act race: a writer can open, commit and close (checkpointing its WAL back into the main file) while the immutable reader is mid-scan, and the reader then reads torn pages — `database disk image is malformed`, or silently wrong row counts (reproduced 40/40 in review). The correct read-only open is `mode=ro` + `PRAGMA query_only=ON` + `busy_timeout`: it takes proper shared read locks and reads a consistent snapshot while writers commit and checkpoint around it. The price — SQLite may leave `-wal`/`-shm` files beside a WAL-mode db that the read-only connection cannot delete — is accepted; a reader never writes a frame into them. Test the property directly: hold a read-only cursor open while a SEPARATE connection commits and closes, then finish the scan and assert the snapshot is whole.
**Origin:** Unified Console fix round 2 (`20f6fc91`, "reads never write") introduced `immutable=1` to stop read routes creating `-wal`/`-shm` beside `/home/rocky/axr-svcengines.state.db`; the focused review reproduced the corruption (scratchpad `imm2.py` vs `imm3.py`); removed in fix round 3; pinned by `tests/test_unified_console_fixes2.py::test_a_readonly_reader_survives_a_writer_committing_and_closing_under_it`.

## LES-360 — Fixing a lower-layer transport defect can change traffic shape enough to trip a DIFFERENT, previously-dormant defect one layer up: tune one layer at a time with a revert ready

**Class:** bug-class
**Scope:** any multi-layer mirror/capture pipeline tuning (tap → tc mirror → gretap → transport vnet → sensor NIC/decap → capture appliance); `twins/*/scripts/*mtu*`, `o2/tcpdump-sensor/`, the adapter's gretap/PMTU handling — generalizes to any live performance-tuning pass across a stack of independently-owned layers
**Consumers:** code-writer, code-reviewer, live-verifier
**Rule:** The corp-twin capture-rate ladder fixed transport fragmentation (full-size mirrored frames split into two outer IP packets at the 1500-MTU transport vnet, `docs/LESSONS.md` LES-356's floor) by giving the twin's gretaps a 1600-byte path — and the very next rung immediately hit a NEW failure: the sensor NIC's GRO, which had never mattered while every big frame arrived as two small fragments, started reassembling whole GRE-carried TCP segments into ≤3.7 KB superframes and overran the capture appliance's AF_XDP queue (24,227 drops in 25 s). The first defect had been masking the second by never producing the packet shape that triggers it — fixing it did not add a new bug, it REMOVED THE COVER off one that was already there. When tuning a multi-layer pipeline: change one layer, keep a cheap revert for that one change alone (a flip script/`ethtool`/`ip rule`, not a redeploy), re-run the full drop-counter sweep before touching the next layer, and expect — don't be surprised by — a fix at one layer exposing a dormant defect at the neighboring one.
**Origin:** `docs/reference/capture-rate/2026-10-01-corp-twin-ladder.md` (rung R1 mirror-MTU fix immediately followed by rung R2's sensor-GRO queue drops); `docs/CHANGELOG.md` 2026-10-01 "corp-twin capture-rate ceiling"; `docs/PRODUCT_GAPS.md` GAP-119.

## LES-361 — tshark's flat `tcp.analysis.*` is meaningless on a capture merged from MULTIPLE tap points: de-duplicate per observation point before judging correctness

**Class:** tool-gotcha
**Scope:** any tshark/Wireshark TCP analysis (`tcp.analysis.retransmission`, `.duplicate_ack`, `.out_of_order`) run against a pcap assembled from more than one tap/mirror point merged into a single capture (GRE-stripped) — mirror/sensor correctness tooling, `o2`/capture-fidelity checks, any future axRange tooling built on raw tshark output
**Consumers:** code-writer, tester, live-verifier
**Rule:** tshark keys TCP analysis purely by IP 5-tuple. A topology tapped at more than one hop (host port, router leg, 802.1Q trunk) and merged into one capture puts the SAME wire segment into the pcap 2–4 times as it crosses each tapped point — tshark reads every repeat after the first as a retransmission, measured as high as **48 %** "retransmissions" on traffic independently known to be healthy. Before computing any retransmission/dup-ACK/reorder ratio from a multi-hop-tapped capture, de-duplicate per **observation point** (eth.src/eth.dst/VLAN tag + 5-tuple direction) and drop exact copies (same IP ID/seq/len/ack/flags — a frame switched across two tapped trunks keeps the same IP ID; a real retransmission carries a fresh one) before reading the ratio off what remains. A flat 5-tuple `tcp.analysis.*` read on such a capture is not a noisier version of the real signal — it is the wrong signal, and large enough to fail a drop-free/zero-regression gate that the real per-observation-point numbers would pass clean.
**Origin:** `docs/reference/capture-rate/2026-10-01-corp-twin-ladder.md` "Correctness method — why not tshark's `tcp.analysis.*`" (baseline B0 measured 48 % false "retransmissions" before the per-observation-point method was built); `docs/CHANGELOG.md` 2026-10-01 "corp-twin capture-rate ceiling".
