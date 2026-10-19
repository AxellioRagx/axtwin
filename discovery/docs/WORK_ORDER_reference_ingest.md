# Work order — reference ingest: operator assertions, a directory export, and the evidence model that ranks them

**STATUS:** **IN-PROGRESS — S0 and S1 LANDED 2026-09-04, S2 LANDED 2026-09-05; S3-S5 OPEN.** (Drafted the same day.)
S2's own additions to the record, for a reader who needs only what changed since the S0/S1 note
below: **(1)** the assertions document lives at `src/aximprint/operator_assertions.py`, a
below-every-layer module rather than inside `ingest/`, because the cast compiler and the reference
plugin sit on opposite branches and both need the same parse; **(2)** **D4 landed here, not in S1**
— `role_evidence_class` is a lookup and `snapshot()`'s roles rows carry a class — and the
cross-class reduction was **removed from the store rather than corrected there**, because
`evidence_rank` lives in `ir`, which `store` may not import, and because keeping both readings is
the house rule (`ir/materialize.py::resolve` picks the winner now); **(3)** **D17 gained a "later"
test the ruling implies but does not state** — drift fires only when the PIT's `observed_to` falls
after the file's `as_of`, or the flag fires on every override ever written and says nothing;
**(4)** the belief-store door **cannot mint an entity** and its rows are dated at the file's
`as_of`, so a default `materialize` (bounded at the last window) does not carry them — both limits
are printed by `assertions ingest` rather than left to be discovered. Also corrected in place: the
S0/S1 note below says D4 *"trips `_check_schema_matches_this_build` on an old store"*; it does not
— that guard compares DDL columns and `snapshot()`'s return shape is not one, verified against a
copy of the CorpAPCN belief store (`LES-139`: a declared reason is a factual claim).
S0's hand-backs and sidecar hygiene and S1's evidence model are built, tested and recorded
(`docs/CHANGELOG.md` 2026-09-04). Three deviations are named in that entry and are the ones a
reader of this document needs: **(1)** the precedence resolver was built in `synth/compiler.py`
rather than at D3's `ir/materialize.py::resolve()`, and **D4's two de-constanted sites were NOT
done** — D4 changes `BeliefStore.snapshot()`'s tuple shape, which trips the store's build guard on
every existing store, including the ones this round had to read to disprove §7.1's premise; both
remain available to S2, the first slice with a producer that needs them. **(2)** `bundle_schema`
ships as `axr-topology/1.3`, not §7.4's drafted `1.4`, because that number names a shape carrying
`services[]` which this build does not emit. **(3)** `overrides` is a `{class, value}` dict naming
the runner-up, not a list of every beaten reading. §7.3's three parser gaps are deferred exactly as
§7.3 defers them (Kerberos `service` + the realm to **S5**; SMB shares to
`WORK_ORDER_protocol_faithful_services.md`), and §7.1's premise is corrected in place while the
`role: adds` revert itself stays for **S4**. One defect the widening exposed was fixed in the same
round and is not in any slice's scope: `ir/merge.py` carried a second evidence rank table.
**Authority:** the direction and every ruling cited is the **user's** (axRange
`docs/design/14-operator-assertions.md` §7 and `docs/design/13-golden-twin-fidelity.md` §8, both
ratified; axRange `docs/CHANGELOG.md` 2026-09-04 ×2, 2026-09-03). This draft — record shapes,
schema bump, slices, and every option in §10 — is **implementation (Claude Code)**, overrulable.
**Genre:** work order (`docs/CONVENTIONS.md` §2).
**Depends-on:** nothing unbuilt here. Sequenced by spec 13 §7 **after the first golden**; slice S0
is hand-backs that should land ahead of it.
**Blocks:** spec rows 7 (security), 4 (state), and row 3's inventory-backed rule; axRange's Console
canvas, whose write-back target is §4's file; the replacement approach for
`docs/WORK_ORDER_windows_responder.md`'s gap (§5.5's real DC).
**Supersedes:** `src/aximprint/catalog/roles.yaml:85-86`'s premise (§7.1) and the two
`domain-controller` exception strings at `scripts/check_role_fixed_point.py:72-75`. Nothing else.

---

## 1. What this actually is

A twin is cast from what one tap point saw, and §2 measures what that costs: role-less hosts
dropped, segments pruned, no OS attribute, no principal, group, SPN or ACL edge anywhere in the
twin, and a domain controller that answers nothing. None of it is a reproduction gap — axRange
builds a deliberately weak AD forest and has live-verified a DCSync against it. It is an
**observation gap**.

This builds the two channels that close it, in the ruled order: **operator assertions first**, the
**LDIF export second**. Both come through one door — a file dropped beside the imprint — and both
need the thing the compiler lacks: **a precedence resolver**. Nothing here resolves two evidence
classes for one fact today, because the store has never held two.

## 2. Measured 2026-09-04, before designing anything

Read-only (`duckdb.connect(path, read_only=True)`) against
`/home/rocky/corpapcn-fidelity-artifacts/src-ir-rematerialized-20260902.duckdb` (PIT
`pit-03de65ec67-1787823960`, 11 windows) and `.../capcn-ir6.duckdb`:

| question | answer |
|---|---|
| placed hosts (`locality='local' AND segment IS NOT NULL`) | **141** |
| role-less, dropped by D5 (`synth/compiler.py:200-201`) | **19**, of which **5** are present in ≥ 80 % of windows |
| observed segments / carrying a roled host | **32 / 26** — 6 pruned |
| `ir_identity` | 730 rows: `dns` 708 (`referenced`), `kerberos` 10, `ntlm` 8, `dhcp` 4 (all `observed`) |
| does the wire carry the forest name? | **yes** — `BW-L$/AXELLIO.DOM`, `desktop-21n0pq1$/AXELLIO.DOM`, `matthew.admin/AXELLIO.DOM` in observed Kerberos rows; `axdc01.Axellio.dom` in observed NTLM rows |
| principals of any kind | 2 users (one is `Guest`), 2 machine accounts, 4 computer FQDNs. **No groups, OUs, SPNs, UAC flags, ACL edges or password policy** |
| port-389 `serves` vote (`synth/personas.py:749-751`) | `dns-resolver: 2`, `domain-controller: 2`, `file-server: 1` — a **genuine 2-2 tie**, broken by `Counter.most_common`'s insertion order; today `dns-resolver` wins |

Two things this settles. **`roles.yaml:85-86`'s premise is false** — *"nothing in a capture
supplies an AD forest name: `ir_identity` on the CorpAPCN run carries only external DNS"* — and it
is the stated reason `role: adds` was reverted, so it is load-bearing. And **LDAP peer resolution
aims at the wrong role**, so even a real DC would not be dialled.

## 3. The evidence model change

`docs/SPEC_network_ir.md` ("Evidence classes") names three and excludes `defaulted` as a *compiler*
label. That reasoning survives; what changes is that two of axRange `14` §2's six classes do not
exist here yet.

| # | Decision | Ruling (proposed) |
|---|---|---|
| **D1** | Which classes join the IR? | **`imported` and `asserted`.** `EVIDENCE_CLASSES` (`ir/schema.py:59`) becomes `("asserted", "imported", "observed", "referenced", "imputed")` — **in precedence order**, so the tuple *is* the ranking and no second table can drift from it (`LES-101`). `referenced` keeps its meaning exactly: second-hand evidence *from inside the capture* (`windowing/entities.py:106,120-124`; the class behind `ir_unseen`'s 510 rows). `compiler` stays **out** of the IR, on the SPEC's existing argument. |
| **D2** | Version bump? | **Yes, MINOR** — a vocabulary widening on an existing column, additive by the argument `UNSEEN_REASONS` already records (`schema.py:99-105`). One new thing the SPEC must say: the tuple now carries a **rank**, so a 1.3 reader that hardcoded "`observed` is strongest" mis-ranks a 1.4 artifact — disclosed in the reading rules, not fixed by the version. Numbering is not clean: **§10 D-a**. |
| **D3** | Where is precedence resolved? | **`ir/materialize.py::resolve()`, from a new `EvidenceSnapshot` field.** That dataclass (`materialize.py:568-613`) is frozen and `resolve()` is pure — *"two PITs built from equal evidence are equal by construction"* — so the resolver belongs on the conclusions side. Add `reference: dict`, fill it in `snapshot_evidence()`, resolve highest-rank-per-(entity, field) in `resolve()`. |
| **D4** | What stops being a constant? | Two sites, right today and wrong the moment a class can be asserted. `materialize.py:760` (`"role_evidence_class": "imputed" if role else None`) becomes a lookup; `BeliefStore.snapshot()`'s `roles` rows (`belief_store.py:615-618`) are 3-tuples whose docstring says *"No `evidence_class` column, deliberately: every row is `imputed` by construction."* The tuple gains a class and **the docstring is corrected in the same change** (`LES-043`). Free on an incubating store (`LES-001`); it trips `_check_schema_matches_this_build` on an old one, which is the intended loud failure (`LES-104`). |
| **D5** | `defaulted` → `compiler` | Renamed in the sidecar (9 `prov(..., "defaulted", ...)` sites: `compiler.py:337,346,352,414,429,432,477,550,614`). The *meaning* is unchanged, which keeps it out of `LES-131`'s hazard — but a consumer outside the producer reads the string (`axrange/api/imprints.py`), so the artifact must announce it: **D6**. |
| **D6** | Does the sidecar declare its own version? | **Proposed yes** — a top-level `"schema": "axr-cast-provenance/1"`. `LES-001`'s amended discriminator is *"a schema is versioned when something OUTSIDE its producer depends on its shape"*, and axRange's imprint reader is exactly that; without it, `compiler` vs `defaulted` is indistinguishable from a consumer bug. **Ratifying a schema is the user's call — §10 D-b.** |
| **D7** | What else does the sidecar gain? | `overrides` plus two per-field extras. An override records the loser: `{"evidence_class": "asserted", "confidence": 1.0, "source": "operator:raghu", "overrides": {"class": "imputed", "value": "dns-resolver"}}`. **`confidence` only where a producer supplies one** (role/segment confidence, an identity binding's, `1.0` for `asserted`); **absent**, never null or invented, elsewhere (`LES-006`). **`signal`** is a short string naming the rule or stem — `"zeek kerberos.log client"`, `"catalog/roles.yaml domain-controller (88/tcp+389/tcp)"` — never a sentence. These are what let axRange's Console shade without inventing numbers (`14` §5). |
| **D8** | Does the `population` block survive? | **Yes, unchanged in shape**, plus `suppressed: [{key, note}]` for §4's `suppress:`. Its reconstruction identity changes once `docs/WORK_ORDER_deployment_scope.md` lands (`role_less`/`excluded_by_occupancy` stop being drops), so the two land together — **§9 seam**. |
| **D9** | Installer copies the sidecar | **Yes.** `scripts/install_cast_as_twin.py:231-233` copies `topology.json` and not `provenance.json`, so **no compiler sidecar exists on disk in any installed cast** — found independently by both axRange drafts. One `if`, one `print`. |

House rule, binding this build: **no class masquerades as a higher one, and an override is
visible, never an erasure.** The store keeps both readings; the sidecar names the loser.

## 4. Input 1 — the assertions file

`<imprint-id>.assertions.yaml`, schema `axr-assertions/1`. axRange `14` §3's draft is close;
four things it does not say are named below.

```yaml
schema: axr-assertions/1
imprint: corpapcn-twin
as_of: 2026-09-04T18:00:00Z
source: "operator: raghu"
add:
  - {kind: segment, key: "10.80.31.0/24", name: seg-10-80-31, zone_type: vlan, note: "the OT lab"}
  - {kind: host, key: "address:10.80.20.5", segment: "10.80.20.0/24", role: domain-controller,
     listen: [{port: 88, proto: tcp}, {port: 389, proto: tcp}, {port: 445, proto: tcp}],
     note: "the second DC; the census read it as a resolver"}
  - {kind: conduit, key: "10.80.20.0/24|10.80.31.0/24", note: "OT lab reaches the DC"}
override:
  - {key: "address:10.80.20.5", field: role,      value: domain-controller, note: "..."}
  - {key: "address:10.80.20.5", field: image,     value: "windows-server-2022@v1", note: "..."}
  - {key: "address:10.80.20.5", field: resources, value: {cpu: 4, ram: 8Gi, disk: 60Gi}, note: "..."}
suppress:
  - {key: "address:203.0.113.9", note: "out of scope: a cloud CDN edge"}
```

| # | Decision | Ruling (proposed) |
|---|---|---|
| **D10** | Keys | The stable keys the IR already mints: `entity_key` is self-describing `<key_kind>:<value>` (`SPEC_network_ir.md:154`) — `address:…` / `hostname:…` / `mac:…`; a segment's key is its **observed** CIDR (never a relocated one — relocation happens after resolution, §5.6); a conduit's key is `"<cidrA>|<cidrB>"`, sorted, matching `compiler.py:514-518`'s own pair ordering. Never a twin-config index. |
| **D11** | Fields `14` §3 omits | **(a) `add: host` needs `segment:`** — the compiler's `placed` test is `locality == 'local' and e["segment"]` (`compiler.py:171`), and an address alone names no segment. Derived when it falls inside an observed or asserted segment; **required** otherwise; refused loudly if neither. **(b) `add: segment` needs `name:`** — optional, defaulting to `windowing.segments.segment_name(cidr)`. **(c) `zone_type`** is a compiler constant today (`compiler.py:352`, labelled `defaulted`, *"no VLAN evidence exists at this vantage"*); an asserted value replaces it and the sidecar records `asserted`. |
| **D12** | `override.field` vocabulary | **Closed, per kind, fail-closed** (`CONVENTIONS.md` §3). Host: `role`, `image`, `resources`, `segment`. Segment: `zone_type`, `name`. Anything else is a refusal naming the field and the accepted set — never a silent skip, which is how an operator's edit vanishes without a message. Asserting `image`/`resources` moves those facts out of `compiler` class into a **scored** row, which is the point (`14` §4). |
| **D13** | "conduits and routes" | **Conduits land; routes do not, and the reason is named.** A conduit is a real object (`axrange/twinconfig/schema.py::TwinConfigConduit`) the compiler already emits. **There is no route object in a twin config at all** — inter-segment reachability is the synthetic gateway's, and the only route-shaped surface is the fabric slice's per-router `routing:`, which the cast never emits. `kind: route` is **rejected in v1, saying so** — §10 D-c. |
| **D14** | Ingest and class | A **reference-ingest plugin kind** — non-sensor, snapshot-shaped — reading the file into the belief store as `asserted` assertions with the file's `as_of` as `window_start`, `confidence: 1.0`, `source: "operator:<name>"`. Confidence is never authored (`14` §3). **No belief-store column changes**: the existing `assertions` shape carries all of it. |
| **D15** | Merge across saves | Keyed `(kind, key)` for `add`/`suppress` and `(key, field)` for `override`; a save **replaces** a same-key record and appends otherwise; `as_of` is rewritten; a key in both `add` and `suppress` is a **refusal**, not a precedence puzzle. The file is re-emitted in canonical order (`add` by `(kind, key)`, `override` by `(key, field)`, `suppress` by `key`) so a no-op save is **byte-identical**. |
| **D16** | Determinism | Same store + same file → same cast, byte for byte (`LES-002`). Assertions are read once into the frozen snapshot and resolved by a total order over `(class-rank, key, field)`; nothing iterates in file order. `scripts/verify_determinism.py` gains an assertions fixture. |
| **D17** | Assertion drift | **Flag only** (ruled). A later PIT carrying a lower-class contradicting fact does not demote anything: the assertion still wins, the sidecar's `overrides` record gains `"drift": {"class": "observed", "value": "web-tier", "pit_id": "<pit>"}`, and a `caveats` line names it. Both readings stay in the artifact. |
| **D18** | Suppression accounting | A suppressed entity **never silently vanishes**: excluded from `placed`, listed in `population.suppressed` with its note (D8), named in `caveats`. That preserves the block's own rule — *"the original population must be reconstructible from the sidecar alone"* (`compiler.py:699-703`). |

**One thing `14` §3 assumes that does not exist.** It places the file *"beside the imprint's other
artifacts in the imprint directory"*. **There is no imprint directory in the CLI's model**:
`aximprint cast --store <ir.duckdb> --pit <pit-id> --out <dir>` names a PIT and an output
directory, and the stable imprint name is chosen later by `scripts/install_cast_as_twin.py`.
Proposed: `cast` gains `--imprint-id` (the parameter `synth/imprint_bundle.py::build_topology_bundle`
already accepts, defaulting to the pit id) and `--assertions`, defaulting to
`<dirname(store)>/<imprint-id>.assertions.yaml`.

**A limit, stated up front.** `snapshot_evidence()` raises when a span has no windows
(`materialize.py:633-637`) — *"a PIT with no observation extent could not be compared against
anything"* — so an **assertions-only imprint cannot be materialized**. Assertions enrich a
capture; they do not substitute for one. Seeding a twin from a design document alone (`13` §3's
operator channel) is out of scope.

## 5. Input 2 — the LDIF directory export

Ruled (`13` §8.4): an LDIF export, file-dropped, run by the reference owner, **no credential and
no network reach from our side**.

### 5.1 The command lines this project publishes

```
:: domain NC -- users, groups, computers, OUs, the domain object
ldifde -f corp-domain.ldf -u -p subtree -d "DC=corp,DC=example,DC=com" ^
  -r "(|(objectClass=user)(objectClass=group)(objectClass=computer)(objectClass=organizationalUnit)(objectClass=domainDNS))" ^
  -l "<allowlist, comma-separated>"

:: configuration NC -- sites and subnets, the one topology fact the wire never gives
ldifde -f corp-config.ldf -u -p subtree -d "CN=Sites,CN=Configuration,DC=corp,DC=example,DC=com" ^
  -r "(|(objectClass=site)(objectClass=subnet)(objectClass=server))" ^
  -l "cn,siteObject,location,dNSHostName,distinguishedName"
```

`ldapsearch` equivalent for a non-Windows or read-only-bind site — AD pages at 1000 objects, so the
paged control is not optional:

```
ldapsearch -LLL -x -H ldaps://dc01.corp.example.com -D 'CORP\svc-export' -W \
  -b 'DC=corp,DC=example,DC=com' -E 'pr=1000/noprompt' \
  '(|(objectClass=user)(objectClass=group)(objectClass=computer)(objectClass=organizationalUnit))' \
  <allowlist, space-separated> > corp-domain.ldif
```

### 5.2 The attribute allowlist this project ships

A data file (`CONVENTIONS.md` §3, *"catalogs and templates are data files"*) — proposed
`src/aximprint/ingest/reference/ldif_allowlist.yaml` — so the owner can diff exactly what was asked.

| group | attributes |
|---|---|
| every object | `objectClass`, `distinguishedName`, `cn`, `name`, `objectSid`, `objectGUID`, `whenCreated`, `whenChanged` |
| user | `sAMAccountName`, `userPrincipalName`, `displayName`, `userAccountControl`, `servicePrincipalName`, `memberOf`, `primaryGroupID`, `adminCount`, `msDS-AllowedToDelegateTo`, `msDS-AllowedToActOnBehalfOfOtherIdentity`, `pwdLastSet`, `accountExpires`, `lastLogonTimestamp` |
| computer | `sAMAccountName`, `dNSHostName`, `operatingSystem`, `operatingSystemVersion`, `userAccountControl`, `servicePrincipalName`, `memberOf`, `primaryGroupID`, `msDS-AllowedToDelegateTo`, `lastLogonTimestamp` |
| group | `sAMAccountName`, `member`, `memberOf`, `groupType`, `adminCount` |
| OU | `ou`, `distinguishedName` |
| domain object | `minPwdLength`, `pwdProperties`, `maxPwdAge`, `minPwdAge`, `pwdHistoryLength`, `lockoutThreshold`, `lockoutDuration`, `lockOutObservationWindow`, `ms-DS-MachineAccountQuota`, `msDS-Behavior-Version` |
| configuration NC | `cn` (a subnet object's `cn` **is** its CIDR), `siteObject`, `location`, `dNSHostName` |
| ACL edges | `nTSecurityDescriptor` — **DACL only.** We do not ask for the SACL, and therefore do not ask for `SeSecurityPrivilege`; `READ_CONTROL`, which authenticated users hold on most objects, is enough |

**Excluded by name, so the owner can verify the negative:** `unicodePwd`, `dBCSPwd`,
`ntPwdHistory`, `lmPwdHistory`, `supplementalCredentials`, `userPassword`, `msDS-ManagedPassword`
(gMSA), `ms-Mcs-AdmPwd` / `ms-Mcs-AdmPwdExpirationTime` (LAPS v1), `msLAPS-Password` /
`msLAPS-EncryptedPassword` / `msLAPS-EncryptedPasswordHistory` (LAPS v2), `msDS-KeyCredentialLink`
(shadow-credential material), `msPKI-*`, `thumbnailPhoto` / `jpegPhoto`, and personal-contact
attributes (`mail`, `telephoneNumber`, `homeDirectory`) that map to no twin fact.

**This does not contradict `CONVENTIONS.md` §3's forensic-completeness rule.** That rule governs
what aximprint *records of what it observes*, and refuses no field on disclosure grounds. This is a
list of what we **ask a third party to run**, which is a different act — `13` §5's line is the
operative one: *inject posture, never secrets.* Whatever the owner does put in the file is parsed
and recorded in full; the allowlist is a request, not a redactor. A secret-bearing attribute that
turns up anyway is a **loud refusal of the file, naming the attribute** — not a silent drop, which
would leave a sanitized artifact indistinguishable from a clean one.

### 5.3 Parsing

| # | Decision | Ruling (proposed) |
|---|---|---|
| **D19** | LDIF parser | **In-repo RFC 2849 reader** (`ingest/reference/ldif.py`), no new dependency. Four line shapes matter — `attr: value`, `attr:: <base64>`, continuation lines beginning with one space, blank-line record separators — plus `version: 1` and `#` comments; `changetype:` records are **refused** (an export, not a changelog). `pyproject.toml`'s runtime deps are deliberately two packages; `python-ldap` is a C extension against OpenLDAP headers, `ldif3` is unmaintained. Strict: a line matching none of the shapes is an error naming the line number (`LES-007`). |
| **D20** | Security descriptor | **A real dependency question — §10 D-d.** The DACL is a documented binary layout (`SECURITY_DESCRIPTOR_RELATIVE` → offsets → `ACL` header → `ACCESS_ALLOWED_ACE` / `ACCESS_ALLOWED_OBJECT_ACE` → `SID`s), parseable with `struct` in ~200 lines; impacket's `ldaptypes` is the reference implementation everyone else uses. Recommend **in-repo**, on the thin-dependency posture and because `LES-008`'s "parse the value, don't pattern-match its shape" is easier to hold in our own parser — with the caveat named in §10. |
| **D21** | Which ACEs become edges | Only rights the twin can express, mapped onto `IdentityEntry.acl`'s vocabulary (`axrange/ir/schema.py:335-339`): `GenericAll`, `GenericWrite`, `WriteDacl`, `WriteOwner`, `ForceChangePassword` (extended right `00299570-…`), `AddMember` (validated write `bf9679c0-…`). Everything else is **counted and disclosed, never bucketed into the nearest neighbour** (`entities.py:100-103`'s idiom). Inherited ACEs carry an `inherited` flag and are **not** emitted by default — a twin carrying every inherited edge is noise, not posture. |

### 5.4 Where the facts land

Assertions first (DESIGN_BRIEF §7's uniform shape — one shape for every learner), then the IR.
Predicates are a **declared closed vocabulary** in one module (proposed `REFERENCE_PREDICATES`),
because every table keyed by a vocabulary must be enumerable from one place (`LES-087`):
`is_principal`, `member_of`, `has_spn`, `in_ou`, `runs_os`, `acl_<right>`, `delegates_to`,
`policy_<param>`, `site_subnet`.

The IR then gains **three tables** (additive; `ir_identity` keeps its job of naming *identifiers*,
which is not what a principal record is):

- **`ir_principal`** — per (pit, principal): `principal_key` (`sam:`/`sid:`), `entity_key`
  (**nullable** — most directory principals map to no observed entity, and that asymmetry is
  §5.7's cross-check), `kind` (user/computer/group/ou/domain), `sam`, `upn`, `dn`, `ou`, `sid`,
  `os`, `uac`, `asrep_roastable`, `delegation`, `delegation_to` (JSON), `spns` (JSON), `member_of`
  (JSON), `admin_count`, `evidence_class`, `confidence`, `source`, `as_of`.
- **`ir_acl_edge`** — per (pit, src, right, target): `src_principal`, `right`, `target_principal`,
  `inherited`, `evidence_class`, `source`.
- **`ir_policy`** — per (pit, scope, parameter): password policy, machine-account quota,
  functional level, **and the realm** (§5.6).

Sites and subnets are deferred to the slice that consumes them (row 2's topology hint), on
`LES-101`'s rule: a declared table nothing reads is documentation in a data costume.

### 5.5 The compile step

`synth/compiler.py` gains an identity pass writing `twin_config.identity` in exactly the shape
`axrange/twinconfig/schema.py::TwinConfigIdentityUser` accepts — `id`, `groups`, `ou`, `password`,
`spn`, `asrep_roastable`, `acl`, `delegation`, `host` — plus, on the host the census named
`domain-controller`, `install: [{role: adds}]` and `provides: [{domain: <realm>}]`, and `joins:`
on every host the export shows joined. That is the shape
`twin-configs/corp-enterprise/corp-enterprise.yaml:47-52,101,147-168` already ships and axRange
has live-verified to a DCSync.

**One field does not fit and must not be dropped silently: `delegation_to`.** `IdentityEntry` has
it (`axrange/ir/schema.py:342`) but `TwinConfigIdentityUser` does not, and
`axrange/twinconfig/expand.py:384-401` forwards nine fields, not ten — so **constrained delegation
read from `msDS-AllowedToDelegateTo` cannot reach a twin today**. Seam item (§9); until it lands,
such a principal is emitted without it and named in `caveats`.

**Passwords are lab passwords**, set only to make a posture reproducible — a Kerberoastable account
gets a crackable lab password so its TGS is crackable, as `corp-enterprise.yaml:158` already does.
No hash, no real password, ever, under any class.

### 5.6 The realm, and the one place the model bends

Ruled: **the twin's forest is the lab realm `corp.axr.lab`**; the import supplies principals,
groups and posture. The observed realm was offered and not chosen.

- The realm **is** a first-class fact and is recorded as one: `ir_policy(scope='domain',
  parameter='realm', value='AXELLIO.DOM', evidence_class='observed')` from the wire, `imported`
  from the export's domain object. Today it survives only inside `Identifier.raw`, because
  `windowing/entities.py:241`'s `components = parts[:-1]` drops it by design and `raw` has no
  column at the IR boundary — a parser-and-schema change, not a capture problem (§7.3).
- `identity.domain` in the twin config is the **lab realm**, class **`compiler`**, with
  `observed_realm` recorded beside it in the sidecar.
- **Principal names:** `sAMAccountName` is carried **verbatim** (`alice` stays `alice`) — it is
  what a detection rule reads. Only the *suffix* is rewritten: `alice@axellio.dom` →
  `alice@corp.axr.lab`, and an SPN's host part follows the twin's own hostname under the lab realm
  (`ldap/axdc01.axellio.dom` → `ldap/axdc01.corp.axr.lab`). Groups, OUs, UAC flags, delegation and
  ACL edges stay **`imported`**, unchanged. Anything with no directory evidence — lab-default users
  where the export carried none — is **`imputed`**, as ruled.

**The wrinkle, stated plainly.** A `compiler`-class value is winning over an `imported` one, which
inverts the precedence order the whole model rests on. The resolution: **it is not a precedence
event at all.** It is a *declared deployment transformation*, the exact analogue of `--relocate`,
which already moves an observed CIDR to avoid colliding with the deployment target's own address
space and records the original (`compiler.py:346,349-351`). The rule this build proposes, and which
`14` §2 does not currently have: **transformations declared by the compiler for deployability are
not evidence, do not participate in precedence, are applied after resolution, and always record the
pre-transformation value.** Two consequences: a scorecard must score such a row as *reproduced
under a declared transformation* rather than as a mismatch — which it must already do for relocated
CIDRs, the same question unnoticed — and the escape hatch is an ordinary `override: {field:
domain}` assertion, which restores precedence by moving the choice into a class outranking
`imported`.

### 5.7 The cross-check that makes the import honest

`13` §5.6: *the directory says who should exist, the wire says who acts.* With `ir_principal`
carrying a nullable `entity_key`, both asymmetries are a query: principals declared and never seen,
hosts seen and never declared. Reported, never reconciled — a mismatch is evidence about the
vantage, not an error.

## 6. Boundaries and honest limits

**Not built here:** a live LDAP bind (one parser, shaped so a bind later feeds it unchanged);
BloodHound JSON; host inventory or device configs — the third and fourth ingests (`13` §5); the
Console canvas, which is axRange's, over §4's file; any change to what a *sensor* ingest produces
(the `acquire` boundary is untouched); a seed-a-twin-from-nothing path (§4's limit).

**Limits that survive the build.** An assertion is not an observation, is never scored as one, and
a twin can be made to pass a scorecard on assertions alone — the guardrail is the per-row evidence
mix (`14` §2), which lives in axRange's scorecard, so **nothing in this repo prevents that, by
design**. An export is a point in time and says nothing about the moment the capture was taken;
two `imported` snapshots of different ages are not comparable, and the time bound is the only thing
that says so. The DACL is not the whole policy: inherited ACEs, deny ACEs and edges reached through
deep group nesting are recorded but not necessarily reproduced, and per-rule firewall policy stays
the one reproduction gap injection cannot fill (`13` §3.2). **No credential, no bind, no network
reach** from this project into a reference network, in this build or any follow-on reusing this
parser.

## 7. Hand-backs folded in

**7.1 The `roles.yaml` premise — fixed here (S4).** `catalog/roles.yaml:85-86` and
`docs/JOURNAL_corpapcn_fidelity.md:695` justify reverting `{role: adds}` on a claim §2 measures
false. `scripts/check_role_fixed_point.py:72-75`'s two rows leave `DECLARED` entirely rather than
getting a new reason, because the twin will genuinely serve 88 and 389.

**7.2 The port-389 tie-break — fixed here (S0), in the mechanism.** `synth/personas.py:751` is
`{p: v.most_common(1)[0][0] for p, v in serves_votes.items()}`; `Counter.most_common` is
insertion-ordered on a tie, and §2 measures a real 2-2 tie on 389. Fixed by ranking ties on the
catalog's **declaration order**, already most-specific-first and already ruled on as a decision
with a measurable blast radius (`LES-136`), falling back to role name for a total order. Pinning
`389/tcp` at the DC in the catalog is the content workaround and is **not** the fix — it leaves the
mechanism order-dependent for every other contended port (445 is 2-1 today).

**7.3 The services draft's three parser gaps — two here, one there.**
`ingest/zeek/plugin.py::_kerberos` drops the `service` column carrying
`ldap/axdc01.axellio.dom` — the cleanest single statement in the capture that `axdc01` is the
domain's LDAP server — and the realm is dropped by `entities.py:241`. **Both land here (S5)**,
because §5.7's cross-check depends on them and §5.6 needs the realm as a column either way. **SMB
share paths stay** in `docs/WORK_ORDER_protocol_faithful_services.md` as its S4 input: they serve a
share list on a real server, not identity.

**7.4 `topology.json` 1.4 — the block is theirs, the version carrier is ours.** The `services[]` +
`observations` block is specified once, in that work order's §6.1; this one does not duplicate it.
But **`topology.json` has no version of its own**: `synth/imprint_bundle.py:87` emits
`"schema_version": pit_row.get("schema_version")`, which is the **IR's** version passed through. So
"topology.json 1.3 → 1.4" as written either bumps the IR or means nothing. Proposed: the bundle
gains its own `"bundle_schema": "axr-topology/1.4"`, distinct from the passthrough — **§10 D-e**.

**BUILT AS `axr-topology/1.5`, 2026-09-05.** `1.4` shipped `services[]` + `observations` with
the protocol-faithful-services build, exactly as this section proposed; `1.5` adds
**`cast_invocation`** — a block this section did not anticipate and axRange's Console apply path
found the need for. A bundle could describe the twin it held and not RE-DERIVE it: `--relocate`
survived only as per-field `transformation:` records in the sidecar, and `--max-count` /
`--no-chatter` / `--assertions` survived nowhere, so a recompile from the bundle silently
reproduced the SOURCE network's CIDRs. See CHANGELOG 2026-09-05 "the apply contract" and
`docs/SPEC_network_ir.md` "The three cast-output files" for the shape, the two deliberate
omissions (the store path; `assertions` as a basename) and why `code_fingerprint` is the CASTING
build's rather than the PIT's.

**7.5 The uncopied sidecar** (D9) — one change, not three; whoever touches
`install_cast_as_twin.py` first ships it. **DONE 2026-09-04**, and that file became a thin
wrapper on 2026-09-05: the install moved into `src/aximprint/synth/install.py` and is reachable as
`aximprint install-cast`, with three-valued exit codes (0 installed / 1 finding / 2 usage-or-shape).

## 8. Build slices

| # | Slice | Acceptance a tester can verify |
|---|---|---|
| **S0** | Hand-backs + sidecar hygiene: §7.2's tie-break, D5's rename, D6's `schema:`, D7's extras, D9's installer copy. No IR change. **Grew one row on landing (2026-09-04): §7.1's PREMISE correction** — the false claim is load-bearing in three places and arguing future work out of existence, so the reason strings are corrected here while the `role: adds` revert itself stays in S4, where it belongs. | `aximprint cast` on a fixture PIT is byte-identical except the renamed class and new keys; **no `"defaulted"` remains in an emitted sidecar**; `install_cast_as_twin.py` writes `<stable>.provenance.json`; a synthetic PIT with a two-role port tie resolves to the **same** role under 10 shuffled row orders (`LES-009`). |
| **S1** | The evidence model: D1's tuple, D2's bump, D3's resolver, D4's two de-constanted sites. No producer of the new classes yet. | Re-materializing the CorpAPCN store yields a PIT **byte-identical except `ir_pit.schema_version`**; a hand-built `EvidenceSnapshot` carrying two classes for one role resolves to the higher and the sidecar names the loser; `tests/test_ir_schema.py` passes against the updated SPEC table. |
| **S2** | **LANDED 2026-09-05.** Assertions: the reference-ingest kind, the strict reader, D10–D18, `cast --imprint-id/--assertions`. | ✅ Every acceptance met. Verified end to end against a **read-only copy** of `src-ir-rematerialized-20260902.duckdb`: the fixture's 9 records changed the CorpAPCN cast **exactly and only** as declared — `seg-10-80-31-0` `simple`→`vlan`, `dns-resolver-heavy-10-80-2-core` → `domain-controller-heavy-10-80-2-core-asserted` carrying the asserted image and sizing, one added `file-server-10-80-31-asserted`, one added conduit (31→32), one suppressed host gone (a second suppression on an EXTERNAL peer left the documents byte-identical, as it must). Two runs byte-identical; a cast with no file byte-identical to the pre-S2 build. Drift flagged on a synthetic later PIT and NOT on the PIT the assertion was written against. `population.suppressed` carries both records with `placed: true/false` and the identity closes at `141 == 140 + 1`. All four refusals loud with the offending value. |
| **S3** | LDIF parse: D19, D20, the allowlist file, the three IR tables. | A synthetic fixture — 3 users (one AS-REP-roastable, one with an SPN, one with `msDS-AllowedToDelegateTo`), 2 groups with membership, 1 computer with `operatingSystem`, 1 OU, 1 domain object with a password policy, 1 `nTSecurityDescriptor` with a `GenericAll` and an inherited `WriteDacl` — parses to an exact expected row set; base64 (`::`) values and folded lines round-trip; a fixture containing `msLAPS-Password` is **refused by name**; a `changetype:` record is refused. |
| **S4** | The identity compile step: §5.5, §5.6, §7.1. | The cast of a PIT carrying imported principals emits a twin config **axRange's own validator accepts** (via `AXRANGE_ROOT`, the pattern `check_role_fixed_point.py` already uses); the DC node carries `install: [{role: adds}]` + `provides: [{domain: corp.axr.lab}]`; `check_role_fixed_point.py` passes with the two `domain-controller` rows gone; a constrained-delegation principal produces a `caveats` line naming the missing `delegation_to` passthrough. |
| **S5** | Wire identity strengthened: the Kerberos `service` column, the realm as an `ir_policy` row, §5.7's cross-check. | The CorpAPCN store yields `realm = AXELLIO.DOM` as a first-class `observed` row; `ldap/axdc01.axellio.dom` reaches `ir_principal.spns` from the wire alone; the cross-check reports both asymmetries with counts. |

Sizing: **S0** small · **S1** medium · **S2** medium-large · **S3** large (the two parsers dominate)
· **S4** medium · **S5** small-medium.

**What S2 measured that the draft did not predict, recorded because each one changes what a
later slice can assume.**

1. **`10.80.31.0/24` is already in the cast**, so §4's worked `add: segment` record confirms
   rather than creates. The design note's illustration (*"pruned by the cast, seen in 2 of 11
   windows"*) predates `docs/WORK_ORDER_deployment_scope.md`, which stopped the compiler pruning
   thinly-covered segments. What the record actually supplies is `zone_type`, which no vantage
   can observe — the more interesting half, and the one D11(c) is about.
2. **The design note's worked `add: conduit` is also already observed** (an edge crosses
   `10.80.20.0/24`↔`10.80.31.0/24`). The fixture keeps it and adds a second, genuinely unobserved
   pair, so both outcomes are pinned.
3. **Suppressing an external peer changes nothing in the emitted documents.** It is accounting
   only — externals were never twin nodes — so `population.suppressed` carries `placed: false`
   and the reconstruction identity is stated over the placed ones. §4's own worked example
   (`address:203.0.113.9`, "a cloud CDN edge") is exactly this case, so it is worth saying that
   the first edit an operator copies out of the design note is the one with no visible effect.
4. **An assertion can reach a cast through two doors** — the file, and a store that ingested it —
   and when both are used the two readings are ONE fact, not two. `resolve_precedence` refuses two
   facts of one class, correctly, so the duplicate is removed where they meet.
5. **Nothing guaranteed node-name uniqueness before this slice.** `_node_suffix`'s trailing-zero
   truncation can already collapse two segments onto one suffix; assertions add a second way to
   collide. A positive gate now refuses a cast with a duplicate name, since `net:`, a `tfc`
   assignment and a tap binding all address a node BY NAME.

**Unit tests.** Three checked-in fixtures, none derived from run data (`LES-003`): the synthetic
LDIF above, an assertions file exercising every record kind, and a **golden sidecar** for a tiny
hand-built PIT — the last is what pins D5's rename, D7's extras and D17's drift record against
silent drift, and it belongs in `scripts/verify_determinism.py`'s gate as well as in `tests/`,
because determinism is what the middle tier exists to catch (`CONVENTIONS.md` §4).

**Cross-repo test seam.** `axrange/api/fixtures/imprints/corp-enterprise/` is a **hand-written**
normalized shape whose `fields` keys are prose (`"host dc01 - role adds"`) where the compiler emits
paths (`"twin_config.nodes[3].image"`). Ruled: the real compiler shape is canonical. The handoff —
this repo adds `scripts/emit_fixture_imprint.py`, compiling a tiny checked-in fixture PIT into
`<id>.yaml` + `<id>.provenance.json` + `<id>.topology.json`; **axRange copies those three files in
and adapts its reader.** The generator is ours, the copy and the adapter are theirs, neither repo
imports the other.

## 9. Seam items for axRange

1. **`observation:` block shape** — owned by `docs/WORK_ORDER_deployment_scope.md`, not here.
2. **`14` §3's assertions schema** needs D11's three additions, D13's refusal, and the
   imprint-directory correction (§4's last paragraph). **Settled by the S2 build**, and four
   things the note does not say are now true of the implementation and are the Console's contract:
   an `add` record may carry the same content fields its kind's `override` set allows (plus
   `listen`/`dial` on a host), and a `(key, field)` declared by BOTH an add and an override is a
   refusal; `suppress` takes **host keys only** (a segment key is refused, naming why);
   `source:` accepts `operator: name` and `operator:name` and canonicalizes to the second; and a
   **canonical re-emit drops YAML comments** — every record's mandatory `note` is what survives,
   which is where an author's reasoning belongs anyway.
   **PUBLISHED AS AN ARTIFACT 2026-09-05**, which is what this item was really asking for: the
   vocabulary now ships as `src/aximprint/schemas/axr-assertions-1.schema.json` (JSON Schema draft
   2020-12), printed verbatim by `aximprint assertions schema`, canonical
   (`json.dumps(sort_keys=True, indent=2)` + trailing newline, ASCII-safe) so **axRange vendors a
   byte-identical copy and compares it by test**. Edited here and copied there, never the reverse.
   `tests/test_assertions_schema.py` checks every closed set against the parser's own constants
   and runs an accept/refuse corpus; the six refusals no per-instance keyword can reach (a
   non-strict CIDR, a self-conduit, an unparseable `as_of`, and the three cross-record ones) are
   NAMED in the file's own `$comment` so a consumer never reads schema-valid as parser-accepted.
   `$defs.evidenceClass` publishes aximprint's five-class order and names the divergence from
   `14` §2's six-class one (`compiler` is a sidecar-only label here and is deliberately not
   rankable) rather than letting it be discovered in a join.
3. **The precedence exception for declared deployment transformations** (§5.6) is a genuine
   addition to `14` §2's model, and it affects the scorecard's treatment of relocated CIDRs, not
   only the realm.
4. **`TwinConfigIdentityUser` has no `delegation_to`** (§5.5) — a one-field passthrough in
   `axrange/twinconfig/{schema,expand}.py`, without which constrained delegation cannot reach a
   twin from any source.
5. **`topology.json`'s version carrier** (§7.4) — `bundle_schema` vs the IR passthrough.
6. **Sidecar shape** — D6's `schema:`, D7's `overrides`/`confidence`/`signal`, D8's
   `population.suppressed`, D9's installed copy. **All shipped as of S2**, plus two the Console
   and the scorecard read for asserted facts and which are not in `14`: an asserted row carries
   the operator's own **`note`**, and the sidecar carries a **`population.assertions`** block
   (`schema`, `path`, `imprint`, `as_of`, `source`, `counts`, `added_hosts`/`added_segments`/
   `added_conduits`, `ineffective`, `unmatched`) so a consumer reads what was asserted rather than
   parsing prose out of a caveat. An `overrides` record's `class` may be **`compiler`** where the
   value displaced was a catalog default or `ZONE_TYPE` — outside `EVIDENCE_CLASSES` on purpose:
   it answers *"what would this have been"*, and it never passes through `evidence_rank`.
7. **The recompile invocation, now concrete** (`docs/WORK_ORDER_console_canvas.md` §9 seam 4,
   which records `--assertions` as not existing): it does now.
   `aximprint cast --store <ir.duckdb> --pit <PIT_ID> --out <dir> --imprint-id <id> --assertions
   <path>` — `--imprint-id` defaults to the PIT id and also fills the topology bundle's
   `imprint_id`; `--assertions` defaults to `<dir of --store>/<imprint-id>.assertions.yaml`, whose
   ABSENCE is the ordinary state, while an explicit path that does not exist is an error. A shape
   error in the file exits **2** with the offending value on stderr, never a traceback. The
   Console's D4(a) location (`twin-configs-cast/<id>/<id>.assertions.yaml`) needs no change here:
   it is passed by path.

## 10. Decisions for the user

- **D-a — the IR version number, which is not clean.** `listen_ports`/`dial_ports` were added to
  `ir_entity` on 2026-08-27 (commit `9723772`) and documented at `SPEC_network_ir.md:293-294`, but
  **the version table was never bumped**, against the SPEC's own *additive → MINOR* rule.
  **(a)** one bump to `1.4` whose entry names both changes and records that stores written since
  2026-08-27 say `1.3` while already carrying the two columns, so readers must use column-presence
  for those two; **(b)** `1.4` retro-dated to the columns and `1.5` for the classes — honest per
  change, but mints a version no artifact ever carried; **(c)** bump only for the classes.
  **Recommend (a).**
- **D-b — does the provenance sidecar become a versioned schema?** (D6). Ratifying is the user's
  call (`CONVENTIONS.md` §1.2, `LES-001`). **Recommend yes**, on `LES-001`'s own discriminator:
  axRange reads it on an independent lifecycle, and the rename is unannounceable without it. Scope
  stays narrow — the sidecar only; store, event model and plugin contract stay incubating.
- **D-c — `kind: route`** (D13). **(a)** refuse in v1, naming the reason; **(b)** accept as an
  alias for `kind: conduit`; **(c)** build a `routes:` passthrough on the gateway node.
  **Recommend (a)** — (b) makes two words mean one thing, (c) is the fabric slice's territory.
- **D-d — the security-descriptor parser** (D20): impacket's `ldaptypes` (proven, heavy, a new
  dependency on a pentest library for a repo whose runtime deps are two packages) vs ~200 lines of
  `struct` in-repo. **Recommend in-repo**, caveat stated: a hand-rolled SD parser is the kind of
  code that is subtly wrong for a year, so S3's fixture must include an inherited ACE, an object
  ACE with a GUID, and a well-known SID.
- **D-e — `topology.json`'s version carrier** (§7.4). **Recommend `bundle_schema`**, leaving the
  IR passthrough exactly as it is.
- **D-f — where the assertions file lives** (§4). There is no imprint directory today.
  **Recommend `--imprint-id` + `--assertions`, defaulting beside the IR store.**
- **D-g — 1:1 user reproduction, or capped?** A real directory has thousands of principals, each a
  `New-ADUser` op at deploy time. **(a)** 1:1 — honest and slow; **(b)** `--max-identities N`
  mirroring `--max-count`'s posture (cap, disclose in `caveats` and `population`, never silent);
  **(c)** cap by relevance (privileged groups, SPN holders, delegation holders, ACL-edge
  endpoints, plus a sample). **Recommend (b), defaulting to uncapped**, exactly as `--max-count`
  does — the honest reading is that the directory really has that many — with (c) as a named
  selector later.
- **D-h — hosts an LDIF confirms that the wire never saw.** `ir_unseen` holds 510
  referenced-but-unseen addresses on this baseline (8 local); an export will confirm some and add
  computers the tap never saw at all. **(a)** record, do not place; **(b)** place as
  `generic-endpoint`, `evidence_class: imported`, **with no observation block**, so
  `docs/WORK_ORDER_deployment_scope.md`'s deploy gate treats them the way it treats compiler-class
  nodes (always emitted); **(c)** place only when an operator asserts them. **Recommend (a) now,
  (b) as the follow-on** — (b) is the right answer for row 1, and it should not be decided inside a
  parser slice, because it changes the twin's node set from a source with no occupancy evidence.
- **D-i — new IR tables** (§5.4): three now (`ir_principal`, `ir_acl_edge`, `ir_policy`), sites and
  subnets deferred to their consumer. Confirm, or land all four.

## 11. Rulings and calls, 2026-09-04

**Adopted into the axRange design record (Authority: implementation, axRange CHANGELOG same
date):** §5.6's rule — declared deployment transformations are not precedence events — is now
`docs/design/14-operator-assertions.md` §2; D11's three field additions, D12's closed per-kind
`override.field` set and D13's refusal of `kind: route` are now that note's §3; the assertions file
lives beside the IR store (D-f), and the note's "imprint directory" wording is corrected.

**Taken as implementation calls under `13` §5's "aximprint's schemas are incubating, no ceremony",
open to veto:** **D-a (a)** one `1.4` naming both changes and recording the dated mislabel; **D-b**
yes, the sidecar becomes `axr-cast-provenance/1`; **D-c (a)** routes refused; **D-d** the in-repo
security-descriptor parser, with the three fixture ACEs S3 names; **D-e** `bundle_schema`;
**D-g (b)** `--max-identities`, uncapped by default; **D-h (a)** now, (b) as the follow-on;
**D-i** three tables now.

**Hand-back to axRange, recorded there:** `TwinConfigIdentityUser` lacks a `delegation_to`
passthrough (§5.5), so constrained delegation cannot reach a twin from any source.

**Rulings from the same sitting that touch this file:** role-less hosts are `unroled-endpoint`; the
cast's group key carries a fixed occupancy ladder (`docs/WORK_ORDER_deployment_scope.md` §9).
