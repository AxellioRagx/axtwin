"""The generic ASTF profile (`trafficgen/SPEC.md`, B4): ONE file, pushed into
every generator VM at provision, that builds one traffic line's TRex ASTF
profile from the generator's document (`trafficgen.plan`). No per-twin code is
generated.

Runs inside the generator under TRex v3.08's Python (the bundled `trex.astf.api`),
so it imports nothing from axrange. Loaded per line by the in-VM wrapper:

    ASTFProfile.load("astf_profile.py", tunables=["--doc", DOC, "--line", LINE_ID, "--files", DIR])

`build(doc, line_id, api, files_dir)` is the pure part the engine tests drive
with a fake `api`.

Caps enforced here (T2): each template's `cps` is the line's `rate_cps` times
the app's share, and TRex's scheduler holds it. Concurrency and Mbps have no
per-profile knob in ASTF; the wrapper's controller enforces them by lowering
the line's multiplier (`update`).
"""
import argparse
import json
import os


def _bytes(hexstr):
    return bytes.fromhex(hexstr)


def _program_pair(api, steps, transport):
    """A client/server ASTFProgram pair from rendered steps: the client's sends
    are the server's receives and the server's replies the client's, mirrored
    in order. TCP counts bytes; UDP carries whole messages."""
    stream = transport == "tcp"
    pc, ps = api.ASTFProgram(stream=stream), api.ASTFProgram(stream=stream)

    def emit(prog, st, key):
        data = _bytes(st[key])
        size = int(st.get("size") or 0)
        if not stream:
            if size > len(data):
                data = (data + _bytes(st["fill"]) * (size // max(1, len(st["fill"]) // 2) + 1))[:size]
            prog.send_msg(data)
            return len(data)
        if size > len(data):
            prog.send(data, size=size, fill=_bytes(st["fill"]))
            return size
        prog.send(data)
        return len(data)

    for st in steps:
        if "send" in st:
            n = emit(pc, st, "send")
            if stream:
                ps.recv(n)
            else:
                ps.recv_msg(1)
        elif "reply" in st:
            n = emit(ps, st, "reply")
            if stream:
                pc.recv(n)
            else:
                pc.recv_msg(1)
        elif "recv" in st:
            n = int(st["recv"])
            pc.recv(n)
            ps.send(b"\x00" * n)
        elif "delay_ms" in st:
            ms = int(st["delay_ms"])
            if ms > 0:
                pc.delay(ms * 1000)
    return pc, ps


def build(doc, line_id, api, files_dir):
    """The ASTFProfile for one line of `doc`."""
    lines = {ln["id"]: ln for ln in doc["lines"]}
    if line_id not in lines:
        raise ValueError("line %r is not in generator %r (lines: %s)" % (line_id, doc.get("generator"), sorted(lines)))
    ln = lines[line_id]
    gen = api.ASTFIPGen(
        glob=api.ASTFIPGenGlobal(ip_offset="0.0.0.0"),
        dist_client=api.ASTFIPGenDist(ip_range=list(ln["clients"]), distribution="seq"),
        dist_server=api.ASTFIPGenDist(ip_range=list(ln["servers"]), distribution="seq"),
    )
    caps, templates = [], []
    for i, t in enumerate(ln["templates"]):
        # TRex v3.08 aborts on some exact cps ratios (KxuNuRand, see the
        # trex_astf driver); a distinct ~1e-6 relative nudge per template
        # avoids it. The rate effect is under 1e-5.
        cps = float(t["cps"]) * (1 + 1e-6 * (i + 1))
        assoc = api.ASTFAssociationRule(port=int(t["port"]), ip_start=ln["servers"][0], ip_end=ln["servers"][1])
        if "pcap" in t:
            caps.append(api.ASTFCapInfo(file=os.path.join(files_dir, t["pcap"]["name"]), cps=cps,
                                        assoc=assoc, ip_gen=gen))
            continue
        pc, ps = _program_pair(api, t["program"], t["transport"])
        templates.append(api.ASTFTemplate(
            client_template=api.ASTFTCPClientTemplate(program=pc, ip_gen=gen, port=int(t["port"]), cps=cps),
            server_template=api.ASTFTCPServerTemplate(program=ps, assoc=assoc),
        ))
    kw = {"default_ip_gen": gen}
    if caps:
        kw["cap_list"] = caps
    if templates:
        kw["templates"] = templates
    return api.ASTFProfile(**kw)


class _Profile(object):
    def get_profile(self, tunables, **kwargs):
        import trex.astf.api as api  # the release's bundled client (inside the generator)

        p = argparse.ArgumentParser(prog="astf_profile.py")
        p.add_argument("--doc", required=True)
        p.add_argument("--line", required=True)
        p.add_argument("--files", required=True)
        a = p.parse_args(tunables)
        with open(a.doc) as f:
            doc = json.load(f)
        return build(doc, a.line, api, a.files)


def register():
    return _Profile()
