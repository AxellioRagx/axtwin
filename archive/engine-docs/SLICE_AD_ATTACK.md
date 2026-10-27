# Slice design — the AD attack chain (red-team OP-ACT activity)

> **STATUS: ✅ IMPLEMENTED + LIVE-VERIFIED on dev45 (2026-06-24, CHANGELOG).** The
> §3 kill chain (recon → LLMNR/NBT-NS poison → SMB/LDAP enum → AS-REP roast →
> Kerberoast → DCSync) shipped as `OP-ACT` inline-steps activities on
> `ad-domain-lab` (`ad-killchain`) and `purple-team-range` (`purple-killchain`) —
> **both** archetypes from §5 fork 3, not "`ad-domain-lab` first" alone — following
> the **recommended path from §5** (seeded foothold, Linux-remote, attack-only). No
> engine change: the already-shipped OP-ACT inline-steps engine
> (`docs/SLICE_ACTIVITY.md`, live-verified 2026-06-12) renders the `tool: nmap` +
> `{run: …}` steps verbatim — this is the §9 follow-on #2 (domain-actor
> activities), unblocked by the shipped identity overlay
> (`docs/SLICE_WINDOWS_AD.md`). **Live-verify (`ad-attack-verify`, dev45):**
> deploy `ad-attack-verify-a76ae3bb` → `COMPLETED`, 58/58 ops; **secretsdump
> DCSync SUCCEEDED — krbtgt hash dumped via DRSUAPI** (the domain-admin
> objective); the in-scenario sensor captured **543 attacker↔DC packets**
> (Kerberos 88, LDAP 389/636, SMB 445, GC 3268, kpasswd 464, 131 DRSUAPI/RPC).
> **Correction found at live-verify:** the attacker kit uses Debian's
> `python3-impacket` (apt, off router egress) rather than the pip-install this
> doc's §6 originally assumed — Debian ships `GetNPUsers.py`/`GetUserSPNs.py`
> under `/usr/share/doc/python3-impacket/examples/` (not in `PATH`), so the
> AS-REP/Kerberoast steps invoke them by path. The design content below (§3–§9)
> is kept as the historical scoping record; §4's decomposition and §5's forks are
> the decisions that shipped. **Deferred (§7/A4, unchanged):** Windows-side
> offense, domain-identity actors, Atomic Red Team/Caldera, `schedule:` windows.
> The **AD defense overlay** that closes the purple loop (§5 fork 4) has also
> since shipped — see `docs/SLICE_OVERLAY.md` (network IDS) and
> `docs/SLICE_WINDOWS_AD.md` §8 (host-side audit), both live-verified 2026-06-24.
>
> **Authority:** implementation (Claude Code), per the per-archetype activity
> delegation (CHANGELOG 2026-06-11). The load-bearing forks in §5 were Claude
> Code's to resolve at build time, recorded in CHANGELOG 2026-06-24.
>
> Companion to `docs/SLICE_ACTIVITY.md` (the OP-ACT engine this builds on),
> `docs/SLICE_WINDOWS_AD.md` (the identity substrate the attacks target — DC,
> domain users, joins, all built), and `docs/SLICE_OVERLAY.md` (the Defense
> overlay that closes the purple loop — now also shipped).

---

## 1. What this is

The Windows-AD identity slice (W0→W2) stood up the *target*: a domain controller
(`corp.lab`), domain users/groups (a Domain-Admins `helpdesk`, a service account
`svc-sql`, a regular `jdoe`), and domain-joined workstations. **None of it is
exercised yet.** This slice makes a red team actually *attack* it — the corpus's
canonical AD kill chain — so the SOC sensor + SIEM see real attack telemetry and
the purple loop (red emulates → blue detects → tune → repeat) has something to run.

The two archetypes already **declare** this as their headline deferred work:
- `ad-domain-lab`: *"LLMNR poison → Responder capture → crack; Kerberoast → Rubeus
  → Hashcat; BloodHound path → DCSync → domain admin"* via OP-ACT.
- `purple-team-range`: *"phish ws1 → lateral to ws2 → pivot to the file/app
  servers → exfil the crown jewel; Atomic Red Team technique runs mapped to ATT&CK."*

## 2. The pivotal fact — the engine already exists

The OP-ACT activity plane is **shipped and live-verified** (`docs/SLICE_ACTIVITY.md`,
the inline-steps MVP). Per activity it emits `deploy-behavior-agent` →
`start-activity` (gated on `mirror-ready[$sensor]`, so capture is live before any
attack traffic) → `wait-actor-ready`, running a rendered step script **in-guest
over the QGA channel**, one-shot. An activity is:

```yaml
activity:
  - id: act-ad-killchain
    actor: red-operator          # external actor (supported today)
    inline:
      steps:
        - { tool: nmap, args: "-sV -Pn", target: 10.60.10.10 }
        - { run: "netexec smb 10.60.10.10 -u jdoe -p 'Axr4nge-Lab!' --shares" }
bindings:
  - { activity: act-ad-killchain, host: attacker }   # runs on the attacker host
```

`soc-detection-lab` already carries a working inline recon chain in exactly this
shape (nmap sweeps + `{run:}` port checks), captured at the sensor on dev45. **Two
facts make the AD attack chain mostly free:**

1. **The attacker host is already staged with the toolkit.** `ad_offense.sh` /
   `offensive_setup.sh` pip-install **impacket** (secretsdump / GetUserSPNs /
   getTGT), **netexec** (CrackMapExec successor), **bloodhound.py**, and clone
   **Responder**, and drop a targets crib (`/etc/axrange/ad_targets.txt`:
   DC/WS/SUBNET). (They need router WAN egress for the installs — §6.)
2. **The Linux attacker drives most AD attacks *remotely*.** impacket + netexec
   do Kerberoast (`GetUserSPNs.py` / `netexec ... --kerberoasting`), DCSync
   (`secretsdump.py -just-dc`), SMB/LDAP enumeration, and password spraying
   **from Linux over the wire** — no Windows agent needed. So a single
   attacker-host activity, expressed as ordered `inline.steps` with `{run:}`
   escapes, can express the whole remote kill chain **today**.

So the attack chain is **activity CONTENT on the existing engine**, plus a small
ergonomic vocabulary extension — not a new execution mechanism.

## 3. The kill chain → inline steps

The corpus's "Top-10 AD attacks" map cleanly onto ordered inline steps on the
attacker, against the now-built `corp.lab` identities (lab password `Axr4nge-Lab!`):

| Stage | Tool (staged) | Inline step (sketch) | Telemetry the SOC sees |
|---|---|---|---|
| Recon | nmap | `{tool: nmap, args: -sV -Pn, target: DC}` | scans |
| LLMNR/NBT-NS poison | Responder | `{run: "timeout 120 python3 /opt/Responder/Responder.py -I eth0 ..."}` | LLMNR/NBT-NS, captured NetNTLM |
| Enumerate (auth) | netexec | `{run: "netexec smb DC -u jdoe -p '…' --users --groups --shares"}` | SMB/LDAP binds |
| BloodHound collect | bloodhound.py | `{run: "bloodhound-python -d corp.lab -u jdoe -p '…' -c All -ns DC"}` | LDAP enumeration |
| Kerberoast | impacket/netexec | `{run: "GetUserSPNs.py corp.lab/jdoe:'…' -dc-ip DC -request"}` | Kerberos TGS-REQ (svc-sql) |
| Crack | hashcat/john (apt) | `{run: "hashcat -m 13100 roast.hash wordlist || true"}` | (offline; no traffic) |
| DCSync | impacket | `{run: "secretsdump.py corp.lab/helpdesk:'…'@DC -just-dc"}` | DRSUAPI replication (the crown jewel) |

The chain is **progressive**: start from a low-priv cred (`jdoe`), enumerate,
Kerberoast `svc-sql`, escalate to the Domain-Admins `helpdesk`, then DCSync the
DC. Each step's traffic is captured (the activity gates on `mirror-ready`), giving
the blue side a labeled attack timeline.

## 4. Decomposition

| Piece | What it delivers | Size |
|---|---|---|
| **A1 — AD-attack tool vocabulary** | Extend the closed `_ACTIVITY_TOOLS` (`render.py`: today `nmap/curl/hydra/nc/ssh`) with the AD kit (`netexec`, `impacket-*`, `responder`, `bloodhound-python`) so steps are `{tool: netexec, args, target}` — ergonomic + validatable, not raw `{run:}`. Small render + validator-vocabulary change. | S |
| **A2 — Kill-chain activities (the content)** | Author the ordered `inline.steps` attack chains as `activity:` blocks: `ad-domain-lab`'s LLMNR→Kerberoast→DCSync chain on `attacker`; `purple-team-range`'s phish→lateral→pivot→exfil chain. Per-archetype content. | M |
| **A3 — Cred + targets convention** | How the attacker gets its starting cred + target IPs: reuse the `ad_targets.txt` crib pattern, and the lab-default `Axr4nge-Lab!` for the seeded `jdoe` foothold. A documented convention, maybe a tiny IR/render affordance. | S |
| **A4 — Deferred** (see §7) | Windows-side tools (Rubeus/Mimikatz/SharpHound as **Windows** activities on the now-joined workstations), domain-identity *actors*, the behavior library, Atomic Red Team / Caldera on `purple-team-range`'s emulation host, `schedule:` windows. | — |

A1+A2+A3 is the increment; A4 is the standing follow-on.

## 5. Forks for the user (the load-bearing calls)

1. **Cred model — unauthenticated-first vs. seeded foothold.**
   - *Seeded* (recommended for the MVP): the attacker is handed a low-priv cred
     (`jdoe` / `Axr4nge-Lab!`, "from a phish") and starts authenticated. Simple,
     deterministic, exercises the high-value stages (Kerberoast/DCSync) reliably.
   - *Unauthenticated-first*: start with Responder LLMNR poison → capture a
     NetNTLM hash → crack → cred → authenticated stages. More realistic, but the
     capture depends on a victim generating LLMNR traffic (needs a benign-user
     activity poisoning timing) and the crack depends on a weak password. Flakier.
2. **Where the attacks run — Linux-remote vs. Windows-side.**
   - *Linux-remote* (recommended for the MVP): impacket/netexec from the
     `attacker` host. Covers Kerberoast/DCSync/enum/spray with no Windows agent.
   - *Windows-side*: Rubeus/Mimikatz/SharpHound as activities **on the joined
     workstations** (now possible — join is built). Richer, but needs Windows
     `inline.steps` (PowerShell runner) + the tools staged on Windows. Defer (A4).
3. **First archetype — `ad-domain-lab` vs. `purple-team-range`.**
   - `ad-domain-lab` (recommended): the focused AD kill chain — smallest, most
     direct exercise of the identities just built.
   - `purple-team-range`: the fuller ATT&CK kill chain + the Caldera emulation
     host — bigger, more moving parts.
4. **Detection coupling — attack-only vs. close the purple loop.**
   - *Attack-only* (recommended for this slice): generate the attack traffic; the
     SOC merely captures it. **Detection (Sigma/Suricata rules, the red→blue
     scoring loop) is the Defense overlay** — a *separate* deferred slice. Keeps
     this increment to "make the attack real."
   - *Close the loop now*: bundle the detection-coverage overlay. Larger; couples
     two slices.

**Recommended path:** seeded foothold + Linux-remote + `ad-domain-lab` first +
attack-only — the smallest thing that produces a real, captured AD kill chain on
the substrate we just built, deferring Windows-side tooling and detection.

## 6. Hard dependency — attacker egress for the toolkit

`ad_offense.sh` / `offensive_setup.sh` install impacket/netexec/bloodhound
and git-clone Responder — these need **internet egress**, provided by the
archetypes' `gw`/`core` **router** (router slice Phase 2, built + live-verified).
So the attack chain's live-verify depends on the router giving the attacker WAN
egress. Alternative if egress is unavailable: **bake the toolkit into a custom
attacker image** (an imagebuild `provision:` Kali-like template) — heavier, a
follow-on. The scripts already degrade gracefully (`… deferred (needs egress)`),
so a no-egress deploy stages the range but can't run the authenticated attacks.

> **Resolved at live-verify (2026-06-24, CHANGELOG):** the shipped kits install
> `python3-impacket` via **`apt`** off router egress, not a `pip` install as
> originally sketched — Debian's `python3-impacket` package ships
> `GetNPUsers.py`/`GetUserSPNs.py` under
> `/usr/share/doc/python3-impacket/examples/` (not on `PATH`; only
> `impacket-secretsdump` etc. are), so the AS-REP-roast/Kerberoast steps invoke
> them by that path.

## 7. Deferred / out of this increment

- **Windows-side offense** (Rubeus Kerberoast, Mimikatz, SharpHound) as activities
  on the joined workstations — needs a Windows `inline.steps` runner + Windows
  tool staging. The join (built) unblocks it; it's the natural next attack increment.
- **Domain-identity *actors*** — an activity whose `actor:` *is* a domain identity
  (vs. the external `red-operator`). The §5.6 actor→host resolution + the activity
  engine's domain-actor path are still deferred; the MVP passes creds as step args.
- **The behavior library** (`behavior:` / `overrides:` refs) and the **AI red-team
  role** (OPEN_QUESTIONS Q8) — the attacks here are concrete inline steps, not
  library-resolved behaviors.
- **Atomic Red Team / Caldera** on `purple-team-range`'s `emulation` host — the
  ATT&CK-technique-runner purple loop; a richer A2 follow-on.
- **The Defense / detection overlay** (Sigma rules, Sysmon/Wazuh telemetry, the
  red→blue scoring loop) — a separate slice (§5 fork 4).
- **`schedule:` windows** — one-shot only, per the OP-ACT MVP.

## 8. Done-criteria — ✅ MET on dev45, 2026-06-24 (CHANGELOG)

1. ✅ `axrange validate | plan | deploy` of `ad-domain-lab` (and `purple-team-range`)
   with the kill-chain `activity`; `plan` showed `deploy-behavior-agent →
   start-activity` (gated on `mirror-ready`) → `wait-actor-ready` for it.
2. ✅ On dev45: the attack ran on the `attacker` host (router egress staged the
   toolkit), and the SOC **captured the AD attack traffic at the sensor** — 543
   attacker↔DC packets across Kerberos 88, LDAP 389/636, SMB 445, GC 3268, kpasswd
   464, and 131 DRSUAPI/RPC (DCSync).
3. ✅ A **tangible attack outcome** in-guest: `secretsdump` **DCSync succeeded**,
   dumping the domain's krbtgt hash via DRSUAPI — the chain reached domain-admin
   against the substrate.

## 9. Open questions (resolve by building)

- **Step-runner shell on Linux vs. tool availability** — the inline runner is
  best-effort (a step's nonzero exit is logged, not fatal). For a kill chain where
  later stages depend on earlier output (the cracked cred), do we need light
  inter-step data passing (write to a file, read in the next `{run:}`), or is the
  "seeded cred" model (fork 1) enough to keep steps independent? (Lean: seeded →
  independent steps.)
- **Responder timing** — LLMNR capture needs a victim emitting name-resolution
  traffic. If we keep Responder in the chain (vs. the seeded model), we need a
  benign-user activity on a workstation generating the bad lookups.
- **Cred secrecy** — the lab password is in the scenario/steps in clear. Fine for a
  range; note it (same stance as the existing lab-default passwords).
- **netexec/impacket invocation drift** — exact flags vary by version; the live
  verify pins them (the `ADAPTER.md` §7 resolve-by-building pattern).
