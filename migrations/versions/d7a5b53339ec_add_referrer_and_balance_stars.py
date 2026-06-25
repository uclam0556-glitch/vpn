"""Add referrer and balance_stars

Revision ID: d7a5b53339ec
Revises: 20260623_0002
Create Date: 2026-06-23 19:48:56.328401
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd7a5b53339ec'
down_revision: Union[str, Sequence[str], None] = '20260623_0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('customers', sa.Column('referrer_id', sa.Integer(), nullable=True))
    op.add_column('customers', sa.Column('balance_stars', sa.Integer(), server_default='0', nullable=False))
    op.create_foreign_key(None, 'customers', 'customers', ['referrer_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint(None, 'customers', type_='foreignkey')
    op.drop_column('customers', 'balance_stars')
    op.drop_column('customers', 'referrer_id')
