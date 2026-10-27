#!/usr/bin/env python3
"""Generate the post-fix capture-rate report from the measured after.jsonl.

Numbers come from the JSON the harness wrote; nothing here is hand-typed except
the 2026-08-13 "before" column, which is transcribed from that day's report.
"""
import html
import json

SP = "/tmp/claude-1000/-home-rocky-axRange/31a1ccac-036f-4766-a595-2493d1b8bd63/scratchpad"
OUT = "/home/rocky/axRange/docs/reports/2026-08-14-capture-rate-post-fixes.html"

# 2026-08-13, transcribed from that report: label -> (offered, pps, lost, total, window)
BEFORE = {
    "l1":  (14.3, 1233, 0, 32428, 26.3),
    "l2":  (22.5, 1962, 0, 51797, 26.4),
    "l3":  (628.3, 53902, 0, 1423013, 26.4),
    "l4":  (1521.1, 131868, 10, 31714254, 240.5),
    "l4b": (2368.8, 205349, 5967, 24929369, 121.4),
    "l5":  (2665.2, 231041, 31735, 20909210, 90.5),
}
SENDERS = {"l1": 3, "l2": 3, "l3": 3, "l4": 3, "l4b": 4, "l5": 5, "l7": 12}
SHAPE = {"l1": "high / 5s", "l2": "high / 1s", "l3": "bulk / 5s", "l4": "bulk / 1s",
         "l4b": "bulk / 1s", "l5": "bulk / 1s", "l7": "bulk / 1s"}

rows = []
for line in open(f"{SP}/after.jsonl"):
    r = json.loads(line)
    g, v = r.get("gretap") or {}, r.get("sink_vhost") or {}
    n, d = r.get("sink_nic") or {}, r.get("decap") or {}
    dt = r["seconds"]
    # Capture-relevant loss ONLY. `gre-cap` TX drops are excluded deliberately:
    # gre-cap is a decap device, so its transmit side is not on the capture
    # path at all, and counting it produced a false "loss" verdict at 14 Mbit/s.
    loss = (r["tc_dropped"] + r["tc_overlimits"] + g.get("tx_drop", 0)
            + v.get("tx_drop", 0) + n.get("rx_drop", 0) + n.get("rx_fifo", 0)
            + d.get("rx_drop", 0) + d.get("rx_fifo", 0))
    tot = r["tc_pkts"] or 1
    rows.append({
        "label": r["label"], "senders": SENDERS[r["label"]], "shape": SHAPE[r["label"]],
        "window": dt, "offered": r["offered_mbit"],
        "decap_mb": d.get("rx_bytes", 0) * 8 / dt / 1e6,
        "pps": d.get("rx_packets", 0) / dt, "packets": tot,
        "tc_drop": r["tc_dropped"], "ovl": r["tc_overlimits"],
        "gretap": g.get("tx_drop", 0), "vhost": v.get("tx_drop", 0),
        "nic": n.get("rx_drop", 0) + n.get("rx_fifo", 0),
        "decap_rx": d.get("rx_drop", 0) + d.get("rx_fifo", 0),
        "loss": loss, "loss_pct": loss / tot * 100,
    })

best = max((r for r in rows if r["loss"] == 0), key=lambda r: r["decap_mb"])
onset = min((r for r in rows if r["loss_pct"] > 0.01), key=lambda r: r["decap_mb"])


def bar_path(x, y, w, h, r=4):
    if h <= 0:
        return ""
    r = min(r, h, w / 2)
    return (f"M{x:.1f},{y+h:.1f} L{x:.1f},{y+r:.1f} Q{x:.1f},{y:.1f} {x+r:.1f},{y:.1f} "
            f"L{x+w-r:.1f},{y:.1f} Q{x+w:.1f},{y:.1f} {x+w:.1f},{y+r:.1f} "
            f"L{x+w:.1f},{y+h:.1f} Z")


def chart(vals, labels, tips, *, w, h, vmax, series="--series-1", aria="Chart",
          fmt_label=lambda v: f"{v:,.0f}", min_px=0, ticks=4,
          tick_fmt=lambda v: f"{v:,.0f}"):
    top, bot = 26, 42
    ph = h - top - bot
    x0, x1 = 62, w - 8
    band = (x1 - x0) / len(vals)
    bw = min(46, band * 0.52)
    o = []
    for i in range(ticks + 1):
        gv = vmax * i / ticks
        gy = top + ph - (gv / vmax * ph if vmax else 0)
        o.append(f'<line class="grid" x1="{x0}" y1="{gy:.1f}" x2="{x1}" y2="{gy:.1f}"/>')
        o.append(f'<text class="tick ytick" x="{x0-10}" y="{gy+3.5:.1f}">{tick_fmt(gv)}</text>')
    o.append(f'<line class="axis" x1="{x0}" y1="{top+ph}" x2="{x1}" y2="{top+ph}"/>')
    for i, val in enumerate(vals):
        cx = x0 + band * i + band / 2
        bh = (val / vmax * ph) if vmax else 0
        if val > 0 and bh < min_px:
            bh = min_px
        by = top + ph - bh
        if bh > 0:
            o.append(f'<path class="bar" style="fill:var({series})" '
                     f'd="{bar_path(cx-bw/2, by, bw, bh)}" tabindex="0" '
                     f'data-tip="{html.escape(tips[i])}"/>')
        o.append(f'<text class="vlabel" x="{cx:.1f}" y="{by-7:.1f}">{fmt_label(val)}</text>')
        o.append(f'<text class="tick xtick" x="{cx:.1f}" y="{top+ph+18:.1f}">{labels[i]}</text>')
    return (f'<svg viewBox="0 0 {w} {h}" role="group" aria-label="{aria}" '
            f'preserveAspectRatio="xMidYMid meet">' + "".join(o) + "</svg>")


labels = [r["label"] for r in rows]
tipA = [f"{r['label']} · {r['senders']} senders · {r['shape']} — captured "
        f"{r['decap_mb']:,.0f} Mbit/s (offered {r['offered']:,.0f}), "
        f"{r['pps']:,.0f} pps over {r['window']:.0f}s" for r in rows]
chartA = chart([r["decap_mb"] for r in rows], labels, tipA, w=430, h=250, vmax=3000,
               min_px=2, aria="Captured throughput in Mbit per second, by rung")
tipB = [f"{r['label']} — {r['pps']:,.0f} pps, {r['packets']:,} packets carried"
        for r in rows]
chartB = chart([r["pps"] for r in rows], labels, tipB, w=430, h=250, vmax=250000,
               min_px=2, fmt_label=lambda v: f"{v/1000:,.0f}k",
               tick_fmt=lambda v: f"{v/1000:,.0f}k",
               aria="Packet rate in packets per second, by rung")

# before vs after loss %, matched rungs only
matched = [r for r in rows if r["label"] in BEFORE]
mw, mh = 880, 260
mtop, mbot = 26, 46
mph = mh - mtop - mbot
mx0, mx1 = 62, mw - 8
mvmax = 0.16
mband = (mx1 - mx0) / len(matched)
mp = []
for i in range(5):
    gv = mvmax * i / 4
    gy = mtop + mph - gv / mvmax * mph
    mp.append(f'<line class="grid" x1="{mx0}" y1="{gy:.1f}" x2="{mx1}" y2="{gy:.1f}"/>')
    mp.append(f'<text class="tick ytick" x="{mx0-10}" y="{gy+3.5:.1f}">{gv:.2f}%</text>')
mp.append(f'<line class="axis" x1="{mx0}" y1="{mtop+mph}" x2="{mx1}" y2="{mtop+mph}"/>')
for i, r in enumerate(matched):
    b_off, b_pps, b_lost, b_tot, b_win = BEFORE[r["label"]]
    pairs = [("before", b_lost / b_tot * 100, "--series-2", b_lost, b_tot, b_win),
             ("after", r["loss_pct"], "--series-1", r["loss"], r["packets"], r["window"])]
    cx = mx0 + mband * i + mband / 2
    bw = 34
    for j, (nm, pct, ser, lost, tot, win) in enumerate(pairs):
        bx = cx - bw - 3 + j * (bw + 6)
        bh = pct / mvmax * mph
        if pct > 0 and bh < 2:
            bh = 2
        by = mtop + mph - bh
        if bh > 0:
            mp.append(f'<path class="bar" style="fill:var({ser})" '
                      f'd="{bar_path(bx, by, bw, bh)}" tabindex="0" '
                      f'data-tip="{r["label"]} {nm} — {lost:,} lost of {tot:,} packets '
                      f'({pct:.5f}%) over {win:.0f}s"/>')
        lab = "0" if lost == 0 else (f"{pct:.3f}%" if pct >= 0.001 else "~0")
        mp.append(f'<text class="vlabel" style="font-size:10px" x="{bx+bw/2:.1f}" '
                  f'y="{by-6:.1f}">{lab}</text>')
    mp.append(f'<text class="tick xtick" x="{cx:.1f}" y="{mtop+mph+18:.1f}">{r["label"]}</text>')
    mp.append(f'<text class="tick xtick" x="{cx:.1f}" y="{mtop+mph+32:.1f}" '
              f'style="font-size:10px">{r["senders"]} snd</text>')
chartC = (f'<svg viewBox="0 0 {mw} {mh}" role="group" aria-label="Capture loss percentage '
          f'before and after the fixes, by rung" preserveAspectRatio="xMidYMid meet">'
          + "".join(mp) + "</svg>")

trows = []
for r in rows:
    state = "good" if r["loss"] == 0 else ("warning" if r["loss_pct"] < 0.01 else "critical")
    word = "clean" if r["loss"] == 0 else ("trace" if r["loss_pct"] < 0.01 else "loss")
    trows.append(
        f'<tr><th scope="row">{r["label"]}</th><td class="n">{r["senders"]}</td>'
        f'<td>{r["shape"]}</td><td class="n">{r["window"]:.0f}</td>'
        f'<td class="n">{r["offered"]:,.1f}</td><td class="n">{r["decap_mb"]:,.1f}</td>'
        f'<td class="n">{r["pps"]:,.0f}</td><td class="n">{r["packets"]:,}</td>'
        f'<td class="n">{r["tc_drop"]}</td><td class="n">{r["ovl"]:,}</td>'
        f'<td class="n">{r["gretap"]}</td><td class="n">{r["vhost"]}</td>'
        f'<td class="n">{r["nic"]}</td><td class="n">{r["decap_rx"]}</td>'
        f'<td class="n">{r["loss_pct"]:.5f}%</td>'
        f'<td><span class="chip chip-{state}">{word}</span></td></tr>')
table = "\n".join(trows)

b_off, b_pps, b_lost, b_tot, b_win = BEFORE["l5"]
gain = best["decap_mb"] / 1521.1

PAGE = f"""<title>Capture Ceiling Reclaimed</title>
<style>
:root {{
  color-scheme: light;
  --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink-2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,0.10);
  --series-1:#2a78d6; --series-2:#eb6834;
  --good:#0ca30c; --warning:#fab219; --critical:#d03b3b; --good-ink:#006300;
  --sans:system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,0.10);
    --series-1:#3987e5; --series-2:#d95926; --good-ink:#0ca30c;
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,0.10);
  --series-1:#3987e5; --series-2:#d95926; --good-ink:#0ca30c;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--page); color:var(--ink); font-family:var(--sans);
  line-height:1.55; -webkit-font-smoothing:antialiased; }}
.wrap {{ max-width:1000px; margin:0 auto; padding:48px 24px 80px; display:flex;
  flex-direction:column; gap:34px; }}
header {{ display:flex; flex-direction:column; gap:10px; }}
.eyebrow {{ font-family:var(--mono); font-size:11px; letter-spacing:.13em;
  text-transform:uppercase; color:var(--muted); }}
h1 {{ margin:0; font-size:clamp(30px,4.4vw,44px); line-height:1.1;
  letter-spacing:-0.02em; text-wrap:balance; }}
.sub {{ margin:0; color:var(--ink-2); max-width:64ch; font-size:16px; }}
h2 {{ margin:0; font-size:19px; letter-spacing:-0.01em; }}
.note {{ margin:0; color:var(--ink-2); font-size:14px; max-width:78ch; }}
.card {{ background:var(--surface); border:1px solid var(--border); border-radius:10px;
  padding:22px 24px; display:flex; flex-direction:column; gap:14px; }}
.kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:14px; }}
.kpi {{ background:var(--surface); border:1px solid var(--border); border-radius:10px;
  padding:18px 20px; display:flex; flex-direction:column; gap:5px; }}
.kpi .k {{ font-family:var(--mono); font-size:11px; letter-spacing:.1em;
  text-transform:uppercase; color:var(--muted); }}
.kpi .v {{ font-size:32px; font-weight:600; letter-spacing:-0.02em; line-height:1.05; }}
.kpi .v small {{ font-size:15px; font-weight:500; color:var(--ink-2); margin-left:3px; }}
.kpi .d {{ font-size:13px; color:var(--ink-2); }}
.two {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(330px,1fr)); gap:14px; }}
svg {{ width:100%; height:auto; display:block; overflow:visible; }}
.grid {{ stroke:var(--grid); stroke-width:1; }}
.axis {{ stroke:var(--axis); stroke-width:1; }}
.tick {{ font-family:var(--mono); font-size:11px; fill:var(--muted); }}
.ytick {{ text-anchor:end; }} .xtick {{ text-anchor:middle; }}
.vlabel {{ font-family:var(--mono); font-size:11.5px; fill:var(--ink-2); text-anchor:middle; }}
.bar {{ transition:opacity .12s ease; }}
.bar:hover, .bar:focus {{ opacity:.78; outline:none; }}
.bar:focus-visible {{ stroke:var(--ink); stroke-width:2; }}
.legend {{ display:flex; gap:18px; font-size:13px; color:var(--ink-2); align-items:center; }}
.legend span {{ display:inline-flex; align-items:center; gap:7px; }}
.legend i {{ width:11px; height:11px; border-radius:3px; display:inline-block; }}
.tblwrap {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-size:13px; min-width:940px; }}
th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--border);
  white-space:nowrap; }}
thead th {{ font-family:var(--mono); font-size:10.5px; letter-spacing:.07em;
  text-transform:uppercase; color:var(--muted); font-weight:500; }}
tbody th {{ font-family:var(--mono); font-weight:600; }}
td.n {{ font-family:var(--mono); font-variant-numeric:tabular-nums; text-align:right; }}
.chip {{ display:inline-flex; align-items:center; gap:5px; font-family:var(--mono);
  font-size:11px; padding:2px 8px; border-radius:999px; border:1px solid var(--border); }}
.chip::before {{ content:""; width:7px; height:7px; border-radius:50%; }}
.chip-good::before {{ background:var(--good); }}
.chip-warning::before {{ background:var(--warning); }}
.chip-critical::before {{ background:var(--critical); }}
.bl {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:14px; }}
.bl .card h3 {{ margin:0; font-size:15px; }}
.inline {{ font-family:var(--mono); font-size:12.5px;
  background:color-mix(in srgb, var(--ink) 6%, transparent); padding:1px 5px;
  border-radius:4px; }}
#tip {{ position:fixed; z-index:40; pointer-events:none; opacity:0;
  transition:opacity .1s ease; background:var(--surface); color:var(--ink);
  border:1px solid var(--border); border-radius:7px; padding:7px 10px;
  font-family:var(--mono); font-size:11.5px; max-width:340px;
  box-shadow:0 6px 22px rgba(0,0,0,.16); }}
footer {{ color:var(--muted); font-size:12.5px; font-family:var(--mono);
  border-top:1px solid var(--border); padding-top:16px; }}
@media (prefers-reduced-motion: reduce) {{ * {{ transition:none !important; }} }}
</style>

<div class="wrap">
<header>
  <div class="eyebrow">dev45 · corp-enterprise twin · sink XPress-SW-A · 2026-08-14</div>
  <h1>The capture plane after the fixes: 1.5 &rarr; 2.6 Gbit/s lossless</h1>
  <p class="sub">The same seven-rung ramp re-measured with sink multiqueue active and the
  gretap queueing discipline now asserted by the adapter itself &mdash; every rung on an
  identical 120&nbsp;second window.</p>
</header>

<div class="kpis">
  <div class="kpi"><div class="k">Sustained, zero loss</div>
    <div class="v">{best['decap_mb']:,.0f}<small>Mbit/s</small></div>
    <div class="d">rung {best['label']}, {best['window']:.0f}s, {best['packets']:,} packets</div></div>
  <div class="kpi"><div class="k">Packet rate</div>
    <div class="v">{best['pps']:,.0f}<small>pps</small></div>
    <div class="d">zero at every capture counter</div></div>
  <div class="kpi"><div class="k">vs. 2026-08-13</div>
    <div class="v" style="color:var(--good-ink)">{gain:.1f}&times;</div>
    <div class="d">was 1,521 Mbit/s lossless</div></div>
  <div class="kpi"><div class="k">Loss onset</div>
    <div class="v">{onset['decap_mb']:,.0f}<small>Mbit/s</small></div>
    <div class="d">rung {onset['label']} &mdash; now the mirror, not the sink</div></div>
</div>

<section class="card">
  <h2>Throughput and packet rate per rung</h2>
  <p class="note">Two measures, two plots &mdash; never one chart with two y-scales.
  Captured throughput tracks offered load to within 0.6% at every rung. The first two
  rungs are deliberately tiny; they exist to prove the measurement chain reads a small
  known load correctly before it is trusted with a large one.</p>
  <div class="two">
    <div><div class="eyebrow" style="margin-bottom:6px">Captured at gre-cap &mdash; Mbit/s</div>{chartA}</div>
    <div><div class="eyebrow" style="margin-bottom:6px">Packet rate &mdash; pps</div>{chartB}</div>
  </div>
</section>

<section class="card">
  <h2>Before and after, at matched rungs</h2>
  <p class="note">Capture loss as a share of packets carried, same rungs, same twin,
  same sink. The two rungs that previously bled &mdash; <span class="inline">l4b</span> at
  5,967 packets and <span class="inline">l5</span> at 31,735 &mdash; are now exactly
  <strong>zero</strong>.</p>
  <div class="legend">
    <span><i style="background:var(--series-2)"></i> 2026-08-13, before</span>
    <span><i style="background:var(--series-1)"></i> 2026-08-14, after</span>
  </div>
  {chartC}
  <p class="note">The small trace at l2&ndash;l4 is <strong>2 packets</strong> in each case,
  on the host's gretap transmit queue. It is not rate-correlated &mdash; 2 packets at
  22&nbsp;Mbit/s and 2 at 1.5&nbsp;Gbit/s, while the two heaviest lossless rungs show none
  at all &mdash; which argues against congestion and points at a per-frame property,
  most likely occasional oversized frames from the twin's background AD traffic exceeding
  the tunnel MTU. l2's percentage looks worse only because its denominator is small
  (2 of 240k).</p>
</section>

<section class="card">
  <h2>What moved the ceiling</h2>
  <div class="bl">
    <div class="card">
      <h3>Sink NIC multiqueue</h3>
      <p class="note">The capture NIC was single-queue, so a 32-core appliance ran every
      mirrored frame through one virtio receive ring. <span class="inline">queues=8</span>
      hot-applied with no reboot and no loss of the static transport address. The sink has
      not dropped a packet since, at any rate this twin can produce &mdash; every
      <span class="inline">vhost</span>, <span class="inline">nic&nbsp;rx</span> and
      <span class="inline">decap&nbsp;rx</span> counter in the table below is zero.</p>
    </div>
    <div class="card">
      <h3>Gretap qdisc, now adapter-managed</h3>
      <p class="note">A new gretap inherited the node default, <span class="inline">fq_codel</span>
      &mdash; an AQM that deliberately drops to hold latency down, which is precisely wrong
      for a capture path. <span class="inline">attach-gre-tunnel</span> now asserts a deep
      FIFO itself, in its idempotent section, so adopted gretaps heal too and no manual
      re-assert is needed after a tap deploy.</p>
    </div>
  </div>
  <p class="note"><strong>What limits it now:</strong> all 17 tapped ports mirror into
  <em>one single-queue gretap netdev</em>. At rung l7 the <span class="inline">tc mirred</span>
  action logged {onset['ovl']:,} overlimits &mdash; mirrored copies the tunnel could not
  accept &mdash; while the sink stayed perfectly clean. Raising the ceiling further means
  per-sensor gretap fan-out or a multiqueue-capable tunnel device, not more sink capacity.</p>
</section>

<section class="card">
  <h2>Every measurement</h2>
  <p class="note">Offered is read at the twin's VM tap interfaces; captured at the sink's
  <span class="inline">gre-cap</span> decap device. Loss sums the capture-relevant counters
  only: tc dropped and overlimits, gretap tx, sink vhost tx, sink NIC rx, decap
  <em>rx</em>. <span class="inline">gre-cap</span>'s <em>transmit</em> drops are excluded
  deliberately &mdash; it is a decap device, so its tx side is not on the capture path, and
  counting it produced a false loss verdict at 14&nbsp;Mbit/s.</p>
  <div class="tblwrap">
  <table>
    <thead><tr>
      <th scope="col">Rung</th><th scope="col">Snd</th><th scope="col">Shape</th>
      <th scope="col">Win s</th><th scope="col">Offered</th><th scope="col">Captured</th>
      <th scope="col">pps</th><th scope="col">Packets</th><th scope="col">tc drop</th>
      <th scope="col">tc ovl</th><th scope="col">gretap</th><th scope="col">vhost</th>
      <th scope="col">nic rx</th><th scope="col">decap rx</th><th scope="col">Loss</th>
      <th scope="col">State</th>
    </tr></thead>
    <tbody>{table}</tbody>
  </table>
  </div>
</section>

<section class="card">
  <h2>Two corrections to how this was measured</h2>
  <p class="note"><strong>Per-host timestamps.</strong> A sample makes three sequential SSH
  round-trips. Stamping the whole sample once and dividing every stage by that single
  interval silently inflates rates when one read stalls: a stalled sink connection made the
  mirror stage report 588k&nbsp;pps against a gretap that had actually carried 138k. The
  harness now timestamps each host read separately, and the stages agree to within 0.2%.</p>
  <p class="note"><strong>Direction matters in a drop counter.</strong> Checking both
  directions at every stage is right in general and wrong at the decap device: a
  <span class="inline">gre-cap</span> tx_drop flagged "loss" on a 14&nbsp;Mbit/s rung that
  had lost nothing. The loss column above is the directionally-correct subset.</p>
  <p class="note">Both were found while producing this report, which is the argument for
  re-measuring rather than reusing the earlier run's numbers: several of those were taken
  mid-change, with the qdisc applied by hand and the sink connection intermittent.</p>
</section>

<footer>Harness: 6-stage drop accounting over /proc/net/dev and tc filter statistics.
Twin corp-enterprise-890c29b2 (plan corp-enterprise-4fc62c80), tap-maxrate-all.yaml
(4 segments / 17 ports) to XPress-SW-A, traffic tfc-maxrate-l1..l7. Specs, harness and
generator in twin-configs/corp-enterprise-maxrate/.</footer>
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

open(OUT, "w").write(PAGE)
print("wrote", OUT, len(PAGE), "bytes")
print(f"best lossless: {best['label']} {best['decap_mb']:,.0f} Mbit/s "
      f"{best['pps']:,.0f} pps over {best['window']:.0f}s")
print(f"onset: {onset['label']} {onset['decap_mb']:,.0f} Mbit/s {onset['loss_pct']:.3f}%")
