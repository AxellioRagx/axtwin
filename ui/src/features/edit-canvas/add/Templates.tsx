/**
 * The Add panel's Templates (`docs/design/canvas-add-panel.md` §5): each card a preview drawn from
 * the fragment and its device counts (at the default settings), dragged or clicked like a device
 * card; and the settings form that opens at the drop point (§5.2). Its Add, Esc or a click outside
 * drop the template with the settings as they stand; Cancel drops nothing.
 */
import { useMemo, useState } from "react";

import type { ImageProfile, Template } from "../../../api/types";
import type { PaletteItem } from "../edit/EditContext";
import { AddEntry } from "./AddPanel";
import { countsText, expandTemplate, previewShape, settingsValues, templateCounts, type Values } from "./template";
import { usePopoverAt } from "./usePopoverAt";

export const templateItem = (t: Template): PaletteItem => ({ kind: "template", id: t.id, label: t.label });

export function TemplatePreview({ template }: { template: Template }) {
  const shape = useMemo(() => {
    try {
      return previewShape(expandTemplate(template));
    } catch {
      return null;
    }
  }, [template]);
  if (!shape) return <svg className="cv-tpl-preview" width={120} height={64} aria-hidden="true" />;
  return (
    <svg className="cv-tpl-preview" width={shape.width} height={shape.height} viewBox={`0 0 ${shape.width} ${shape.height}`} aria-hidden="true" data-testid="template-preview">
      {shape.edges.map((e, i) => <line key={`e${i}`} x1={e.a.x} y1={e.a.y} x2={e.b.x} y2={e.b.y} className="cv-tpl-edge" />)}
      {shape.segments.map((s) => <line key={s.name} x1={s.x1} y1={s.y} x2={s.x2} y2={s.y} className="cv-tpl-seg" />)}
      {shape.routers.map((r, i) => <circle key={`r${i}`} cx={r.x} cy={r.y} r={5} className="cv-tpl-router" />)}
      {shape.switches.map((w, i) => <rect key={`w${i}`} x={w.x - 4} y={w.y - 4} width={8} height={8} className="cv-tpl-switch" />)}
      {shape.hosts.map((h, i) => (
        <g key={`h${i}`}>
          <line x1={h.x} y1={30} x2={h.x} y2={h.y - 3} className="cv-tpl-edge" />
          <circle cx={h.x} cy={h.y} r={3} className="cv-tpl-host" />
          {h.count > 1 ? <text x={h.x} y={h.y + 12} className="cv-tpl-count">×{h.count}</text> : null}
        </g>
      ))}
    </svg>
  );
}

/** The Templates section: built in first, then the saved ones. */
export function TemplatesSection({ templates, disabled, extra }: { templates: readonly Template[]; disabled: boolean; extra?: React.ReactNode }) {
  const builtin = templates.filter((t) => t.builtin);
  const saved = templates.filter((t) => !t.builtin);
  const group = (title: string, list: readonly Template[], testId: string) =>
    list.length ? (
      <div className="cv-tpl-group" data-testid={testId}>
        <div className="cv-drop-group-head muted">{title}</div>
        <ul className="cv-add-list">
          {list.map((t) => {
            let counts = "";
            try {
              counts = countsText(templateCounts(expandTemplate(t)));
            } catch (e) {
              counts = e instanceof Error ? e.message : String(e);
            }
            return (
              <AddEntry key={t.id} item={templateItem(t)} detail={counts} disabled={disabled} testId="add-template" media={<TemplatePreview template={t} />} />
            );
          })}
        </ul>
      </div>
    ) : null;
  return (
    <section className="cv-palette-cat" aria-label="Templates" data-testid="add-templates">
      <h4 className="cv-add-head">Templates</h4>
      {group("Built in", builtin, "templates-builtin")}
      {group("Saved", saved, "templates-saved")}
      {extra}
      {templates.length === 0 ? <p className="muted cv-palette-hint">no templates in the catalog</p> : null}
    </section>
  );
}

export interface TemplatePopoverProps {
  template: Template;
  profiles: readonly ImageProfile[];
  at: { x: number; y: number };
  /** Where it landed, in words ("on the empty canvas"). */
  where: string;
  onCommit(values: Values): void;
  onCancel(): void;
}

/** The settings form at the drop point (§5.2 step 2). */
export function TemplatePopover({ template, profiles, at, where, onCommit, onCancel }: TemplatePopoverProps) {
  const [raw, setRaw] = useState<Record<string, string>>(() => Object.fromEntries(template.params.map((p) => [p.name, p.default === undefined || p.default === null ? "" : String(p.default)])));
  const values = (): Values => Object.fromEntries(template.params.map((p) => [p.name, (p.type ?? "int") === "int" || p.type === "percent" ? Number(raw[p.name]) : (raw[p.name] ?? "").trim() || String(p.default ?? "")]));
  const preview = useMemo(() => {
    try {
      return countsText(templateCounts(expandTemplate(template, values(), profiles)));
    } catch (e) {
      return e instanceof Error ? e.message : String(e);
    }
  }, [raw, template, profiles]);
  const commit = () => onCommit(settingsValues(template.params, values()));
  const { box, pos } = usePopoverAt(at, commit);
  return (
    <div ref={box} className="cv-popover cv-drop-popover" role="dialog" aria-label={`Add ${template.label}`} data-testid="template-popover" style={{ left: pos.x, top: pos.y }} onClick={(e) => e.stopPropagation()} onDoubleClick={(e) => e.stopPropagation()}>
      <div className="cv-popover-head">
        <span>
          {template.label} <span className="muted">{where}</span>
        </span>
      </div>
      {template.description ? <p className="muted">{template.description}</p> : null}
      {template.params.map((p) => {
        const kind = p.type ?? "int";
        return (
          <label key={p.name} className="cv-field">
            <span className="cv-field-label">{p.label ?? p.name}</span>
            <input
              type={kind === "int" || kind === "percent" ? "number" : "text"}
              aria-label={p.label ?? p.name}
              data-param={p.name}
              {...(kind === "int" || kind === "percent" ? { min: p.min ?? 0, max: p.max ?? (kind === "percent" ? 100 : undefined) } : {})}
              placeholder={kind === "cidr" ? "auto (the next free /24)" : undefined}
              value={raw[p.name] ?? ""}
              onChange={(e) => setRaw((r) => ({ ...r, [p.name]: kind === "name" ? e.target.value.replace(/[^A-Za-z0-9._-]+/g, "-") : e.target.value }))}
            />
          </label>
        );
      })}
      <p className="muted" data-testid="template-counts">{preview}</p>
      <div className="row">
        <button type="button" className="primary" data-testid="template-add" onClick={commit}>
          Add
        </button>
        <button type="button" data-testid="template-cancel" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
