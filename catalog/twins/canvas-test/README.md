# canvas-test — the first twin designed on the axTwin canvas, stood up on dev45

Designed 2026-11-06 entirely through the console's canvas (`/canvas/new`, then
the palette, the inspector and Save), not by hand: two segments, two
workstations fanned out from the catalog's `corp-workstation` group, a file
server, and a router placed on the pane and attached to both segments. The
engine put the router's unpinned legs at each segment's gateway address.

| File | Tier | What it is |
|---|---|---|
| `te.yaml` | `te` | the twin config as the canvas saved it (`office` 10.70.10.0/24, `servers` 10.70.20.0/24; `gw` router; `corp-workstation` ×2; `file-server`) |
| `tfc/default.yaml` | `tfc` | one persona per role, both bundle-local (`personas/`): the library's endpoint personas require roles this twin has no host for |
| `personas/office-workstation.yaml`, `personas/file-share.yaml` | shared | scripted personas: SMB/HTTP from the office workstations to the file server across the boundary, and its answer |
| `tap/default.yaml` | `tap` | the `office` ↔ `servers` boundary to the external packetXpress sink `XPress-SW-A` (adapter.conf `EXTERNAL_SENSORS`) |

## Deploy (as run on 2026-11-06)

```sh
R=/home/rocky/axr-product/deploy-dev45    # the dev45 image catalog (images/manifests, images/bindings/proxmox)
D=~/.axrange/state.db
axrange te deploy  --config canvas-test --images $R --db $D --fresh   # 48 ops, COMPLETED -> canvas-test-f919dae1
axrange tap deploy --twin canvas-test --db $D                          # tap-canvas-test-f919dae1-1a070fcc
axrange tfc deploy --twin canvas-test --db $D                          # tfc-canvas-test-f919dae1-bb83ca92
axrange te inventory canvas-test-f919dae1 --db $D                      # gw at 10.70.10.1 / 10.70.20.1, hosts .2 .3 / .10
```

## How the capture was confirmed

`tap status` reports the external sink as capture-UNVERIFIED by design. Two
out-of-band reads proved it:

- On the sink (`ssh fxadmin@xpress-sw-a.swlab.axellio.dom`), `ip -s link show
  gre-cap` climbed from 21 to 233 packets in the first minutes after the
  traffic profile applied. `tcpdump` on that interface sees nothing: an XDP
  program consumes the packets first.
- On dev45 (`ssh root@10.80.76.108`), `tcpdump -ni any -c 14 "proto gre and
  host 10.255.251.21"` showed the mirror's GRE payload: `10.70.10.3 →
  10.70.20.10:80` and `10.70.10.2 → 10.70.20.10:445` SYNs and the server's
  RSTs, i.e. the twin's own traffic crossing the tapped boundary through the
  router in both directions.

The resets are expected: the personas are scripted (traffic shape) and the
file server runs no smbd or httpd, because the canvas cannot yet author a
node's `install:` list.

## Tear down

```sh
axrange te destroy canvas-test-f919dae1 --allow-destroy --db ~/.axrange/state.db   # cascades tfc -> tap -> te
```
