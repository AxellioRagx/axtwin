# The canvas "Add" panel: devices, roles and templates

**Status:** D1–D4 signed off 2026-11-20. All three phases built 2026-11-20:
devices, built-in templates, save as template. The live acceptance (§10,
"eyes") is still to run. Phases are in §9.

**Loop step:** **customize** (step 3). A user must be able to design a twin
by hand on the canvas, not only through Claude writing catalog files.

**Replaces:** the palette described in `ui/src/features/edit-canvas/SPEC.md`
("Palette & placement", §7.1 drop rules): a searchable list of segments,
routers, images, personas and "From other twins" node groups
(`Palette.tsx`, `paletteItems()` in `CanvasPage.tsx`).

## 1. Problem

The palette lists what the twin-config is *made of*, not what a designer
*thinks in*:

- **Images** (`debian-12-cloud@v1`, `windows-server-2022@v1`) are deployment
  details tied to a platform. On the canvas nothing useful can be done with
  one.
- **Personas** come as one flat list of 41 ids. About 11 of them (`loadgen-*`,
  `xseg-*`) are traffic generators, not "what this server is". Nothing says
  which persona is a server role, which is a workstation profile, or which OS
  it needs.
- **"From other twins"** lists every node group of up to 12 other twins:
  long, unlabelled, and impossible to scan for what you need.

## 2. What already exists to build on

- **Persona data.** Every persona has `os_family` (33 linux, 8 windows),
  `drive` (scripted / real-service / replay), `emits` (protocol, ports, peer
  role, cadence, volume) and `requires` (peer roles). `GET /api/personas`
  serves all of it (`Persona` in `ui/src/api/types.ts`).
- **Copy with renaming.** Subtree duplicate (`edit/subtree.ts`
  `duplicateSubtree`, `copyPositions`) already copies a group with a name
  prefix and fresh CIDRs. It is the engine for dropping templates.
- **Marquee select.** `marqueeSubtree` in `Canvas.tsx` already selects a
  section of a twin.
- **Connect by dragging and free layout** (manual design phase 1, a separate
  change) make the devices this panel drops wireable.

## 3. The panel

A docked panel (the current palette slot), top to bottom:

1. **Search.** One box over devices, roles, templates and tools. "file
   server", "AD", "plc", "office" each go straight to the right card, with
   the role preselected.
2. **Devices** (§4).
3. **Templates** (§5): Built in · My templates · From the team.
4. **Tools:** Internet, Sensor / tap, Attacker (§6).
5. **Recent and favourites:** the last 6 items added and pinned items.
6. **Design health line** at the bottom: hints only, never a Check, e.g.
   "2 roles missing peers · 1 segment with no router".

Every card can be **dragged** onto the canvas or **clicked**. A click drops
it on the selected segment, else at the centre of the view.
**Double-click on empty canvas** opens the same search as a quick-add
popup at the pointer.

**Removed:** Images, the raw persona list, and "From other twins" (its use
case is covered by templates, §5, and by Open → Clone).

## 4. Devices

Five cards, each with an icon and one line of explanation:

| Card | Writes | Options |
|---|---|---|
| **Router / firewall** | `kind: router` | Type: router or firewall. Placement: edge (internet uplink) or internal. Legs come from drawing links. |
| **Switch** | `kind: switch` | Plain, or managed (VLANs). |
| **Server** | `kind: host` + a server role | OS: Linux or Windows Server. Role (§4.1). |
| **Workstation** | `kind: host` + an endpoint profile | OS: Linux or Windows. Profile: office user, developer, executive, power user, VoIP softphone, IoT, … |
| **OT device** | `kind: host` + an OT role | PLC, HMI, historian, protocol gateway. |

Every card shares the same quick settings:

- **count** (×N, written as `count`);
- **name prefix** (instances named `{prefix}-{n}`);
- **segment** (set automatically when dropped on one).

### 4.1 The drop popover

On drop, a small popover opens at the drop point. It is the same form the
inspector shows, just in context. Esc or a click outside keeps the defaults.

- **OS:** a segmented control.
- **Role / profile:** grouped by category and filtered by the chosen OS.
  Each row shows a label and one plain sentence built from the persona's
  `emits`, e.g. "serves SMB/NFS shares to workstations; bursty in business
  hours".
- **Needs:** when the role's `requires` names peers that aren't on the
  canvas, show "needs workstations" with a one-click **Add 5
  workstations** (a Workstation card pre-set to the required profile,
  dropped on the same segment).
- **Advanced** (collapsed): the resolved image, which can be overridden; the
  address; traffic intensity and schedule (persona knobs).

Role categories:

- Identity & infrastructure: domain controller, DNS/DHCP
- Business apps: file, mail, web, database, backup
- Network services: proxy, VPN, bastion, monitoring
- Endpoints: the workstation profiles
- OT/ICS: PLC, HMI, historian, protocol gateway
- Traffic generators: `loadgen-*`, `xseg-*`. Hidden by default, shown under
  Advanced.

### 4.2 Where the category, label and blurb come from (D1)

Add three optional fields to each persona file:

- `category`: one of the groups above;
- `label`: the human name ("File server");
- `blurb`: one sentence.

A missing `category` falls back to `kind: traffic` when the id starts with
`loadgen-` or `xseg-`, else to "Other".

This follows the plugin rule in Claude.md: the persona library is a
registry, and the UI lists whatever is installed. The metadata lives with
the persona, not in a UI table.

### 4.3 OS to image (D2)

The twin-config needs an `image` on every host and router. The canvas writes
it from a mapping, `catalog/image-profiles.yaml`:

```yaml
profiles:
  linux-server:     { image: debian-12-cloud@v1 }
  linux-client:     { image: ubuntu-desktop-22@v1 }   # example
  windows-server:   { image: windows-server-2022@v1 }
  windows-client:   { image: win11-finance@v4 }       # example
  router:           { image: <the router image the catalog's routers use> }
```

- The profile is chosen from card + OS (Server + Windows → `windows-server`).
- A later phase may key profiles per target platform. The canvas never
  shows an image unless the user opens Advanced.
- It is served by a new read-only `GET /api/image-profiles`. Unknown
  images (not in the target's image manifest) show a warning on Check, as
  today.

## 5. Templates

A template is a slice of a twin-config, plus relative positions, plus
settings:

- **Slice:** segments, nodes, links, conduits, taps, persona assignments.
- **Positions:** relative to the template's top-left, so it drops where it
  lands.
- **Settings:** values the user fills in at drop time.

```yaml
template:
  id: office-segment
  label: Office segment
  description: A user LAN with workstations and a printer, behind one switch.
  icon: office
  params:
    - { name: workstations, label: Workstations, type: int, default: 20, min: 1, max: 500 }
    - { name: windows_share, label: "Windows %", type: percent, default: 80 }
    - { name: prefix, label: Name prefix, type: name, default: office }
    - { name: subnet, label: Subnet, type: cidr, default: auto }
  ports:                      # open connections the user wires after dropping
    - { name: uplink, device: "{prefix}-sw" }
  fragment:                   # twin-config vocabulary, with {param} substitution
    segments: [...]
    nodes: [...]
    links: [...]
  layout: { "{prefix}-sw": { x: 0, y: 0 }, ... }
```

### 5.1 Built-in set (first cut)

| Template | Contents | Settings |
|---|---|---|
| Office segment | switch, N workstations, printer | workstations, Windows %, prefix, subnet |
| Datacenter rack | ToR switch, web ×N, app ×N, db ×N | tier counts, prefix, subnet |
| DMZ | firewall, web, reverse proxy | prefix, subnet |
| AD domain | domain controller, file server, N workstations | workstations, prefix |
| Branch office | edge router, switch, N users | users, prefix |
| Three-tier web app | web, app, db, each on its own segment, one router | counts |
| OT cell | protocol gateway, PLC ×N, HMI, historian | PLCs, prefix |
| Internet edge | edge router, proxy, internet sink | prefix |

### 5.2 Dropping a template

1. The card shows a small preview of the shape and the device counts.
2. On drop, a settings form opens at the drop point.
3. The fragment is expanded with the settings, then names are prefixed and
   CIDRs allocated through the duplicate-subtree engine.
4. The result lands as **one undo step**, expanded, with its open ports
   highlighted.
5. Each component can then be configured like any other.

### 5.3 Save a selection as a template (D3)

1. On any twin (discovered, cast or authored), marquee-select a section.
2. Right-click → **Save as template…**
3. The dialog asks for name, description and icon, then shows **what becomes
   a setting**:
   - each node group's count → an int setting ("web servers: N");
   - the segments' CIDRs → `subnet: auto`;
   - the common name stem → `prefix`;
   - discovered names (`web-tier-10-74-20-1`) get a suggested clean name
     ("web-tier"), editable.
4. **Stripped:** observed addresses, `source` / `confidence` / `vantage`
   provenance, evidence, and taps bound to sensors outside the selection.
   **Kept:** structure, roles, OS, conduits inside the selection.
5. Links leaving the selection become **ports**.
6. Saved through the API to `catalog/templates/<id>/template.yaml`. It
   appears under **My templates** at once.

Storage and API:

- `GET /api/templates` (built-in + catalog), `GET /api/templates/{id}`,
  `POST /api/templates`, `PUT /api/templates/{id}`,
  `DELETE /api/templates/{id}`.
- Same auth (write token) and path-safety rules as twin-configs.
- Built-in templates ship as files in `catalog/templates/` marked
  `builtin: true` (read-only through the API).
- "From the team" is the same catalog read by everyone. No sharing model
  beyond that in this design.
- A template is validated on save: its fragment expanded with default
  settings and wrapped in a minimal twin must pass the twin-config validator.

## 6. Tools

- **Internet:** an internet-sink/uplink a router can be wired to (the
  concept tier-cast's `internet-sink` already uses).
- **Sensor / tap:** drop on a segment or device to add a tap binding there
  (existing `addTap`).
- **Attacker:** a host with the attack framework's agent role, for the
  inject step. Shown only when an attack framework is installed (the
  registry).

## 7. What does not change

- The twin-config schema. Every drop writes today's vocabulary (`kind`,
  `image`, `count`, `nets`, persona assignment).
- Check stays on demand.
- Save rules and drafts stay as built on 2026-11-20.
- Tree layout for cast twins. Authored designs use the free layout (manual
  design phase 1).

## 8. Decisions for sign-off

- **D1:** add `category`, `label`, `blurb` (optional) to persona files and
  serve them in `GET /api/personas`.
- **D2:** new `catalog/image-profiles.yaml` and read-only
  `GET /api/image-profiles`; the canvas resolves images from card + OS.
- **D3:** new `catalog/templates/` and the templates API (§5.3).
- **D4:** remove Images and "From other twins" from the panel; traffic
  generators hidden under Advanced.

## 9. Phases

1. **Devices.** The new panel shell (search, Devices, Tools, Recent), the
   drop popover, OS → role filtering, the needs hints, and image
   resolution. Needs D1, D2, D4.
2. **Built-in templates.** The template format, the drop-time settings form,
   and expansion through the duplicate-subtree engine. Ships the §5.1 set as
   read-only files. Needs D3's read side (GET only).
3. **Save as template.** Marquee → dialog → `POST /api/templates`, and My
   templates. Needs D3 in full.

## 10. Acceptance

- **Automated (phase 1):**
  - the panel shows exactly five device cards, Tools, Recent, and no
    Images or "From other twins";
  - search "file" → Server with role file-server preselected;
  - Windows filters roles to `os_family: windows`;
  - dropping a Server writes a host with the profile image and the persona;
  - a role with unmet `requires` shows the needs hint, and its button adds
    the peers.
- **Automated (phase 2):**
  - each built-in template expands with default settings into a document
    that passes the twin-config validator;
  - a drop is one undo step;
  - names carry the prefix and CIDRs don't collide.
- **Automated (phase 3):**
  - Save as template from a cast twin strips provenance and observed
    addresses;
  - counts become settings and outgoing links become ports;
  - the template round-trips (save → drop) to the same structure.
- **Live (eyes):** on an empty canvas, a user builds a branch office, an AD
  domain and a DMZ from the panel, wires them with drag-to-connect, and
  passes Check without opening a YAML file or the Advanced section.
