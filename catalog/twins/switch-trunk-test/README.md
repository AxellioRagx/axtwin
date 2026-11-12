# switch-trunk-test — two native switches on a trunk

The switch-to-switch fixture beside `switch-test`
(`docs/design/native-switches.md` §11). `gw` has one access link to `core`
per segment (Te1 `office`, Te2 `servers`); `core` Po1 is a trunk to
`access-sw` Po1 carrying both; the two workstations (P1, P2, `office`) and the
file server (P3, `servers`) sit on `access-sw`. Both segments are carried by
native switches, so the twin has no SDN zone: `office` and `servers` are VLANs
10 and 20 on two OVS bridges joined by one patch pair. Every host reaches `gw`
across that patch, so reachability proves the trunk forwards both VLANs.

Phase 2 adds the tap tier: a `span` session on `access-sw` Po1, a trunk port.

```sh
R=/home/rocky/axr-product/deploy-dev45; D=~/.axrange/state.db
axrange te deploy --config switch-trunk-test --images $R --db $D --fresh
```
