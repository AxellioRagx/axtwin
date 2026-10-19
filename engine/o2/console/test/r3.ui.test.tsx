/**
 * R3 as RENDERED (`docs/WORK_ORDER_console_level0_rendering.md` §3 R3) — the
 * half `r3.infra.test.ts` cannot see.
 *
 * The geometry can be right and the drawing still wrong in exactly the two ways
 * this slice is about: a stub that never reaches the DOM leaves the diagonal
 * tie-lines in place, and a badge rendered without its count is the unlabeled
 * histogram bar again in a different shape.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { buildGraph } from "../src/model/graph";
import { NO_FILTERS } from "../src/model/zoom";
import { Level0 } from "../src/views/Level0";
import { trimmedCast, trimmedExport, withUnplacedDevice } from "./fixtures/trimmed";

const graph = buildGraph(trimmedExport, trimmedCast);

function draw(g = graph): ReturnType<typeof render> {
  return render(<Level0 graph={g} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} />);
}

describe("infrastructure stubs replace the tie-lines (F3)", () => {
  it("draws one stub per placed device, and NO tie-line anywhere", () => {
    const { container } = draw();
    expect(screen.getAllByTestId("infra-stub")).toHaveLength(3);
    // The defect this slice retires: `<line class="tie">` from a device's
    // bottom to a tile's top, across whatever lay between.
    expect(container.querySelectorAll("line.tie")).toHaveLength(0);
    expect(container.querySelectorAll(".tie")).toHaveLength(0);
  });

  it("draws each stub as a two-point polyline at ONE x", () => {
    draw();
    for (const stub of screen.getAllByTestId("infra-stub")) {
      const points = (stub.getAttribute("points") ?? "").split(" ");
      expect(points).toHaveLength(2);
      const xs = points.map((p) => p.split(",")[0]);
      expect(xs[0], stub.getAttribute("data-device") ?? "").toBe(xs[1]);
    }
  });

  it("paints the stubs BEFORE the columns, so a box covers the end of its stub", () => {
    const { container } = draw();
    const svg = container.querySelector("svg.level0")!;
    const order = [...svg.querySelectorAll("[data-testid]")].map((n) =>
      n.getAttribute("data-testid"),
    );
    expect(order.indexOf("infra-stub")).toBeLessThan(order.indexOf("segment-tile"));
    expect(order.indexOf("infra-stub")).toBeLessThan(order.indexOf("infra-device"));
  });

  it("names the device and the group on every stub, for a failure that reads", () => {
    draw();
    const sites = screen.getAllByTestId("infra-stub").map((s) => s.getAttribute("data-site"));
    expect(sites.sort()).toEqual(["site:10.20", "site:10.80", "site:10.80"]);
  });

  it("tells assistive tech the devices sit over their group", () => {
    draw();
    expect(screen.getByTestId("level0-desc").textContent).toContain(
      "3 infrastructure devices are drawn above the site group each one serves",
    );
  });
});

describe("a device whose segment has no tile", () => {
  it("is dashed and carries no stub", () => {
    const { container } = draw(buildGraph(withUnplacedDevice(), trimmedCast));
    const devices = [...container.querySelectorAll("rect.device")];
    expect(devices).toHaveLength(4);
    const dashed = devices.filter((d) => d.classList.contains("is-unplaced"));
    expect(dashed).toHaveLength(1);
    // Three devices, three stubs -- the fourth has nothing to point at.
    expect(screen.getAllByTestId("infra-stub")).toHaveLength(3);
    // ...and it SAYS so, rather than looking like a device nobody connected.
    expect(
      screen.getByLabelText("dns-resolver: address:10.250.0.9 (its segment has no tile on this map)"),
    ).toBeDefined();
  });
});

describe("role badges replace the unlabeled histogram bar (F5)", () => {
  it("draws up to three badges per tile, each naming a role and a count", () => {
    const { container } = draw();
    // The bar is gone -- with it, the only thing that ever showed roles here.
    expect(container.querySelectorAll(".role-histogram")).toHaveLength(0);
    const badges = screen.getAllByTestId("role-badge");
    expect(badges.length).toBeGreaterThan(0);
    // 10.80.20.0/24: its two infrastructure roles, then its busiest bucket.
    const tile = container.querySelector('[data-pinnable="segment:10.80.20.0/24"]')!;
    const text = [...tile.querySelectorAll(".role-badge-text")].map((t) => t.textContent);
    expect(text).toEqual(["DC 1", "DNS 1", "EP 11"]);
  });

  it("keeps the full role name on each badge, so the abbreviation is never the only label", () => {
    const { container } = draw();
    const tile = container.querySelector('[data-pinnable="segment:10.80.20.0/24"]')!;
    const titles = [...tile.querySelectorAll(".role-badge title")].map((t) => t.textContent);
    expect(titles).toEqual([
      "domain-controller: 1",
      "dns-resolver: 1",
      "generic-endpoint: 11",
    ]);
  });

  it("puts the roles in the tile's own aria-label, since the badges are aria-hidden", () => {
    draw();
    expect(
      screen.getByLabelText(
        // R6 added the byte figure between the host count and the roles: the
        // tile says it, so the one reader who cannot see the tile is told it too.
        "Segment 10.80.20.0/24, 18 hosts, 0 B north-south and 150 B east-west in this window, roles: domain-controller 1, dns-resolver 1, generic-endpoint 11, +2 more roles",
      ),
    ).toBeDefined();
  });

  it("draws no badge group for a segment with no placed hosts", () => {
    // A segment the export carries with nothing placed in it. Built here rather
    // than asserted vacuously: the previous form of this test checked only
    // `max <= 3` and `every n >= 0` -- the second true of any array -- so it
    // never exercised the zero case its name promises.
    const empty = buildGraph(
      {
        ...trimmedExport,
        segments: [
          ...trimmedExport.segments,
          {
            cidr: "10.77.0.0/24",
            name: "seg-10-77-0-0",
            entities: 0,
            coverage_source: "inferred_from_traffic",
          },
        ],
      },
      trimmedCast,
    );
    const { container } = draw(empty);
    const tile = container.querySelector('[data-pinnable="segment:10.77.0.0/24"]')!;
    expect(tile, "the empty segment drew no tile at all").toBeTruthy();
    expect(tile.querySelectorAll("[data-testid='role-badge']")).toHaveLength(0);
    expect(tile.querySelectorAll(".role-badges")).toHaveLength(0);
    // ...and it still says how many hosts it has, without a roles clause.
    expect(tile.getAttribute("aria-label")).toBe(
      "Segment 10.77.0.0/24, 0 hosts, 0 B north-south and 0 B east-west in this window",
    );
  });

  it("discloses the roles it does NOT badge, on the row and in the label (S2)", () => {
    // The badge set is a projection -- and, with the infrastructure
    // reservation, not even a ranking -- so three badges must not read as the
    // whole answer. 10.80.20.0/24 holds five buckets and badges three.
    const { container } = draw();
    const tile = container.querySelector('[data-pinnable="segment:10.80.20.0/24"]')!;
    expect(tile.querySelector(".role-badges > title")?.textContent).toBe(
      "3 roles shown of 5 on this segment — +2 more in the inspector",
    );
    expect(tile.getAttribute("aria-label")).toContain(", +2 more roles");
    // A tile whose badges ARE the whole histogram says so, and claims no
    // residual: 192.168.0.0/24 has exactly one bucket.
    const whole = container.querySelector('[data-pinnable="segment:192.168.0.0/24"]')!;
    expect(whole.querySelector(".role-badges > title")?.textContent).toBe("1 role, all of them");
    expect(whole.getAttribute("aria-label")).not.toContain("more roles");
  });
});
