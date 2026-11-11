# switch-test — track 2's dev45 fixture for the device tree

canvas-test's shape (`office` 10.71.10.0/24, `servers` 10.71.20.0/24, `gw`,
`corp-workstation` ×2, `file-server`) plus one **imageless `kind: switch`**,
`access-sw`, wired by `links[]`: a trunk from `gw` carrying `office`. (Until
2026-11-11 it also had a `state: down` access link to the file server that
deployed nothing; it was removed when the canvas began drawing segments as
switches, §7.1.) The second
workstation carries a `members[]` role override (`jump-host`). The switch
deploys as nothing (`docs/design/topology-layers.md` §2.5), so the deploy is
canvas-test's: 48 ops, four VMs, the `office` ↔ `servers` boundary tapped to
XPress-SW-A.

```sh
R=/home/rocky/axr-product/deploy-dev45; D=~/.axrange/state.db
axrange te deploy  --config switch-test --images $R --db $D --fresh
axrange tap deploy --twin switch-test --db $D
axrange tfc deploy --twin switch-test --db $D
```

## Deployed and measured (2026-11-07)

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
