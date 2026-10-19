/**
 * D4 (fix round 3): the persona editor's rows must fit the host panel at
 * 1280-1920 px. jsdom computes no layout, so this is a STRUCTURAL guard over
 * the stylesheet -- decisive only together with the headless-browser check
 * at 1280/1600/1920 recorded in the fix-round report (LES-311: say which
 * seam a test exercises). It pins the three rules whose absence produced the
 * overflow: a <fieldset> shrinks (its default is `min-width: min-content`),
 * a <select> never exceeds its row (it sizes to its LONGEST option, and the
 * persona sentences are long), and the action rows wrap.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(join(__dirname, "..", "src/theme/app.css"), "utf8");

function rule(selector: string): string {
  const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = new RegExp(`(?:^|\\n|,\\s*)${esc}\\s*(?:,[^{]*)?\\{([^}]*)\\}`).exec(css);
  if (!m) throw new Error(`no rule for ${selector}`);
  return m[1]!;
}

describe("the persona editor fits its panel [structural: stylesheet]", () => {
  it("the draft fieldset may shrink below its content", () => {
    expect(rule(".persona-editor .persona-draft")).toMatch(/min-width:\s*0/);
  });

  it("every select in the editor is capped at its row's width", () => {
    const r = rule(".persona-editor select");
    expect(r).toMatch(/max-width:\s*100%/);
    expect(r).toMatch(/width:\s*100%/);
  });

  it("the swap / remove and add rows wrap, with the select flexible and the button fixed", () => {
    expect(rule(".persona-editor .row-actions")).toMatch(/flex-wrap:\s*wrap/);
    expect(rule(".persona-editor .row-actions label")).toMatch(/min-width:\s*0/);
    expect(rule(".persona-editor .row-actions .btn")).toMatch(/flex:\s*0 0 auto/);
    expect(rule(".persona-editor .add-row label")).toMatch(/min-width:\s*0/);
  });

  it("the panel column stays bounded, so 'fits the panel' means fits the page", () => {
    expect(rule(".twin-body.has-panel")).toMatch(/minmax\(22rem,\s*30rem\)/);
  });
});
