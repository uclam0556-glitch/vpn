"""add withdrawal method and requisites to customers

Revision ID: c1d2e3f4a5b6
Revises: af36f112bcd0
Create Date: 2026-06-26
"""

import sqlalchemy as sa
from alembic import op

revision = "c1d2e3f4a5b6"
down_revision = "af36f112bcd0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("withdrawal_method", sa.String(length=32), nullable=True))
    op.add_column(
        "customers", sa.Column("withdrawal_requisites", sa.String(length=255), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("customers", "withdrawal_requisites")
    op.drop_column("customers", "withdrawal_method")
