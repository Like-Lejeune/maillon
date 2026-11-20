"""Schéma initial.

Applique migrations/sql/0001_initial.sql, copie figée de docs/schema.sql à la
version 1. Toute évolution du schéma passe par une NOUVELLE migration : ce
fichier et son SQL ne se modifient plus une fois déployés.

Revision ID: 0001
Revises:
"""
from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0001_initial.sql"


def upgrade() -> None:
    # exec_driver_sql envoie le script tel quel, sans interpréter les « : » ni
    # les « % » qu'il contient.
    op.get_bind().exec_driver_sql(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("Le schéma initial ne se retire pas : restaurer une sauvegarde.")
