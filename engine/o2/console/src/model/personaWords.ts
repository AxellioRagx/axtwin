/**
 * A persona in one readable sentence (fix round 2): "Browses the web (HTTP,
 * HTTPS, QUIC) to the internet sink" instead of "tcp/80 -> sink (optional)
 * (all); tcp/443 -> …".
 *
 * Built ONLY from the persona's declared emits, through a small table of
 * well-known protocols and ports. A port the table does not know is shown as
 * the literal `proto/port` -- never guessed into a word. The exact list stays
 * available (the server's `summary`) behind a "details" disclosure in the views.
 *
 * Pure.
 */
import type { PersonaEmit, PersonaInfo } from "../api/runtimeTypes";

type Category = "web" | "files" | "names" | "signin" | "mail" | "remote" | "time" | "data" | "manage" | "other";

interface Known {
  label: string;
  category: Category;
}

/** `transport/port` -> what it is. Only protocols whose port is unambiguous. */
const BY_PORT: Readonly<Record<string, Known>> = {
  "tcp/80": { label: "HTTP", category: "web" },
  "tcp/8080": { label: "HTTP", category: "web" },
  "tcp/443": { label: "HTTPS", category: "web" },
  "udp/443": { label: "QUIC", category: "web" },
  "tcp/445": { label: "SMB", category: "files" },
  "tcp/139": { label: "NetBIOS", category: "files" },
  "tcp/2049": { label: "NFS", category: "files" },
  "tcp/21": { label: "FTP", category: "files" },
  "udp/53": { label: "DNS", category: "names" },
  "tcp/53": { label: "DNS", category: "names" },
  "udp/137": { label: "NetBIOS names", category: "names" },
  "udp/5353": { label: "mDNS", category: "names" },
  "tcp/88": { label: "Kerberos", category: "signin" },
  "udp/88": { label: "Kerberos", category: "signin" },
  "tcp/389": { label: "LDAP", category: "signin" },
  "udp/389": { label: "LDAP", category: "signin" },
  "tcp/636": { label: "LDAPS", category: "signin" },
  "tcp/3268": { label: "Global Catalog", category: "signin" },
  "tcp/135": { label: "RPC", category: "signin" },
  "tcp/25": { label: "SMTP", category: "mail" },
  "tcp/587": { label: "SMTP", category: "mail" },
  "tcp/143": { label: "IMAP", category: "mail" },
  "tcp/993": { label: "IMAPS", category: "mail" },
  "tcp/110": { label: "POP3", category: "mail" },
  "tcp/22": { label: "SSH", category: "remote" },
  "tcp/3389": { label: "RDP", category: "remote" },
  "tcp/5985": { label: "WinRM", category: "remote" },
  "tcp/23": { label: "Telnet", category: "remote" },
  "udp/123": { label: "NTP", category: "time" },
  "tcp/3306": { label: "MySQL", category: "data" },
  "tcp/5432": { label: "PostgreSQL", category: "data" },
  "tcp/1433": { label: "SQL Server", category: "data" },
  "tcp/6379": { label: "Redis", category: "data" },
  "udp/161": { label: "SNMP", category: "manage" },
  "udp/514": { label: "syslog", category: "manage" },
  "tcp/514": { label: "syslog", category: "manage" },
  "udp/67": { label: "DHCP", category: "manage" },
};

/** Protocols named without a port (ICMP has none). */
const BY_PROTO: Readonly<Record<string, Known>> = {
  icmp: { label: "ping", category: "manage" },
};

/** What a client of this kind DOES, and the preposition to its peer. */
const CLIENT: Readonly<Record<Category, { verb: string; prep: string }>> = {
  web: { verb: "browses the web", prep: "to" },
  files: { verb: "opens file shares", prep: "on" },
  names: { verb: "looks up names", prep: "with" },
  signin: { verb: "signs in", prep: "against" },
  mail: { verb: "sends and reads mail", prep: "with" },
  remote: { verb: "opens remote sessions", prep: "to" },
  time: { verb: "syncs its clock", prep: "with" },
  data: { verb: "queries databases", prep: "on" },
  manage: { verb: "sends management traffic", prep: "to" },
  other: { verb: "talks", prep: "to" },
};

/** What a SERVICE of this kind offers. */
const SERVER: Readonly<Record<Category, string>> = {
  web: "serves web pages",
  files: "serves file shares",
  names: "answers name lookups",
  signin: "handles sign-ins",
  mail: "handles mail",
  remote: "accepts remote sessions",
  time: "serves the time",
  data: "serves a database",
  manage: "answers management traffic",
  other: "serves",
};

/** A peer role in words: `sink` / `egress` are the internet; a hyphenated
 *  role reads as its words with an article; `all` addresses every holder. */
export function peerWords(role: string | null, scope: string | null): string {
  if (!role) return "any host";
  if (role === "sink") return "the internet sink";
  if (role === "egress" || role === "internet") return "the internet";
  const words = role.replace(/[-_]+/g, " ");
  if (scope === "all") return `every ${words}`;
  return `${/^[aeiou]/i.test(words) ? "an" : "a"} ${words}`;
}

/** One emit's literal form, the same one the server's summary uses. */
export function literal(e: Pick<PersonaEmit, "transport" | "ports" | "proto">): string {
  const t = e.transport ?? e.proto;
  return e.ports.length === 0 ? `${t}/*` : `${t}/${e.ports.join(",")}`;
}

interface Piece {
  category: Category;
  labels: string[];
  peer: string;
  optional: boolean;
}

function piecesOf(emits: readonly PersonaEmit[]): Piece[] {
  const out: Piece[] = [];
  for (const e of emits) {
    const peer = peerWords(e.peer_role, e.peer_scope);
    const transport = (e.transport ?? "tcp").toLowerCase();
    const known: Known[] = [];
    const unknown: string[] = [];
    const byProto = BY_PROTO[e.proto?.toLowerCase() ?? ""];
    if (byProto && e.ports.length === 0) known.push(byProto);
    else if (e.ports.length === 0) unknown.push(literal(e));
    for (const port of e.ports) {
      const k = BY_PORT[`${transport}/${port}`];
      if (k) known.push(k);
      else unknown.push(`${transport}/${port}`);
    }
    for (const k of known) add(out, k.category, k.label, peer, e.optional);
    for (const u of unknown) add(out, "other", u, peer, e.optional);
  }
  return out;
}

function add(out: Piece[], category: Category, label: string, peer: string, optional: boolean): void {
  const same = out.find((p) => p.category === category && p.peer === peer && p.optional === optional);
  if (same) {
    if (!same.labels.includes(label)) same.labels.push(label);
  } else {
    out.push({ category, labels: [label], peer, optional });
  }
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

const MAX_CLAUSES = 3;

/**
 * The sentence. A `real-service` persona SERVES what it declares; every other
 * drive dials its peers. No emits at all: it sends nothing of its own.
 */
export function personaSentence(persona: Pick<PersonaInfo, "emits" | "drive">): string {
  const emits = persona.emits ?? [];
  if (emits.length === 0) return "Sends no traffic of its own.";
  const serves = persona.drive === "real-service";
  const clauses = piecesOf(emits).map((p) => {
    const labels = p.labels.join(", ");
    const sometimes = p.optional ? " (sometimes)" : "";
    if (serves) {
      return p.category === "other"
        ? `serves ${labels} to ${p.peer}${sometimes}`
        : `${SERVER[p.category]} (${labels}) to ${p.peer}${sometimes}`;
    }
    const c = CLIENT[p.category];
    return p.category === "other"
      ? `talks ${labels} to ${p.peer}${sometimes}`
      : `${c.verb} (${labels}) ${c.prep} ${p.peer}${sometimes}`;
  });
  const shown = clauses.slice(0, MAX_CLAUSES);
  const more = clauses.length - shown.length;
  return `${capitalize(shown.join("; "))}${more > 0 ? `; and ${more} more` : ""}.`;
}
