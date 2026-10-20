#!/usr/bin/env bash
# Crown-jewel business app + a synthetic "sensitive data" marker (exfil objective
# for the deferred purple-team kill chain). Idempotent. Synthetic data only.
set -euo pipefail

systemctl enable --now nginx

install -d -m 0755 /var/www/html
cat > /var/www/html/index.html <<'HTML'
<!doctype html><html><body><h1>ACME ERP</h1>
<p>Finance portal — authorized users only.</p></body></html>
HTML

# Synthetic crown-jewel data (fake; the deferred data overlay would seed at scale).
install -d -m 0700 /srv/crownjewel
cat > /srv/crownjewel/customers.csv <<'CSV'
id,name,ssn,card
1,Synthetic Alice,000-00-0001,4111111111111111
2,Synthetic Bob,000-00-0002,4111111111111112
CSV
printf 'CLASSIFICATION=pii\nCROWN_JEWEL=true\n' > /srv/crownjewel/.label

echo "crown-app staged: ACME ERP + synthetic PII at /srv/crownjewel (exfil objective)"
