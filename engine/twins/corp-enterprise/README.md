# corp-enterprise — a complete five-tier deployment bundle

Everything needed to stand up the **corp-enterprise** digital twin and exercise it, in one folder.
An AD-workhorse enterprise: `corp-lan` / `server-vlan` / `dmz` / `mgmt` behind a router with WAN
egress, a full `corp.axr.lab` domain (vulnerable-AD accounts + crown-jewel PII), and a LAN-foothold
attacker. Authored in the post-2026-08-04 schema (`name:`/`role:` split, no inert fields) and
name-targeted throughout, so no file is welded to a minted deploy id — deploy, tear down, redeploy,
and every plan/profile/spec still resolves.

## Files

| File | Tier | What it is |
|---|---|---|
| `corp-enterprise.yaml` | `te` | the twin config — segments, nodes (incl. the AD DC, file/mail/dmz servers, workstation fleet, and the `attacker`), the domain identity overlay, and the crown-jewel data overlay |
| `roles.yaml` | shared | the tfc host→role map, `roles_from:`-included by the profiles |
| `tfc-default.yaml` | `tfc` | benign traffic — one persona per host role, cadence-driven |
| `tfc-busy.yaml` | `tfc` | same role map (shared via `roles_from:`), heavier traffic (workstation fleet forced 24×7) |
| `tap-default.yaml` | `tap` | capture: `server-vlan` → `o2-suricata`, the `corp-lan`↔`dmz` boundary → `Tcpdump-SW-A` |
| `tap-reshard.yaml` | `tap` | an alternate capture sharding |
| `tap-xpress.yaml`, `tap-xpress-peri.yaml` | `tap` | bind to the external packetXpress sinks instead |
| `exr-ad-killchain.yaml` | `exr` | the AD kill chain (recon → LLMNR → SMB/LDAP enum → AS-REP roast → Kerberoast → DCSync) run from the `attacker` node |

## Deploy sequence

Run from the repo root. On the `dev45` lab, add `--images deploy-dev45 --db deploy-dev45/state.db`
to every command (GAP-024: `--images` points at the *parent* of `images/`); elsewhere use your own
manifest root and state DB. `--config corp-enterprise` discovers this folder under `./twins`.

```sh
# 0. Precondition: the standing O2 surface must be up, so the tap plan has
#    sensors to bind to. Check it:
axrange o2 status                       # expect o2-suricata (and any external sinks) present

# 1. The twin (te). ~30 VMs; prints the deploy id when done.
axrange te deploy --config corp-enterprise

# 2. Capture (tap). server-vlan -> the standing o2-suricata; the boundary ->
#    the external Tcpdump-SW-A sink (both must exist; see `o2 status` / adapter.conf).
axrange tap deploy twins/corp-enterprise/tap/default.yaml --target corp-enterprise

# 3. Benign traffic (tfc). Personas onto the twin's host roles.
axrange tfc deploy twins/corp-enterprise/tfc/default.yaml --target corp-enterprise
#    …swap traffic shape on the STANDING twin, no redeploy:
#    axrange tfc deploy twins/corp-enterprise/tfc/busy.yaml --target corp-enterprise

# 4. The exercise (exr). The AD kill chain from the attacker node; ephemeral.
axrange exr run twins/corp-enterprise/exr/ad-killchain.yaml --target corp-enterprise
```

Every `--target corp-enterprise` is the twin-config **name**, resolved to the newest live
`corp-enterprise` te deploy at run time. The `exr` spec also carries `twin: corp-enterprise`
directly, so step 4's `--target` is optional (it overrides the spec when given).

## Inspect

```sh
axrange te inventory corp-enterprise    # the published host roster (roles, IPs, os)
axrange te info                         # access facts: per-node ip / login / effective password
axrange tfc status --target corp-enterprise
axrange tap status --target corp-enterprise
axrange exr status <exr-deploy-id>      # from list-deploys --tier exr
```

## Teardown

One `te destroy` cascades the whole bundle — it tears down the bound `exr` → `tfc` → `tap` deploys
first (reverse of stand-up), then the twin's own resources:

```sh
axrange te destroy corp-enterprise --allow-destroy
```

It leaves the **standing O2 surface, the external sinks, and the golden templates untouched** — the
blast-radius guard refuses any `o2`/`img`/external-sink resource. Re-running it is an idempotent
no-op.

## Notes

- **Repurpose without redeploy.** Roles and personas are `tfc`-owned: `tfc deploy … --target
  corp-enterprise` with a different profile re-labels and re-personas the *standing* twin, touching
  no VM (W1, live-verified 2026-08-04).
- **Vulnerable-AD content is intentional.** `svc-sql` (Kerberoastable, `Summer2024!`), `jsmith`
  (AS-REP-roastable), and `bob-admin` (Domain Admins, lab-default password) make the DCSync objective
  reachable — the point of the exercise. This is a disposable lab twin, not a hardened build.
- **The exercise is disposable and replay-identical.** `exr run` is ephemeral (each run its own
  deploy); the same spec+seed reproduces an identical `playout_hash`, which is what makes a
  run1-vs-run2 defense-validation delta meaningful.
