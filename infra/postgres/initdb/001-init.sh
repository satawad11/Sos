#!/bin/bash
# Runs once, on first start of the postgres volume.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE ROLE chirpstack LOGIN PASSWORD '${CHIRPSTACK_DB_PASSWORD}';
    CREATE DATABASE chirpstack OWNER chirpstack;
    CREATE ROLE sos LOGIN PASSWORD '${SOS_DB_PASSWORD}';
    CREATE DATABASE sos OWNER sos;
EOSQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname chirpstack <<-EOSQL
    CREATE EXTENSION pg_trgm;
    CREATE EXTENSION hstore;
EOSQL
