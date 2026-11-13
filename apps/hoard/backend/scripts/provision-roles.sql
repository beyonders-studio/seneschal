-- Hoard's two database logins, made by a deployment: its role job runs this as
-- the database's superuser, with each role's password as a psql variable.
-- Hoard never runs it, and refuses to serve as a superuser itself.
--
--   psql "$SUPERUSER_DSN" -v ON_ERROR_STOP=1 \
--     -v owner_password="$OWNER_PASSWORD" -v app_password="$APP_PASSWORD" \
--     -f scripts/provision-roles.sql
--
-- hoard_owner owns the database and runs the migrations, which make every
-- table, grant and policy; hoard_app serves, owns nothing and is bound by row
-- security. Idempotent: running it again with new passwords rotates them.

BEGIN;

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'hoard_owner') THEN
    CREATE ROLE hoard_owner NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'hoard_app') THEN
    CREATE ROLE hoard_app NOLOGIN;
  END IF;
END $$;

ALTER ROLE hoard_owner WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS
  PASSWORD :'owner_password';
ALTER ROLE hoard_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS
  PASSWORD :'app_password';

DO $$ BEGIN
  EXECUTE format('ALTER DATABASE %I OWNER TO hoard_owner', current_database());
END $$;
ALTER SCHEMA public OWNER TO hoard_owner;

COMMIT;
