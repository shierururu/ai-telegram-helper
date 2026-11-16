#!/usr/bin/env bash
set -eu
: "${ALFA_MIGRATOR_DB_PASSWORD:?required}"
: "${ALFA_BOT_DB_PASSWORD:?required}"
: "${ALFA_WORKER_DB_PASSWORD:?required}"
# psql getenv avoids passwords in process arguments; :'var' quotes SQL literals.
psql -X --no-password --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 <<'SQL'
\getenv migrator_password ALFA_MIGRATOR_DB_PASSWORD
\getenv bot_password ALFA_BOT_DB_PASSWORD
\getenv worker_password ALFA_WORKER_DB_PASSWORD
BEGIN;
-- Also runs before migrations on existing databases: preserve roles and rotate passwords.
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT', name)
FROM (VALUES ('alfa_migrator'), ('alfa_bot'), ('alfa_worker')) AS roles(name)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = name)
\gexec
ALTER ROLE alfa_migrator PASSWORD :'migrator_password';
ALTER ROLE alfa_bot PASSWORD :'bot_password';
ALTER ROLE alfa_worker PASSWORD :'worker_password';
SELECT current_database() AS database_name \gset
REVOKE ALL ON DATABASE :"database_name" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"database_name" TO alfa_migrator, alfa_bot, alfa_worker;
GRANT CREATE ON DATABASE :"database_name" TO alfa_migrator;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
COMMIT;
SQL
