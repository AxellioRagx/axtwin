#!/usr/bin/env python3
"""corp-enterprise TWIN-CONFIG blueprint diagram (from the YAML, not a live
deploy). Distinct from the deployed view: N_WORKSTATIONS=25 default, and NO
capture/tap/O2/traffic/exercise plane — a twin config authors none of that;
it shows the declared infra + apps + the identity & data overlays instead.
Coordinates computed on a grid (no render-to-check available)."""
import html

OUT = "/home/rocky/axRange/docs/reports/2026-08-14-corp-enterprise-config-topology.html"

# --- straight from twin-configs/corp-enterprise/corp-enterprise.yaml ---
VLANS = [
    ("corp-lan", "10.62.0.0/24", "vlan", [
        ("attacker", "debian-12-cloud · offense kit", "nmap/impacket/hashcat/john", False),
        ("corp-workstation ×25", "win11-finance · joins corp.axr.lab", "N_WORKSTATIONS", False),
    ]),
    ("server-vlan", "10.62.10.0/24", "vlan", [
        ("domain-controller", "windows-server-2022 · adds", "hosts corp.axr.lab forest", "dc"),
        ("file-server", "ubuntu-server-22 · samba", "+ customer-pii (crown-jewel)", "data"),
    ]),
    ("dmz", "10.62.20.0/24", "vlan", [
        ("mail-server", "ubuntu-server-22 · postfix", "", False),
        ("egress-proxy", "debian-12-cloud · squid", "", False),
        ("dmz-web", "ubuntu-server-22 · nginx", "", False),
    ]),
    ("mgmt", "10.62.30.0/24", "vlan", [
        ("monitoring-collector", "debian-12-cloud · prometheus", "", False),
    ]),
]
# identity overlay accounts: (id, detail, attack tag or None)
ACCTS = [
    ("alice", "Domain Users · OU Finance", None),
    ("bob-admin", "Domain Admins · OU IT", "DCSync / domain-admin objective"),
    ("svc-sql", "SPN MSSQLSvc/sql01…:1433 · Summer2024!", "Kerberoastable"),
    ("jsmith", "asrep_roastable · Password123 · OU Finance", "AS-REP roastable"),
]

W = 1080
COLX = [156, 402, 648, 894]
HB_W = 190
HDR_Y, HDR_H = 190, 46
H_Y0, H_H, H_GAP = 250, 50, 12
svg = []


def esc(s):
    return html.escape(str(s))


def box(x, y, w, h, cls, rx=7):
    svg.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" rx="{rx}" class="{cls}"/>')


def txt(x, y, s, cls, anchor="middle"):
    svg.append(f'<text x="{x:.0f}" y="{y:.0f}" text-anchor="{anchor}" class="{cls}">{esc(s)}</text>')


def line(x1, y1, x2, y2, cls, marker=""):
    m = f' marker-end="url(#{marker})"' if marker else ""
    svg.append(f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" class="{cls}"{m}/>')


def poly(pts, cls, marker=""):
    m = f' marker-end="url(#{marker})"' if marker else ""
    p = " ".join(f"{x:.0f},{y:.0f}" for x, y in pts)
    svg.append(f'<polyline points="{p}" class="{cls}" fill="none"{m}/>')


# WAN (adapter-injected for kind: router)
box(64, 44, 174, 40, "cloud")
txt(151, 61, "Internet / WAN", "lbl-b")
txt(151, 75, "adapter-injected on vmbr0", "mono-s")

GW_X, GW_Y, GW_W, GW_H = 330, 110, 420, 48
box(GW_X, GW_Y, GW_W, GW_H, "router")
txt(GW_X + GW_W / 2, GW_Y + 20, "gw · kind: router · debian-12-cloud", "lbl-b")
txt(GW_X + GW_W / 2, GW_Y + 36, "4 VLAN legs (.1 each) · WAN egress + NAT", "mono-s")
poly([(151, 84), (151, 98), (330, 98), (GW_X, GW_Y + 8)], "wire", "arrow")
txt(240, 94, "egress", "mono-s")

for i, (name, cidr, zt, hosts) in enumerate(VLANS):
    cx = COLX[i]
    hx = cx - HB_W / 2
    box(hx, HDR_Y, HB_W, HDR_H, "seg")
    txt(cx, HDR_Y + 19, name, "seg-name")
    txt(cx, HDR_Y + 35, f"{cidr} · zone {zt}", "mono-s")
    gx = GW_X + (i + 0.5) * (GW_W / 4)
    line(gx, GW_Y + GW_H, cx, HDR_Y, "wire")
    last_y = HDR_Y + HDR_H
    for (n, sub, tag, kind) in hosts:
        y = H_Y0 + (hosts.index((n, sub, tag, kind))) * (H_H + H_GAP)
        line(cx, last_y, cx, y, "wire")
        box(hx, y, HB_W, H_H, "node-hot" if kind else "node")
        txt(cx, y + 17, n, "lbl-b")
        txt(cx, y + 31, sub, "mono-s")
        if tag:
            txt(cx, y + 44, tag, "hot-lbl" if kind else "mono-s")
        last_y = y + H_H

# ---- overlays band ----
OV_Y = 452
txt(60, OV_Y, "Overlays the config authors — identity + data (no capture / traffic / attack intent)",
    "sec", anchor="start")

# identity panel
IX, IY, IW, IH = 60, OV_Y + 16, 660, 150
box(IX, IY, IW, IH, "panel")
txt(IX + 16, IY + 24, "Identity overlay · AD domain  corp.axr.lab", "panel-h", anchor="start")
txt(IX + 16, IY + 41, "hosted on domain-controller (adds) · 25 corp-workstations domain-joined",
    "mono-s", anchor="start")
ry = IY + 62
for (uid, detail, atk) in ACCTS:
    hot = atk is not None
    svg.append(f'<circle cx="{IX+24}" cy="{ry-4}" r="3.2" class="{"hot-dot" if hot else "dot"}"/>')
    txt(IX + 38, ry, uid, "lbl-b", anchor="start")
    txt(IX + 150, ry, detail, "mono-s", anchor="start")
    if atk:
        txt(IX + IW - 16, ry, "◂ " + atk, "hot-lbl", anchor="end")
    ry += 21

# data panel
DX, DY, DW, DH = 740, OV_Y + 16, 280, 96
box(DX, DY, DW, DH, "panel")
txt(DX + 16, DY + 24, "Data overlay", "panel-h", anchor="start")
svg.append(f'<circle cx="{DX+22}" cy="{DY+44}" r="3.2" class="hot-dot"/>')
txt(DX + 36, DY + 48, "customer-pii", "lbl-b", anchor="start")
txt(DX + 16, DY + 66, "PII · crown-jewel · 2 GiB synthetic", "hot-lbl", anchor="start")
txt(DX + 16, DY + 82, "on file-server (server-vlan)", "mono-s", anchor="start")

# tiers-are-separate note panel
TY = DY + DH + 14
box(DX, TY, DW, 40, "panel-dim")
txt(DX + 16, TY + 17, "Separate tier specs (not shown):", "mono-s", anchor="start")
txt(DX + 16, TY + 31, "tap-*.yaml · tfc-*.yaml · exr-*.yaml", "mono-s", anchor="start")

# legend
LX, LY = 792, 96
svg.append(f'<line x1="{LX}" y1="{LY}" x2="{LX+24}" y2="{LY}" class="wire"/>')
txt(LX + 30, LY + 4, "network & hosts", "leg", anchor="start")
svg.append(f'<circle cx="{LX+12}" cy="{LY+18}" r="3.4" class="hot-dot"/>')
txt(LX + 30, LY + 22, "seeded attack surface (purple-team)", "leg", anchor="start")

VB_H = IY + IH + 26
SVG = (f'<svg viewBox="0 0 {W} {VB_H:.0f}" role="img" '
       f'aria-label="Blueprint of the corp-enterprise twin config: four VLAN segments behind a '
       f'multi-homed router with WAN egress, nine node types with their images and installed apps '
       f'(25 domain-joined workstations, a domain controller, servers and a LAN attacker), and the '
       f'identity and data overlays the config authors — an AD domain with deliberately-vulnerable '
       f'accounts (Kerberoastable, AS-REP-roastable, domain-admin) and crown-jewel PII on the file '
       f'server. No capture, traffic or attack tiers, which are separate specs.">'
       '<defs>'
       '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
       'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="ah"/></marker>'
       '</defs>' + "".join(svg) + '</svg>')

PAGE = f"""<title>corp-enterprise Blueprint</title>
<style>
:root {{
  color-scheme: light;
  --page:#f7f8f9; --surface:#fcfcfb; --ink:#14171a; --muted:#6b7280;
  --border:rgba(20,23,26,0.22); --seg:#eef1f4; --panel:#f2f4f7; --panel-dim:#f7f8f9;
  --accent:#b1461f; --accent-soft:#f6e4dc;
  --sans:system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --page:#0e0f11; --surface:#191c1f; --ink:#e8eaed; --muted:#98a0aa;
    --border:rgba(255,255,255,0.24); --seg:#1e2329; --panel:#1b1f24; --panel-dim:#15181c;
    --accent:#e58259; --accent-soft:#3a2117;
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --page:#0e0f11; --surface:#191c1f; --ink:#e8eaed; --muted:#98a0aa;
  --border:rgba(255,255,255,0.24); --seg:#1e2329; --panel:#1b1f24; --panel-dim:#15181c;
  --accent:#e58259; --accent-soft:#3a2117;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--page); color:var(--ink);
  font-family:var(--sans); line-height:1.55; -webkit-font-smoothing:antialiased; }}
.wrap {{ max-width:1120px; margin:0 auto; padding:44px 24px 72px; }}
.eyebrow {{ font-family:var(--mono); font-size:11px; letter-spacing:.13em;
  text-transform:uppercase; color:var(--muted); }}
h1 {{ margin:6px 0 6px; font-size:clamp(26px,3.6vw,36px); letter-spacing:-0.02em;
  line-height:1.12; text-wrap:balance; }}
.sub {{ margin:0 0 26px; color:var(--muted); max-width:72ch; font-size:15.5px; }}
figure {{ margin:0; background:var(--surface); border:1px solid var(--border);
  border-radius:12px; padding:20px 20px 10px; }}
.figscroll {{ overflow-x:auto; }}
svg {{ width:100%; min-width:900px; height:auto; display:block; color:var(--ink); }}
figcaption {{ color:var(--muted); font-size:13px; margin-top:14px; padding-top:12px;
  border-top:1px solid var(--border); }}
.cloud {{ fill:var(--surface); stroke:var(--muted); stroke-width:1.2; stroke-dasharray:5 3; }}
.router {{ fill:var(--seg); stroke:currentColor; stroke-width:1.5; }}
.seg {{ fill:var(--seg); stroke:var(--border); stroke-width:1.2; }}
.node {{ fill:var(--surface); stroke:var(--border); stroke-width:1.2; }}
.node-hot {{ fill:var(--surface); stroke:var(--accent); stroke-width:1.6; }}
.panel {{ fill:var(--panel); stroke:var(--border); stroke-width:1.2; }}
.panel-dim {{ fill:var(--panel-dim); stroke:var(--border); stroke-width:1; stroke-dasharray:4 3; }}
.wire {{ stroke:currentColor; stroke-width:1.3; opacity:.55; fill:none; }}
.ah {{ fill:currentColor; opacity:.6; }}
.dot {{ fill:var(--muted); }}
.hot-dot {{ fill:var(--accent); }}
text {{ font-family:var(--sans); }}
.lbl-b {{ fill:currentColor; font-size:12.5px; font-weight:600; }}
.seg-name {{ fill:currentColor; font-size:13.5px; font-weight:700; letter-spacing:.02em; }}
.mono-s {{ fill:var(--muted); font-family:var(--mono); font-size:10px; }}
.hot-lbl {{ fill:var(--accent); font-size:10.5px; font-weight:600; }}
.sec {{ fill:currentColor; font-size:13px; font-weight:600; }}
.panel-h {{ fill:currentColor; font-size:13px; font-weight:700; }}
.leg {{ fill:var(--muted); font-size:11.5px; }}
.notes {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(250px,1fr));
  gap:14px; margin-top:22px; }}
.note {{ background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:14px 16px; }}
.note h3 {{ margin:0 0 5px; font-size:13.5px; }}
.note p {{ margin:0; color:var(--muted); font-size:13px; }}
.inl {{ font-family:var(--mono); font-size:12px;
  background:color-mix(in srgb, var(--ink) 7%, transparent); padding:1px 5px; border-radius:4px; }}
footer {{ color:var(--muted); font-size:12px; font-family:var(--mono);
  margin-top:26px; padding-top:14px; border-top:1px solid var(--border); }}
</style>

<div class="wrap">
  <div class="eyebrow">twin config · blueprint (not a live deploy) · 2026-08-14</div>
  <h1>corp-enterprise — as configured</h1>
  <p class="sub">Drawn from <span class="inl">twin-configs/corp-enterprise/corp-enterprise.yaml</span>
  alone: the default <span class="inl">N_WORKSTATIONS=25</span>, every declared node with its image
  and app, and the identity + data overlays. A twin config authors <em>no</em> capture, traffic, or
  attack intent — those are separate tier specs — so unlike the deployed view, there is no tap plane
  or O2 surface here.</p>

  <figure>
    <div class="figscroll">{SVG}</div>
    <figcaption>The production plane (grey) is the config's <span class="inl">segments</span> +
    <span class="inl">nodes</span>: four VLAN zones behind the multi-homed <span class="inl">gw</span>
    router, whose WAN NIC is adapter-injected for <span class="inl">kind: router</span> (the config
    declares four VLAN legs and WAN-egress intent, not the NIC). The accent marks the seeded
    purple-team attack surface the overlays bake in — a deliberately-weak AD domain
    (a Kerberoastable service account, an AS-REP-roastable user, a domain-admin DCSync objective) and
    crown-jewel PII on the file server. IPs are auto-allocated at expand time and so are not shown.</figcaption>
  </figure>

  <div class="notes">
    <div class="note"><h3>Infra + apps (config-declared)</h3><p>9 node types: a
    <span class="inl">gw</span> router, a Windows DC (<span class="inl">adds</span>), Linux file /
    mail / web / proxy / monitoring servers each with one <span class="inl">install:</span> package,
    25 Win11 workstations that <span class="inl">joins:</span> the domain, and a Debian attacker with
    an apt offense kit.</p></div>
    <div class="note"><h3>Overlays (config-authored)</h3><p><b>identity</b> — the
    <span class="inl">corp.axr.lab</span> forest with four accounts, three deliberately weak;
    <b>data</b> — <span class="inl">customer-pii</span>, a 2&nbsp;GiB synthetic crown-jewel on the
    file server. These are what a config <em>does</em> seed.</p></div>
    <div class="note"><h3>Not in a twin config</h3><p>Capture (tap plans), traffic (persona
    profiles), and attack (exercise specs) are <em>separate</em> tiers — <span class="inl">tap-*</span>,
    <span class="inl">tfc-*</span>, <span class="inl">exr-*</span> in this folder — bound to a
    <em>deployed</em> twin, never authored in the config. That separation is the 2026-07-28
    tap-eligibility model.</p></div>
  </div>

  <footer>Source: twin-configs/corp-enterprise/corp-enterprise.yaml (blueprint). Compare with
  2026-08-14-corp-enterprise-topology.html — the same twin deployed (N=6) with its live tap + O2 plane.</footer>
</div>
"""

open(OUT, "w").write(PAGE)
print("wrote", OUT, len(PAGE), "bytes; viewBox H =", VB_H)
