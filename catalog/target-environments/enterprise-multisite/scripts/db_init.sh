#!/usr/bin/env bash
# Initialize the PostgreSQL tier. Bound to BOTH db-prod and db-dw; idempotent and
# role-agnostic — it creates both the OLTP (sales) and warehouse schemas so either
# host can serve either role for the representative corp-traffic activity.
set -uo pipefail
export DEBIAN_FRONTEND=noninteractive
systemctl enable --now postgresql 2>/dev/null || true
sudo -u postgres psql <<'SQL' 2>/dev/null || true
DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='etl_ro') THEN CREATE ROLE etl_ro LOGIN PASSWORD 'Etl-Lab!1'; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='etl_rw') THEN CREATE ROLE etl_rw LOGIN PASSWORD 'Etl-Lab!1'; END IF; END $$;
SELECT 'CREATE DATABASE sales'     WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='sales')\gexec
SELECT 'CREATE DATABASE warehouse' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='warehouse')\gexec
SQL
sudo -u postgres psql -d sales     -c "CREATE TABLE IF NOT EXISTS orders(id serial primary key, amt numeric); GRANT SELECT ON orders TO etl_ro;" 2>/dev/null || true
sudo -u postgres psql -d warehouse -c "CREATE TABLE IF NOT EXISTS daily_sales(d date, n int); GRANT INSERT,SELECT ON daily_sales TO etl_rw;" 2>/dev/null || true
# Allow the ETL hosts (hq-server zone) to connect over the network.
PGHBA=$(ls /etc/postgresql/*/main/pg_hba.conf 2>/dev/null | head -1)
PGCONF=$(ls /etc/postgresql/*/main/postgresql.conf 2>/dev/null | head -1)
[ -n "$PGCONF" ] && { grep -q "listen_addresses = '\*'" "$PGCONF" || echo "listen_addresses = '*'" >> "$PGCONF"; }
[ -n "$PGHBA" ]  && { grep -q "10.50.30.0/24" "$PGHBA" || echo "host all all 10.50.30.0/24 md5" >> "$PGHBA"; }
systemctl restart postgresql 2>/dev/null || true
echo "db_init: postgresql ready (sales + warehouse)"
