# WORK ORDER — Access facts & endpoint resolution in `info`

> **Status: DONE — code/tests/docs complete 2026-07-25 (CHANGELOG "Access
> facts & endpoint resolution").** Authority: user (2026-07-25) for the work
> order and the two framing decisions below; implementation (Claude Code)
> for the build. Reviewed (four fixes folded in, `docs/LESSONS.md`
> LES-128..131) and unit/suite-verified — **1586 passed / 12 skipped / 0
> xfailed (1598 collected)**; `doc-stats.py --check` clean. **Gated `dev45`
> live-verify PASSED 2026-07-25** — Phase 1 (read-only, standing
> `corp-enterprise` twin + o2 surface): `te info` access sheet, `o2 info`
> endpoint resolution off VM 102's live IP (+ explicit-URL override
> precedence), `XPress-SW` external-sensor resolve, bound-only `tap info`,
> and graceful degradation against an unroutable API all confirmed on real
> cluster data. Phase 2 (throwaway `te` deploy `access-facts-verify-5a1239e0`):
> a **password-only SSH login (pubkey disabled) succeeded**, proving the new
> Linux `chpasswd`/`ssh_pwauth` path; explicit-id `te destroy` swept it clean
> with every standing resource untouched. Genre: Pipeline work order
> (`docs/CONVENTIONS.md`). Goal: make `axrange … info` a self-service "how
> do I get into every node / where are the services" sheet, so an operator
> never has to reverse-engineer ip/user/password or hunt for the
> Grafana/Loki/… URLs by hand.

## Motivation

Real-use pain (operator, 2026-07-25): finding the **IP / user / password**
for each deployed node, and the **observability + external-sensor**
endpoints, currently requires cluster spelunking or asking Claude. `info`
already knows most of the topology; it should just *show the access facts*.

Two user decisions frame this (AskUserQuestion, 2026-07-25):

- **Default password = FALLBACK.** A single deploy-wide default fills every
  node/account that has no password of its own; an explicitly-set password
  (binding, `IdentityEntry.password`, the intentionally-weak AD accounts)
  still wins. `info` shows the *effective* value either way. This is
  already the code pattern (`inp.get("password") or _DEFAULT`); we only
  make the default a config knob.
- **o2-services = REGISTER + AUTO-RESOLVE.** The server-stack VM stays a
  manually-managed docker host (`docs/WORK_ORDER_o2_server_stack.md`,
  unchanged). It is *declared* (vmid/name), and `o2 info` resolves its live
  IP and derives the five endpoint URLs. Explicit `O2_*_URL` keys remain
  optional overrides.

**No version-locked schema changes** (IR/PLANNER/OP_CATALOG/VALIDATOR/
ADAPTER/IMAGE_MANIFEST). No new op type. Everything below is adapter-config
+ CLI-render + a manifest-loader field. If the build finds it *cannot* avoid
a version-locked change, STOP and ask.

## Part A — Deploy-wide default password (`DEFAULT_PASSWORD`)

1. `AdapterConfig.default_password: str = "Axr4nge-Lab!"` — loaded from
   `DEFAULT_PASSWORD` (adapter.conf / env). Default **keeps the current
   constant**, so behavior is unchanged when the key is absent.
2. Replace the four hardcoded `"Axr4nge-Lab!"` literals with a fallback to
   `cfg.default_password` (keep explicit-input precedence exactly as is):
   - `adapter_proxmox/rest.py` `_WINDOWS_DEFAULT_ADMIN_PW`
   - `config_exec/backend.py` `_WINDOWS_DEFAULT_SAFEMODE_PW` /
     `_WINDOWS_DEFAULT_USER_PW` / `_WINDOWS_DOMAIN_ADMIN_PW`
   - Thread `cfg` in where these functions don't already have it. `backend.py`
     already routes `config`-class ops with adapter context — reuse that;
     do not invent a second config channel.
3. **Linux password login (new).** `_render_userdata` (`rest.py`) currently
   emits `lock_passwd: true` and no password → password login impossible.
   Change to set the `ci_user`'s password to `cfg.default_password` (still
   also install the SSH key), `lock_passwd: false`, and `ssh_pwauth: true`
   so both console *and* SSH-by-password work. **Fallback:** if the binding's
   `cloud_init` block specifies a password, that wins. Use `cipassword` on
   the VM config OR the userdata `chpasswd` path — match whichever the
   existing snippet already uses; don't double-set.
4. Windows already sets Administrator/safemode/user/domain-admin from the
   constants → now from `cfg.default_password` via step 2.

## Part B — o2-services register + auto-resolve

Authoritative resolution source is **adapter.conf** (so a *bare* `o2 info`
resolves with no manifest re-read and no persisted-state/op change):

1. New keys: `O2_SERVICES_VMID` (int) and/or `O2_SERVICES_NAME` (str). At
   least one identifies the externally-managed services VM.
2. Optional per-service port overrides with built-in defaults:
   grafana `3000`, loki `3100`, prometheus `9090`, caldera `8888`,
   wazuh `443` (wazuh is `https`, the rest `http`). Keys e.g.
   `O2_GRAFANA_PORT` etc. — optional; the defaults match
   `adapter.conf.example`.
3. `AdapterConfig` gains a resolved-endpoints helper. **Layered precedence
   per service** (already the "O2_* keys are overrides" contract):
   1. `O2_<SVC>_URL` set → use verbatim.
   2. else if a services VM is declared → resolve its live IP (Proxmox API,
      best-effort) and derive `http[s]://<ip>:<port>`.
   3. else → `(not configured)`.
4. IP resolution is **best-effort + graceful**: on API failure or a VM with
   no guest-agent IP, fall back to `(services vmid=N — IP unresolved)`
   rather than raising. `info` must still print the rest of its output.
5. Also accept an optional `services:` stanza in the **surface manifest**
   (`o2control/manifest.py`) — `{ vmid / name, ports? }` — parsed into
   `SurfaceManifest` and shown by `o2 info <manifest.yaml>` / echoed at
   `o2 deploy`. adapter.conf stays the resolution source for the *standing*
   surface's bare `o2 info`. (Manifest stanza is additive; if it complicates
   the build, ship Part B on adapter.conf alone and note the manifest stanza
   as a follow-up.)

## Part C — External sensors (packetXpress) resolve + display

`EXTERNAL_SENSORS` is already a JSON list (multi-node ready). Enhance
`o2 info` (and `tap info` where an external sensor is a tap target) to, for
**each** entry, resolve via the Proxmox API (best-effort):

- live **VM name** (from vmid) and **management IP** (guest-agent / config),
- alongside the declared `type`, `vmid`, `transport_ip`, `capture_iface`,
- plus the access line (user = declared or default; password =
  `cfg.default_password`; method = SSH/RDP by `type`/os if known).

Degrade gracefully to the declared fields when the API can't be reached.
`--json` includes the resolved fields.

## Part D — Access sheet in `info` (the core deliverable)

Extend `te info` (and mirror into `o2`/`tap`/`tfc` info where a node roster
exists) so every node shows a compact **access block**:

- management IP (primary segment IP, or `wan_ip` for a router),
- login **user** (`ci_user` for Linux, `Administrator` for Windows),
- **effective password** (`cfg.default_password` unless the binding set an
  explicit one),
- a paste-ready **access hint**: `ssh <user>@<ip>` (Linux) /
  `rdp <ip>  (user=Administrator)` (Windows).

Details:
- `te info` bare/`[id]` path must now `load_config(args.adapter_config)` to
  obtain `default_password` (+ live-resolve fallback). The subparser already
  has `--adapter-config`.
- Detect os-family (Linux/Windows) from the existing signal (image binding /
  the Windows userdata path / a windows-specific op) — best-effort; default
  to Linux/`ssh` when unknown.
- **Effective IP:** prefer the persisted op IP; if absent (e.g. DHCP hosts
  like the router WAN), best-effort live-resolve via the adapter, else show
  `(unresolved)`.
- **Secrets:** passwords are shown by default (this is the whole point).
  Add a `--no-secrets` flag that masks them (`********`) for
  screen-sharing; `--json` always includes them (it's operator-local).
- Everything new appears in `--json`.

## Non-goals / guardrails

- Do **not** make axRange deploy/manage the o2-services or XPress VMs — they
  stay operator-owned + teardown-protected (already are).
- Do **not** change the fallback semantics — explicit passwords win.
- Keep `o2control` thin: resolution helpers live in `adapter_proxmox`
  (config/rest) or a small `o2control` helper; the CLI stays render-only.
- Live-resolution is best-effort everywhere: a cluster that's unreachable
  must degrade `info` to declared/persisted facts, never crash.

## Done criteria

1. `DEFAULT_PASSWORD` in adapter.conf changes every node's login + the four
   Windows constants; unset ⇒ identical to today. Linux nodes accept
   console + SSH password login.
2. `axrange o2 info` shows the five endpoint URLs derived from
   `O2_SERVICES_VMID`/`_NAME` (or the explicit `O2_*_URL` overrides), and
   lists each `EXTERNAL_SENSORS` entry with resolved name + mgmt IP.
3. `axrange te info` shows, per node, ip + user + effective password + a
   paste-ready access hint; `--no-secrets` masks; `--json` carries all.
4. Cluster-unreachable ⇒ `info` still prints declared/persisted facts.
5. `adapter.conf.example` documents `DEFAULT_PASSWORD`, `O2_SERVICES_VMID`/
   `_NAME`, the optional `O2_*_PORT` keys. Full suite green; doc-stats clean.
