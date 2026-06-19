-- Read-only Postgres role for the «Что растёт» Grafana analytics dashboard.
-- Run as supabase_admin (the `postgres` role is restricted on this Supabase box).
-- Grants SELECT ONLY on the three tables the dashboard reads — nothing else.
--
--   psql "postgresql://supabase_admin:<pw>@localhost:5433/postgres" -f grafana_readonly_role.sql
--
-- Then set a strong password (replace CHANGE_ME) and use it in the Grafana datasource.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro') THEN
    CREATE ROLE grafana_ro LOGIN PASSWORD 'CHANGE_ME_STRONG_PASSWORD';
  END IF;
END
$$;

GRANT CONNECT ON DATABASE postgres TO grafana_ro;
GRANT USAGE  ON SCHEMA public      TO grafana_ro;

-- Only the analytics tables. Do NOT grant ALL — keep the blast radius tiny.
GRANT SELECT ON public.quest_devices  TO grafana_ro;
GRANT SELECT ON public.identifications TO grafana_ro;
GRANT SELECT ON public.plants          TO grafana_ro;

-- Belt-and-suspenders: make sure it can never write.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM grafana_ro;
