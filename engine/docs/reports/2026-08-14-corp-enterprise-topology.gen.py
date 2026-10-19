#!/usr/bin/env python3
"""Generate the corp-enterprise + O2 topology artifact. Coordinates computed on
a grid so the hand-SVG can't drift into overlap (no render-to-check available).
Data is the live dev45 inventory read this session."""
import html

OUT = "/home/rocky/axRange/docs/reports/2026-08-14-corp-enterprise-topology.html"

# --- live data (read from `te inventory` + qm config, 2026-08-14) ---
VLANS = [
    ("corp-lan", "10.62.0.0/24", [
        ("attacker", ".8", "exr foothold · impacket"),
        ("corp-workstation ×6", ".2–.7", "win11 · domain-joined"),
    ]),
    ("server-vlan", "10.62.10.0/24", [
        ("domain-controller", ".2", "WS2022 · corp.axr.lab"),
        ("file-server", ".3", "ubuntu · crown-jewel PII"),
    ]),
    ("dmz", "10.62.20.0/24", [
        ("mail-server", ".2", "postfix"),
        ("egress-proxy", ".3", "squid"),
        ("dmz-web", ".4", "nginx"),
    ]),
    ("mgmt", "10.62.30.0/24", [
        ("monitoring-collector", ".2", "prometheus"),
    ]),
]
SINKS = [
    ("XPress-SW-A", "vmid 104 · .21", "ACTIVE tap target · queues=8", True),
    ("XPress-SW", "vmid 103 · .20", "idle sink", False),
    ("Tcpdump-SW-A", "vmid 260 · .12", "idle sink", False),
    ("o2-suricata", "vmid 883610", "network IDS", False),
]

# --- grid ---
W = 1080
COLX = [156, 402, 648, 894]          # VLAN column centers
HB_W = 188                            # host/header box width
HDR_Y, HDR_H = 196, 46               # VLAN header row
H_Y0, H_H, H_GAP = 254, 42, 13       # host rows
CAP_LBL_Y = 436
BUS_Y = 494                           # transport vnet bus
SINK_Y, SINK_H = 520, 50
SINKX = [188, 424, 654, 888]
SINK_W = 196
O2_Y, O2_H = 606, 54
svg = []


def esc(s):
    return html.escape(str(s))


def box(x, y, w, h, cls, rx=7):
    svg.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" '
               f'rx="{rx}" class="{cls}"/>')


def txt(x, y, s, cls, anchor="middle"):
    svg.append(f'<text x="{x:.0f}" y="{y:.0f}" text-anchor="{anchor}" '
               f'class="{cls}">{esc(s)}</text>')


def line(x1, y1, x2, y2, cls, marker=""):
    m = f' marker-end="url(#{marker})"' if marker else ""
    svg.append(f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
               f'class="{cls}"{m}/>')


def poly(pts, cls, marker=""):
    m = f' marker-end="url(#{marker})"' if marker else ""
    p = " ".join(f"{x:.0f},{y:.0f}" for x, y in pts)
    svg.append(f'<polyline points="{p}" class="{cls}" fill="none"{m}/>')


# ---- WAN cloud ----
box(64, 44, 150, 40, "cloud")
txt(139, 62, "Internet / WAN", "lbl-b")
txt(139, 76, "vmbr0 uplink", "mono-s")

# ---- gw router (the multi-homed hub) ----
GW_X, GW_Y, GW_W, GW_H = 330, 112, 420, 48
box(GW_X, GW_Y, GW_W, GW_H, "router")
txt(GW_X + GW_W / 2, GW_Y + 20, "gw · router (kind: router) · vmid 184429", "lbl-b")
txt(GW_X + GW_W / 2, GW_Y + 36, ".1 on every VLAN · WAN + NAT on vmbr0", "mono-s")
# WAN -> gw
poly([(139, 84), (139, 100), (330, 100), (GW_X, GW_Y + 8)], "wire", "arrow")
txt(232, 96, "egress / NAT", "mono-s")

# ---- VLAN columns ----
for i, (name, cidr, hosts) in enumerate(VLANS):
    cx = COLX[i]
    hx = cx - HB_W / 2
    # header
    box(hx, HDR_Y, HB_W, HDR_H, "seg")
    txt(cx, HDR_Y + 19, name, "seg-name")
    txt(cx, HDR_Y + 35, cidr + "  · gw .1", "mono-s")
    # gw -> header trunk
    gx = GW_X + (i + 0.5) * (GW_W / 4)
    line(gx, GW_Y + GW_H, cx, HDR_Y, "wire")
    # column bus + host boxes
    last_y = HDR_Y + HDR_H
    for j, (n, ip, tag) in enumerate(hosts):
        y = H_Y0 + j * (H_H + H_GAP)
        line(cx, last_y, cx, y, "wire")          # bus segment
        box(hx, y, HB_W, H_H, "node")
        txt(cx, y + 18, n, "lbl", )
        txt(cx, y + 33, ip + "  " + tag, "mono-s")
        last_y = y + H_H
    # tap drop point (accent) from the column down toward the bus
    tap_y = 408
    line(cx, last_y, cx, tap_y, "wire")
    svg.append(f'<circle cx="{cx}" cy="{tap_y}" r="4" class="cap-dot"/>')
    poly([(cx, tap_y), (cx, BUS_Y)], "cap", "caparrow")

# ---- capture-plane label ----
txt(W / 2, CAP_LBL_Y, "Capture plane · Option 1 · per-tap  tc  mirror (rx) → keyed gretap → flow-based sink",
    "cap-lbl")

# ---- transport vnet bus ----
line(96, BUS_Y, 984, BUS_Y, "cap")
txt(96, BUS_Y - 9, "transport vnet  o2v7dc98 · 10.255.251.0/24 · isolated (no scenario traffic; PVE host .1)",
    "cap-lbl", anchor="start")

# ---- sinks on the transport bus ----
active_cx = None
for i, (n, meta, tag, active) in enumerate(SINKS):
    cx = SINKX[i]
    sx = cx - SINK_W / 2
    cls = "sink-active" if active else "sink"
    line(cx, BUS_Y, cx, SINK_Y, "cap" if active else "muted")
    box(sx, SINK_Y, SINK_W, SINK_H, cls)
    txt(cx, SINK_Y + 19, n, "lbl-b" if active else "lbl")
    txt(cx, SINK_Y + 34, meta, "mono-s")
    txt(cx, SINK_Y + 46, tag, "cap-lbl" if active else "mono-s")
    if active:
        active_cx = cx

# ---- O2 services ----
O2X, O2W = 300, 480
box(O2X, O2_Y, O2W, O2_H, "o2")
txt(O2X + O2W / 2, O2_Y + 20, "O2 services · VM 102 · vmbr0", "lbl-b")
txt(O2X + O2W / 2, O2_Y + 37, "Prometheus · Loki · Grafana · Caldera · Wazuh", "mono-s")
# telemetry from sinks -> o2-services
poly([(active_cx, SINK_Y + SINK_H), (active_cx, O2_Y - 12),
      (O2X + O2W / 2, O2_Y - 12), (O2X + O2W / 2, O2_Y)], "muted", "arrow")
txt((active_cx + O2X + O2W / 2) / 2, O2_Y - 16, "ship: pcap + IDS alerts → Loki / Prometheus", "mono-s")

# ---- legend ----
LX, LY = 792, 92
svg.append(f'<line x1="{LX}" y1="{LY}" x2="{LX+26}" y2="{LY}" class="cap"/>')
txt(LX + 32, LY + 4, "live capture / mirror path", "leg", anchor="start")
svg.append(f'<line x1="{LX}" y1="{LY+18}" x2="{LX+26}" y2="{LY+18}" class="wire"/>')
txt(LX + 32, LY + 22, "production network", "leg", anchor="start")
svg.append(f'<line x1="{LX}" y1="{LY+36}" x2="{LX+26}" y2="{LY+36}" class="muted"/>')
txt(LX + 32, LY + 40, "idle sink / management", "leg", anchor="start")

VB_H = O2_Y + O2_H + 24
SVG = (f'<svg viewBox="0 0 {W} {VB_H:.0f}" role="img" '
       f'aria-label="Topology of the corp-enterprise digital twin and the O2 surface on dev45: '
       f'four VLAN segments behind the multi-homed gw router, each mirrored rx-only over an isolated '
       f'transport vnet to the XPress-SW-A capture sink, alongside the idle sinks, the o2-suricata IDS '
       f'and the O2 services stack.">'
       '<defs>'
       '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
       'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="ah"/></marker>'
       '<marker id="caparrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
       'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="ah-cap"/></marker>'
       '</defs>' + "".join(svg) + '</svg>')

PAGE = f"""<title>corp-enterprise Topology</title>
<style>
:root {{
  color-scheme: light;
  --page:#f7f8f9; --surface:#fcfcfb; --ink:#14171a; --muted:#6b7280;
  --border:rgba(20,23,26,0.22); --seg:#eef1f4; --o2:#f0f3f6;
  --accent:#2a78d6; --accent-soft:#dbe9fb;
  --sans:system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --page:#0e0f11; --surface:#191c1f; --ink:#e8eaed; --muted:#98a0aa;
    --border:rgba(255,255,255,0.24); --seg:#1e2329; --o2:#1b2026;
    --accent:#4f9bef; --accent-soft:#1d3a5f;
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --page:#0e0f11; --surface:#191c1f; --ink:#e8eaed; --muted:#98a0aa;
  --border:rgba(255,255,255,0.24); --seg:#1e2329; --o2:#1b2026;
  --accent:#4f9bef; --accent-soft:#1d3a5f;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--page); color:var(--ink);
  font-family:var(--sans); line-height:1.55; -webkit-font-smoothing:antialiased; }}
.wrap {{ max-width:1120px; margin:0 auto; padding:44px 24px 72px; }}
.eyebrow {{ font-family:var(--mono); font-size:11px; letter-spacing:.13em;
  text-transform:uppercase; color:var(--muted); }}
h1 {{ margin:6px 0 6px; font-size:clamp(26px,3.6vw,36px); letter-spacing:-0.02em;
  line-height:1.12; text-wrap:balance; }}
.sub {{ margin:0 0 26px; color:var(--muted); max-width:70ch; font-size:15.5px; }}
figure {{ margin:0; background:var(--surface); border:1px solid var(--border);
  border-radius:12px; padding:20px 20px 10px; }}
.figscroll {{ overflow-x:auto; }}
svg {{ width:100%; min-width:880px; height:auto; display:block; color:var(--ink); }}
figcaption {{ color:var(--muted); font-size:13px; margin-top:14px; padding-top:12px;
  border-top:1px solid var(--border); }}
/* SVG element classes (defined here, applied to SVG nodes — none inside the svg fragment) */
.cloud {{ fill:var(--surface); stroke:var(--muted); stroke-width:1.2; stroke-dasharray:5 3; }}
.router {{ fill:var(--accent-soft); stroke:var(--accent); stroke-width:1.6; }}
.seg {{ fill:var(--seg); stroke:var(--border); stroke-width:1.2; }}
.node {{ fill:var(--surface); stroke:var(--border); stroke-width:1.2; }}
.o2 {{ fill:var(--o2); stroke:var(--border); stroke-width:1.4; }}
.sink {{ fill:var(--surface); stroke:var(--muted); stroke-width:1.2; stroke-dasharray:4 3; }}
.sink-active {{ fill:var(--accent-soft); stroke:var(--accent); stroke-width:1.8; }}
.wire {{ stroke:currentColor; stroke-width:1.3; opacity:.55; fill:none; }}
.cap {{ stroke:var(--accent); stroke-width:2.1; fill:none; }}
.cap-dot {{ fill:var(--accent); stroke:none; }}
.muted {{ stroke:var(--muted); stroke-width:1.3; stroke-dasharray:4 3; fill:none; opacity:.8; }}
.ah {{ fill:currentColor; opacity:.6; }}
.ah-cap {{ fill:var(--accent); }}
text {{ font-family:var(--sans); }}
.lbl {{ fill:currentColor; font-size:13px; }}
.lbl-b {{ fill:currentColor; font-size:13px; font-weight:600; }}
.seg-name {{ fill:currentColor; font-size:13.5px; font-weight:700; letter-spacing:.02em; }}
.mono-s {{ fill:var(--muted); font-family:var(--mono); font-size:10.5px; }}
.cap-lbl {{ fill:var(--accent); font-size:11.5px; font-weight:600; }}
.leg {{ fill:var(--muted); font-size:11.5px; }}
.notes {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(250px,1fr));
  gap:14px; margin-top:22px; }}
.note {{ background:var(--surface); border:1px solid var(--border); border-radius:10px;
  padding:14px 16px; }}
.note h3 {{ margin:0 0 5px; font-size:13.5px; }}
.note p {{ margin:0; color:var(--muted); font-size:13px; }}
.inl {{ font-family:var(--mono); font-size:12px;
  background:color-mix(in srgb, var(--ink) 7%, transparent); padding:1px 5px; border-radius:4px; }}
footer {{ color:var(--muted); font-size:12px; font-family:var(--mono);
  margin-top:26px; padding-top:14px; border-top:1px solid var(--border); }}
</style>

<div class="wrap">
  <div class="eyebrow">dev45 · standing environment · 2026-08-14</div>
  <h1>corp-enterprise twin &amp; the O2 surface</h1>
  <p class="sub">The live topology of deploy <span class="inl">corp-enterprise-890c29b2</span>:
  four VLAN segments behind one multi-homed router, every segment mirrored rx-only over an
  isolated transport network to a single capture sink, beside the standing O2 observability nodes.</p>

  <figure>
    <div class="figscroll">{SVG}</div>
    <figcaption>Solid grey lines are the production network (SDN VLAN vnets behind the
    <span class="inl">gw</span> router, which also NATs to the WAN on <span class="inl">vmbr0</span>).
    The blue path is the live capture plane: a per-tap <span class="inl">tc</span> mirror on each
    segment feeds a keyed gretap that carries frames over the isolated <span class="inl">o2v7dc98</span>
    transport vnet to <span class="inl">XPress-SW-A</span> — the one sink the standing tap plan
    (<span class="inl">tap-…-c9cab1c4</span>, all four segments) binds today. The other sinks sit on
    the same transport net but are unbound; the O2 services VM collects telemetry over management.</figcaption>
  </figure>

  <div class="notes">
    <div class="note"><h3>Production plane · the twin</h3><p>14 VMs: the
    <span class="inl">gw</span> router multi-homed onto corp-lan / server-vlan / dmz / mgmt (a
    <span class="inl">.1</span> leg on each) plus a WAN uplink. Windows AD domain
    <span class="inl">corp.axr.lab</span> on the DC; 6 domain-joined workstations + a LAN attacker
    foothold on corp-lan.</p></div>
    <div class="note"><h3>Capture plane · Option 1</h3><p>Each segment's ports are mirrored
    <em>rx-only</em> by <span class="inl">tc</span> into a per-(bridge,sensor) keyed gretap whose
    qdisc is a deep FIFO (adapter-managed). All four converge on <span class="inl">XPress-SW-A</span>
    at <span class="inl">10.255.251.21</span>, measured lossless to ~2.6&nbsp;Gbit/s.</p></div>
    <div class="note"><h3>Traffic &amp; exercise (transient)</h3><p>A <span class="inl">tfc</span>
    profile drives realistic personas on the hosts; the AD kill-chain <span class="inl">exr</span>
    (recon → roast → DCSync) runs from the attacker against the DC when deployed. Both bind to this
    twin and are torn down independently of it.</p></div>
  </div>

  <footer>Source: live <span style="font-family:var(--mono)">te inventory corp-enterprise-890c29b2</span>
  + <span style="font-family:var(--mono)">qm config</span>, dev45, 2026-08-14. Workstation fleet
  collapsed (×6). Transport IPs on o2v7dc98 (10.255.251.0/24).</footer>
</div>
"""

open(OUT, "w").write(PAGE)
print("wrote", OUT, len(PAGE), "bytes; viewBox H =", VB_H)
# overlap sanity: print column host-box x-extents
for i, cx in enumerate(COLX):
    print(f"  col{i} x {cx-HB_W/2:.0f}..{cx+HB_W/2:.0f}")
for i, cx in enumerate(SINKX):
    print(f"  sink{i} x {cx-SINK_W/2:.0f}..{cx+SINK_W/2:.0f}")
