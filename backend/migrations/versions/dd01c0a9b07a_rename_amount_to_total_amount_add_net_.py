"""rename amount to total_amount, add net_amount

Revision ID: dd01c0a9b07a
Revises: 3cd2216f1a9f
Create Date: 2026-07-22 17:12:34.962053

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dd01c0a9b07a'
down_revision: Union[str, Sequence[str], None] = '3cd2216f1a9f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Rename in place — autogenerate proposed drop+add, which would discard
    every existing amount. `amount` always held the gross value, so it becomes
    `total_amount` directly."""
    op.alter_column('transactions', 'amount', new_column_name='total_amount')
    op.add_column('transactions', sa.Column('net_amount', sa.Float(), nullable=True))


def downgrade() -> None:
    """Reverse the rename, likewise without data loss."""
    op.drop_column('transactions', 'net_amount')
    op.alter_column('transactions', 'total_amount', new_column_name='amount')
