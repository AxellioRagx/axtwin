#!/usr/bin/env bash
# Stage a representative ETL job (extract from prod OLTP -> load the warehouse).
# The corp-traffic activity drives the live run; this just lands the job + a marker.
set -uo pipefail
install -d -m 0755 /opt/etl
cat > /opt/etl/daily_load.sh <<'JOB'
#!/usr/bin/env bash
# Daily ETL: count prod orders, upsert the warehouse aggregate.
export PGPASSWORD='Etl-Lab!1'
N=$(psql -h 10.50.40.10 -U etl_ro -d sales -tAc "SELECT count(*) FROM orders;" 2>/dev/null || echo 0)
psql -h 10.50.40.20 -U etl_rw -d warehouse -c "INSERT INTO daily_sales(d,n) VALUES (current_date, ${N:-0}) ON CONFLICT DO NOTHING;" 2>/dev/null || true
JOB
chmod +x /opt/etl/daily_load.sh
echo "etl_setup: /opt/etl/daily_load.sh staged"
