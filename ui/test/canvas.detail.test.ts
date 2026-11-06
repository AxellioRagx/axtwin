/** `model/detail.ts`: the zoom hysteresis and the chip's short name, pure. */
import { describe, expect, it } from "vitest";

import { DETAIL_COLLAPSE, DETAIL_EXPAND, INV_ZOOM_MAX, glyphCapacity, invZoom, nextLevel, nextMode, shortBytes, shortName, zoomBucket } from "../src/features/edit-canvas/model/detail";

describe("the level of detail follows the zoom with hysteresis", () => {
  it("expands at 0.6 and collapses below 0.5; between the two it holds", () => {
    expect(DETAIL_EXPAND).toBe(0.6);
    expect(DETAIL_COLLAPSE).toBe(0.5);
    expect(nextLevel("compact", 0.6)).toBe("full");
    expect(nextLevel("compact", 0.59)).toBe("compact");
    expect(nextLevel("full", 0.55)).toBe("full");
    expect(nextLevel("full", 0.5)).toBe("full");
    expect(nextLevel("full", 0.49)).toBe("compact");
    expect(nextLevel("compact", 0.55)).toBe("compact");
    expect(zoomBucket(0.6)).toBe("hi");
    expect(zoomBucket(0.55)).toBe("mid");
    expect(zoomBucket(0.49)).toBe("lo");
  });
  it("the rail's toggle cycles auto, full, compact", () => {
    expect(nextMode("auto")).toBe("full");
    expect(nextMode("full")).toBe("compact");
    expect(nextMode("compact")).toBe("auto");
  });
});

describe("a chip's short name is derived, never stored", () => {
  it("takes a hostname's first label, an address's last three octets, a mac's last two bytes", () => {
    expect(shortName("hostname:fin-ws01.corp.example", null)).toBe("fin-ws01");
    expect(shortName("address:10.80.20.4", "10.80.20.4")).toBe(".80.20.4"); // three: two collided across 10.80.20.x and 10.20.20.x
    expect(shortName("mac:aa:bb:cc:dd:ee:ff", null)).toBe("ee:ff");
  });
  it("keeps a design node's group name, and falls back to the address", () => {
    expect(shortName("web01", "10.1.1.20")).toBe("web01");
    expect(shortName("debian-12-cloud-1", "10.1.1.10")).toBe("debian-12-cloud-1");
    expect(shortName("10.1.1.20", "10.1.1.20")).toBe(".1.1.20");
    expect(shortName("", "10.1.1.20")).toBe(".1.1.20");
  });
});

describe("the inspector's width", () => {
  it("is per tab until dragged, and clamped to the window", async () => {
    const { inspectorWidth, INSPECTOR_MIN } = await import("../src/features/edit-canvas/chrome");
    expect(inspectorWidth("selection", null, 1600)).toBe(300);
    expect(inspectorWidth("checks", null, 1600)).toBe(460);
    expect(inspectorWidth("checks", 380, 1600)).toBe(380);
    expect(inspectorWidth("checks", 100, 1600)).toBe(INSPECTOR_MIN);
    expect(inspectorWidth("checks", 2000, 1600)).toBe(960);
    expect(inspectorWidth("checks", null, 400)).toBe(INSPECTOR_MIN);
  });
});

describe("bytes on a chrome label", () => {
  it("reads in short units", () => {
    expect(shortBytes(0)).toBe("0 B");
    expect(shortBytes(512)).toBe("512 B");
    expect(shortBytes(15_300)).toBe("15.3 kB");
    expect(shortBytes(310_000_000)).toBe("310 MB");
    expect(shortBytes(1_234_567_890)).toBe("1.2 GB");
    expect(shortBytes(15_494_524_704_945)).toBe("15.5 TB");
  });
});

describe("a compact tile's sizes", () => {
  it("keeps its header text at a constant screen size, capped", () => {
    expect(invZoom(1)).toBe(1);
    expect(invZoom(2)).toBe(1); // zoomed in: never smaller than the canvas size
    expect(invZoom(0.5)).toBe(2);
    expect(invZoom(0.3)).toBe(INV_ZOOM_MAX);
    expect(invZoom(0)).toBe(1);
  });
  it("shows as many glyphs as the tile holds under its header", () => {
    expect(glyphCapacity(156, 96)).toBe(2); // the smallest tile: one row of two
    expect(glyphCapacity(290, 96)).toBe(4); // a two-host tile: one row
    expect(glyphCapacity(290, 148)).toBe(8); // six hosts, three chip rows: two glyph rows
    expect(glyphCapacity(290, 60)).toBe(0);
  });
});

describe("the site rows follow the plane's shape (track 4)", () => {
  it("five corp-twin-sized sites go two or three per row; a lone site is one; a small twin stays one row", async () => {
    const { chooseSitesPerRow, PLANE_ASPECT_TARGET, planeSize } = await import("../src/features/edit-canvas/canvas/bus");
    expect(PLANE_ASPECT_TARGET).toBe(1.8);
    const site = { w: 520, h: 420 };
    const five = Array.from({ length: 5 }, () => site);
    const k = chooseSitesPerRow(five);
    expect(k).toBeGreaterThanOrEqual(2);
    expect(k).toBeLessThanOrEqual(4);
    expect(chooseSitesPerRow([site])).toBe(1);
    expect(chooseSitesPerRow([site, site])).toBe(2);
    expect(chooseSitesPerRow([])).toBe(1);
    expect(planeSize(five, 5).h).toBe(420);
    expect(planeSize(five, 2).h).toBeGreaterThan(3 * 420);
  });
});
