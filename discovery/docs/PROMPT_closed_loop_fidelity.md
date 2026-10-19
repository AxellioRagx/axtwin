# Closed-loop fidelity test — a reusable prompt

> **Genre:** reusable method note. Derived from the 2026-08-27 axRange⇄aximprint run recorded in
> `docs/JOURNAL_imprint_loop.md` — four gaps found, four fixed, in four iterations.

**The shape.** Stand up a system whose truth you hold *exactly* → observe it **only** through a
sensor → reconstruct it with the inference tool → diff against truth → fix the tool → repeat until
a pass over a **fresh** capture finds nothing new.

It works because ground truth is not a judgement call: you built the thing, so every difference is
either a real defect or a known limit of the vantage — never an opinion.

---

## The prompt

> Stand up **[GROUND-TRUTH SYSTEM]** and keep it running for the whole exercise.
>
> Capture **[WINDOW]** of its activity through **[SENSOR / VANTAGE]** only — nothing may read the
> system directly.
>
> Run that capture through **[INFERENCE TOOL]**'s full pipeline to produce **[RECONSTRUCTED
> ARTIFACT]**, then diff it against the real system at **[COMPARISON LEVEL]**. Do not redeploy the
> reconstruction.
>
> Identify the gaps, fix **[TOOL]** to close them, and repeat until a pass over a *fresh* capture
> yields no new actionable gap. Do the work on a new branch and keep an append-only journal of each
> iteration's gaps and fixes.
>
> Before you start, tell me every authorization you need in order to run uninterrupted.

### What to swap

| Slot | This run | Other contexts |
|---|---|---|
| ground-truth system | a 14-host axRange twin | a staging cluster, a k8s namespace, a lab VLAN |
| sensor / vantage | 5-port router SPAN → packetXpress | full-segment tap, NetFlow, eBPF, agent logs |
| window | 10 min, 10×60 s windows | hours/days where cadence inference matters |
| inference tool | aximprint | any discovery / asset-inventory / topology learner |
| reconstructed artifact | `twin_config` + `tfc` + `tap` YAML | CMDB entries, a Terraform plan, a diagram |
| comparison level | config/IR diff | schema diff, entity-set F1, golden-file diff |

---

## Six rules that made it work

1. **Pin the vantage in writing before iteration 1.** Then triage every miss as *"the sensor could
   not see it"* vs *"the tool cannot model it"*. Conflating those manufactures false gaps you then
   "fix" — the single most expensive mistake available here.
2. **Compare artifacts, not deployments.** An artifact diff keeps one iteration at minutes; actually
   deploying the reconstruction makes it hours, and you get two iterations instead of six.
3. **Converge on fresh data.** Re-running the same input only proves determinism, which a
   determinism check already proves. The real claim is: *the same system, observed twice, reconstructs
   to the same artifact while the raw observations underneath it differ.*
4. **Run every gate, especially the cross-cutting ones.** Unit tests plus a determinism check are not
   enough. Here the cross-*sensor* equivalence check found a defect in the fix that had just shipped —
   after I had already declared the loop closed.
5. **Suspect the green pipeline with the empty output.** The worst defect found (Zeek validating
   checksums on a mirrored capture, where every packet fails by construction) made all six stages
   report success and produced an empty result. Ask which stage "succeeded" on unusable input.
6. **The journal appends, never rewrites** — including corrections to your own findings. Two of this
   run's entries correct earlier entries of its own; that trail is worth more than a tidy document.

## Two practical notes

- **Ask for every authorization up front, in one batch** (credentials, permission to touch shared
  infrastructure, scope of comparison, stopping condition). The one thing that still interrupted
  this run was a credential that could not be minted, only granted.
- **Expect the first gaps to be in the harness, not the subject.** Two of four here were found
  before a single packet was read.
