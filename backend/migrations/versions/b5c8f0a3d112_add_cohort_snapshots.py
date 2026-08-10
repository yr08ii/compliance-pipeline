"""add cohort snapshots

Keeps each MCC cohort as the run fitted it, so the case page can plot the
distribution instead of rebuilding it. Rebuilding cost one query per member
over that member's whole history, which was slow enough that the read path had
capped itself at 200 members of cohorts that run to hundreds — and it read
all-time data with no quarantine exclusion, so the points drawn were not the
ones the median and fence were cut from.

No backfill. A cohort is a property of a run's window, and inventing one from
today's data would state a distribution no run ever fitted. Until the next run
writes them, the peer plot is empty — which is the honest reading of a cohort
that has not been fitted.

Revision ID: b5c8f0a3d112
Revises: a7d4e2f19b30
Create Date: 2026-08-10 10:44:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5c8f0a3d112'
down_revision: Union[str, Sequence[str], None] = 'a7d4e2f19b30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'cohort_snapshots',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('as_of', sa.DateTime(timezone=True), nullable=False),
        sa.Column('mcc', sa.String(), nullable=False),
        sa.Column('center', sa.Float(), nullable=False),
        sa.Column('dispersion', sa.Float(), nullable=False),
        sa.Column('q1', sa.Float(), nullable=False),
        sa.Column('q3', sa.Float(), nullable=False),
        sa.Column('upper_fence', sa.Float(), nullable=True),
        sa.Column('n_merchants', sa.Integer(), nullable=False),
        sa.Column('usable', sa.Boolean(), nullable=False),
        # One typical ticket per member, ascending. The distribution itself,
        # not a summary of it: quartiles and plotted points then come from one
        # set of numbers and cannot disagree.
        sa.Column('members', sa.JSON(), nullable=False),
        sa.Column('window_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('window_end', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_cohort_snapshots_as_of'), 'cohort_snapshots', ['as_of'])
    op.create_index(op.f('ix_cohort_snapshots_mcc'), 'cohort_snapshots', ['mcc'])


def downgrade() -> None:
    op.drop_index(op.f('ix_cohort_snapshots_mcc'), table_name='cohort_snapshots')
    op.drop_index(op.f('ix_cohort_snapshots_as_of'), table_name='cohort_snapshots')
    op.drop_table('cohort_snapshots')
