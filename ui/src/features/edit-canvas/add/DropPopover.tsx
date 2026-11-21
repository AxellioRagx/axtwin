/**
 * The drop popover (`docs/design/canvas-add-panel.md` §4.1): the small form that opens where a card
 * landed -- the inspector's fields in context. OS (a segmented control), the role / profile grouped
 * by category and filtered by that OS (each row a label and one plain sentence), count and name
 * prefix, the router's type and placement, the L3 switch's tier (it seeds the prefix only), the switch's kind, the needs hint with its one-click
 * "Add N <peers>", and Advanced (the resolved image with an override, the address, the persona's
 * intensity and schedule, the traffic generators). Nothing is written until it closes: Add, Esc
 * or a click outside commit (Esc and outside keep the defaults), Cancel drops nothing -- so the
 * drop and every choice here are ONE undo step.
 */
import { useEffect, useMemo, useRef, useState } from "react";

import type { ImageProfile, Persona } from "../../../api/types";
import type { TeModel } from "../edit/te";
import type { PaletteItem } from "../edit/EditContext";
import { DEVICE_CARDS, defaultRole, imageFor, missingPeers, peerOffer, personaBlurb, personaLabel, profileFor, rolesFor, type CardId, type Os, type PeerOffer } from "./catalog";
import type { DeviceDrop, DropTarget } from "./drop";
import { usePopoverAt } from "./usePopoverAt";

export type DropCommit = { kind: "device"; spec: DeviceDrop } | { kind: "tap"; segment: string; sensors: string[] };

export interface DropPopoverProps {
  item: PaletteItem;
  target: DropTarget;
  te: TeModel;
  personas: readonly Persona[];
  profiles: readonly ImageProfile[];
  /** The sensors the target's lab declares (the tap tool's suggestions). */
  sensors: readonly string[];
  /** Where it opens, in the canvas shell's pixels. */
  at: { x: number; y: number };
  onCommit(c: DropCommit): void;
  onCancel(): void;
}

function targetText(card: CardId | null, target: DropTarget): string {
  const routerLike = card === "router" || card === "l3switch";
  if ("segment" in target) return target.via ? `on ${target.via}` : `on ${target.segment}`;
  if ("router" in target) return routerLike ? "beside it, no leg yet" : `on a new segment under ${target.router}`;
  return routerLike ? "on its own, no leg yet" : "on a new segment";
}

function roleDefaultPrefix(card: CardId, role: string | null, routerType: string, placement: string, tier: string | null = null): string {
  if (card === "router") return placement === "edge" ? "edge" : routerType === "firewall" ? "fw" : "router";
  // The tier hint only seeds the prefix (A3): nothing else is written.
  if (card === "l3switch") return tier === "core" ? "core" : tier === "aggregation" ? "agg" : "l3sw";
  if (card === "switch") return "sw";
  return role ?? DEVICE_CARDS.find((c) => c.id === card)!.prefix;
}

export function DropPopover({ item, target, te, personas, profiles, sensors, at, onCommit, onCancel }: DropPopoverProps) {
  const card: CardId | null = item.kind === "device" ? (item.card ?? (item.id as CardId)) : null;
  const def = card ? DEVICE_CARDS.find((c) => c.id === card)! : null;
  const isHost = card === "server" || card === "workstation" || card === "ot";
  // A count on a router or an L3 switch makes N separate named nodes (B6).
  // A switch counts too, each on a LAN of its own -- not when it is dropped on a segment (it carries that one).
  const counted = isHost || card === "router" || card === "l3switch" || (card === "switch" && !("segment" in target));
  const [os, setOs] = useState<Os | null>(() => (def?.oses ? (item.os ?? def.oses[0]!) : null));
  const [traffic, setTraffic] = useState(() => Boolean(item.role && personas.find((p) => p.id === item.role)?.category === "traffic"));
  const [role, setRole] = useState<string | null>(() => (card && isHost ? (item.role ?? defaultRole(card, os, personas)) : null));
  // The role list scrolls to the preselected role (a search's "file" -> File server), centred in the
  // list -- the list's own scroll only, never the page's (2026-11-20, round 2).
  const rolesBox = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const box = rolesBox.current;
    const on = box?.querySelector<HTMLElement>('[role="radio"][aria-checked="true"]');
    if (!box || !on) return;
    const b = box.getBoundingClientRect();
    const r = on.getBoundingClientRect();
    box.scrollTop += r.top - b.top - (box.clientHeight - r.height) / 2;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [os]);
  const [count, setCount] = useState(item.count && item.count > 0 ? item.count : 1);
  const [routerType, setRouterType] = useState<"router" | "firewall">("router");
  const [placement, setPlacement] = useState<"internal" | "edge">("internal");
  const [switchType, setSwitchType] = useState<"plain" | "managed">("plain");
  const [tier, setTier] = useState<"core" | "aggregation" | null>(null);
  const [prefix, setPrefix] = useState<string | null>(null);
  const [imageOverride, setImageOverride] = useState("");
  const [ip, setIp] = useState("");
  const [intensity, setIntensity] = useState("");
  const [schedule, setSchedule] = useState("");
  const [peers, setPeers] = useState<PeerOffer[]>([]);
  const [tapSensors, setTapSensors] = useState(sensors[0] ?? "");
  // A switch carrying several segments was dropped on: which one (B2); its default is the switch's own pick.
  const [segChoice, setSegChoice] = useState<string | null>(null);
  const landing: DropTarget = "segment" in target && segChoice ? { segment: segChoice, via: target.via ?? null } : target;
  const groups = useMemo(() => (card ? rolesFor(card, os, personas, traffic) : []), [card, os, personas, traffic]);
  const persona = personas.find((p) => p.id === role) ?? null;
  const profile = card ? profileFor(card, os) : null;
  const resolved = imageFor(profiles, profile);
  const image = imageOverride.trim() || resolved;
  const effPrefix = (prefix ?? (card ? roleDefaultPrefix(card, role, routerType, placement, tier) : "")).trim() || (def?.prefix ?? "node");
  // What the role and every peer already chosen still need (a workstation added for a file server
  // needs a domain controller in turn); a role this drop or its peers hold is met.
  const held = [...(role ? [role] : []), ...peers.map((p) => p.role)];
  const missing = isHost
    ? [persona, ...peers.map((p) => personas.find((x) => x.id === p.persona))]
        .flatMap((x, i) => missingPeers(x, te, held, i === 0 ? count : peers[i - 1]!.count))
        .filter((r, i, all) => all.indexOf(r) === i)
    : [];
  const offers = missing.map((r) => peerOffer(r, personas));

  // A role no longer offered for this OS falls back to the card's default.
  useEffect(() => {
    if (!card || !isHost) return;
    if (!groups.some((g) => g.personas.some((p) => p.id === role))) setRole(defaultRole(card, os, personas));
  }, [card, isHost, groups, role, os, personas]);

  const spec = (): DropCommit | null => {
    if (item.kind === "tool" && item.tool === "tap") {
      if (!("segment" in landing)) return null;
      return { kind: "tap", segment: landing.segment, sensors: tapSensors.split(",").map((s) => s.trim()).filter(Boolean) };
    }
    if (!card) return null;
    const n = Number(intensity);
    return {
      kind: "device",
      spec: {
        card,
        os,
        role: isHost ? role : null,
        persona: isHost && persona ? persona.id : null,
        count: counted ? Math.max(1, count) : 1,
        prefix: effPrefix,
        target: landing,
        image: card === "switch" ? null : image,
        ip: ip.trim() || null,
        intensity: intensity.trim() && Number.isFinite(n) ? n : null,
        schedule: schedule.trim() || null,
        routerType,
        placement,
        switchType,
        tier: card === "l3switch" ? tier : null,
        peers: peers.map((p) => ({ card: p.card, os: p.os, role: p.role, persona: p.persona, count: p.count, image: imageFor(profiles, profileFor(p.card, p.os)) })),
      },
    };
  };
  const commit = () => {
    const c = spec();
    if (c) onCommit(c);
    else onCancel();
  };
  const { box, pos } = usePopoverAt(at, commit);

  const title = item.kind === "tool" ? item.label : def!.label;
  return (
    <div
      ref={box}
      className="cv-popover cv-drop-popover"
      role="dialog"
      aria-label={`Add ${title}`}
      data-testid="drop-popover"
      style={{ left: pos.x, top: pos.y }}
      onClick={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
    >
      <div className="cv-popover-head">
        <span>
          {title} <span className="muted">{targetText(card, target)}</span>
        </span>
      </div>
      {"segment" in target && target.choices && target.choices.length > 1 ? (
        <label className="cv-field">
          <span className="cv-field-label">Segment</span>
          <select aria-label="Segment" data-testid="drop-segment" value={segChoice ?? target.segment} onChange={(e) => setSegChoice(e.target.value)}>
            {target.choices.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {item.kind === "tool" && item.tool === "tap" ? (
        <label className="cv-field">
          <span className="cv-field-label">Sensors</span>
          <input type="text" aria-label="Tap sensors" list="cv-drop-sensors" placeholder="sensor id, comma-separated" value={tapSensors} onChange={(e) => setTapSensors(e.target.value)} />
          <datalist id="cv-drop-sensors">
            {sensors.map((s) => <option key={s} value={s} />)}
          </datalist>
        </label>
      ) : null}
      {def?.oses ? (
        <div className="cv-seg" role="group" aria-label="OS" data-testid="drop-os">
          {def.oses.map((o) => (
            <button key={o} type="button" aria-pressed={os === o} className={os === o ? "on" : ""} onClick={() => setOs(o)}>
              {o === "windows" ? (card === "server" ? "Windows Server" : "Windows") : "Linux"}
            </button>
          ))}
        </div>
      ) : null}
      {card === "router" ? (
        <>
          <div className="cv-seg" role="group" aria-label="Type">
            {(["router", "firewall"] as const).map((t) => <button key={t} type="button" aria-pressed={routerType === t} className={routerType === t ? "on" : ""} onClick={() => setRouterType(t)}>{t}</button>)}
          </div>
          <div className="cv-seg" role="group" aria-label="Placement">
            {(["internal", "edge"] as const).map((t) => <button key={t} type="button" aria-pressed={placement === t} className={placement === t ? "on" : ""} onClick={() => setPlacement(t)}>{t === "edge" ? "edge (internet uplink)" : "internal"}</button>)}
          </div>
        </>
      ) : null}
      {card === "l3switch" ? (
        <div className="cv-seg" role="group" aria-label="Tier" title="seeds the name prefix only">
          {(["core", "aggregation"] as const).map((t) => <button key={t} type="button" aria-pressed={tier === t} className={tier === t ? "on" : ""} onClick={() => setTier((cur) => (cur === t ? null : t))}>{t}</button>)}
        </div>
      ) : null}
      {card === "switch" ? (
        <div className="cv-seg" role="group" aria-label="Switch kind">
          {(["plain", "managed"] as const).map((t) => <button key={t} type="button" aria-pressed={switchType === t} className={switchType === t ? "on" : ""} onClick={() => setSwitchType(t)}>{t === "managed" ? "managed (VLANs, RSTP)" : "plain"}</button>)}
        </div>
      ) : null}
      {isHost ? (
        <div ref={rolesBox} className="cv-drop-roles" role="radiogroup" aria-label={card === "workstation" ? "Profile" : "Role"} data-testid="drop-roles">
          {groups.length === 0 ? <p className="muted">no {card === "workstation" ? "profile" : "role"} for this OS in the persona library</p> : null}
          {groups.map((g) => (
            <div key={g.category} className="cv-drop-group">
              <div className="cv-drop-group-head muted">{g.label}</div>
              {g.personas.map((p) => (
                <button key={p.id} type="button" role="radio" aria-checked={role === p.id} data-testid="drop-role" data-role={p.id} data-os={p.os_family} className={`cv-drop-role${role === p.id ? " on" : ""}`} onClick={() => setRole(p.id)}>
                  <span className="cv-drop-role-label">{personaLabel(p)}</span>
                  <span className="cv-drop-role-blurb muted">{personaBlurb(p)}</span>
                </button>
              ))}
            </div>
          ))}
        </div>
      ) : null}
      {card ? (
        <div className="row cv-drop-quick">
          {counted ? (
            <label className="cv-count">
              ×
              <input type="number" min={1} max={999} aria-label="Count" value={count} onChange={(e) => setCount(Math.max(1, Math.min(999, Number(e.target.value) || 1)))} />
            </label>
          ) : null}
          <label className="cv-field">
            <span className="cv-field-label">Name prefix</span>
            <input type="text" aria-label="Name prefix" value={prefix ?? effPrefix} onChange={(e) => setPrefix(e.target.value.replace(/[^A-Za-z0-9._-]+/g, "-"))} />
          </label>
        </div>
      ) : null}
      {offers.length || peers.length ? (
        <div className="cv-drop-needs" data-testid="drop-needs">
          {offers.map((o) => (
            <div key={o.role} className="row">
              <span className="muted">needs {o.card === "workstation" ? "workstations" : (personas.find((p) => p.id === o.role) ? personaLabel(personas.find((p) => p.id === o.role)!) : o.role)}</span>
              <button type="button" data-testid="drop-needs-add" data-role={o.role} onClick={() => setPeers((ps) => [...ps, o])}>
                {o.label}
              </button>
            </div>
          ))}
          {peers.map((p) => (
            <div key={p.role} className="row muted" data-testid="drop-peer">
              + {p.count} × {p.role} on the same segment
              <button type="button" className="link" aria-label={`Do not add ${p.role}`} onClick={() => setPeers((ps) => ps.filter((x) => x.role !== p.role))}>undo</button>
            </div>
          ))}
        </div>
      ) : null}
      {card && card !== "switch" ? (
        <details className="cv-drop-advanced" data-testid="drop-advanced">
          <summary>Advanced</summary>
          <label className="cv-field">
            <span className="cv-field-label">Image</span>
            <input type="text" aria-label="Image" placeholder={resolved ?? "no image for this profile"} value={imageOverride} onChange={(e) => setImageOverride(e.target.value)} />
          </label>
          <p className="muted" data-testid="drop-image">{resolved ? `profile ${profile}: ${resolved}` : `no image for this profile (${profile})`}</p>
          {isHost && count === 1 && "segment" in target ? (
            <label className="cv-field">
              <span className="cv-field-label">Address</span>
              <input type="text" aria-label="Address" placeholder="next free" value={ip} onChange={(e) => setIp(e.target.value)} />
            </label>
          ) : null}
          {isHost && persona ? (
            <>
              <label className="cv-field">
                <span className="cv-field-label">Intensity</span>
                <input type="text" aria-label="Intensity" placeholder="persona default" value={intensity} onChange={(e) => setIntensity(e.target.value)} />
              </label>
              <label className="cv-field">
                <span className="cv-field-label">Schedule</span>
                <input type="text" aria-label="Schedule" placeholder="persona default (24x7, business-hours…)" value={schedule} onChange={(e) => setSchedule(e.target.value)} />
              </label>
            </>
          ) : null}
          {isHost && card !== "ot" ? (
            <label className="row">
              <input type="checkbox" aria-label="Show traffic generators" checked={traffic} onChange={(e) => setTraffic(e.target.checked)} /> show traffic generators
            </label>
          ) : null}
        </details>
      ) : null}
      <div className="row">
        <button type="button" className="primary" data-testid="drop-add" onClick={commit}>
          Add
        </button>
        <button type="button" data-testid="drop-cancel" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
