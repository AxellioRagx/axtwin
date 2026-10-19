import { describe, expect, it } from "vitest";

import {
  checkEntityKey,
  cidrToRoute,
  formatEntityKey,
  ipv4NetworkRange,
  ipv4ToInt,
  parseCidr,
  parseEntityKey,
  routeToCidr,
  siteKeyForCidr,
} from "../src/model/keys";
import { strictCidr } from "../src/edit/records";

describe("entity keys", () => {
  it("parses the three kinds and round-trips them", () => {
    for (const raw of [
      "hostname:axdc01",
      "address:10.1.10.222",
      "mac:00:11:22:33:44:55",
    ]) {
      const key = parseEntityKey(raw);
      expect(key, raw).not.toBeNull();
      expect(formatEntityKey(key!)).toBe(raw);
    }
  });

  it("keeps everything after the FIRST colon, so a MAC survives", () => {
    expect(parseEntityKey("mac:00:11:22:33:44:55")).toEqual({
      kind: "mac",
      value: "00:11:22:33:44:55",
    });
  });

  it("refuses a bare hostname rather than promoting it", () => {
    expect(parseEntityKey("axdc01")).toBeNull();
    expect(parseEntityKey("hostname:")).toBeNull();
    expect(parseEntityKey("nickname:axdc01")).toBeNull();
  });

  it("refuses a CIDR in an address key, naming the network form", () => {
    // The same refusal `axrange/api/console_files.py` makes server-side --
    // a host assertion keyed on a network addresses nothing.
    const reason = checkEntityKey("address:10.80.20.0/24");
    expect(reason).toMatch(/network/);
    expect(checkEntityKey("address:10.80.20.5")).toBeNull();
    expect(checkEntityKey("hostname:anything-goes")).toBeNull();
  });
});

describe("CIDR <-> route encoding", () => {
  const cases = ["10.80.20.0/24", "10.1.10.0/24", "192.168.4.0/22", "10.0.0.0/8"];

  it("writes the prefix length with a hyphen, never %2F", () => {
    expect(cidrToRoute("10.80.20.0/24")).toBe("10.80.20.0-24");
    expect(cidrToRoute("10.80.20.0/24")).not.toContain("%2F");
  });

  it("round-trips every shipped CIDR shape", () => {
    for (const cidr of cases) {
      const route = cidrToRoute(cidr);
      expect(route, cidr).not.toBeNull();
      expect(routeToCidr(route!)).toBe(cidr);
    }
  });

  it("round-trips an IPv6 CIDR too (the rule is the LAST slash)", () => {
    expect(cidrToRoute("2001:db8::/64")).toBe("2001:db8::-64");
    expect(routeToCidr("2001:db8::-64")).toBe("2001:db8::/64");
  });

  it("refuses shapes that are not a CIDR", () => {
    expect(cidrToRoute("10.80.20.0")).toBeNull();
    expect(cidrToRoute("/24")).toBeNull();
    expect(cidrToRoute("10.80.20.0/xx")).toBeNull();
    expect(routeToCidr("10.80.20.0")).toBeNull();
    expect(routeToCidr("-24")).toBeNull();
  });
});

describe("the site bucket stub (O1)", () => {
  it("buckets an RFC 1918 /24 by two octets", () => {
    expect(siteKeyForCidr("10.80.20.0/24")).toBe("site:10.80");
    expect(siteKeyForCidr("192.168.4.0/24")).toBe("site:192.168");
    expect(siteKeyForCidr("172.16.9.0/24")).toBe("site:172.16");
  });

  it("gives anything else its own bucket rather than inventing a grouping", () => {
    // The real rule is a LONGEST COMMON prefix, which is a property of the
    // whole segment set and belongs to S3's layout module. The singleton
    // fallback spells its CIDR in the ROUTE form -- see the pin test below.
    expect(siteKeyForCidr("203.0.113.0/24")).toBe("site:203.0.113.0-24");
    expect(siteKeyForCidr("10.80.0.0/16")).toBe("site:10.80.0.0-16");
    expect(siteKeyForCidr("not-a-cidr")).toBeNull();
  });

  it("emits only keys the LAYOUT API will accept as pins", () => {
    // Copied from `axrange/api/console_files.py`'s `_SITE_KEY_RE`
    // (`^site:[0-9A-Za-z._:-]+$`), which every `PUT .../layout` pin key is
    // validated against. It admits no `/`, so a site key spelled
    // `site:203.0.113.0/24` is a 400 the moment S4 drags an unbucketed site
    // -- produced by this module, refused by the server, with the operator
    // holding neither half.
    const SERVER_SITE_KEY_RE = /^site:[0-9A-Za-z._:-]+$/;
    for (const cidr of [
      "10.80.20.0/24",
      "192.168.4.0/24",
      "172.31.9.0/24",
      "203.0.113.0/24",
      "10.80.0.0/16",
      "198.51.100.128/25",
      "2001:db8::/64",
    ]) {
      const key = siteKeyForCidr(cidr);
      expect(key, cidr).not.toBeNull();
      expect(SERVER_SITE_KEY_RE.test(key!), `${cidr} -> ${key}`).toBe(true);
    }
  });
});

describe("parseCidr -- a DIGITS-ONLY prefix length (the fix round's B1)", () => {
  it("refuses an empty, signed, spaced, hex or fractional prefix -- `Number()` accepted every one", () => {
    // `Number("")` is 0: a half-typed `10.80.31.0/` was a /0 that "overlapped"
    // every network; `Number("0x18")` is 24.
    for (const bad of ["10.80.31.0/", "10.80.31.0/0x18", "10.80.31.0/ 24", "10.80.31.0/+24", "10.80.31.0/24.0", "10.80.31.0/1e1", "10.80.31.0/-1", "10.80.31.0/1234"]) {
      expect(parseCidr(bad), bad).toBeNull();
    }
    expect(parseCidr("/24")).toBeNull();
    expect(parseCidr("10.80.31.0")).toBeNull();
  });

  it("accepts one to three digits up to 128, and leaves the address to the caller", () => {
    expect(parseCidr("10.80.31.0/24")).toEqual({ address: "10.80.31.0", prefix: 24 });
    expect(parseCidr("10.80.31.0/0")).toEqual({ address: "10.80.31.0", prefix: 0 });
    expect(parseCidr("2001:db8::/128")).toEqual({ address: "2001:db8::", prefix: 128 });
    expect(parseCidr("2001:db8::/129")).toBeNull();
  });

  it("and `strictCidr`, which now reads it, agrees on every one of those", () => {
    expect(strictCidr("10.80.31.0/")).toBeNull();
    expect(strictCidr("10.80.31.0/0x18")).toBeNull();
    expect(strictCidr(" 10.80.31.0/24 ")).toBe("10.80.31.0/24");
  });
});

describe("ipv4NetworkRange -- the one network-mask rule (B8)", () => {
  const range = (cidr: string) => {
    const p = parseCidr(cidr)!;
    return ipv4NetworkRange(ipv4ToInt(p.address)!, p.prefix);
  };

  it("masks host bits off at every prefix length, /0 and /32 included", () => {
    expect(range("10.80.20.77/24")).toEqual({ start: ipv4ToInt("10.80.20.0"), end: ipv4ToInt("10.80.20.255") });
    expect(range("10.80.20.77/32")).toEqual({ start: ipv4ToInt("10.80.20.77"), end: ipv4ToInt("10.80.20.77") });
    // A shift would take 32 mod 32 and make this a /32.
    expect(range("10.80.20.77/0")).toEqual({ start: 0, end: 2 ** 32 - 1 });
    // Above 2^31 a signed result would go negative.
    expect(range("255.255.255.0/24")).toEqual({ start: ipv4ToInt("255.255.255.0"), end: 2 ** 32 - 1 });
  });

  it("is what `strictCidr` asks: the network address, and nothing with host bits", () => {
    for (let p = 0; p <= 32; p++) {
      const network = `10.80.20.0/${p}`;
      const isNetwork = range(network).start === ipv4ToInt("10.80.20.0");
      expect(strictCidr(network) !== null, network).toBe(isNetwork);
    }
  });
});

describe("siteKeyForCidr -- reads the prefix through parseCidr too (B1)", () => {
  it("does not take a hex prefix for a /24", () => {
    expect(siteKeyForCidr("10.80.20.0/24")).toBe("site:10.80");
    expect(siteKeyForCidr("10.80.20.0/0x18")).toBeNull();
    expect(siteKeyForCidr("10.80.20.0/")).toBeNull();
  });
});
