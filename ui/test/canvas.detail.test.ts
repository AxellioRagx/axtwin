/** `model/detail.ts`: the zoom hysteresis and the chip's short name, pure. */
import { describe, expect, it } from "vitest";

import { DETAIL_COLLAPSE, DETAIL_EXPAND, nextLevel, nextMode, shortName, zoomBucket } from "../src/features/edit-canvas/model/detail";

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
  it("takes a hostname's first label, an address's last two octets, a mac's last two bytes", () => {
    expect(shortName("hostname:fin-ws01.corp.example", null)).toBe("fin-ws01");
    expect(shortName("address:10.80.20.4", "10.80.20.4")).toBe(".20.4");
    expect(shortName("mac:aa:bb:cc:dd:ee:ff", null)).toBe("ee:ff");
  });
  it("keeps a design node's group name, and falls back to the address", () => {
    expect(shortName("web01", "10.1.1.20")).toBe("web01");
    expect(shortName("debian-12-cloud-1", "10.1.1.10")).toBe("debian-12-cloud-1");
    expect(shortName("10.1.1.20", "10.1.1.20")).toBe(".1.20");
    expect(shortName("", "10.1.1.20")).toBe(".1.20");
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
