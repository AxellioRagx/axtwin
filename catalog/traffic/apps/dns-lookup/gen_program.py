#!/usr/bin/env python3
"""Print the `source.program` steps of dns-lookup/app.yaml: one DNS A query for
intranet.corp.example and its answer (203.0.113.10, TTL 300), wire format, UDP.
The id, name and answer are fixed bytes: every flow asks the same question."""
import struct
NAME, ANSWER, QID = "intranet.corp.example", "203.0.113.10", 0x5a3c
q = b"".join(bytes([len(p)]) + p.encode() for p in NAME.split(".")) + b"\x00" + struct.pack(">HH", 1, 1)
query = struct.pack(">HHHHHH", QID, 0x0100, 1, 0, 0, 0) + q
ans = b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 300, 4) + bytes(int(x) for x in ANSWER.split("."))
resp = struct.pack(">HHHHHH", QID, 0x8180, 1, 1, 0, 0) + q + ans
print(f'      - {{ send: "{query.hex()}" }}\n      - {{ reply: "{resp.hex()}" }}')
