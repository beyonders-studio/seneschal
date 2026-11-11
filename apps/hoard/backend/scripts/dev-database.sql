-- Hoard's development database and its two logins, with development passwords.
-- docker-compose.yml runs this once, when the database volume is first made.
-- A deployment makes its own logins with passwords of its own.
CREATE ROLE hoard_owner LOGIN PASSWORD 'hoard_owner' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE hoard_app LOGIN PASSWORD 'hoard_app' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE DATABASE hoard OWNER hoard_owner;
