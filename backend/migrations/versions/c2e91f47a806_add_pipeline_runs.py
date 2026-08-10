"""add pipeline runs

A run is a statement about a scored day made under a particular set of
parameters. Two runs over one day carry the same `as_of` — that is the normal
case, not an edge one, since the reason to re-score a day is usually that a
threshold moved — so until now nothing in the schema could tell them apart.

Alerts already in the table are grouped into runs by the day they were written,
which is the only discriminator those rows carry. The reconstructed runs get no
settings snapshot: the thresholds in force at the time were never recorded, and
writing today's into a historic run would assert something nobody verified. All
but the newest are marked superseded, so the queue shows one statement per day.

Revision ID: c2e91f47a806
Revises: b5c8f0a3d112
Create Date: 2026-08-10 16:02:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c2e91f47a806'
down_revision: Union[str, Sequence[str], None] = 'b5c8f0a3d112'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'pipeline_runs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('as_of', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        # NULL means this run currently speaks for its scored day.
        sa.Column('superseded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('settings', sa.JSON(), nullable=False),
        sa.Column('rules', sa.JSON(), nullable=False),
        sa.Column('alert_count', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(), nullable=True),
        sa.Column('triggered_by', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_pipeline_runs_as_of'), 'pipeline_runs', ['as_of'])
    op.create_index(
        op.f('ix_pipeline_runs_superseded_at'), 'pipeline_runs', ['superseded_at']
    )

    op.add_column('alerts', sa.Column('run_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_alerts_run_id'), 'alerts', ['run_id'])
    op.create_foreign_key(
        'fk_alerts_run_id', 'alerts', 'pipeline_runs', ['run_id'], ['id']
    )

    _backfill_runs()


def _backfill_runs() -> None:
    """Reconstruct one run per day of alert-writing, and attribute the alerts.

    Grouped on the date the rows were written, because that is the only thing
    distinguishing the runs that produced them — `as_of` is identical across
    all of them. Coarse by construction: two runs on one calendar day would be
    reconstructed as one, and no reconstruction can recover what was never
    recorded. It is enough to make the existing queue attributable and to give
    supersession something to act on.
    """
    bind = op.get_bind()
    days = bind.execute(
        sa.text(
            "SELECT DATE(created_at) AS day, MIN(created_at) AS started, "
            "       MAX(created_at) AS finished, COUNT(*) AS n, MIN(as_of) AS as_of "
            "FROM alerts WHERE as_of IS NOT NULL "
            "GROUP BY DATE(created_at) ORDER BY day"
        )
    ).fetchall()

    for index, row in enumerate(days):
        is_newest = index == len(days) - 1
        run_id = bind.execute(
            sa.text(
                "INSERT INTO pipeline_runs "
                "(as_of, started_at, finished_at, superseded_at, settings, rules, "
                " alert_count, label, triggered_by) "
                "VALUES (:as_of, :started, :finished, :superseded, '{}', '[]', "
                "        :n, :label, 'backfill') "
                "RETURNING id"
            ),
            {
                "as_of": row.as_of,
                "started": row.started,
                "finished": row.finished,
                # Everything but the newest run for a day is history.
                "superseded": None if is_newest else row.finished,
                "n": row.n,
                "label": f"reconstructed from alerts written {row.day}",
            },
        ).scalar_one()

        bind.execute(
            sa.text(
                "UPDATE alerts SET run_id = :run_id "
                "WHERE DATE(created_at) = :day AND as_of IS NOT NULL"
            ),
            {"run_id": run_id, "day": row.day},
        )


def downgrade() -> None:
    op.drop_constraint('fk_alerts_run_id', 'alerts', type_='foreignkey')
    op.drop_index(op.f('ix_alerts_run_id'), table_name='alerts')
    op.drop_column('alerts', 'run_id')
    op.drop_index(op.f('ix_pipeline_runs_superseded_at'), table_name='pipeline_runs')
    op.drop_index(op.f('ix_pipeline_runs_as_of'), table_name='pipeline_runs')
    op.drop_table('pipeline_runs')
