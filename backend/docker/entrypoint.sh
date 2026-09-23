#!/bin/sh
# Container startup: make the environment ready, then hand off to the CMD.
# `set -e` matters — if a migration fails the container must die and be
# restarted, not serve traffic against a half-migrated schema.
set -e

# True for true/True/TRUE/1/yes, so compose files may spell booleans the same
# way .env does ("True"/"False") without silently flipping the meaning.
is_true() {
    case "$1" in
        [Tt][Rr][Uu][Ee]|1|[Yy][Ee][Ss]) return 0 ;;
        *) return 1 ;;
    esac
}

: "${DJANGO_SETTINGS_MODULE:?DJANGO_SETTINGS_MODULE must be set}"

# ---------------------------------------------------------------------------
# Wait for Postgres. compose's depends_on/healthcheck already gates this, but
# the database can also drop out and come back under the container's feet.
# ---------------------------------------------------------------------------
if [ -n "${DB_HOST:-}" ]; then
    echo "entrypoint: waiting for database at ${DB_HOST}:${DB_PORT:-5432}"
    attempts=0
    until python -c "
import os, sys, psycopg
try:
    psycopg.connect(
        dbname=os.environ['DB_NAME'], user=os.environ['DB_USER'],
        password=os.environ.get('DB_PASSWORD', ''), host=os.environ['DB_HOST'],
        port=os.environ.get('DB_PORT', '5432'), connect_timeout=3,
    ).close()
except Exception as exc:
    print(f'  not ready: {exc}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null; do
        attempts=$((attempts + 1))
        if [ "$attempts" -ge "${DB_WAIT_ATTEMPTS:-30}" ]; then
            echo "entrypoint: database unreachable after ${attempts} attempts" >&2
            exit 1
        fi
        sleep 2
    done
    echo "entrypoint: database is up"
fi

# ---------------------------------------------------------------------------
# Schema, static assets and the permission catalogue.
# Skip with RUN_MIGRATIONS=false when several replicas start at once and a
# separate release job owns migrations.
# ---------------------------------------------------------------------------
if is_true "${RUN_MIGRATIONS:-true}"; then
    echo "entrypoint: applying migrations"
    python manage.py migrate --noinput

    # The permission catalogue is declared in code, so it is re-synced on every
    # boot rather than carried in a data migration.
    echo "entrypoint: syncing permissions"
    python manage.py sync_permissions
fi

if is_true "${RUN_COLLECTSTATIC:-true}"; then
    echo "entrypoint: collecting static files"
    python manage.py collectstatic --noinput --clear
fi

echo "entrypoint: starting -> $*"
exec "$@"
