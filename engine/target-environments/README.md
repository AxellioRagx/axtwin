# Stage-2 sample scenario library

Corpus-derived axRange scenarios synthesized by Claude Code from the Stage-1
knowledge corpus (`scenario_ai_research/stage2/CORPUS_DIGEST.md`, 324 videos). **Saved here
for repeatable redeployment + experimentation** — each is versioned IR on the wip
branch, gated through `axrange validate` and `axrange plan`. WIP / not ratified;
not on `master`.

## Archetypes

| Scenario | Corpus basis | Segments + hosts | Deployable layers | Deferred (designed) |
|---|---|---|---|---|
| [`soc-detection-lab`](soc-detection-lab/scenario.yaml) | HTB/SimSpace/home-lab "red+blue SOC range" | dmz/corp/soc + router; web victim (ubuntu), Win workstation, attacker, Suricata/Zeek **sensor** + Wazuh **SIEM** | Infra + Apps&Tools | OP-ACT attack chain, OP-OVL defense overlay, Windows provisioning |
| [`ad-domain-lab`](ad-domain-lab/scenario.yaml) | "Top-10 AD attacks", Vulnerable-AD, Rubeus/BloodHound/Responder | flat corp + router; **DC (windows-server-2022, promoted)** + workstation (Win), attacker w/ full AD kit (impacket/netexec/bloodhound.py/Responder), sensor + SIEM | Infra + Apps&Tools + **AD identity** (`role: adds` DC promotion + domain users/groups + **ws01 domain-join** via `joins:`, W2a+W2b+§6.3) | OP-ACT AD kill-chain, OP-OVL defense overlay, Vulnerable-AD weakening |
| [`ctf-range`](ctf-range/scenario.yaml) | CTF/BTLO/TryHackMe + scoring | flat arena + router; player kit, CTFd (docker), web + service/pwn + Windows challenges (planted flags), sensor | Infra + Apps&Tools | scoring/solve-detection + player activity (OP-ACT), rich `config` flag-injection, Windows challenge setup, OP-OVL |
| [`ot-ics-range`](ot-ics-range/scenario.yaml) | Labshock/OpenPLC, grid/chemical/aviation OT ranges | **3 Purdue zones** (IT/OT-DMZ/OT) + conduit; Win eng-ws, attacker, MQTT historian, 2 Modbus PLCs (pymodbus), Node-RED HMI, OT sensor | Infra (3 zones) + Apps&Tools | conduit firewall **policy** (default-deny), OT attack activity (Modbus — OP-ACT), OT detection overlay (Modbus/DNP3 — OP-OVL), physical-process sim |
| [`vuln-management-lab`](vuln-management-lab/scenario.yaml) **(new)** | Tenable/Greenbone "VM program start-to-finish", STIG | mgmt/servers + router; Greenbone scanner, analyst, vulnerable web + weak-services (anon FTP/SMB) + Windows fleet | Infra + Apps&Tools | scan→triage→remediate→rescan activity (OP-ACT), STIG/patch as `config`/`role`, remediation overlay (OP-OVL) |
| [`purple-team-range`](purple-team-range/scenario.yaml) **(new, flagship)** | "train red+blue together" (HTB/SimSpace), Atomic Red Team, Cyber Kill Chain | **4 segments** (user/server/soc/red) + core; **DC** + Win lateral pair, crown-jewel app + file server, sensor + SIEM, attacker + **emulation** host | Infra (4 segments) + Apps&Tools + crown-jewel **data overlay** + **AD identity** (DC promotion + domain users + **ws1/ws2 domain-join** via `joins:`, W2a+W2b+§6.3) | ATT&CK emulation kill-chain (OP-ACT), detection-coverage overlay + Sigma rules (OP-OVL), Windows app provisioning |
| [`dfir-hunt-lab`](dfir-hunt-lab/scenario.yaml) **(new)** | BTLO/CyberDefenders "investigate the PCAP", Redline/FLARE/Volatility, host forensics | enterprise/soc + router; compromised victim (planted benign artifacts) + Win endpoint, DFIR analyst kit (Volatility/tshark/Sleuth Kit/YARA), OpenSearch+Zeek hunt stack | Infra + Apps&Tools | compromise activity that seeds evidence (OP-ACT), live PCAP-from-sensor retrieval, Windows DFIR (Redline/FLARE), hunt-content overlay (OP-OVL) |

All seven **pass `axrange validate` and `axrange plan`** against the fixture image catalog
(`tests/fixtures/images`: `debian-12-cloud@v1`, `ubuntu-server-22@v5`, `vyos@1.5`,
`win11-finance@v4`).

## Use

```bash
# validate (hard gate)
.venv/bin/axrange validate target-environments/<name>/scenario.yaml --images tests/fixtures

# plan (DAG). With a real adapter.conf it reads DEFAULT_NODE + the sensor IP from it:
.venv/bin/axrange plan target-environments/<name>/scenario.yaml \
    --images tests/fixtures --adapter-config adapter.conf

# ...or, with NO adapter.conf (e.g. the authoring research subsystem), supply a placement node
# and a sensor IP on the CLI (the tapped hosts' mirror ops need a sensor target):
.venv/bin/axrange plan target-environments/<name>/scenario.yaml \
    --images tests/fixtures --node <node> --sensor-ip <ip>

# deploy (needs a configured cluster + adapter.conf; use a self-contained state db)
.venv/bin/axrange deploy target-environments/<name>/scenario.yaml \
    --images tests/fixtures --adapter-config adapter.conf --db target-environments/<name>/state.db
```

## Conventions used across the library

- **Apps are Linux-only, `pkg` + one `script` each.** Install actions use `pkg` (apt) and a
  single idempotent `script` per app — the implemented `config`-class kinds (provision-app +
  run-script over the QGA channel). `config`/`role`/`container` kinds and Windows app/role
  install are deferred, so Windows hosts deploy as substrate.
- **Scripts are idempotent and run as root in-guest.** Each `scripts/*.sh` is `set -euo
  pipefail`, safe to re-run (provision-app may retry), and degrades gracefully when WAN egress
  isn't up yet (logs `deferred (needs egress)` rather than failing the deploy).
- **`requires:` is sharpened to image capabilities** — `kernel:ebpf`/`kernel:audit` on
  sensors, `kernel:edr-hooks`/`kernel:powershell-5plus` on Windows victims,
  `kernel:container-runtime` on Docker hosts — all checked against the manifests' `provides:`.
- **No tap-related content in these IRs** (`docs/WORK_ORDER_tap_eligibility.md`,
  Authority: user, 2026-07-28) — a twin substrate no longer authors `tap:`
  flags at all; which victims/targets get mirrored to a sensor is decided
  entirely by a standalone tap plan (`axrange tap deploy`), resolved against
  the deployed twin's published topology, not by anything in this file.

## Caveats for real deploys (recorded by Stage 2)

- **Windows templates** (`win11-finance`) must exist in the target cluster's catalog with the
  guest agent; the fixture catalog references them but the platform must actually have them
  built. Stage-1/imagebuild has only produced the Debian template (`debian-12-cloud`) so far.
- **apt/pip/Docker egress (✅ via the router — Phase 2):** each archetype includes a **router**
  node (`fw`/`gw`/`conduit`/`core`, `kind: router`) whose LAN IPs are the network gateways. The
  router gets a WAN uplink (DHCP on `vmbr0`) and **masquerades LAN→WAN** (`OP-HOST-090
  configure-router`), so the VLAN-zone hosts get real internet/apt/pip/Docker egress +
  inter-segment routing — **live-verified on dev45** (host `ping 8.8.8.8` via router NAT;
  cross-segment; sensor captured both). The router is the segmentation chokepoint; per-rule
  firewall policy (default-deny conduit / OT air-gap) is a follow-on (IR firewall-rule
  extension). Real deploys still need the package mirror / Docker registry reachable *through*
  the router's WAN.
- **`state.db`** per scenario keeps deploys independent and resume-safe (`state.db` is
  gitignored).
- These reference `tests/fixtures/images` as the catalog; point `--images` at a real catalog
  for production redeploys.

See `scenario_ai_research/stage2/CORPUS_DIGEST.md` for the corpus→layer mapping, the gaps Stage 2 found,
and the deferred OP-ACT/OP-OVL/Identity/Data specs each archetype is designed around.
