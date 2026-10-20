/**
 * Personas in plain words (fix round 2): the sentence comes from the declared
 * emits through a small table; an unknown port stays literal, never guessed.
 */
import { describe, expect, it } from "vitest";

import type { PersonaEmit } from "../src/api/runtimeTypes";
import { peerWords, personaSentence } from "../src/model/personaWords";

function emit(o: Partial<PersonaEmit>): PersonaEmit {
  return { proto: "tcp", ports: [], peer_role: null, transport: "tcp", cadence: null, volume: null, optional: false, peer_scope: "one", ...o };
}

describe("personaSentence", () => {
  it("the web browser from the live screenshot reads as one sentence", () => {
    const emits = [
      emit({ proto: "http", ports: [80], transport: "tcp", peer_role: "sink", optional: true, peer_scope: "all" }),
      emit({ proto: "https", ports: [443], transport: "tcp", peer_role: "sink", optional: true, peer_scope: "all" }),
      emit({ proto: "quic", ports: [443], transport: "udp", peer_role: "sink", optional: true, peer_scope: "all" }),
    ];
    expect(personaSentence({ emits, drive: "scripted" })).toBe("Browses the web (HTTP, HTTPS, QUIC) to the internet sink (sometimes).");
  });

  it("file shares and name lookups to their peers", () => {
    const emits = [
      emit({ proto: "smb", ports: [445], peer_role: "file-server" }),
      emit({ proto: "dns", ports: [53], transport: "udp", peer_role: "domain-controller" }),
    ];
    expect(personaSentence({ emits, drive: "scripted" })).toBe(
      "Opens file shares (SMB) on a file server; looks up names (DNS) with a domain controller.",
    );
  });

  it("a real-service persona SERVES what it declares", () => {
    expect(personaSentence({ emits: [emit({ ports: [445], peer_role: "corp-workstation" })], drive: "real-service" })).toBe(
      "Serves file shares (SMB) to a corp workstation.",
    );
  });

  it("an unknown port falls back to the literal proto/port -- never a guess", () => {
    expect(personaSentence({ emits: [emit({ ports: [8443, 443], peer_role: "web-tier" })], drive: "scripted" })).toBe(
      "Browses the web (HTTPS) to a web tier; talks tcp/8443 to a web tier.",
    );
    expect(personaSentence({ emits: [emit({ proto: "udp", transport: "udp", ports: [], peer_role: null })], drive: "scripted" })).toBe(
      "Talks udp/* to any host.",
    );
  });

  it("no emits says so; many clauses are capped and counted", () => {
    expect(personaSentence({ emits: [], drive: "scripted" })).toBe("Sends no traffic of its own.");
    const many = [22, 25, 123, 3306, 161].map((p, i) =>
      emit({ ports: [p], transport: p === 123 || p === 161 ? "udp" : "tcp", peer_role: `r${i}` }),
    );
    expect(personaSentence({ emits: many, drive: "scripted" })).toMatch(/; and 2 more\.$/);
  });

  it("peer roles read as words", () => {
    expect(peerWords("egress", "one")).toBe("the internet");
    expect(peerWords("app-server", "one")).toBe("an app server");
    expect(peerWords("dns-resolver", "all")).toBe("every dns resolver");
    expect(peerWords(null, null)).toBe("any host");
  });
});
