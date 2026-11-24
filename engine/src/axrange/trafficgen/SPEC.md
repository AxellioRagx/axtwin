# trafficgen: emulated instances and traffic lines on TRex ASTF generators

## What it is

Load that a tfc profile asks for without a VM per source: **emulated
instances** (addresses in a node group's segment, no VM) and **traffic lines**
(`from` group → `to` group, an app mix, a rate), played by **generator VMs**
the tfc tier plans and runs TRex ASTF on. Loop step: **drive realistic
traffic**, proved by the capture gate (tapped traffic reaches the sensor).

Status: phase 2 built and live-verified on dev45 2026-11-24 (design §10.2). Decisions and later
phases (canvas, discovery, client-only lines): `docs/design/traffic-generation-trex-astf.md`.

## Seam

- **Persona registry** (`axrange.personas`): an **app mix** is a persona with
  `drive: trex-astf` and a `mix:` list. It plays on traffic lines only, never
  on a VM. The per-VM form of the same drive (`knobs.side`, `trex-test`) is a
  different shape of the same drive; a persona carries exactly one of the two.
- **Traffic-app registry** (`axrange.trafficgen.apps`, `list_apps()`): lists every
  `catalog/traffic/apps/<id>/app.yaml` installed (`axrange.paths.TRAFFIC_APPS_DIR`);
  no hard-coded table. Lazy; a bad file is a warning and a validator finding.
- **tfc tier** (`axrange.o2control.traffic`): plans generator VMs, legs,
  addresses and one profile JSON per generator (`trafficgen.plan`, `trafficgen.compose`).
- **Persona driver** `trex-astf` (`axrange.personas.drivers.trex_astf`) and
  ops `provision-generator`, `load-traffic-profile`, `start-traffic`,
  `update-traffic`, `stop-traffic` (catalog `OP-TFC-030`…`070`; `OP-TFC-020`
  is `assign-role`), all over the guest agent through an in-VM
  wrapper (`trafficgen/guest_tg.py`).
- **API**, read-only: `GET /api/traffic-apps`, `GET /api/traffic-apps/{id}`;
  `GET /api/personas` adds `mix` and `real_server_ok` to each persona (empty /
  false for non-mixes).

## Config

### App mix (a persona file, `catalog/personas/<id>.yaml`)

```yaml
persona:
  id: web-browse
  category: traffic
  label: "Web browsing (emulated)"
  blurb: "Emulated users fetch pages over HTTP and HTTPS."
  os_family: linux               # the generator's OS
  drive: trex-astf
  emits:                         # as for any persona; must cover every app's (proto, port)
    - { proto: http, ports: [80],  peer_role: web-tier, transport: tcp }
    - { proto: tls,  ports: [443], peer_role: web-tier, transport: tcp }
  mix:                           # weights > 0, normalised per mix into shares of the line's rate
    - { app: http-browse, weight: 7 }
    - { app: tls-session, weight: 3 }
```

All of a line's apps go to its one peer, so a mix is coherent per peer role.
Built in: `web-browse` (http-browse 7, tls-session 3; web-tier),
`api-east-west` (rest-api-call; web-tier / app), `sql-oltp` (sql-query;
db-tier). `dns-lookup` is installed and in no mix yet.

### Traffic app (`catalog/traffic/apps/<id>/app.yaml`, plus files it names)

```yaml
app:
  id: http-browse                # = the directory name
  label: "HTTP browse"
  blurb: "GET a page, read the response, close."
  proto: http                    # what a sensor should log (Zeek service)
  transport: tcp                 # tcp | udp
  port: 80                       # default server port; a line may override
  real_server_ok: true           # the client end works against a real server
  source:                        # exactly one of program | pcap
    program:
      - { send_text: "GET {path} HTTP/1.1\r\nHost: {server_name}\r\nUser-Agent: {user_agent}\r\nAccept: */*\r\nConnection: close\r\n\r\n" }
      - { reply_text: "HTTP/1.1 200 OK\r\nServer: nginx\r\nContent-Type: text/html\r\nContent-Length: {resp_bytes}\r\n\r\n" }
      - { reply: "3c68746d6c3e", size: "{resp_bytes}", fill: "3c703e" }
    # pcap: capture.pcap         # one flow, beside app.yaml, replayed as captured
  tunables:                      # name -> {default, min, max} | {default, choices}
    path:       { default: "/index.html" }
    resp_bytes: { default: 8192, min: 64, max: 1048576 }
    think_ms:   { default: 0, min: 0, max: 60000 }
  provenance: "Hand-written request/response, nginx-shaped."
```

Program steps (the driver's step language): `send` / `reply` (hex),
`recv: <n>`, `size` / `fill` padding, `send_text` / `reply_text` (UTF-8 with
`{name}` placeholders filled at plan time from tunables and the peer:
`server_name`, `server_ip`, `client_ip` from the peer, the rest from
tunables), `delay_ms`. Literal braces in text are doubled (`{{` `}}`, as in
JSON bodies); hex is always quoted (an all-digit hex string is a YAML
number). A UDP program has no `recv`. Binary protocols keep a
`gen_program.py` beside their `app.yaml` that prints the steps. Programs are the default:
replayed captures carry their original Host headers, URIs and DNS answers
(spike A4). Replay is for TLS-shaped sessions.

### Validator rules

Text in `axrange.validator.catalog`, listed in `docs/contracts/VALIDATOR.md`
§5.12. PSN rules run with the persona catalog; TGN rules run in
`trafficgen.validate.check_lines` (from `axrange tfc validate`, and before a
tfc plan), against the twin's te file read through `trafficgen.twin.TwinView`.

| code | rule |
|---|---|
| PSN-120 | a `trex-astf` persona has exactly one of `mix` / `knobs.side`; `mix` only on `trex-astf` |
| PSN-130 | every `mix[].app` is installed; weights > 0 |
| PSN-140 | every app's (proto, port) appears in the mix's `emits` |
| TGN-010 | a line's `from` group has emulated instances |
| TGN-020 | a line's `to` group has emulated instances (no line to a real server in this phase) |
| TGN-030 | every emulated group, line end, external segment and mix resolves |
| TGN-040 | planned cps and estimated Mbps fit the budget |
| TGN-050 | emulated addresses fit their segment and collide with no real address |
| TGN-060 | an app mix is not bound to a role or node in `assignments[]` (real VMs, no emulated instances); checked on every tfc profile: Check / Save (`POST /api/validate`, the document PUT), `tfc validate`, and at resolve, so every apply refuses it |
| TGN-070 | the target's platform has a generator image |

App-file shape errors are warnings from the registry (`list_apps`), and
`AppSchemaError` from `load_app`.

### tfc profile additions

`budget {cps, mbps}`, `emulated[] {group, count, external, segment, role,
start}`, `lines[] {id, from, to, mix, rate_cps, max_concurrent}`; unknown keys
inside the blocks are refused. Example: `catalog/twins/three-tier-dc-manual/
tfc/trafficgen.yaml`; field rules in the design doc §9 T2.

### Generator image

Image profile `generator` in `catalog/image-profiles.yaml`:
`{ image: null, platforms: { proxmox: trex-astf@v1 } }`. `trex-astf@v1` is
the golden template the `trex-test` twin already uses (Debian 12, TRex v3.08,
sha256-pinned; dev45 template 102). Live manifest and binding in the images
root (`manifests/trex-astf.yaml`, `bindings/proxmox/trex-astf.yaml`), copies
as test fixtures under `engine/tests/fixtures/images/`. No generator binding
on a platform (libvirt today) → TRex lines are refused at validate.
Per generator, set at clone: `cores = 1 + ceil(pairs / 4)`,
`memory = 2 GiB + 0.1 GiB × legs`, one NIC per leg.

## TRex constraints the plan must respect

Measured 2026-11-24 on dev45, TRex v3.08, `three-tier-dc-manual` (design §10.1).

- `low_end: true`, legs by Linux name (`net_af_packet`), one core; one
  data-path thread per port pair.
- **GRO/GSO/TSO off** on every leg, or TRex drops merged frames
  (`err_len_err`) and the server end retransmits.
- **Emulated addresses in the segment:** `stack: linux_based`, and each
  address added as a `/32` on its port's namespace veth
  (`trex-a-<port>-0-L`) so the kernel answers ARP. No router routes.
- **Legs:** one per (segment, role): a client leg per segment with emulated
  clients, a server leg per segment with emulated servers, padded with
  `dummy` to an even count. Any client port reaches any server port;
  `client_mask` bit n = TRex port n. A same-segment line needs a client leg
  whose `default_gw` is the server leg's IP (else it hairpins via the router).
- **Pools:** with N pairs every pool holds > N addresses, and a profile
  limited to one client port uses 1/N of each pool. One pool per (line, leg).
- **Live control:** one `ASTFClient` owns all profiles of a TRex process
  (two clients steal the context). `update(mult, pid_input=)` acts within 5 s;
  profiles start and stop independently; `stop()` cuts that profile's own
  open flows; an identical profile under a new id needs the old one removed
  (`stop(is_remove=True)`).
- Map legs by MAC (the sixth NIC is `enp1s1`, not `ens24`, on i440fx).
- Hugepages: `20 + 40 × ports` 2 MiB pages (the image bakes 256).

## Acceptance

- **Real release check of the generic profile** (`astf_profile.py`): push it,
  a generator document and the replay files into a `trex-astf` VM and run
  `astf-sim -f astf_profile.py --full --pcap -o OUT -t --doc DOC --line ID
  --files DIR`; Zeek over OUT logs the apps (2026-11-24: `mysql.log` login /
  SELECT / UPDATE / quit, `http.log` GET 200 with the peer's Host,
  `ssl.log` TLS 1.3 with SNI, every flow `SF`). Use `--full`: the simple
  mode segfaults on a port + address-range association (v3.08), which the
  real daemon accepts (spike A3).
- **Automated** (beside the code, `engine/tests/test_trafficgen_*.py`): the
  apps registry lists what is installed and warns on a bad file, and the
  shipped programs are well-formed (REST lengths, whole MySQL packets, one DNS
  question and answer; `test_trafficgen_apps.py`); each
  validator rule above has a code and a test; the planner turns a fixture of
  `three-tier-dc-manual` plus a hand-written tfc into the same generators,
  legs, addresses and profile JSON every run; the driver and ops run against a
  faked TRex (as the Zeek runner is faked); existing personas and tfc profiles
  load and plan unchanged.
- **Live** (dev45, `three-tier-dc-manual`, hand-written tfc): all tfc ops
  green and the twin's real personas still green; per-line counters climb in
  the tfc status; tapped traffic reaches XPress-SW-A and Zeek over a sample
  shows the emulated addresses in `conn.log` and `http.log`;
  `update-traffic` doubles one line's rate and the counters follow; tfc
  destroy removes the generators. The capture gate in `Claude.md` applies.

## Notes

- **The tap must see the generator.** A switch span selects the te ports
  present when the tap plan was applied; a generator's legs are not among
  them, so generator↔generator traffic on one segment is never mirrored and
  a routed flow is seen half per span. Re-apply the tap with the generator's
  legs after provisioning.
- No traffic from a real VM's address. Emulated → real lines need every app
  `real_server_ok` (refused in this phase). A twin's real databases may not
  listen on the LAN at all (Debian mariadb binds 127.0.0.1).
- One MAC and one TCP/IP fingerprint per generator leg; replayed payloads are
  byte-identical (TLS certificate included); lines are independent.
- Licence: TRex is Apache-2.0; its bundled Python (scapy 2.4.3 is GPL-2) runs
  only inside the generator VM, as separate processes.
- Open: intermittent server retransmits on a first run after idle on a path
  through the core routers (design §10.1, S3).
