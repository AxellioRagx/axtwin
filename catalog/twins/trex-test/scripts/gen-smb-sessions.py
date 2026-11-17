#!/usr/bin/env python3
"""trex-test -- generate the crowd's SMB templates (TRex ASTF L7 programs).

Writes the block between `# BEGIN generated smb` and `# END generated smb` in
personas/trex-crowd-client.yaml. Re-run after changing anything here:

    python3 catalog/twins/trex-test/scripts/gen-smb-sessions.py

Each template is ONE complete SMB 3.1.1 session as a Windows 10 client and a
Windows Server 2022 file server exchange it, byte for byte in SMB2 framing:

    NEGOTIATE (2.0.2-3.1.1, preauth/encryption/netname/signing contexts)
      -> 3.1.1, SPNEGO NegTokenInit (Kerberos, NTLMSSP)
    SESSION_SETUP #1  NTLMSSP_NEGOTIATE  -> MORE_PROCESSING, NTLMSSP_CHALLENGE
    SESSION_SETUP #2  NTLMSSP_AUTH (NTLMv2, CROWD\\<user>) -> SUCCESS (signed)
    TREE_CONNECT  \\\\fs01.crowd.axr.lab\\shared
    CREATE <file>, then READ (download) or WRITE (upload) in 64 KiB chunks
    CLOSE, TREE_DISCONNECT, LOGOFF

NTLM, not Kerberos: a Kerberos session would need a TGS exchange with the
twin's real DC (dc01) from each emulated client, which TRex cannot do; NTLM
inside SMB is what Windows itself falls back to whenever Kerberos is not
available. The names (domain CROWD / crowd.axr.lab, server FS01, users) match
the twin's AD (te.yaml `identity:`, scripts/prepare-dc.ps1 registers fs01).

What is NOT real: the cryptography. Challenges, NTLMv2 proofs, session keys,
MICs and SMB signatures are deterministic random bytes (no peer verifies
them), and every flow of a template replays the same bytes. File bodies are a
real file-format header (a ZIP/OOXML local header) then a repeated 256-byte
random block (TRex's send size/fill).
"""
import random
import re
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PERSONA = HERE.parent / "personas" / "trex-crowd-client.yaml"

DOMAIN_NB, DOMAIN_DNS = "CROWD", "crowd.axr.lab"
# The file servers: fs01 = 10.250.2.1, fs02 = 10.250.2.2 (registered in the
# DC's DNS by prepare-dc.ps1). One session template per server: ASTF tells two
# server templates on one port apart only by the server address range.
SERVERS = {"fs01": "10.250.2.1", "fs02": "10.250.2.2"}
CHUNK = 65536
FT_2026 = 134_100_000_000_000_000  # a FILETIME in 2025-2026 (100 ns since 1601)

SESSIONS = [
    # name, cps, server, user, workstation, mode, path, size
    ("smb-read", 3, "fs01", "alice", "WS-FIN-017", "read", "Finance\\Q3-forecast.xlsx", 262_144),
    ("smb-write", 2, "fs02", "bob", "WS-ENG-042", "write", "Projects\\design-notes.docx", 98_304),
]


def names(server: str) -> tuple[str, str, str]:
    """(NetBIOS name, DNS name, share UNC) of one file server."""
    dns = f"{server}.{DOMAIN_DNS}"
    return server.upper(), dns, f"\\\\{dns}\\shared"

# --- DER / SPNEGO -----------------------------------------------------------

def der(tag: int, body: bytes) -> bytes:
    n = len(body)
    if n < 0x80:
        ln = bytes([n])
    else:
        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        ln = bytes([0x80 | len(b)]) + b
    return bytes([tag]) + ln + body


def oid(dotted: str) -> bytes:
    parts = [int(p) for p in dotted.split(".")]
    out = bytearray([40 * parts[0] + parts[1]])
    for p in parts[2:]:
        enc = [p & 0x7F]
        p >>= 7
        while p:
            enc.append(0x80 | (p & 0x7F))
            p >>= 7
        out += bytes(reversed(enc))
    return der(0x06, bytes(out))


SPNEGO = oid("1.3.6.1.5.5.2")
MS_KRB5, KRB5 = oid("1.2.840.48018.1.2.2"), oid("1.2.840.113554.1.2.2")
KRB5_U2U, NTLMSSP = oid("1.2.840.113554.1.2.2.3"), oid("1.3.6.1.4.1.311.2.2.10")


def neg_token_init(mechs: list[bytes], token: bytes | None = None, hints: bool = False) -> bytes:
    body = der(0xA0, der(0x30, b"".join(mechs)))
    if token is not None:
        body += der(0xA2, der(0x04, token))
    if hints:
        body += der(0xA3, der(0x30, der(0xA0, der(0x1B, b"not_defined_in_RFC4178@please_ignore"))))
    return der(0x60, SPNEGO + der(0xA0, der(0x30, body)))


def neg_token_resp(state: int | None = None, mech: bytes | None = None,
                   token: bytes | None = None, mic: bytes | None = None) -> bytes:
    body = b""
    if state is not None:
        body += der(0xA0, der(0x0A, bytes([state])))
    if mech is not None:
        body += der(0xA1, mech)
    if token is not None:
        body += der(0xA2, der(0x04, token))
    if mic is not None:
        body += der(0xA3, der(0x04, mic))
    return der(0xA1, der(0x30, body))

# --- NTLMSSP ----------------------------------------------------------------

def verifier(rnd: random.Random) -> bytes:
    """An NTLMSSP message signature (SPNEGO mechListMIC): version 1, an
    8-byte checksum, sequence number 0."""
    return struct.pack("<I", 1) + rnd.randbytes(8) + struct.pack("<I", 0)


def u16(s: str) -> bytes:
    return s.encode("utf-16-le")


def av(av_id: int, val: bytes) -> bytes:
    return struct.pack("<HH", av_id, len(val)) + val


def version(build: int) -> bytes:
    return struct.pack("<BBH3xB", 10, 0, build, 15)


def ntlm_negotiate() -> bytes:
    return (b"NTLMSSP\0" + struct.pack("<II", 1, 0xE2088297)
            + struct.pack("<HHI", 0, 0, 0) * 2 + version(19041))


def server_av_pairs(nb: str) -> bytes:
    return (av(2, u16(DOMAIN_NB)) + av(1, u16(nb)) + av(4, u16(DOMAIN_DNS))
            + av(3, u16(f"{nb}.{DOMAIN_DNS}"))
            + av(5, u16(DOMAIN_DNS)) + av(7, struct.pack("<Q", FT_2026)))


def ntlm_challenge(rnd: random.Random, nb: str) -> bytes:
    name, info = u16(DOMAIN_NB), server_av_pairs(nb) + av(0, b"")
    off_name = 56
    off_info = off_name + len(name)
    return (b"NTLMSSP\0" + struct.pack("<I", 2) + struct.pack("<HHI", len(name), len(name), off_name)
            + struct.pack("<I", 0xE28A8215) + rnd.randbytes(8) + b"\0" * 8
            + struct.pack("<HHI", len(info), len(info), off_info) + version(20348) + name + info)


def ntlm_auth(rnd: random.Random, user: str, ws: str, nb: str, dns: str) -> bytes:
    blob = (struct.pack("<BBHIQ", 1, 1, 0, 0, FT_2026 + 1_000) + rnd.randbytes(8) + b"\0" * 4
            + server_av_pairs(nb) + av(6, struct.pack("<I", 2))
            + av(8, struct.pack("<II", 48, 0) + b"\0" * 8 + rnd.randbytes(32))
            + av(10, b"\0" * 16) + av(9, u16(f"cifs/{dns}")) + av(0, b"") + b"\0" * 4)
    nt = rnd.randbytes(16) + blob
    lm = b"\0" * 24
    dom, usr, wks, key = u16(DOMAIN_NB), u16(user), u16(ws), rnd.randbytes(16)
    payload, fields, off = b"", {}, 88
    for k, v in (("dom", dom), ("usr", usr), ("wks", wks), ("lm", lm), ("nt", nt), ("key", key)):
        fields[k] = struct.pack("<HHI", len(v), len(v), off)
        payload += v
        off += len(v)
    return (b"NTLMSSP\0" + struct.pack("<I", 3) + fields["lm"] + fields["nt"] + fields["dom"]
            + fields["usr"] + fields["wks"] + fields["key"] + struct.pack("<I", 0xE2888215)
            + version(19041) + rnd.randbytes(16) + payload)

# --- SMB2 -------------------------------------------------------------------

NEGOTIATE, SESSION_SETUP, LOGOFF, TREE_CONNECT, TREE_DISCONNECT, CREATE, CLOSE, READ, WRITE = (
    0, 1, 2, 3, 4, 5, 6, 8, 9)
STATUS_MORE = 0xC0000016


class Conv:
    """One session's message stream: client/server SMB2 frames with the
    running MessageId, SessionId, TreeId and signing state."""

    def __init__(self, rnd: random.Random):
        self.rnd, self.mid, self.sess, self.tree, self.signed = rnd, 0, 0, 0, False

    def hdr(self, cmd: int, *, resp: bool, status: int = 0, charge: int = 1, credits: int = 1,
            sign: bool | None = None) -> bytes:
        sign = self.signed if sign is None else sign
        flags = (1 if resp else 0) | (8 if sign else 0)
        sig = self.rnd.randbytes(16) if sign else b"\0" * 16
        return struct.pack("<4sHHIHHIIQIIQ16s", b"\xfeSMB", 64, charge, status, cmd, credits,
                           flags, 0, self.mid, 0xFEFF, self.tree, self.sess, sig)

    @staticmethod
    def nb(msg: bytes, extra: int = 0) -> bytes:
        """NetBIOS session framing; `extra` is fill the program appends after
        `msg` (file data), counted in the length."""
        return b"\0" + (len(msg) + extra).to_bytes(3, "big") + msg


def ctx(ctype: int, data: bytes) -> bytes:
    return struct.pack("<HHI", ctype, len(data), 0) + data


def pad8(b: bytes) -> bytes:
    return b + b"\0" * (-len(b) % 8)


def contexts(items: list[bytes]) -> bytes:
    out = b""
    for i, c in enumerate(items):
        out += pad8(c) if i < len(items) - 1 else c
    return out


def session(name: str, server: str, user: str, ws: str, mode: str, path: str, size: int, seed: int):
    rnd = random.Random(seed)
    nb_name, dns, share = names(server)
    c = Conv(rnd)
    steps: list[tuple[str, bytes, int, str]] = []  # (op, framed bytes, total size or 0, comment)

    def client(msg: bytes, note: str, data: int = 0):
        steps.append(("send", c.nb(msg, data), 4 + len(msg) + data if data else 0, note))

    def server(msg: bytes, note: str, data: int = 0):
        steps.append(("reply", c.nb(msg, data), 4 + len(msg) + data if data else 0, note))

    # NEGOTIATE
    dialects = struct.pack("<5H", 0x0202, 0x0210, 0x0300, 0x0302, 0x0311)
    cctx = contexts([
        ctx(1, struct.pack("<HHH", 1, 32, 1) + rnd.randbytes(32)),
        ctx(2, struct.pack("<5H", 4, 2, 1, 4, 3)),
        ctx(5, u16(dns)),
        ctx(8, struct.pack("<3H", 2, 2, 1)),
    ])
    fixed = 36 + len(dialects)
    off = 64 + fixed + (-(64 + fixed) % 8)
    body = struct.pack("<HHHHI16sIHH", 36, 5, 1, 0, 0x7F, rnd.randbytes(16), off, 4, 0) + dialects
    body += b"\0" * (off - 64 - len(body))
    client(c.hdr(NEGOTIATE, resp=False, charge=0, credits=31) + body + cctx,
           "NEGOTIATE request: dialects 2.0.2-3.1.1, preauth/encryption/netname/signing contexts")
    sec = neg_token_init([MS_KRB5, KRB5, KRB5_U2U, NTLMSSP], hints=True)
    sctx = contexts([
        ctx(1, struct.pack("<HHH", 1, 32, 1) + rnd.randbytes(32)),
        ctx(2, struct.pack("<2H", 1, 2)),
        ctx(8, struct.pack("<2H", 1, 2)),
    ])
    sec_off = 128
    ctx_off = sec_off + len(sec) + (-(sec_off + len(sec)) % 8)
    body = struct.pack("<HHHH16sIIIIQQHHI", 65, 1, 0x0311, 3, rnd.randbytes(16), 0x2F,
                       8_388_608, 8_388_608, 8_388_608, FT_2026, 0, sec_off, len(sec), ctx_off)
    body += sec
    body += b"\0" * (ctx_off - 64 - len(body))
    server(c.hdr(NEGOTIATE, resp=True, charge=0, credits=1) + body + sctx,
           "NEGOTIATE response: SMB 3.1.1, SPNEGO NegTokenInit (Kerberos, NTLMSSP)")

    # SESSION_SETUP x2 (NTLMSSP)
    c.mid += 1
    tok = neg_token_init([NTLMSSP], token=ntlm_negotiate())
    client(c.hdr(SESSION_SETUP, resp=False, credits=31) + struct.pack("<HBBIIHHQ", 25, 0, 1, 1, 0, 88, len(tok), 0) + tok,
           "SESSION_SETUP request #1: NTLMSSP_NEGOTIATE")
    c.sess = rnd.getrandbits(64) | 0x0000_0400_0000_0000
    tok = neg_token_resp(1, NTLMSSP, ntlm_challenge(rnd, nb_name))
    server(c.hdr(SESSION_SETUP, resp=True, status=STATUS_MORE, credits=33) + struct.pack("<HHHH", 9, 0, 72, len(tok)) + tok,
           f"SESSION_SETUP response #1: MORE_PROCESSING_REQUIRED, NTLMSSP_CHALLENGE ({DOMAIN_NB}/{nb_name})")
    c.mid += 1
    tok = neg_token_resp(token=ntlm_auth(rnd, user, ws, nb_name, dns), mic=verifier(rnd))
    client(c.hdr(SESSION_SETUP, resp=False, credits=31) + struct.pack("<HBBIIHHQ", 25, 0, 1, 1, 0, 88, len(tok), 0) + tok,
           f"SESSION_SETUP request #2: NTLMSSP_AUTH NTLMv2 {DOMAIN_NB}\\{user} from {ws}")
    tok = neg_token_resp(0, mic=verifier(rnd))
    server(c.hdr(SESSION_SETUP, resp=True, credits=32, sign=True) + struct.pack("<HHHH", 9, 0, 72, len(tok)) + tok,
           "SESSION_SETUP response #2: SUCCESS (signed); the session signs from here")
    c.signed = True

    # TREE_CONNECT
    c.mid += 1
    p = u16(share)
    client(c.hdr(TREE_CONNECT, resp=False) + struct.pack("<HHHH", 9, 0, 72, len(p)) + p,
           f"TREE_CONNECT request: {share}")
    c.tree = rnd.randrange(1, 0xFFFF)
    server(c.hdr(TREE_CONNECT, resp=True) + struct.pack("<HBBIII", 16, 1, 0, 0, 0, 0x001F01FF),
           "TREE_CONNECT response: disk share, full maximal access")

    # CREATE
    c.mid += 1
    fname = u16(path)
    if mode == "read":
        acc, attrs, share, disp, action = 0x00120089, 0, 0x7, 1, 1
    else:
        acc, attrs, share, disp, action = 0x0012019F, 0x80, 0x3, 5, 2
    client(c.hdr(CREATE, resp=False) + struct.pack("<HBBIQQIIIIIHHII", 57, 0, 0, 2, 0, 0, acc, attrs,
                                                     share, disp, 0x40, 120, len(fname), 0, 0) + fname,
           f"CREATE request: {path} ({'open for read' if mode == 'read' else 'overwrite-if for write'})")
    fid = rnd.randbytes(16)
    eof = size if mode == "read" else 0
    alloc = (eof + 4095) // 4096 * 4096
    server(c.hdr(CREATE, resp=True) + struct.pack("<HBBIQQQQQQII16sII", 89, 0, 0, action, FT_2026 - 9_000_000_000,
                                                  FT_2026, FT_2026 - 9_000_000_000, FT_2026 - 9_000_000_000,
                                                  alloc, eof, 0x20, 0, fid, 0, 0) + b"\0",
           f"CREATE response: {'FILE_OPENED' if action == 1 else 'FILE_CREATED'}, EndOfFile {eof}")

    # The file: an OOXML (ZIP) local file header, then fill.
    head = (b"PK\x03\x04\x14\x00\x06\x00\x08\x00\x00\x00!\x00" + rnd.randbytes(12)
            + struct.pack("<HH", 19, 0) + b"[Content_Types].xml")
    offset = 0
    while offset < size:
        n = min(CHUNK, size - offset)
        c.mid += 1
        first = head if offset == 0 else b""
        if mode == "read":
            client(c.hdr(READ, resp=False) + struct.pack("<HBBIQ16sIIIHHB", 49, 0x50, 0, n, offset, fid, 0, 0, 0, 0, 0, 0),
                   f"READ request: {n} bytes at offset {offset}")
            server(c.hdr(READ, resp=True) + struct.pack("<HBBIII", 17, 0x50, 0, n, 0, 0) + first,
                   f"READ response: {n} bytes of file data", data=n - len(first))
        else:
            client(c.hdr(WRITE, resp=False) + struct.pack("<HHIQ16sIIHHI", 49, 0x70, n, offset, fid, 0, 0, 0, 0, 0) + first,
                   f"WRITE request: {n} bytes of file data at offset {offset}", data=n - len(first))
            server(c.hdr(WRITE, resp=True) + struct.pack("<HHIIHH", 17, 0, n, 0, 0, 0) + b"\0",
                   f"WRITE response: {n} bytes written")
        offset += n

    # CLOSE, TREE_DISCONNECT, LOGOFF
    c.mid += 1
    client(c.hdr(CLOSE, resp=False) + struct.pack("<HHI16s", 24, 1, 0, fid), "CLOSE request (post-query attributes)")
    alloc = (size + 4095) // 4096 * 4096
    server(c.hdr(CLOSE, resp=True) + struct.pack("<HHIQQQQQQI", 60, 1, 0, FT_2026 - 9_000_000_000, FT_2026 + 2_000,
                                                 FT_2026 + 2_000, FT_2026 + 2_000, alloc, size, 0x20),
           f"CLOSE response: EndOfFile {size}")
    c.mid += 1
    client(c.hdr(TREE_DISCONNECT, resp=False) + struct.pack("<HH", 4, 0), "TREE_DISCONNECT request")
    server(c.hdr(TREE_DISCONNECT, resp=True) + struct.pack("<HH", 4, 0), "TREE_DISCONNECT response")
    c.mid += 1
    client(c.hdr(LOGOFF, resp=False) + struct.pack("<HH", 4, 0), "LOGOFF request")
    server(c.hdr(LOGOFF, resp=True) + struct.pack("<HH", 4, 0), "LOGOFF response")
    return steps


def render() -> str:
    fill = random.Random(445).randbytes(256).hex()
    lines = [
        "      # BEGIN generated smb -- scripts/gen-smb-sessions.py writes this block; edit the script, not these lines.",
        "      # Each template is one whole SMB 3.1.1 session against one file server (behind trex-server on lan-b).",
    ]
    first_fill = True
    for i, (name, cps, server, user, ws, mode, path, size) in enumerate(SESSIONS):
        steps = session(name, server, user, ws, mode, path, size, seed=1000 + i)
        share = names(server)[2]
        verb = "downloads" if mode == "read" else "uploads"
        lines += [
            f"      # {name}: {DOMAIN_NB}\\{user} on {ws} {verb} {share}\\{path} ({size} bytes, {CHUNK // 1024} KiB chunks)",
            f"      - name: {name}",
            "        port: 445",
            f"        cps: {cps}",
            f"        servers: {{ range: {SERVERS[server]}/32 }}",
            "        program:",
        ]
        for op, msg, total, note in steps:
            lines.append(f"          # {note}")
            if total > len(msg):
                ref = "&smbfill " if first_fill else ""
                val = f'"{fill}"' if first_fill else "*smbfill"
                lines.append(f'          - {{ {op}: "{msg.hex()}", size: {total}, fill: {ref}{val} }}')
                first_fill = False
            else:
                lines.append(f'          - {{ {op}: "{msg.hex()}" }}')
    lines.append("      # END generated smb")
    return "\n".join(lines) + "\n"


def main() -> int:
    text = PERSONA.read_text()
    pat = re.compile(r"^      # BEGIN generated smb.*?^      # END generated smb\n", re.S | re.M)
    if not pat.search(text):
        print(f"no '# BEGIN generated smb' .. '# END generated smb' block in {PERSONA}", file=sys.stderr)
        return 1
    PERSONA.write_text(pat.sub(lambda _m: render(), text, count=1))
    print(f"wrote {len(SESSIONS)} SMB templates into {PERSONA}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
