/**
 * Groups and members (`docs/design/topology-layers.md` §5, track 5c): a
 * `×N` group's instances are `{name}01 … {name}NN`; a te-tier override is
 * `members: [{index, role}]` on the group and a traffic one an
 * `assignments[]` entry by the instance id. Split picks members into a new
 * group; merge folds one group into another of the same image and
 * segments, every conflicting role becoming a member override; detach
 * splits one member off as its own `×1` group; move-one is a detach then a
 * merge. All of them are topology changes (a re-plan). Each works over the
 * working document's files and keeps every untouched line.
 */
import type { Files } from "./commands";
import { readDoc, setPersona, type DocModel, type WorkingDoc } from "./document";
import { addNode, copyNames, removeNode, setLinkField, setMemberRole, setNodeField, type TeNode } from "./te";
import { removeAssignment, renameNodeBindings, type TfcAssignment } from "./tfc";

/** The instance id the engine mints for member `i` of a group. */
export function instanceId(name: string, i: number): string {
  return `${name}${String(i).padStart(2, "0")}`;
}

function group(model: DocModel, name: string): TeNode {
  const n = model.te.nodes.find((x) => x.name === name);
  if (!n) throw new Error(`no node group named '${name}'`);
  return n;
}

function numericCount(n: TeNode): number {
  if (typeof n.count !== "number") throw new Error(`'${n.name}' has a parameterised count (${String(n.count)}); set a number before splitting or merging it`);
  return n.count;
}

function te(doc: WorkingDoc): string {
  const text = doc.files[doc.teFile];
  if (text === undefined) throw new Error(`the document has no '${doc.teFile}' to edit`);
  return text;
}

/** The instance-level traffic bindings of a group, by member index. */
function instanceBindings(model: DocModel, n: TeNode): Map<number, TfcAssignment> {
  const out = new Map<number, TfcAssignment>();
  for (let i = 1; i <= n.countValue; i++) {
    const a = model.tfc?.assignments.find((x) => x.by === "node" && x.target === instanceId(n.name, i));
    if (a) out.set(i, a);
  }
  return out;
}

/** Re-key instance bindings `{from}{i}` → `{to}{j}` for every pair, through a
 *  temporary name so a chain (03 → 02, 02 → 01) never collides. */
function rekeyBindings(text: string, pairs: ReadonlyArray<{ from: string; to: string }>): string {
  let out = text;
  for (const p of pairs) out = renameNodeBindings(out, p.from, `__moving__${p.from}`);
  for (const p of pairs) out = renameNodeBindings(out, `__moving__${p.from}`, p.to);
  return out;
}

/** Split members `indices` of `name` off into a new group `newName` (the
 *  original keeps its name and the other members, re-indexed). The copy
 *  starts with the original's role, image, segments and group-level persona;
 *  each moved member keeps its role override and its instance bindings. */
export function splitGroup(doc: WorkingDoc, model: DocModel, name: string, indices: readonly number[], newName: string): Files {
  const n = group(model, name);
  const total = numericCount(n);
  const picked = [...new Set(indices)].sort((a, b) => a - b);
  if (!picked.length) throw new Error("pick at least one member to split off");
  if (picked.some((i) => i < 1 || i > total)) throw new Error(`a member index is outside 1 … ${total}`);
  if (picked.length >= total) throw new Error(`splitting all ${total} members leaves nothing in '${name}'; rename the group instead`);
  if (!newName.trim()) throw new Error("name the new group");
  if (model.te.nodes.some((x) => x.name === newName)) throw new Error(`a node group named '${newName}' already exists`);
  const kept = Array.from({ length: total }, (_, i) => i + 1).filter((i) => !picked.includes(i));
  const bindings = instanceBindings(model, n);

  let text = te(doc);
  // The new group, after the original (count omitted when one).
  text = addNode(text, { name: newName, kind: n.kind, role: n.role, image: n.image, count: picked.length, nets: n.nets.map((l) => ({ segment: l.segment, ip: null })), requires: n.requires, resources: n.resources });
  text = setNodeField(text, name, "count", kept.length === 1 ? null : kept.length);
  // Member overrides: clear the original's, then re-set the kept ones at their new index and the moved ones on the new group.
  for (const m of n.members) text = setMemberRole(text, name, m.index, null);
  kept.forEach((old, i) => {
    const m = n.members.find((x) => x.index === old);
    if (m?.role) text = setMemberRole(text, name, i + 1, m.role);
  });
  picked.forEach((old, i) => {
    const m = n.members.find((x) => x.index === old);
    if (m?.role) text = setMemberRole(text, newName, i + 1, m.role);
  });
  let files: Files = { ...doc.files, [doc.teFile]: text };

  // Traffic: the group-level binding copies to the new group; instance bindings follow their member.
  const groupBinding = model.tfc?.assignments.find((a) => a.by === "node" && a.target === name) ?? null;
  if (groupBinding) {
    const next = readDoc({ ...doc, files });
    files = setPersona({ ...doc, files }, next, "node", newName, { persona: groupBinding.persona, intensity: groupBinding.intensity, schedule: groupBinding.schedule });
  }
  if (model.tfcPath && files[model.tfcPath] !== undefined && bindings.size) {
    const pairs = [...kept.map((old, i) => ({ from: instanceId(name, old), to: instanceId(name, i + 1) })), ...picked.map((old, i) => ({ from: instanceId(name, old), to: instanceId(newName, i + 1) }))].filter((p) => p.from !== p.to && bindings.has(Number(p.from.slice(name.length))));
    files[model.tfcPath] = rekeyBindings(files[model.tfcPath]!, pairs);
  }
  return files;
}

/** Split member `index` off as its own `×1` group named `{name}-{index}` (or the next free). */
export function detachMember(doc: WorkingDoc, model: DocModel, name: string, index: number): { files: Files; newName: string } {
  const taken = new Set(model.te.nodes.map((x) => x.name));
  const newName = copyNames(`${name}-${index}`, 1, taken)[0]!;
  return { files: splitGroup(doc, model, name, [index], newName), newName };
}

/** The groups `from` may merge into: hosts of the same image on the same segments. */
export function mergeTargets(model: DocModel, from: string): string[] {
  const n = model.te.nodes.find((x) => x.name === from);
  if (!n || n.kind !== "host" || typeof n.count !== "number") return [];
  const segs = n.nets.map((l) => l.segment).sort().join("|");
  return model.te.nodes.filter((x) => x.name !== from && x.kind === "host" && x.image === n.image && typeof x.count === "number" && x.nets.map((l) => l.segment).sort().join("|") === segs).map((x) => x.name);
}

/** Fold `from` into `into` (the same image and segments): `into.count` grows
 *  by `from`'s, each of `from`'s members lands at the next index with a role
 *  override where its role differs from `into`'s, its instance bindings and
 *  its group binding become instance bindings; links naming `from` re-point
 *  to `into`; `from` goes. */
export function mergeGroups(doc: WorkingDoc, model: DocModel, from: string, into: string): Files {
  if (from === into) throw new Error("a group cannot merge into itself");
  const a = group(model, from);
  const b = group(model, into);
  if (!mergeTargets(model, from).includes(into)) throw new Error(`'${from}' and '${into}' differ in image or segments; a merge keeps one image on one set of segments`);
  const na = numericCount(a);
  const nb = numericCount(b);
  const bindings = instanceBindings(model, a);
  const groupBinding = model.tfc?.assignments.find((x) => x.by === "node" && x.target === from) ?? null;
  const intoBinding = model.tfc?.assignments.find((x) => x.by === "node" && x.target === into) ?? null;

  let text = te(doc);
  text = setNodeField(text, into, "count", na + nb);
  for (let i = 1; i <= na; i++) {
    const m = a.members.find((x) => x.index === i);
    const role = m?.role ?? a.role;
    if (role && role !== b.role) text = setMemberRole(text, into, nb + i, role);
  }
  for (const l of model.te.links) {
    if (l.a.device === from) text = setLinkField(text, l.index, "a.device", into);
    if (l.b.device === from) text = setLinkField(text, l.index, "b.device", into);
  }
  text = removeNode(text, from);
  let files: Files = { ...doc.files, [doc.teFile]: text };

  if (model.tfcPath && files[model.tfcPath] !== undefined) {
    const pairs = [...bindings.keys()].map((i) => ({ from: instanceId(from, i), to: instanceId(into, nb + i) }));
    files[model.tfcPath] = rekeyBindings(files[model.tfcPath]!, pairs);
    files[model.tfcPath] = removeAssignment(files[model.tfcPath]!, "node", from);
  }
  // `from`'s group persona differs from `into`'s: each merged instance without its own binding keeps it as one.
  if (groupBinding && (!intoBinding || intoBinding.persona !== groupBinding.persona || intoBinding.intensity !== groupBinding.intensity || intoBinding.schedule !== groupBinding.schedule)) {
    for (let i = 1; i <= na; i++) {
      if (bindings.has(i)) continue;
      const next = readDoc({ ...doc, files });
      files = setPersona({ ...doc, files }, next, "node", instanceId(into, nb + i), { persona: groupBinding.persona, intensity: groupBinding.intensity, schedule: groupBinding.schedule });
    }
  }
  return files;
}

/** Move one member of `from` into `into`: a detach, then a merge of the `×1`. */
export function moveMember(doc: WorkingDoc, model: DocModel, from: string, index: number, into: string): Files {
  const a = group(model, from);
  if (numericCount(a) === 1) return mergeGroups(doc, model, from, into);
  const d = detachMember(doc, model, from, index);
  const next = readDoc({ ...doc, files: d.files });
  return mergeGroups({ ...doc, files: d.files }, next, d.newName, into);
}
