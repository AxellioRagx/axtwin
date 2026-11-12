# switch-test — the native-switch fixture

canvas-test's shape (`office` 10.71.10.0/24, `servers` 10.71.20.0/24, `gw`,
`corp-workstation` ×2, `file-server`) plus one **imageless `kind: switch`**,
`access-sw`, wired by `links[]`: a trunk from `gw` (native `office`) on SFP1
and one access link per workstation on P1 and P2. The workstations are a
`count: 2` group, so the links name the members by instance id
(`corp-workstation01` / `02`, `docs/design/native-switches.md` §12 S1). The
second workstation carries a `members[]` role override (`jump-host`).

On Proxmox, which declares `native-switch`, `access-sw` deploys as a native
switch (`docs/design/native-switches.md`): one adapter-owned OVS bridge
`tesw_<8hex>`, `office` as VLAN 10 on it (no SDN vnet for `office`), and the
three VM NICs as access ports on it. `servers` stays an SDN vnet. Four VMs.
On a platform without the capability (libvirt today) `access-sw` is logical
and the deploy is canvas-test's.

The tap plan is still the `segment` tap on `office` from before the
conversion; phase 2 of the design replaces it with a `span` session on
access-sw (SFP1, both) and compiles `segment` on a switch-hosted network to
an OVS mirror. Until then, deploy the tap only on the pre-conversion engine.

```sh
R=/home/rocky/axr-product/deploy-dev45; D=~/.axrange/state.db
axrange te deploy  --config switch-test --images $R --db $D --fresh
axrange tfc deploy --twin switch-test --db $D
```

## Deployed and measured (2026-11-07, before the conversion: access-sw deployed as nothing)

`switch-test-5c614b0e` (48 ops, COMPLETED), tap `…-1a070fcc`-style binding of
the boundary to XPress-SW-A, tfc `tfc-switch-test-5c614b0e-8adbc15c`. The
inventory read four VMs and no VM for `access-sw`; `corp-workstation02` carries
`role=jump-host` from its member override, so the traffic profile's
`role: corp-workstation` assignment reached instance 01 only (the override's
intended effect).

| read | value |
|---|---|
| sink `gre-cap` RX packets before the tap | 264537 |
| after the traffic profile, two reads a minute apart | 264626, 264640 |
| dev45 `tcpdump -ni any "proto gre and host 10.255.251.21"` | `10.71.10.2 → 10.71.20.10:445` SYNs and the server's RSTs, both ways, plus the gateway's ARP |

Tear down: `axrange te destroy switch-test-5c614b0e --allow-destroy --db ~/.axrange/state.db`.
