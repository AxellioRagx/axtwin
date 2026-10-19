#!/bin/bash
# Fresh post-change ramp: every rung measured over an identical 120s window,
# with multiqueue active on the sink and the gretap qdisc now adapter-managed.
set -u
cd /home/rocky/axRange
SP=/tmp/claude-1000/-home-rocky-axRange/31a1ccac-036f-4766-a595-2493d1b8bd63/scratchpad
OUT=$SP/after.jsonl
: > "$OUT"
# every twin VM, so "offered" is the whole twin's contribution at each rung
VMIDS=538216,925880,957670,184429,478772,148957,277900,794991,832023,599034,707900,703259,825914,492612

for rung in l1 l2 l3 l4 l4b l5 l7; do
  echo "############ RUNG $rung ############"
  timeout 900 .venv/bin/axrange tfc deploy \
    "twin-configs/corp-enterprise/tfc-maxrate-$rung.yaml" 2>&1 | grep -E 'status:' | head -1
  sleep 30   # let the persona units start and reach steady state
  timeout 400 .venv/bin/python "$SP/measure.py" --seconds 120 \
    --vmids "$VMIDS" --label "$rung" --json-out "$OUT" 2>&1 | tail -11
done

echo "############ restore rung 4 (the standing max-lossless state) ############"
timeout 900 .venv/bin/axrange tfc deploy \
  twin-configs/corp-enterprise/tfc-maxrate-l4.yaml 2>&1 | grep -E 'status:' | head -1
echo "RAMP-COMPLETE"
wc -l < "$OUT"
