/**
 * Light/dark contrast of the run-time areas' TEXT (fix round 1): every text
 * colour `app.css` uses is an AA-safe `--t-*` tone (the token's hue mixed 22%
 * toward ink) or ink/ink-soft, and the mix is computed here from the real
 * `tokens.css` values against every surface the areas draw text on.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const read = (f: string): string => readFileSync(join(__dirname, "..", f), "utf8");
const tokens = read("src/theme/tokens.css");
const app = read("src/theme/app.css");

function block(selectorStart: string): string {
  const at = tokens.indexOf(selectorStart);
  return tokens.slice(at, tokens.indexOf("}", at));
}
function vars(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of text.matchAll(/--([a-z0-9-]+):\s*(#[0-9a-f]{6})/gi)) out[m[1]!] = m[2]!;
  return out;
}
const LIGHT = vars(block(":root {"));
const DARK = vars(block(':root[data-theme="dark"]'));

const rgb = (h: string): number[] => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const lum = (c: number[]): number => {
  const f = (v: number): number => (v / 255 <= 0.03928 ? v / 255 / 12.92 : ((v / 255 + 0.055) / 1.055) ** 2.4);
  return 0.2126 * f(c[0]!) + 0.7152 * f(c[1]!) + 0.0722 * f(c[2]!);
};
const ratio = (a: number[], b: number[]): number => {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => p - q) as [number, number];
  return (y + 0.05) / (x + 0.05);
};
const mix = (a: number[], b: number[], p: number): number[] => a.map((v, i) => v * p + b[i]! * (1 - p));

describe("text contrast in the run-time areas (WCAG AA 4.5:1)", () => {
  it("the mix percentage in app.css is the one checked here", () => {
    for (const t of ["neutral", "good", "warn", "focus"]) {
      expect(app).toMatch(new RegExp(`--t-${t}: color-mix\\(in srgb, var\\(--${t}\\) 78%, var\\(--ink\\)\\)`));
    }
  });

  for (const [name, theme] of [["light", LIGHT], ["dark", DARK]] as const) {
    it(`every --t-* tone reaches 4.5:1 on paper, paper-2 and paper-3 (${name})`, () => {
      for (const t of ["neutral", "good", "warn", "focus"]) {
        const fg = mix(rgb(theme[t]!), rgb(theme["ink"]!), 0.78);
        for (const bg of ["paper", "paper-2", "paper-3"]) {
          expect(ratio(fg, rgb(theme[bg]!)), `${name} --t-${t} on --${bg}`).toBeGreaterThanOrEqual(4.5);
        }
      }
      // Primary button text: paper on focus.
      expect(ratio(rgb(theme["paper"]!), rgb(theme["focus"]!))).toBeGreaterThanOrEqual(4.5);
    });
  }

  it("no text colour in app.css uses a raw tone that fails AA in light", () => {
    const textColours = [...app.matchAll(/(?<![-\w])color:\s*var\(--([a-z0-9-]+)\)/g)].map((m) => m[1]);
    expect(textColours.filter((c) => ["neutral", "good", "warn", "focus"].includes(c!))).toEqual([]);
  });
});
