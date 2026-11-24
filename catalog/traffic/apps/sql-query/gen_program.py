#!/usr/bin/env python3
"""Print the `source.program` steps of sql-query/app.yaml: a MariaDB 10.11 client/server
exchange in MySQL wire format (protocol 10, mysql_native_password): server greeting,
login as `app` to database `orders`, OK, one SELECT returning 3 columns x ROWS rows,
one UPDATE (1 row affected), COM_QUIT. Re-run after editing; paste the output under
`source: program:`. The scramble and auth response are fixed bytes, so no real server
would accept this login (real_server_ok: false)."""
import struct

ROWS = 10
def pkt(seq, payload): return struct.pack("<I", len(payload))[:3] + bytes([seq]) + payload
def lenenc_str(s): b = s.encode(); assert len(b) < 251; return bytes([len(b)]) + b
def cstr(s): return s.encode() + b"\x00"

scramble = bytes.fromhex("3a5f2c7e41316b265d2f6e4c3b7a215e56294d33")
greeting = (b"\x0a" + cstr("5.5.5-10.11.19-MariaDB-0+deb12u1") + struct.pack("<I", 4711) + scramble[:8] + b"\x00"
            + struct.pack("<H", 0xf7fe) + b"\x2d" + struct.pack("<H", 0x0002) + struct.pack("<H", 0x81bf)
            + bytes([21]) + b"\x00" * 6 + struct.pack("<I", 0x0000001d) + scramble[8:] + b"\x00"
            + cstr("mysql_native_password"))
caps = 0x0001 | 0x0004 | 0x0008 | 0x0200 | 0x2000 | 0x8000 | 0x80000
auth = bytes.fromhex("9c0e7d3b5a41f2286e17c4b09d53a6e1f0827c4d")
login = (struct.pack("<I", caps) + struct.pack("<I", 16777216) + b"\x2d" + b"\x00" * 23 + cstr("app")
         + bytes([len(auth)]) + auth + cstr("orders") + cstr("mysql_native_password"))
ok = lambda affected=0: b"\x00" + bytes([affected]) + b"\x00" + struct.pack("<H", 0x0002) + b"\x00\x00"
eof = b"\xfe\x00\x00" + struct.pack("<H", 0x0002)
def coldef(name, ctype, length):
    return (lenenc_str("def") + lenenc_str("orders") + lenenc_str("orders") + lenenc_str("orders")
            + lenenc_str(name) + lenenc_str(name) + b"\x0c" + struct.pack("<H", 0x2d)
            + struct.pack("<I", length) + bytes([ctype]) + struct.pack("<H", 0) + b"\x00" + b"\x00\x00")
select = "SELECT id, status, total FROM orders WHERE customer_id = 4711 ORDER BY id DESC LIMIT %d" % ROWS
update = "UPDATE orders SET status = 'shipped' WHERE id = 90412"
rs = [pkt(1, b"\x03"), pkt(2, coldef("id", 0x03, 11)), pkt(3, coldef("status", 0xfd, 64)),
      pkt(4, coldef("total", 0xf6, 12)), pkt(5, eof)]
for i in range(ROWS):
    rs.append(pkt(6 + i, lenenc_str(str(90412 - i)) + lenenc_str(["pending", "paid", "shipped"][i % 3])
                  + lenenc_str("%d.%02d" % (40 + 7 * i, (13 * i) % 100))))
rs.append(pkt(6 + ROWS, eof))
steps = [("reply", pkt(0, greeting)), ("send", pkt(1, login)), ("reply", pkt(2, ok())),
         ("send", pkt(0, b"\x03" + select.encode())), ("reply", b"".join(rs)),
         ("delay_ms", "{think_ms}"),
         ("send", pkt(0, b"\x03" + update.encode())), ("reply", pkt(1, ok(1))),
         ("send", pkt(0, b"\x01"))]
for k, v in steps:
    print(f'      - {{ {k}: "{v.hex() if isinstance(v, bytes) else v}" }}')
