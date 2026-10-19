#!/usr/bin/env bash
# DMZ web victim — stand up a deliberately weak web surface for the lab.
# Idempotent: safe to re-run (provision-app may retry).
set -euo pipefail

systemctl enable --now nginx

# A minimal "ACME Corp" site plus a deliberately weak endpoint the red-team
# recon/exploit chain (deferred OP-ACT) can target. Real DVWA/Drupal needs a DB;
# this keeps the victim self-contained while still presenting an attack surface.
install -d -m 0755 /var/www/html
cat > /var/www/html/index.html <<'HTML'
<!doctype html><html><head><title>ACME Corp</title></head>
<body><h1>ACME Corp — DMZ Portal</h1>
<p>Internal tools: <a href="/admin/">admin</a></p></body></html>
HTML

# Weak admin stub (no auth) — an obvious finding for a scanner / blue-team hunt.
install -d -m 0755 /var/www/html/admin
cat > /var/www/html/admin/index.html <<'HTML'
<!doctype html><html><body><h1>admin</h1>
<!-- TODO: remove default creds admin/admin before go-live --></body></html>
HTML

# Server-status-ish info leak (another classic recon signal).
printf 'server: nginx\nenv: production\nbuild: 2024.1\n' > /var/www/html/version.txt

systemctl reload nginx || systemctl restart nginx || true
echo "web victim ready: / , /admin/ , /version.txt"
