/**
 * The seven edit gestures of `docs/design/14-operator-assertions.md` §4, each
 * mapped to **exactly one** assertion record (`15` §8's table). This is the
 * heart of S5: a pure gesture -> record function with the closed record set
 * and a client-side pre-flight that MIRRORS the server's rules
 * (`axrange/api/console_files.py`) without diverging from them silently.
 *
 * **The server is the authority, not this module.** `PUT .../assertions`
 * validates against the vendored `axr-assertions/1` schema and names the
 * accepted set on a refusal; the compiler is the reader of record. So a check
 * here that is looser than the server merely lets the record reach the server
 * and be refused with its own message (never a silent success); a check that
 * were STRICTER could refuse a record the server would have accepted, which is
 * the divergence to avoid. Every rule below is therefore no stricter than the
 * server, and the one rule the UI must enforce before a record can exist is
 * `14` §3's own: **every record carries a non-empty `note`.**
 *
 * The records produced match the **file** shape of `14` §3 exactly: an `add`
 * record carries `kind`; an `override`/`suppress` record does NOT (the block
 * is the kind, and the server strips a transport `kind` on write). That is
 * what the round-trip acceptance reads back.
 *
 * Pure -- no React, no fetch, no clock. `views/` build a typed `Gesture` from
 * the form inputs and hand it here; the store takes the `{block, record}` this
 * returns. `src/edit/store.ts` (the reducer) stays untouched.
 */
import type { AssertionBlock, AssertionRecord } from "../api/types";
import { ipv4NetworkRange, ipv4ToInt, isIpv4, parseCidr, parseEntityKey } from "../model/keys";

// ---------------------------------------------------------------------------
// The closed vocabulary, mirrored from the vendored schema for a local
// pre-flight. The server derives these from `axr-assertions-1.schema.json` at
// import time; a drift between the two is caught by the server refusing (and
// by `tests/test_api_console.py`'s field-set guard, LES-258). Restated here
// so a form can offer the right options without a round trip.
// ---------------------------------------------------------------------------
export const ZONE_TYPES = ["simple", "vlan"] as const;
export type ZoneType = (typeof ZONE_TYPES)[number];

export const PORT_PROTOS = ["icmp", "tcp", "udp"] as const;
export type PortProto = (typeof PORT_PROTOS)[number];

export const RESOURCE_KEYS = ["cpu", "disk", "ram"] as const;
export type ResourceKey = (typeof RESOURCE_KEYS)[number];

export interface PortEntry {
  port: number;
  proto: PortProto;
}

export type Resources = Partial<Record<ResourceKey, string | number>>;

// ---------------------------------------------------------------------------
// The seven gestures (`14` §4, first edit set; `15` §8's table)
// ---------------------------------------------------------------------------
export type Gesture =
  /** level 0: add a segment the vantage did not supply. */
  | { kind: "add-segment"; cidr: string; name: string; zoneType?: ZoneType; note: string }
  /** level 0/1: add a host on a segment, with an optional role and listen set.
   *  `dial`, `image` and `resources` are the schema's other `add: host` fields
   *  (`axr-assertions/1` `$defs.addRecord`); no single-record form sets them --
   *  a CLONE carries them over from its source host (S5.1.b). */
  | {
      kind: "add-host";
      key: string;
      segment: string;
      role?: string;
      listen?: PortEntry[];
      dial?: PortEntry[];
      image?: string;
      resources?: Resources;
      note: string;
    }
  /** level 1/2: change a discovered host's role. */
  | { kind: "change-role"; key: string; role: string; note: string }
  /** any: suppress a host as out of scope. */
  | { kind: "suppress-host"; key: string; note: string }
  /** inspector: override a host's image. */
  | { kind: "override-image"; key: string; image: string; note: string }
  /** inspector: override a host's sizing. */
  | { kind: "override-resources"; key: string; resources: Resources; note: string }
  /** level 0: add a conduit the vantage did not supply. */
  | { kind: "add-conduit"; cidrA: string; cidrB: string; note: string };

export type GestureKind = Gesture["kind"];

export interface StagedRecord {
  block: AssertionBlock;
  record: AssertionRecord;
}

export type BuildResult =
  | { ok: true; staged: StagedRecord }
  | { ok: false; error: string };

// ---------------------------------------------------------------------------
// gesture -> record
// ---------------------------------------------------------------------------
/**
 * Build the single record a gesture produces, or a refusal naming what is
 * wrong -- the same shape the server answers with, so a form can show the
 * message inline instead of after a round trip.
 */
export function buildRecord(g: Gesture): BuildResult {
  const noteErr = checkNote(g.note);
  if (noteErr) return fail(noteErr);

  switch (g.kind) {
    case "add-segment": {
      const cidr = strictCidr(g.cidr);
      if (cidr === null) return fail(cidrError(g.cidr, "a segment key"));
      const nameErr = checkSegmentName(g.name);
      if (nameErr) return fail(nameErr);
      if (g.zoneType !== undefined && !isZoneType(g.zoneType)) {
        return fail(`zone type ${g.zoneType!} is not one of ${list(ZONE_TYPES)}`);
      }
      const record: AssertionRecord = { kind: "segment", key: cidr, name: g.name.trim() };
      if (g.zoneType !== undefined) record.zone_type = g.zoneType;
      record.note = g.note.trim();
      return ok("add", record);
    }

    case "add-host": {
      const keyErr = checkHostKey(g.key);
      if (keyErr) return fail(keyErr);
      const segment = strictCidr(g.segment);
      if (segment === null) return fail(cidrError(g.segment, "a host's `segment`"));
      const record: AssertionRecord = { kind: "host", key: g.key.trim(), segment };
      if (g.role !== undefined) {
        if (!isNonEmpty(g.role)) return fail("a role is a non-empty string");
        record.role = g.role.trim();
      }
      if (g.listen !== undefined && g.listen.length > 0) {
        const ports = validatePorts(g.listen);
        if (typeof ports === "string") return fail(ports);
        // Widen each entry to the record shape `AssertionRecord.listen` carries
        // (a plain `{port, proto}` object; the DTO mirror has no `PortEntry`).
        record.listen = ports.map((p): Record<string, unknown> => ({ port: p.port, proto: p.proto }));
      }
      // The three fields only a clone supplies. Each is checked the way the
      // server's `_validate_field` checks it, and an EMPTY list is omitted
      // rather than written: the schema's `portList` is `minItems: 1`.
      if (g.dial !== undefined && g.dial.length > 0) {
        const ports = validatePorts(g.dial);
        if (typeof ports === "string") return fail(ports);
        record.dial = ports.map((p): Record<string, unknown> => ({ port: p.port, proto: p.proto }));
      }
      if (g.image !== undefined) {
        if (!isNonEmpty(g.image)) return fail("an image is a non-empty string");
        record.image = g.image.trim();
      }
      if (g.resources !== undefined) {
        const resources = validateResources(g.resources);
        if (typeof resources === "string") return fail(resources);
        record.resources = resources;
      }
      record.note = g.note.trim();
      return ok("add", record);
    }

    case "change-role": {
      const keyErr = checkHostKey(g.key);
      if (keyErr) return fail(keyErr);
      if (!isNonEmpty(g.role)) return fail("a role is a non-empty string");
      return ok("override", {
        key: g.key.trim(),
        field: "role",
        value: g.role.trim(),
        note: g.note.trim(),
      });
    }

    case "override-image": {
      const keyErr = checkHostKey(g.key);
      if (keyErr) return fail(keyErr);
      if (!isNonEmpty(g.image)) return fail("an image is a non-empty string");
      return ok("override", {
        key: g.key.trim(),
        field: "image",
        value: g.image.trim(),
        note: g.note.trim(),
      });
    }

    case "override-resources": {
      const keyErr = checkHostKey(g.key);
      if (keyErr) return fail(keyErr);
      const resources = validateResources(g.resources);
      if (typeof resources === "string") return fail(resources);
      return ok("override", {
        key: g.key.trim(),
        field: "resources",
        value: resources,
        note: g.note.trim(),
      });
    }

    case "suppress-host": {
      // `suppress` takes HOST keys only: pruning a segment that still carries
      // placed hosts is a topology edit, not a suppression (the server refuses
      // a CIDR key here by name).
      const keyErr = checkHostKey(g.key, { context: "suppress" });
      if (keyErr) return fail(keyErr);
      return ok("suppress", { key: g.key.trim(), note: g.note.trim() });
    }

    case "add-conduit": {
      const a = strictCidr(g.cidrA);
      if (a === null) return fail(cidrError(g.cidrA, "a conduit half"));
      const b = strictCidr(g.cidrB);
      if (b === null) return fail(cidrError(g.cidrB, "a conduit half"));
      if (a === b) return fail("a conduit joins two DIFFERENT segments");
      // Canonicalize by SORTING the two halves, matching the server's
      // `_canonical_conduit_key` -- the compiler derives its own conduits from
      // `sorted((a, b))`, so the other order would name one conduit twice. A
      // lexical compare on the canonical CIDR strings matches Python's
      // `sorted([...])` on the same strings (never a numeric compare, which
      // disagrees: "10.80" < "10.9" as strings, 10.80 > 10.9 as ints).
      const [left, right] = a < b ? [a, b] : [b, a];
      return ok("add", { kind: "conduit", key: `${left}|${right}`, note: g.note.trim() });
    }
  }
}

/**
 * The two CROSS-record refusals `14` §3 names, mirrored from the server's own
 * `_cross_check` so the UI refuses at stage time rather than only at save time:
 * a key in both `add` and `suppress`, and a `(key, field)` present in both an
 * `add` record's own fields and an `override`. Both are refusals, never a
 * precedence -- axRange does not resolve precedence, so a document that would
 * need it must not be assembled. `null` when the staged record is clean.
 *
 * A same-key second `add` is NOT flagged here: the store keys an add by its
 * key alone (`entryId`), so re-staging replaces rather than duplicating, and
 * only ever one add per key ever exists.
 */
export function crossRecordConflict(
  existing: ReadonlyArray<{ block: AssertionBlock; record: AssertionRecord }>,
  staged: StagedRecord,
): string | null {
  const key = staged.record.key;
  if (key === undefined) return null;
  const has = (block: AssertionBlock, pred: (r: AssertionRecord) => boolean): boolean =>
    existing.some((e) => e.block === block && pred(e.record));

  if (staged.block === "suppress" && has("add", (r) => r.key === key)) {
    return `key ${key} is already in \`add\`; a key in both add and suppress is a refusal, not a precedence`;
  }
  if (staged.block === "add") {
    if (has("suppress", (r) => r.key === key)) {
      return `key ${key} is already in \`suppress\`; a key in both add and suppress is a refusal`;
    }
    for (const field of addContentFields(staged.record)) {
      if (has("override", (r) => r.key === key && r.field === field)) {
        return `(${key}, ${field}) is already overridden; carry the value on the add record alone`;
      }
    }
  }
  if (staged.block === "override" && staged.record.field !== undefined) {
    const field = staged.record.field;
    if (has("add", (r) => r.key === key && addContentFields(r).includes(field))) {
      return `(${key}, ${field}) is already carried by an \`add\` record; a (key, field) in both add and override is a refusal`;
    }
  }
  return null;
}

/** An add record's own content fields (everything but `kind`/`key`/`note`) --
 *  the exact set the server's `_cross_check` compares against `override`. */
function addContentFields(record: AssertionRecord): string[] {
  return Object.keys(record).filter((k) => k !== "kind" && k !== "key" && k !== "note");
}

/**
 * The change-list tag (`15` §8, `docs/WORK_ORDER_console_canvas.md` §3): the
 * kind of deploy a record implies for a STANDING twin, **derivable from the
 * record's block alone** -- which is fortunate, since the bundle publishes no
 * mapping from an entity key to the cast node spec it landed in (§2). An `add`
 * or a `suppress` is a whole-node add/remove and flows through
 * `te deploy --update`; an `override` mutates an existing node's fields and is
 * a fresh deploy (`docs/WORK_ORDER_twin_incremental_update.md` §3.1).
 */
export type DeployConsequence = "te deploy --update" | "fresh deploy";

export function deployConsequence(block: AssertionBlock): DeployConsequence {
  return block === "override" ? "fresh deploy" : "te deploy --update";
}

// ---------------------------------------------------------------------------
// save metadata (`14` §3: every save carries `source` and `as_of`)
// ---------------------------------------------------------------------------
const SOURCE_RE = /^\s*operator:\s*\S/i;

/** Whether `source` is the `operator: <name>` form the server requires
 *  (`_canonical_source`). A name after the prefix is mandatory. */
export function isSourceValid(source: string): boolean {
  return SOURCE_RE.test(source);
}

/**
 * Whether the metadata every save needs is present -- a valid `source` and a
 * non-empty `as_of` (`14` §3; the server never invents `as_of`). Shared by the
 * Save button's gate AND the remove-that-re-saves path, so a removal can never
 * `PUT` metadata the server refuses when Save itself is disabled (S2).
 */
export function metadataReady(source: string, asOf: string): boolean {
  return isSourceValid(source) && asOf.trim().length > 0;
}

// ---------------------------------------------------------------------------
// helpers -- the pre-flight, mirroring the server but never stricter
// ---------------------------------------------------------------------------
function ok(block: AssertionBlock, record: AssertionRecord): BuildResult {
  return { ok: true, staged: { block, record } };
}

function fail(error: string): BuildResult {
  return { ok: false, error };
}

/** The `add` record kinds `axr-assertions/1` knows (`api/types.ts`). */
const ADD_KINDS: readonly string[] = ["segment", "host", "conduit"];

/** The `override` fields the writer produces (`buildRecord` above). */
const OVERRIDE_FIELDS: readonly string[] = ["role", "image", "resources"];

/**
 * Whether a record READ BACK from storage is one this writer could have made,
 * or the reason it is not (review S8).
 *
 * `buildRecord` validates a GESTURE on the way in; a draft (`edit/draft.ts`)
 * carries finished records, and a hand-edited one can carry anything -- so a
 * draft holding `{nonsense: true, key: 42}` used to arrive as a pending record
 * and be refused only by the server, three gestures later. This is the same
 * closed-set discipline applied to the one input that does not come from a
 * gesture. It is deliberately a SHAPE check, not a re-validation of every field
 * the writer checks: the server remains the authority (LES-271 -- a client
 * mirror may be looser than its server, never stricter), and this refuses only
 * what could not have come from this app at all.
 */
export function invalidStoredRecord(block: AssertionBlock, record: AssertionRecord): string | null {
  const r = record as unknown as Record<string, unknown>;
  if (typeof r["key"] !== "string" || !isNonEmpty(r["key"] as string)) {
    return "a record needs a non-empty string `key`";
  }
  if (typeof r["note"] !== "string" || !isNonEmpty(r["note"] as string)) {
    return checkNote("") ?? "a record needs a non-empty note";
  }
  if (block === "add" && !ADD_KINDS.includes(String(r["kind"]))) {
    return `an \`add\` record's kind is one of ${list(ADD_KINDS)}, not ${JSON.stringify(r["kind"])}`;
  }
  if (block === "override" && !OVERRIDE_FIELDS.includes(String(r["field"]))) {
    return `an \`override\` record's field is one of ${list(OVERRIDE_FIELDS)}, not ${JSON.stringify(r["field"])}`;
  }
  return null;
}

/** The one rule the UI MUST enforce before a record can be created (`14` §3):
 *  a non-empty `note`. A canonical re-emit of the file drops YAML comments, so
 *  the note is the only surviving record of why the assertion was made. */
function checkNote(note: string): string | null {
  return isNonEmpty(note)
    ? null
    : "every assertion record needs a non-empty note (docs/design/14-operator-assertions.md §3)";
}

function checkSegmentName(name: string): string | null {
  if (!isNonEmpty(name)) return "a segment name is a non-empty identifier";
  if (/\s/.test(name.trim())) {
    return "a segment name has no whitespace -- it is what a node's `net:` references";
  }
  return null;
}

/**
 * A host key must be one of the self-describing `address:`/`hostname:`/`mac:`
 * shapes (`14` §3). The value part is NOT parsed as an IP: the server accepts
 * `address:not-an-ip` and only WARNS (it joins against nothing), so refusing
 * it here would be stricter than the reader of record. In the `suppress`
 * context a CIDR key is a distinct, worth-catching mistake -- `parseEntityKey`
 * returns `null` for a CIDR, so the generic message already covers it, and the
 * `context` tailors it.
 */
function checkHostKey(key: string, opts: { context?: "suppress" } = {}): string | null {
  const parsed = parseEntityKey(key.trim());
  if (parsed) return null;
  const shapes = "address:<ip> / hostname:<name> / mac:<addr>";
  return opts.context === "suppress"
    ? `suppress takes a host key only (${shapes}); a segment/CIDR key is a topology edit, not a suppression`
    : `not a host entity key: expected one of ${shapes}`;
}

function validatePorts(entries: readonly PortEntry[]): PortEntry[] | string {
  const out: PortEntry[] = [];
  for (const entry of entries) {
    if (!Number.isInteger(entry.port) || entry.port < 1 || entry.port > 65535) {
      return `a port is an integer 1..65535, got ${JSON.stringify(entry.port)}`;
    }
    if (!isPortProto(entry.proto)) {
      return `unknown protocol ${JSON.stringify(entry.proto)}; accepted: ${list(PORT_PROTOS)}`;
    }
    out.push({ port: entry.port, proto: entry.proto });
  }
  return out;
}

/**
 * A port list in the COMPILER's canonical form -- deduplicated and ordered by
 * the `"<port>/<proto>"` string, which is exactly what aximprint's parser turns
 * `listen`/`dial` into (`operator_assertions.py` `_validate_field`:
 * `sorted({f"{port}/{proto}"})`) and what its re-emit writes back. axRange's own
 * writer keeps the authored order, so a list already in this order is a fixed
 * point of BOTH re-emits: the file the Console writes is the file either side
 * would write.
 *
 * The order is a string compare on purpose, never a numeric one: `"1883/tcp"`
 * sorts before `"502/tcp"`, which is also the order the topology export's own
 * `listen_ports`/`dial_ports` arrive in (the same producer, the same sort) --
 * so a clone copies its source's list verbatim. JavaScript's `<` on these
 * ASCII strings matches Python's `sorted()`.
 */
export function canonicalPorts(entries: readonly PortEntry[]): PortEntry[] {
  const byText = new Map<string, PortEntry>();
  for (const entry of entries) {
    const text = `${entry.port}/${entry.proto}`;
    if (!byText.has(text)) byText.set(text, { port: entry.port, proto: entry.proto });
  }
  return [...byText.entries()]
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([, entry]) => entry);
}

/**
 * The topology export's `"<port>/<proto>"` census strings (`GraphNode.listenPorts`
 * / `dialPorts`) as `{port, proto}` entries -- or a refusal naming the first
 * string that is not one. A string the writer's closed sets cannot hold (a
 * protocol outside `PORT_PROTOS`, a port outside 1..65535) is REFUSED rather than
 * dropped: a clone that silently lost part of a listen set would stage a host
 * that answers on fewer ports than the one it copies, and say nothing.
 */
export function portsFromCensus(values: readonly string[]): PortEntry[] | string {
  const out: PortEntry[] = [];
  for (const value of values) {
    const match = /^(\d{1,5})\/([a-z0-9-]+)$/i.exec(value.trim());
    const port = match ? Number(match[1]) : NaN;
    const proto = match ? match[2]!.toLowerCase() : "";
    if (!match || !Number.isInteger(port) || port < 1 || port > 65535 || !isPortProto(proto)) {
      return (
        `${JSON.stringify(value)} is not a {port, proto} entry the writer accepts ` +
        `(a port 1..65535 and one of ${list(PORT_PROTOS)})`
      );
    }
    out.push({ port, proto });
  }
  return canonicalPorts(out);
}

function validateResources(resources: Resources): Record<string, string | number> | string {
  const keys = Object.keys(resources);
  const extra = keys.filter((k) => !isResourceKey(k));
  if (extra.length > 0) {
    return `unknown resource key(s) ${JSON.stringify(extra)}; accepted: ${list(RESOURCE_KEYS)}`;
  }
  const out: Record<string, string | number> = {};
  for (const k of RESOURCE_KEYS) {
    const v = resources[k];
    if (v === undefined) continue;
    if (typeof v === "boolean" || (typeof v !== "string" && typeof v !== "number")) {
      return `resource ${k} must be a string or an integer`;
    }
    if (typeof v === "string" && !isNonEmpty(v)) continue;
    out[k] = v;
  }
  if (Object.keys(out).length === 0) {
    return `sizing needs at least one of ${list(RESOURCE_KEYS)}`;
  }
  return out;
}

/**
 * A CIDR the server would accept as a segment/conduit key, in its canonical
 * form, or `null`. IPv4 is checked to the NETWORK form (host bits zero) the
 * server enforces via `ipaddress.ip_network(strict=True)`; IPv6 is accepted
 * loosely, exactly as the schema's own pattern is (the deployed content is all
 * IPv4, and the server is the authority on the rest).
 */
export function strictCidr(cidr: string): string | null {
  const raw = cidr.trim();
  // The split and the digits-only prefix are `parseCidr`'s (one rule).
  const parsed = parseCidr(raw);
  if (parsed === null) return null;
  const { address, prefix } = parsed;

  if (address.includes(":")) {
    // IPv6: the schema's pattern is deliberately loose here; accept it and let
    // the server (the reader of record) have the final word.
    return prefix >= 0 && prefix <= 128 ? raw : null;
  }
  if (!isIpv4(address) || prefix > 32) return null;
  // A leading-zero octet (`10.080.31.0`) passes `isIpv4` here but the server's
  // `ip_network(strict=True)` rejects it ("leading zeros are not permitted"),
  // so refuse it NOW -- a delayed 400 on save becomes an immediate inline
  // refusal (N1). Confined to CIDR keys, where the server is strict; a host
  // key's value part stays as-is (the server accepts and warns it).
  if (address.split(".").some((o) => o.length > 1 && o.startsWith("0"))) return null;
  const int = ipv4ToInt(address);
  if (int === null) return null;
  // Host bits must be zero, or the server refuses it -- asked of the one
  // network-range rule `model/keys.ts` publishes.
  return ipv4NetworkRange(int, prefix).start === int ? `${address}/${prefix}` : null;
}

function cidrError(value: string, role: string): string {
  return `${JSON.stringify(value)} is not a valid CIDR for ${role} -- it must be the network address (host bits zero), e.g. 10.80.31.0/24`;
}

function isNonEmpty(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

export function isZoneType(value: string): value is ZoneType {
  return (ZONE_TYPES as readonly string[]).includes(value);
}

function isPortProto(value: string): value is PortProto {
  return (PORT_PROTOS as readonly string[]).includes(value);
}

function isResourceKey(value: string): value is ResourceKey {
  return (RESOURCE_KEYS as readonly string[]).includes(value);
}

function list(values: readonly string[]): string {
  return values.join(" / ");
}
