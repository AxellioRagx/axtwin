#!/usr/bin/env bash
# Outdated/vulnerable web stack — a deliberate scan finding surface. Idempotent.
set -euo pipefail

systemctl enable --now nginx

install -d -m 0755 /var/www/html
cat > /var/www/html/index.html <<'HTML'
<!doctype html><html><body><h1>Legacy App v1.2 (2018)</h1>
<p>Powered by an unpatched stack. <a href="/phpinfo.php">status</a></p></body></html>
HTML

# Classic info-leak finding (phpinfo) + a fake outdated-version banner the
# scanner/analyst flags. (No real CVE payload — it's a finding surface.)
cat > /var/www/html/phpinfo.php <<'PHP'
<?php // intentionally exposed for the lab — a vuln-scan finding
echo "app=legacy-1.2\nphp=7.2 (EOL)\nopenssl=1.0.2 (EOL)\n"; ?>
PHP

printf 'Server: nginx/1.14.0 (legacy)\nX-App-Version: 1.2.0\n' > /var/www/html/version.txt
systemctl reload nginx || systemctl restart nginx || true
echo "web-old staged: legacy banners + phpinfo (scan finding surface)"
