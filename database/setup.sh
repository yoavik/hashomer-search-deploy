#!/bin/bash
set -euo pipefail

# =============================================================================
# Database Setup (Supabase / PostgreSQL)
# =============================================================================
# Applies schema.sql + all migrations.
# Search runs on EC2 (not as a Supabase Edge Function).
#
# Usage:
#   ./deploy/database/setup.sh --supabase         # guided Supabase setup
#   ./deploy/database/setup.sh --psql <DB_URL>     # direct psql apply
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_DIR="${SCRIPT_DIR}/src"
MODE="${1:---supabase}"

echo "=== Database Setup ==="

case "${MODE}" in
    --supabase)
        echo ""
        echo "Step 1: Create a free Supabase project at https://supabase.com/dashboard"
        echo ""
        echo "Step 2: Apply schema + migrations via SQL Editor (Dashboard > SQL Editor)"
        echo "  Paste and run each file in this order:"
        echo "    1. src/schema.sql"
        for migration in $(ls "${SRC_DIR}/migrations/"*.sql 2>/dev/null | sort); do
            echo "    - src/migrations/$(basename "${migration}")"
        done
        echo ""
        echo "Step 3: Collect credentials for EC2 services:"
        echo "  - SUPABASE_URL (Project URL)"
        echo "  - SUPABASE_KEY (service_role key)"
        ;;

    --psql)
        DB_URL="${2:?Usage: $0 --psql <DATABASE_URL>}"
        echo "Applying to: ${DB_URL}"

        echo "Enabling extensions..."
        psql "${DB_URL}" -c "CREATE EXTENSION IF NOT EXISTS vector;"
        psql "${DB_URL}" -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"

        echo "Applying schema..."
        psql "${DB_URL}" -f "${SRC_DIR}/schema.sql"

        echo "Applying migrations..."
        for migration in $(ls "${SRC_DIR}/migrations/"*.sql 2>/dev/null | sort); do
            echo "  $(basename "${migration}")"
            psql "${DB_URL}" -f "${migration}"
        done

        echo "=== Database setup complete ==="
        ;;

    *)
        echo "Usage: $0 [--supabase | --psql <DATABASE_URL>]"
        exit 1
        ;;
esac
