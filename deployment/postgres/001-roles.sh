#!/usr/bin/env bash
set -eu
: "${ALFA_MIGRATOR_DB_PASSWORD:?required}"
: "${ALFA_BOT_DB_PASSWORD:?required}"
: "${ALFA_WORKER_DB_PASSWORD:?required}"
# psql getenv avoids passwords in process arguments; :'var' quotes SQL literals.
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 <<'SQL'
\getenv migrator_password ALFA_MIGRATOR_DB_PASSWORD
\getenv bot_password ALFA_BOT_DB_PASSWORD
\getenv worker_password ALFA_WORKER_DB_PASSWORD
CREATE ROLE alfa_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'migrator_password';
CREATE ROLE alfa_bot LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'bot_password';
CREATE ROLE alfa_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'worker_password';
SELECT current_database() AS database_name \gset
REVOKE ALL ON DATABASE :"database_name" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"database_name" TO alfa_migrator, alfa_bot, alfa_worker;
GRANT CREATE ON DATABASE :"database_name" TO alfa_migrator;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
SQL
