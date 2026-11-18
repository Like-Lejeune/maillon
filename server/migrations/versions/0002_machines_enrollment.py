"""Machines : expiration du jeton d'inscription et retrait par le fournisseur.

- enrollment_expires_at : un jeton d'inscription n'est valable que 24 heures.
- removed_at : une machine retirée disparaît des listes et perd son jeton
  d'agent, mais sa ligne reste pour l'historique des tâches et des gains.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE machines ADD COLUMN enrollment_expires_at timestamptz")
    op.execute("ALTER TABLE machines ADD COLUMN removed_at timestamptz")
    op.execute(
        "CREATE INDEX machines_provider_active_idx ON machines (provider_id, created_at DESC) "
        "WHERE removed_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS machines_provider_active_idx")
    op.execute("ALTER TABLE machines DROP COLUMN IF EXISTS removed_at")
    op.execute("ALTER TABLE machines DROP COLUMN IF EXISTS enrollment_expires_at")
