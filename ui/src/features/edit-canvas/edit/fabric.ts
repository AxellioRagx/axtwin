/**
 * Whole-fabric conversion of bridge VMs to native switches (`docs/design/native-switches.md` §12 S5,
 * D11): the engine reads the bridge recipe's port tables and answers a plan
 * (`POST /api/fabric-conversion`, `axrange/twinconfig/fabric.py`); this applies it to the working
 * document as ONE edit, so a single undo restores the bridge VMs. Offered in the inspector of a bridge
 * VM, never automatic: the twin deploys byte-identically until the operator converts and saves it.
 */
import type { FabricPlan } from "../../../api/types";
import type { Files } from "./commands";
import type { DocModel, WorkingDoc } from "./document";
import { addSpanBinding, removeTapBinding, setTapSelector } from "./tap";
import { addLink, addNode, removeNode, removeSegment, setNodeNets, type TeNode } from "./te";

/** A bridge VM: a `kind: host`, `role: l2-switch` node with an image (corp-twin's switch VMs). */
export function isBridgeVm(n: TeNode): boolean {
  return n.kind === "host" && n.role === "l2-switch" && Boolean(n.image);
}

/** `plan` applied to the document: bridge VMs replaced by imageless switches, the isolated partner and
 *  trunk segments removed (their legs moved to each VLAN's surviving segment), the links added, and
 *  each tap file's bindings retargeted / folded into SPAN sessions. */
export function applyFabricPlan(doc: WorkingDoc, model: DocModel, plan: FabricPlan): Files {
  let te = doc.files[doc.teFile];
  if (te === undefined) throw new Error(`the document has no '${doc.teFile}' to edit`);
  for (const b of plan.bridges) {
    if (!model.te.nodes.some((n) => n.name === b)) throw new Error(`the plan converts '${b}', which the document does not have`);
  }
  // Legs first (a moved leg must not dangle on a removed segment), then the segments.
  for (const x of plan.node_nets) te = setNodeNets(te, x.node, x.nets);
  for (const sw of plan.switches) {
    te = removeNode(te, sw.name);
    te = addNode(te, { name: sw.name, kind: "switch", role: sw.role, image: "", nets: sw.nets, stp: sw.stp });
  }
  for (const s of plan.remove_segments) te = removeSegment(te, s);
  for (const l of plan.links) te = addLink(te, { a: l.a, b: l.b, mode: l.mode, native: l.native, vlans: l.vlans, state: l.state });
  const files: Files = { ...doc.files, [doc.teFile]: te };
  for (const t of plan.taps) {
    let text = files[t.path];
    if (text === undefined) throw new Error(`the plan rewrites '${t.path}', which the document does not have`);
    for (const r of t.retarget) text = setTapSelector(text, r.index, { scope: "segment", target: r.target });
    for (const i of [...t.remove].sort((a, b) => b - a)) text = removeTapBinding(text, i);
    // `mode` absent in the tap plan is fanout (`o2control/taps.py`); written out so the line is explicit.
    for (const s of t.add_spans) text = addSpanBinding(text, { switch: s.switch, sources: s.sources, sensors: s.sensors, mode: s.mode ?? "fanout" });
    files[t.path] = text;
  }
  return files;
}
