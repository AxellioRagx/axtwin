# switch-test — track 2's dev45 fixture for the device tree

canvas-test's shape (`office` 10.71.10.0/24, `servers` 10.71.20.0/24, `gw`,
`corp-workstation` ×2, `file-server`) plus one **imageless `kind: switch`**,
`access-sw`, wired by `links[]`: a trunk from `gw` carrying `office`, and a
`state: down` access link to the file server that deploys nothing. The second
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

The capture is confirmed as canvas-test's was: `ip -s link show gre-cap` on the
sink climbs once the traffic profile runs, and `tcpdump -ni any "proto gre and
host 10.255.251.21"` on dev45 shows the inner 10.71.x addresses.
