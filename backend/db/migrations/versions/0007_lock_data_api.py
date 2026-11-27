"""No access through the Supabase Data API: RLS on every table, client-role grants revoked.

The application connects to Postgres directly and does not use the Data API (D-022). Every table
gets row-level security with no policy (the owner and roles with BYPASSRLS, such as `postgres`,
are unaffected). On Supabase, where the roles `anon`, `authenticated` and `service_role`
exist, their grants on existing tables and sequences and their default privileges on future
objects are revoked (supabase.com/docs/guides/api/securing-your-api, read 2026-11-27).
Elsewhere (local Postgres) these roles do not exist and only RLS is enabled.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen list of the tables at this revision.
TABLES = (
    "alembic_version",
    "chunks",
    "chunk_versions",
    "changelog",
    "query_logs",
    "rules",
    "usage_counters",
)

REVOKE_CLIENT_ROLES = """
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon')
     AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated')
     AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated, service_role;
    REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated, service_role;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'postgres')
       AND pg_has_role(current_user, 'postgres', 'MEMBER') THEN
      ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
        REVOKE ALL ON TABLES FROM anon, authenticated, service_role;
      ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
        REVOKE ALL ON SEQUENCES FROM anon, authenticated, service_role;
      ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
        REVOKE ALL ON FUNCTIONS FROM anon, authenticated, service_role;
    END IF;
  END IF;
END
$$;
"""


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(REVOKE_CLIENT_ROLES)


def downgrade() -> None:
    """Disables RLS. Revoked Data API grants are not restored (they were never used)."""
    for table in TABLES:
        op.execute(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY")
