#!/usr/bin/env bash
# Minimal intranet business-app landing page (the corp-traffic activity health-checks it).
set -uo pipefail
install -d -m 0755 /var/www/html
echo "<h1>ACME Intranet</h1><p>Business application — internal.</p>" > /var/www/html/index.html
systemctl enable --now nginx 2>/dev/null || true
echo "app_setup: intranet app page deployed"
