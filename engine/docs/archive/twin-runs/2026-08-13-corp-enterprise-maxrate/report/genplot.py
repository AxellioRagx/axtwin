#!/usr/bin/env python3
"""Generate the capture-rate report page. Geometry is computed, not hand-typed."""
import html

RUNGS = [
    # label, persona, senders, window_s, offered, captured, pps, gretap, sink
    ("R1", "loadgen-l1",  3,  26.3,   14.3,   14.1,   1233,  0,     0),
    ("R2", "loadgen-l2",  3,  26.4,   22.5,   22.5,   1962,  0,     0),
    ("R3", "loadgen-l3",  3,  26.4,  628.3,  621.8,  53902,  0,     0),
    ("R4", "loadgen-l4",  3, 240.5, 1521.1, 1521.1, 131868, 10,     0),
    ("R4b", "+loadgen-corp", 4, 121.4, 2368.8, 2367.3, 205349, 2, 5965),
    ("R5", "+loadgen-srv",  5,  90.5, 2665.2, 2659.5, 231041, 2, 31733),
]

# Post-multiqueue (XPress-SW-A net1 queues=8, applied 2026-08-13). Same twin,
# same sink, same deep-FIFO gretap qdisc -- only the sink NIC queue count changed.
# label, senders, window_s, offered, captured, pps, tc_overlimits, gretap_drop, sink_drop
POST = [
    ("R4",  3, 120.5, 1538.9, 1541.5, 133629,      0,  6, 0),
    ("R5",  5, 150.5, 2583.1, 2585.8, 224158,   1605, 10, 0),
    ("R7", 12, 121.5, 2748.4, 2713.7, 235250, 325446,  3, 0),
]


def totals(r):
    _, _, _, win, _, _, pps, gt, sk = r
    tot = pps * win
    loss = gt + sk
    return tot, loss, (loss / tot * 100.0) if tot else 0.0


def fmt(n, dp=0):
    return f"{n:,.{dp}f}"


def bar_path(x, y, w, h, r=4):
    """Bar with rounded data-end, square base (anchored to baseline)."""
    if h <= 0:
        return ""
    r = min(r, h, w / 2)
    return (f"M{x:.1f},{y+h:.1f} L{x:.1f},{y+r:.1f} Q{x:.1f},{y:.1f} {x+r:.1f},{y:.1f} "
            f"L{x+w-r:.1f},{y:.1f} Q{x+w:.1f},{y:.1f} {x+w:.1f},{y+r:.1f} "
            f"L{x+w:.1f},{y+h:.1f} Z")


def chart(vals, labels, tips, *, w, h, vmax, series="--series-1", aria="Chart",
          fmt_label=lambda v: f"{v:,.0f}", pad_l=8, pad_r=8, min_px=0,
          ticks=4, tick_fmt=lambda v: f"{v:,.0f}"):
    """One column chart. Returns SVG markup."""
    top, bot = 26, 42
    plot_h = h - top - bot
    x0, x1 = pad_l + 54, w - pad_r
    band = (x1 - x0) / len(vals)
    bw = min(46, band * 0.52)
    out = []
    # gridlines + y ticks (recessive hairlines, never dashed)
    for i in range(ticks + 1):
        gv = vmax * i / ticks
        gy = top + plot_h - (gv / vmax * plot_h if vmax else 0)
        out.append(f'<line class="grid" x1="{x0}" y1="{gy:.1f}" x2="{x1}" y2="{gy:.1f}"/>')
        out.append(f'<text class="tick ytick" x="{x0-10}" y="{gy+3.5:.1f}">{tick_fmt(gv)}</text>')
    out.append(f'<line class="axis" x1="{x0}" y1="{top+plot_h}" x2="{x1}" y2="{top+plot_h}"/>')
    for i, v in enumerate(vals):
        cx = x0 + band * i + band / 2
        bx = cx - bw / 2
        bh = (v / vmax * plot_h) if vmax else 0
        if v > 0 and bh < min_px:
            bh = min_px
        by = top + plot_h - bh
        if bh > 0:
            out.append(
                f'<path class="bar" style="fill:var({series})" d="{bar_path(bx, by, bw, bh)}" '
                f'tabindex="0" data-tip="{html.escape(tips[i])}"/>')
        lab = fmt_label(v)
        out.append(f'<text class="vlabel" x="{cx:.1f}" y="{by-7:.1f}">{lab}</text>')
        out.append(f'<text class="tick xtick" x="{cx:.1f}" y="{top+plot_h+18:.1f}">{labels[i]}</text>')
    return (f'<svg viewBox="0 0 {w} {h}" role="group" aria-label="{aria}" preserveAspectRatio="xMidYMid meet">'
            + "".join(out) + "</svg>")


labels = [r[0] for r in RUNGS]

# --- chart A: captured throughput -------------------------------------------
capt = [r[5] for r in RUNGS]
tipsA = [f"{r[0]} · {r[1]} · {r[2]} senders — captured {fmt(r[5],1)} Mbit/s "
         f"(offered {fmt(r[4],1)}), {fmt(r[6])} pps, {r[3]:.0f}s window" for r in RUNGS]
chartA = chart(capt, labels, tipsA, w=430, h=250, vmax=2800,
               fmt_label=lambda v: f"{v:,.0f}", ticks=4, min_px=2,
               aria="Captured throughput in Mbit per second, by load rung")

# --- chart B: packet rate ----------------------------------------------------
pps = [r[6] for r in RUNGS]
tipsB = [f"{r[0]} — {fmt(r[6])} pps over {r[3]:.0f}s "
         f"(~{fmt(totals(r)[0]/1e6,1)}M packets)" for r in RUNGS]
chartB = chart(pps, labels, tipsB, w=430, h=250, vmax=250000,
               fmt_label=lambda v: f"{v/1000:,.0f}k",
               tick_fmt=lambda v: f"{v/1000:,.0f}k", ticks=4, min_px=2,
               aria="Packet rate in packets per second, by load rung")

# --- chart C: loss ------------------------------------------------------------
lossp = [totals(r)[2] for r in RUNGS]
tipsC = []
for r in RUNGS:
    tot, loss, pct = totals(r)
    tipsC.append(f"{r[0]} — {fmt(loss)} lost of {fmt(tot)} packets ({pct:.5f}%) · "
                 f"gretap {r[7]}, sink {fmt(r[8])}")


def loss_label(v):
    if v == 0:
        return "0"
    if v < 0.001:
        return f"{v:.5f}%"
    return f"{v:.3f}%"


chartC = chart(lossp, labels, tipsC, w=880, h=250, vmax=0.16, series="--series-2",
               fmt_label=loss_label, min_px=2, ticks=4,
               tick_fmt=lambda v: f"{v:.2f}%",
               aria="Packet loss as a percentage of packets carried, by load rung")

# --- chart D: the qdisc fix, same 90s window at rung 4b -----------------------
FIX = [("fq_codel\n(node default)", 9353, 18068866, "--series-1-soft"),
       ("pfifo 20000\n(applied)",       0, 18663272, "--series-1")]
dw, dh = 880, 190
dtop, dbot = 26, 46
dplot = dh - dtop - dbot
dx0, dx1 = 62, dw - 8
dvmax = 10000
dparts = []
for i in range(3):
    gv = dvmax * i / 2
    gy = dtop + dplot - gv / dvmax * dplot
    dparts.append(f'<line class="grid" x1="{dx0}" y1="{gy:.1f}" x2="{dx1}" y2="{gy:.1f}"/>')
    dparts.append(f'<text class="tick ytick" x="{dx0-10}" y="{gy+3.5:.1f}">{gv:,.0f}</text>')
dparts.append(f'<line class="axis" x1="{dx0}" y1="{dtop+dplot}" x2="{dx1}" y2="{dtop+dplot}"/>')
dband = (dx1 - dx0) / 2
for i, (nm, lost, tot, ser) in enumerate(FIX):
    cx = dx0 + dband * i + dband / 2
    bw = 120
    bh = lost / dvmax * dplot
    if lost == 0:
        bh = 0
    by = dtop + dplot - bh
    if bh > 0:
        dparts.append(f'<path class="bar" style="fill:var({ser})" d="{bar_path(cx-bw/2, by, bw, bh)}" '
                      f'tabindex="0" data-tip="{html.escape(nm.replace(chr(10)," "))} — '
                      f'{lost:,} packets lost of {tot:,}"/>')
    dparts.append(f'<text class="vlabel" x="{cx:.1f}" y="{by-7:.1f}">'
                  f'{"0 lost" if lost==0 else f"{lost:,} lost"}</text>')
    for j, line in enumerate(nm.split("\n")):
        dparts.append(f'<text class="tick xtick" x="{cx:.1f}" y="{dtop+dplot+18+j*14:.1f}">{line}</text>')
chartD = (f'<svg viewBox="0 0 {dw} {dh}" role="group" aria-label="Packets lost at rung 4b before and after replacing the gretap queueing discipline" preserveAspectRatio="xMidYMid meet">'
          + "".join(dparts) + "</svg>")

# --- chart E: multiqueue before/after, sink drops at ~2.6 Gbit/s ----------------
MQ = [("single queue\n(Combined: 1)", 31733, "--series-2"),
      ("queues=8\n(Combined: 8)",         0, "--series-1")]
ew, eh = 880, 190
etop, ebot = 26, 46
eplot = eh - etop - ebot
ex0, ex1 = 70, ew - 8
evmax = 35000
eparts = []
for i in range(3):
    gv = evmax * i / 2
    gy = etop + eplot - gv / evmax * eplot
    eparts.append(f'<line class="grid" x1="{ex0}" y1="{gy:.1f}" x2="{ex1}" y2="{gy:.1f}"/>')
    eparts.append(f'<text class="tick ytick" x="{ex0-10}" y="{gy+3.5:.1f}">{gv:,.0f}</text>')
eparts.append(f'<line class="axis" x1="{ex0}" y1="{etop+eplot}" x2="{ex1}" y2="{etop+eplot}"/>')
eband = (ex1 - ex0) / 2
for i, (nm, lost, ser) in enumerate(MQ):
    cx = ex0 + eband * i + eband / 2
    bw = 120
    bh = lost / evmax * eplot
    by = etop + eplot - bh
    if bh > 0:
        eparts.append(f'<path class="bar" style="fill:var({ser})" d="{bar_path(cx-bw/2, by, bw, bh)}" '
                      f'tabindex="0" data-tip="{html.escape(nm.replace(chr(10), " "))} — '
                      f'{lost:,} packets dropped at the sink vhost"/>')
    eparts.append(f'<text class="vlabel" x="{cx:.1f}" y="{by-7:.1f}">'
                  f'{"0 dropped" if lost == 0 else f"{lost:,} dropped"}</text>')
    for j, line in enumerate(nm.split("\n")):
        eparts.append(f'<text class="tick xtick" x="{cx:.1f}" y="{etop+eplot+18+j*14:.1f}">{line}</text>')
chartE = (f'<svg viewBox="0 0 {ew} {eh}" role="group" aria-label="Sink packet drops at 2.6 gigabit per second, single queue versus eight queues" preserveAspectRatio="xMidYMid meet">'
          + "".join(eparts) + "</svg>")

# --- table --------------------------------------------------------------------
rows = []
for r in RUNGS:
    tot, loss, pct = totals(r)
    state = "clean" if loss == 0 else ("trace" if pct < 0.001 else "loss")
    chip = {"clean": "good", "trace": "warning", "loss": "critical"}[state]
    word = {"clean": "clean", "trace": "trace", "loss": "loss"}[state]
    rows.append(f"""<tr>
<th scope="row">{r[0]}</th><td>{r[1]}</td><td class="n">{r[2]}</td><td class="n">{r[3]:.0f}</td>
<td class="n">{fmt(r[4],1)}</td><td class="n">{fmt(r[5],1)}</td><td class="n">{fmt(r[6])}</td>
<td class="n">{fmt(tot)}</td><td class="n">{r[7]}</td><td class="n">{fmt(r[8])}</td>
<td class="n">{pct:.5f}%</td>
<td><span class="chip chip-{chip}">{word}</span></td></tr>""")
table = "\n".join(rows)

prows = []
for lbl, snd, win, off, cap, pps_, ovl, gt, sk in POST:
    tot = pps_ * win
    loss = ovl + gt + sk
    pct = loss / tot * 100
    chip = "good" if loss == 0 else ("warning" if pct < 0.01 else "critical")
    word = "clean" if loss == 0 else ("trace" if pct < 0.01 else "loss")
    prows.append(f"""<tr><th scope="row">{lbl}</th><td class="n">{snd}</td>
<td class="n">{win:.0f}</td><td class="n">{off:,.1f}</td><td class="n">{cap:,.1f}</td>
<td class="n">{pps_:,}</td><td class="n">{tot:,.0f}</td><td class="n">{ovl:,}</td>
<td class="n">{gt}</td><td class="n">{sk}</td><td class="n">{pct:.5f}%</td>
<td><span class="chip chip-{chip}">{word}</span></td></tr>""")
ptable = "\n".join(prows)

PAGE = f"""<title>Capture Ceiling</title>
<style>
:root {{
  color-scheme: light;
  --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink-2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,0.10);
  --series-1:#2a78d6; --series-1-soft:#9ec5f4; --series-2:#eb6834;
  --good:#0ca30c; --warning:#fab219; --critical:#d03b3b; --good-ink:#006300;
  --sans:system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,0.10);
    --series-1:#3987e5; --series-1-soft:#1c5cab; --series-2:#d95926;
    --good-ink:#0ca30c;
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,0.10);
  --series-1:#3987e5; --series-1-soft:#1c5cab; --series-2:#d95926;
  --good-ink:#0ca30c;
}}
* {{ box-sizing:border-box; }}
body {{
  margin:0; background:var(--page); color:var(--ink);
  font-family:var(--sans); line-height:1.55;
  -webkit-font-smoothing:antialiased;
}}
.wrap {{ max-width:1000px; margin:0 auto; padding:48px 24px 80px; display:flex; flex-direction:column; gap:34px; }}
header {{ display:flex; flex-direction:column; gap:10px; }}
.eyebrow {{ font-family:var(--mono); font-size:11px; letter-spacing:.13em; text-transform:uppercase; color:var(--muted); }}
h1 {{ margin:0; font-size:clamp(30px,4.4vw,44px); line-height:1.1; letter-spacing:-0.02em; text-wrap:balance; }}
.sub {{ margin:0; color:var(--ink-2); max-width:62ch; font-size:16px; }}
h2 {{ margin:0; font-size:19px; letter-spacing:-0.01em; }}
.note {{ margin:0; color:var(--ink-2); font-size:14px; max-width:78ch; }}
.card {{ background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:22px 24px; display:flex; flex-direction:column; gap:14px; }}
.kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:14px; }}
.kpi {{ background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:18px 20px; display:flex; flex-direction:column; gap:5px; }}
.kpi .k {{ font-family:var(--mono); font-size:11px; letter-spacing:.1em; text-transform:uppercase; color:var(--muted); }}
.kpi .v {{ font-size:32px; font-weight:600; letter-spacing:-0.02em; line-height:1.05; }}
.kpi .v small {{ font-size:15px; font-weight:500; color:var(--ink-2); margin-left:3px; }}
.kpi .d {{ font-size:13px; color:var(--ink-2); }}
.two {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(330px,1fr)); gap:14px; }}
svg {{ width:100%; height:auto; display:block; overflow:visible; }}
.grid {{ stroke:var(--grid); stroke-width:1; }}
.axis {{ stroke:var(--axis); stroke-width:1; }}
.tick {{ font-family:var(--mono); font-size:11px; fill:var(--muted); }}
.ytick {{ text-anchor:end; }}
.xtick {{ text-anchor:middle; }}
.vlabel {{ font-family:var(--mono); font-size:11.5px; fill:var(--ink-2); text-anchor:middle; }}
.bar {{ transition:opacity .12s ease; }}
.bar:hover, .bar:focus {{ opacity:.78; outline:none; }}
.bar:focus-visible {{ stroke:var(--ink); stroke-width:2; }}
.tblwrap {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-size:13px; min-width:820px; }}
th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--border); white-space:nowrap; }}
thead th {{ font-family:var(--mono); font-size:10.5px; letter-spacing:.07em; text-transform:uppercase; color:var(--muted); font-weight:500; }}
tbody th {{ font-family:var(--mono); font-weight:600; }}
td.n {{ font-family:var(--mono); font-variant-numeric:tabular-nums; text-align:right; }}
.chip {{ display:inline-flex; align-items:center; gap:5px; font-family:var(--mono); font-size:11px; padding:2px 8px; border-radius:999px; border:1px solid var(--border); }}
.chip::before {{ content:""; width:7px; height:7px; border-radius:50%; }}
.chip-good::before {{ background:var(--good); }}
.chip-warning::before {{ background:var(--warning); }}
.chip-critical::before {{ background:var(--critical); }}
.bl {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:14px; }}
.bl .card h3 {{ margin:0; font-size:15px; }}
.bl code, .inline {{ font-family:var(--mono); font-size:12.5px; background:color-mix(in srgb, var(--ink) 6%, transparent); padding:1px 5px; border-radius:4px; }}
pre {{ margin:0; font-family:var(--mono); font-size:12px; background:color-mix(in srgb, var(--ink) 5%, transparent); padding:12px 14px; border-radius:8px; overflow-x:auto; color:var(--ink-2); }}
#tip {{ position:fixed; z-index:40; pointer-events:none; opacity:0; transition:opacity .1s ease;
  background:var(--surface); color:var(--ink); border:1px solid var(--border); border-radius:7px;
  padding:7px 10px; font-family:var(--mono); font-size:11.5px; max-width:320px;
  box-shadow:0 6px 22px rgba(0,0,0,.16); }}
footer {{ color:var(--muted); font-size:12.5px; font-family:var(--mono); border-top:1px solid var(--border); padding-top:16px; }}
@media (prefers-reduced-motion: reduce) {{ * {{ transition:none !important; }} }}
</style>

<div class="wrap">
<header>
  <div class="eyebrow">dev45 · corp-enterprise twin · sink XPress-SW-A · 2026-08-13</div>
  <h1>How fast can the capture plane run before it drops a packet?</h1>
  <p class="sub">Six load rungs mirrored from a live 14-node AD twin through per-tap
  <span class="inline">tc</span> &rarr; keyed gretap &rarr; an external packetXpress sink,
  with every stage of the chain instrumented for drops.</p>
</header>

<div class="kpis">
  <div class="kpi"><div class="k">Sustained, sink clean</div><div class="v">1,521<small>Mbit/s</small></div><div class="d">240 s window, rung R4</div></div>
  <div class="kpi"><div class="k">Packet rate</div><div class="v">131,868<small>pps</small></div><div class="d">~31.7 M packets observed</div></div>
  <div class="kpi"><div class="k">Sink-path loss</div><div class="v" style="color:var(--good-ink)">0</div><div class="d">vhost, NIC and decap all clean</div></div>
  <div class="kpi"><div class="k">Sink drops @2.6 Gbit/s</div><div class="v"><span style="color:var(--series-2)">31,733</span> &rarr; <span style="color:var(--good-ink)">0</span></div><div class="d">after enabling NIC multiqueue</div></div>
</div>

<section class="card">
  <h2>Throughput and packet rate per rung</h2>
  <p class="note">Two measures, two charts &mdash; never one plot with two y-scales. Captured
  throughput tracks offered load to within 0.3% at every rung, so the interesting
  variable is not <em>how much arrived</em> but <em>what it cost to arrive</em>.
  R1&ndash;R2 are deliberately tiny: they exist to prove the harness reads a small
  known load before it is trusted with a large one.</p>
  <div class="two">
    <div>
      <div class="eyebrow" style="margin-bottom:6px">Captured at gre-cap &mdash; Mbit/s</div>
      {chartA}
    </div>
    <div>
      <div class="eyebrow" style="margin-bottom:6px">Packet rate &mdash; pps</div>
      {chartB}
    </div>
  </div>
</section>

<section class="card">
  <h2>Where the packets go missing</h2>
  <p class="note">Loss as a share of packets carried, which is the only fair comparison
  when the windows differ (26 s to 240 s). Three rungs are exactly zero. R4's bar is
  drawn at a 2 px minimum so it is visible at all &mdash; its real value is
  <strong>10 packets in 31.7 million</strong>, and all ten were at the host's gretap,
  none at the sink. Every drop at R4b and R5 is the opposite: the sink's virtio
  receive ring, with the host side clean.</p>
  {chartC}
  <p class="note"><strong>The gretap drops are not rate-correlated</strong> &mdash; 10 at
  1.5 Gbit/s over 240 s versus 2 at 2.6 Gbit/s over 90 s. That argues against congestion
  and points at a per-frame property, most likely oversized/GSO frames from background
  twin traffic exceeding the tunnel MTU. Unconfirmed.</p>
</section>

<section class="card">
  <h2>The qdisc was worth 60% of the ceiling</h2>
  <p class="note">The gretap inherited <span class="inline">fq_codel</span>, the node's
  default queueing discipline &mdash; an AQM that <em>deliberately drops</em> to hold latency
  down. On a capture path, where the entire point is that every frame survives, that is a
  silent loss source. Same rung, same 90 s window, only the qdisc changed:</p>
  {chartD}
  <pre>ip link set dev tapg_63256a txqueuelen 20000
tc qdisc replace dev tapg_63256a root pfifo limit 20000</pre>
  <p class="note">Applied by hand, and it <strong>does not survive a tap re-apply</strong>:
  axRange creates the gretap and leaves the node default on it. Re-assert after every
  <span class="inline">tap deploy</span>, or fix it in the adapter. Honest caveat: this
  cleaned the 90 s window shown, but longer 120 s windows at R4b still found loss &mdash;
  which is precisely why the headline number is R4 and not R4b.</p>
</section>

<section class="card">
  <h2>Multiqueue moved the wall off the sink</h2>
  <p class="note">The sink's capture NIC was single-queue &mdash; <span class="inline">net1</span>
  carried no <span class="inline">queues=</span>, so a 32-core appliance ran every mirrored
  frame through one virtio receive ring. Setting <span class="inline">queues=8</span>
  hot-applied with no reboot and no loss of the static transport address:</p>
  {chartE}
  <p class="note">Same rung, same ~2.6 Gbit/s, same deep-FIFO gretap qdisc &mdash; only the
  queue count changed. The sink went from <strong>the</strong> bottleneck to clean at every
  rate this twin can produce. What is left is a different constraint entirely: the
  <span class="inline">tc mirred</span>&rarr;gretap handoff, which starts logging
  <span class="inline">overlimits</span> past ~2.6 Gbit/s &mdash; all 17 tapped ports funnel
  into one single-queue tunnel netdev.</p>
  <div class="tblwrap">
  <table>
    <thead><tr>
      <th scope="col">Rung</th><th scope="col">Send</th><th scope="col">Win s</th>
      <th scope="col">Offered</th><th scope="col">Captured</th><th scope="col">pps</th>
      <th scope="col">Packets</th><th scope="col">tc overlim</th><th scope="col">gretap</th>
      <th scope="col">sink</th><th scope="col">Loss</th><th scope="col">State</th>
    </tr></thead>
    <tbody>{ptable}</tbody>
  </table>
  </div>
  <p class="note"><strong>A counter that lies:</strong> the harness also differences tc's
  mirrored-packet count against the gretap's transmitted count. Ignore it below ~1%. The two
  are read in separate SSH round-trips, so at 230k pps a fifth of a second of skew invents
  ~46,000 phantom &ldquo;missing&rdquo; frames &mdash; and at R4 the figure came out
  <em>negative</em>, which is impossible and neatly proves the point.
  <span class="inline">overlimits</span> is the countable signal.</p>
</section>

<section class="card">
  <h2>Every measurement &mdash; before multiqueue</h2>
  <p class="note">Offered is measured at the twin's VM tap interfaces; captured at the
  sink's <span class="inline">gre-cap</span> decap device. Loss is the sum across all six
  instrumented stages.</p>
  <div class="tblwrap">
  <table>
    <thead><tr>
      <th scope="col">Rung</th><th scope="col">Persona</th><th scope="col">Send</th>
      <th scope="col">Win s</th><th scope="col">Offered</th><th scope="col">Captured</th>
      <th scope="col">pps</th><th scope="col">Packets</th><th scope="col">gretap</th>
      <th scope="col">sink</th><th scope="col">Loss</th><th scope="col">State</th>
    </tr></thead>
    <tbody>{table}</tbody>
  </table>
  </div>
</section>

<section>
  <h2 style="margin-bottom:14px">What actually limits it</h2>
  <div class="bl">
    <div class="card">
      <h3>1 &middot; Sink NIC &mdash; fixed</h3>
      <p class="note">Was the wall above ~2.4 Gbit/s: a 32-core box seeing
      <span class="inline">Combined: 1</span>, with every drop on
      <span class="inline">tap104i1</span> (host&rarr;guest). Now
      <span class="inline">queues=8</span>, hot-applied, 8 rx + 8 tx queues host-side. The
      sink has not dropped a packet since, at any rate this twin can reach.
      <strong>Now the binding constraint is the single-queue gretap</strong> that all 17
      tapped ports mirror into.</p>
    </div>
    <div class="card">
      <h3>2 &middot; Generation, below 2.4 Gbit/s</h3>
      <p class="note">The persona driver's rendered client is a single-threaded loop, so one
      instance pegs exactly one core &mdash; measured at 94.1%. Offered load scales by adding
      <em>sending hosts</em>, never by raising <span class="inline">volume:</span> on one host.
      R1&rarr;R4 was generation-bound; the capture path was never the constraint until R4b.</p>
    </div>
  </div>
</section>

<footer>Measured with a 6-stage drop-accounting harness: VM tap ifaces &rarr; tc mirror
&rarr; gretap tx &rarr; sink vhost &rarr; sink NIC rx &rarr; gre-cap decap. Counters read
from /proc/net/dev and tc filter statistics. Twin corp-enterprise-890c29b2,
plan corp-enterprise-4fc62c80.</footer>
</div>

<div id="tip" role="status" aria-live="polite"></div>
<script>
(function () {{
  var tip = document.getElementById('tip');
  function show(e, t) {{
    tip.textContent = t; tip.style.opacity = '1';
    var r = tip.getBoundingClientRect();
    var x = Math.min(Math.max(8, e.clientX + 14), window.innerWidth - r.width - 8);
    var y = Math.max(8, e.clientY - r.height - 12);
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  }}
  function hide() {{ tip.style.opacity = '0'; }}
  document.querySelectorAll('[data-tip]').forEach(function (el) {{
    el.addEventListener('mousemove', function (e) {{ show(e, el.dataset.tip); }});
    el.addEventListener('mouseleave', hide);
    el.addEventListener('focus', function () {{
      var b = el.getBoundingClientRect();
      show({{clientX: b.left + b.width / 2, clientY: b.top}}, el.dataset.tip);
    }});
    el.addEventListener('blur', hide);
  }});
}})();
</script>
"""

open("/tmp/claude-1000/-home-rocky-axRange/31a1ccac-036f-4766-a595-2493d1b8bd63/scratchpad/capture-rate.html", "w").write(PAGE)
print("written", len(PAGE), "bytes")
for r in RUNGS:
    tot, loss, pct = totals(r)
    print(f"  {r[0]:<4} {r[5]:>8.1f} Mbit/s  {r[6]:>7,} pps  {tot:>12,.0f} pkts  loss {loss:>6,} = {pct:.5f}%")
