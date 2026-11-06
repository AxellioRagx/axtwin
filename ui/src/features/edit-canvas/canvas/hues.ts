/**
 * One hue per router (rendering rule 2, track 4, 2026-11-06): the k-th router
 * of the twin, in the cast's order, wears the k-th hue of an eight-hue
 * palette on its icon ring, its legs and a small dot at the left end of every
 * bus it serves; past eight, grey. The palette reads on the light and the
 * dark theme and no two neighbours share a family. Pure.
 */
export const ROUTER_HUES: readonly string[] = [
  "#d6453a", // red
  "#2b6fd6", // blue
  "#2f9e44", // green
  "#e08a1e", // orange
  "#8e44ad", // purple
  "#1a9c9c", // teal
  "#d63aa8", // magenta
  "#8a6d3b", // brown
];

/** The hue of every router past the palette's eight. */
export const HUE_GREY = "#8a8f98";

/** The hue of the router at `index` in the twin's router order. */
export function hueOf(index: number): string {
  return ROUTER_HUES[index] ?? HUE_GREY;
}
