#!/usr/bin/env bash
# Web challenge — vulnerable web surface with a planted flag. Idempotent.
set -euo pipefail

systemctl enable --now nginx

install -d -m 0755 /var/www/html
cat > /var/www/html/index.html <<'HTML'
<!doctype html><html><body><h1>SecureBank login</h1>
<form><input name=user><input name=pass type=password></form>
<!-- dev note: backup at /backup.zip --></body></html>
HTML

# Planted flag behind a classic "exposed backup / path traversal" style finding.
printf 'flag{w3b_3xp0sed_b4ckup}\n' > /var/www/html/.flag
cat > /var/www/html/robots.txt <<'TXT'
User-agent: *
Disallow: /backup/
Disallow: /.flag
TXT

systemctl reload nginx || systemctl restart nginx || true
echo "web challenge ready (flag planted: web-chal)"
