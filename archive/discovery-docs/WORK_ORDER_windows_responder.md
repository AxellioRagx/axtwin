# Work order — a Windows responder, so the twin's Windows hosts answer at all

**Status:** **SUPERSEDED IN APPROACH, 2026-09-03 (Authority: user).** The gap this document names —
the twin's Windows hosts, above all the domain controller, answer no connection at all — is real
and still open; §§1-2's diagnosis stands untouched. What is superseded is §3 onward, the **fix**.
The convergence loop this work order was written to serve retired its own objective the same day
(`docs/CHANGELOG.md` 2026-09-03, "the convergence loop closes"): distance-minimizing traffic shape
is no longer the goal, so making the DC merely *answer* (a `.NET` listener under a Scheduled Task,
§3) stops being the right-sized fix for what it costs, especially once §4 already concedes a
responder alone does not restore `domain-controller` inference — only better-attributed silence.
**Replacement approach:** a real, promoted domain controller, populated from a directory export via
the golden-twin model's identity-injection channel (`referenced` evidence feeding
`ir_identity`/`ir_unseen`) rather than a listener wearing a Windows accent — closing GAP-C8
(`roles.yaml:80-94`) for real, instead of leaving it a declared exception beside a host that merely
answers. Tracked under the golden-twin design work (`wip/golden-twin`), not this document.
**Nothing in §§3-7 below should be built as written.** Original framing, kept for the record: the
**structural** blocker standing behind the CorpAPCN fidelity loop's `role_edges` axis — unchanged by
iterations 15, 16, 17 and 18, and named as still-standing in the journal's own "next lever" section.
Unlike `docs/WORK_ORDER_proto_weight_rows.md` (a ranking correction worth several hundred relabelled
edges), this one is worth **roughly a third of the source's local edge mass**, and no amount of
persona tuning reaches it.
**Genre:** work order (`docs/CONVENTIONS.md` §2).
**Supersedes:** nothing. It closes the scoped gap `src/aximprint/synth/responder.py:132` declares
in its own docstring, on that docstring's own terms.

---

## 1. The gap

`responder.py` synthesizes a **POSIX shell** action — `set -eu`, a `cat > … <<'EOF'` heredoc, and
`systemd-run --unit=axr-responder` (`responder.py:146-171`). Windows nodes are skipped outright:

```
src/aximprint/synth/responder.py:132
def is_windows_image(image: str) -> bool:
    """Windows nodes are skipped by `listener_ports`' consumers for now. …"""
    return "win" in (image or "").lower()
```

The consumer is the cast (`src/aximprint/cli.py:602-611`): on a Windows image it appends a caveat
and `continue`s, so **no `install:` action is written for that node at all**. The gap is declared,
not silent — which is the right posture and is also why closing it is a build rather than a
discovery.

**Measured on the standing cast** (`cast-verify`, the 138-VM twin's own config): **5 Windows node
groups, 7 instances, every one of them `install: None`.**

```
corp-workstation-10-80-2    win11-finance@v4          count 3     install: None
domain-controller-10-80-2   windows-server-2022@v1    count 1     install: None
corp-workstation-10-80-48   win11-finance@v4          count 1     install: None
corp-workstation-10-80-72   win11-finance@v4          count 1     install: None
corp-workstation-10-80-78   win11-finance@v4          count 1     install: None
```

Those are the only two Windows-imaged roles the catalog can produce (`domain-controller` →
`windows-server-2022@v1`, `corp-workstation` → `win11-finance@v4`). **Seven VMs in this twin refuse
every connection made to them.**

---

## 2. Why this is not cosmetic

aximprint infers a role from **observed behaviour**. `listen_ports` is written from flows the
responder *accepted* (`src/aximprint/windowing/windows.py:925-934`, gated on
`FLOW_ACCEPTED = {established, reset_orig, reset_resp}`, `events/model.py:36`), so a host that
answers nothing has an empty listen census, matches no `requires`-bearing catalog row, and falls
through to whichever `dials`-only row fires — in practice `generic-endpoint`
(`roles.yaml:376`ff, `requires: []`). **A silent host is not merely unmeasured; it is measured as
something else.**

Measured at iteration 18, on the two keys that carry it:

```
role edge (LOCAL)                          source    twin
corp-workstation -> generic-endpoint            4      12
generic-endpoint -> generic-endpoint            9      14
```

Both are Kerberos and LDAP emits striking a silent DC and being attributed to a catch-all. The
journal records the correction that goes with them, and it is the more important half: a
`corp-workstation->generic-endpoint` residual of 6 had been carried in the working notes as
"Windows background chatter", and it was never chatter — it was the Kerberos emit striking a
misroled DC. **A mislabelled real effect, not a constant.** A constant gets subtracted and
forgotten; a real effect keeps moving, and this one moved every time the emit table did.

The larger consequence is what the histogram cannot contain at all. Every source edge naming
`domain-controller` is structurally unreachable, because no twin entity will ever carry that role
while the DC answers nothing: **11 local edge keys are permanently `MISSING FROM TWIN` — roughly a
third of the source's local edge mass.** `role_edges_local`'s key-set Jaccard is 0.444 at
iteration 18 (eight shared keys); a third of the missing side is unreachable by construction, so
the axis has a **ceiling well below convergence** that no persona, budget, allocator or ranking
change can lift.

There is a second, self-inflicted cost that iteration 17 already priced. A dialled port with no
listener draws an ICMP port-unreachable, which Zeek records with the **replier** as originator and
a twin host as destination — i.e. **scored as a local edge, on the exact axis the loop is judged
on**. Iteration 17 measured 220 such rows from 228 dead-port emits and showed each one cost twice:
the conversation that should have happened is missing, *and* a fabricated reverse edge appears in
its place. The Windows nodes are a permanent, standing instance of that same mechanism.

**A standing self-check exists and does not cover this.** The cast's per-node caveat
(`cli.py:605-610`) reports emit rows landing at Windows roles that get no responder — 16 at
iteration 18. But it compares a cast against itself, and the deployed twin's own listeners are
something the repo cannot see. Only the guest can answer that, which is why `ss -lnH -tu` is part
of the loop rather than an incident response to it.

---

## 3. Design note — a .NET `TcpListener` under a Scheduled Task

**The delivery channel already exists and needs nothing built.** axRange's `run-script`
(OP-APP-030) dispatches on `os_family` (`axrange/config_exec/backend.py:240`) into
`_run_script_windows` (`backend.py:636`), which writes the script as `.ps1` and runs it via
`powershell -NoProfile -ExecutionPolicy Bypass -File`. `os_family` is derived from the image
manifest and threaded through emission (`axrange/planner/pass1_emit.py`, e.g. `:1087`, `:1111`),
so a Windows node's `install: [{script: …}]` **already** arrives as PowerShell. What fails today is
only the script's *text*: a POSIX heredoc interpreted as PowerShell dies with *"Missing file
specification after redirection operator"* (measured on dev45, recorded in the `is_windows_image`
docstring).

**A background job dies with its session on Windows.** The same failure class as axRange's
GAP-020/LES-086 on Linux, where a `nohup`'d process is reaped when the QGA guest-exec session that
launched it ends — the responder's Linux branch already avoids it with a named transient unit
(`responder.py:165`, `systemd-run --unit=axr-responder --collect`). The Windows analog is a
**Scheduled Task**, and axRange has already solved it, twice, in code this work order should
follow rather than re-derive:

- **`axrange/config_exec/render.py:1827` — `scheduled_task_start_block(task_name, exe, args, *,
  label, settle_s=3)`.** The shared primitive. Registers an `AtStartup` task running as `SYSTEM`
  (`-LogonType ServiceAccount` — the QGA agent is already SYSTEM, so there is no credential to
  store), idempotent via `Stop-`/`Unregister-ScheduledTask -ErrorAction SilentlyContinue` before
  `Register-ScheduledTask -Force`, and **fail-loud**: after `Start-ScheduledTask` plus a settle it
  asserts `.State -eq 'Running'`, the exact mirror of the Linux branch's `systemctl is-active`.
  Its docstring carries one detail worth not rediscovering: `-ExecutionTimeLimit
  ([TimeSpan]::Zero)`, because the default `PT72H` silently kills a long-running process after
  three days with no relaunch until the next reboot.
- **`axrange/personas/drivers/scripted.py:676` — `render_scripted_launch_windows`.** The pattern in
  use: write the PowerShell client to disk, then launch it with
  `scheduled_task_start_block(paths["unit"], "powershell.exe", '-NoProfile -ExecutionPolicy Bypass
  -File "…"', label=…)`. **The responder should be shaped the same way**, so a twin's Windows
  listeners and its Windows persona clients are launched by one mechanism rather than two.

The listener itself is `System.Net.Sockets.TcpListener` / `UdpClient` — in-box .NET, no package
manager and no egress, matching the Linux branch's constraint (`responder.py:147-149`: *"python3
only … reaching for a package would put the fix behind the egress a twin segment may not have"*).
Three behaviours from the Linux responder must carry over exactly, because each was a bug fixed
once already:

1. **Bind every socket up front, then serve** (`937eeaa`). A socket bound lazily fails after the
   op has already reported success.
2. **`EADDRINUSE` is a success, not a failure** (`responder.py:91-99`). A port a real service
   already owns needs no responder; the point is that *something* answers. Anything else exits
   non-zero.
3. **Echo, do not drain** (`responder.py:44-58`). A persona measuring bytes needs the far side to
   move them back, and axRange's own note applies: *"a refused connect is 2 packets, not 64 MiB."*

---

## 4. What this fixes, and one thing it does not — stated before the run

**It fixes attribution.** The forward conversation completes, so the edge is recorded as itself
rather than as an ICMP phantom on the reverse key, and the 16 currently-dead emit rows at Windows
roles start carrying real conversations.

**It does not, on its own, restore `domain-controller` inference — and the reason is worth knowing
before the run, not after.** `match_role` needs `88/tcp` **and** `389/tcp` together
(`ROLE_CATALOG[0]`, `roles.yaml:74`). The cast's responder binds exactly the ports its personas
dial at a role (`responder.py:112 listener_ports`), and on the current cast those are:

```
domain-controller     [(88, 'tcp'), (88, 'udp'), (3268, 'tcp')]
dns-resolver          [(53, 'tcp'), (53, 'udp'), (123, 'udp'), (389, 'tcp'), (445, 'tcp')]
```

**389/tcp is dialled at `dns-resolver`, not at the DC.** So a Windows responder would bind 88 and
3268, miss `ROLE_CATALOG[0]`, and the DC would still be roled by a catch-all — better attributed
than today (no ICMP phantom, real edges recorded) but not yet re-inferable.

The cause is one line up the pipe: `serves` (`personas.py:743`) resolves each port to the role with
the most listen votes, and on this source **389 is a 2–2 tie** — `dns-resolver` 2 votes against
`domain-controller` 2 — decided by `Counter.most_common`'s insertion order, i.e. by whatever order
`ir_entity` rows came back from DuckDB. That is a determinism hazard in its own right (`LES-002`;
the same unordered-result class as the review's S2 finding) and it is the specific reason the DC's
own port lands on the wrong role. **Ordering the vote deterministically, and breaking a tie by
something better than row order, belongs in this work order** — it is the difference between "the
DC answers" and "the DC answers on the ports that make it a DC".

Note also what this still does **not** reach: the DC does not become a *real* domain controller.
`roles.yaml:80-94` records why `install: [{role: adds}]` was deliberately reverted — axRange
requires it to be paired with `provides: [{domain: <fqdn>}]`, and **nothing in a capture supplies
an AD forest name**, so emitting one would fabricate the single most identifying string in the
twin. The fixed point (`scripts/check_role_fixed_point.py`, GAP-C8) stays open, as a declared
exception. A responder makes the host *answer*; it does not make it *serve AD*, and the work order
must not blur the two.

---

## 5. Build steps

1. **`responder.py` grows a Windows branch.** `responder_script_windows(ports)` returning
   PowerShell: write the `TcpListener`/`UdpClient` listener to disk, launch it via
   `scheduled_task_start_block`'s pattern (§3), carrying over the three behaviours in §3's list.
   `is_windows_image` stops being a skip predicate and becomes the **dispatch** predicate.
2. **`cli.py:602-611` dispatches instead of skipping.** The caveat is replaced, not deleted:
   whatever remains unreachable (the AD fixed point, §4) is still disclosed. A cast that quietly
   drops a caveat when the gap only narrowed is `LES-006` at the artifact level.
3. **Make the `serves` vote deterministic and tie-break deliberately** (§4). Order the entity
   iteration; decide a tie by something stated in code rather than by DuckDB's row order. If the
   chosen rule puts `389/tcp` at `domain-controller`, say so in the comment and say why.
4. **`scripts/install_cast_as_twin.py`** — no change expected: it copies `scripts/` wholesale and
   already checks every referenced `script:` exists on disk (`:212-226`). **Verify, do not assume**;
   this is the failure that lands with the previous twin already destroyed.
5. **Re-cast, install, deploy.** See §7 — this is the one work order in the arc that **does** need
   a `te`-tier step.
6. **Verify in-guest before measuring.** `Get-ScheduledTask`'s `.State` on each of the 7 Windows
   instances, and the ports actually bound (`Get-NetTCPConnection -State Listen` /
   `netstat -ano`). Iteration 17's rule stands: a unit reporting active is not a listener.
7. **Capture, fingerprint, record as its own iteration.** Not bundled with anything — see §7.

---

## 6. Acceptance

- **All 7 Windows instances answer.** Verified in-guest (step 6), not inferred from op status.
- **The ICMP phantom at Windows roles is gone.** The cast's dead-emit self-check reports **0** at
  Windows roles (16 at iteration 18), and the twin's `icmp` local row count falls.
- **`role_edges_local` improves and its key-set Jaccard rises** — the honest headline here is
  coverage, not TVD: keys that were structurally unreachable become reachable. Report both, the
  way iteration 13 and iteration 15 both had to, because losing a wrong edge costs coverage and
  buys fidelity and only reporting both makes it legible.
- **`role_edges` improves**, or, if it does not, the reason is measured rather than assumed.
- **Must not regress**: `external_dst_edge_share` 0.8641 (source 0.8476), `fan_out_degree_local`
  0.280, `role_edges_local` 0.533.
- **Tier 1 green**, plus a unit test that the Windows branch emits a Scheduled Task and never a
  POSIX heredoc, and that `EADDRINUSE` is tolerated on the Windows path too.
- **`scripts/check_role_fixed_point.py`** unchanged in its finding count — the AD exception stays
  declared, and no *new* non-fixed-point row appears.

---

## 7. What this deliberately does not do

- **It does not promote the DC.** No `role: adds`, no fabricated forest name (`roles.yaml:80-94`,
  `LES-006`). Closing GAP-C8 needs domain extraction from Kerberos/LDAP in the capture, which is
  real work and is not this.
- **It does not run in the same iteration as anything else.** Iteration 16 shipped two changes at
  once and moved five axes in both directions, which is unattributable by construction, and
  iteration 17 then showed the reading of that bundle had been wrong. This is the largest single
  effect left in the loop and it gets a clean iteration.
- **It costs a redeploy, and that is the honest price.** `install:` is a `twin_config` field, so
  adding a responder to 7 nodes changes `twin_config.yaml` — the one thing every iteration since
  16 has kept byte-identical. Two routes: a full re-deploy, or `te deploy --update`, which adds and
  removes whole nodes but **hard-refuses an in-place field mutation** — and an added `install:`
  action on an existing node is exactly that. **Assume a full redeploy** unless a measurement says
  otherwise, and note the standing complication: 19 guests already run responder text that differs
  from their persisted `script_content` (iteration 17), so `--update` would read those ops as
  unchanged when they are not.
- **It does not change the Linux responder.** Same census, same script, untouched — so that any
  fingerprint movement is attributable to the Windows side alone.
- **It does not touch `proto_weights`, the catalog ordering, or the source-side locality filters.**
  Those are `docs/WORK_ORDER_proto_weight_rows.md` and
  `docs/WORK_ORDER_source_locality_filters.md`. This work order touches `personas.py` only at
  `:730-743` (the `serves` vote), which neither of them edits.
- **No GAP-C number.** The journal's own accounting: the responder divergence is a discipline in
  the harness rather than a defect in either repo, and the Windows skip is a declared scoped gap
  with a docstring, not an undiscovered one.
