"""add alert_type column

Caches the triage badge the queue filters and counts on. Derived in Python, it
forced every page view to materialise the entire open queue — both JSON columns
included — to answer a question about twenty rows.

Backfilled here from the same detector map the badge itself uses, so existing
alerts are filterable the moment this lands rather than after the next run.

Revision ID: a7d4e2f19b30
Revises: 3b883bb20491
Create Date: 2026-08-10 10:41:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7d4e2f19b30'
down_revision: Union[str, Sequence[str], None] = '3b883bb20491'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('alerts', sa.Column('alert_type', sa.String(), nullable=True))
    op.create_index(op.f('ix_alerts_alert_type'), 'alerts', ['alert_type'], unique=False)
    # The queue is ordered by rank on every page; with the slice pushed into
    # SQL, the ordered scan is what remains to pay for.
    op.create_index(op.f('ix_alerts_rank'), 'alerts', ['rank'], unique=False)
    _backfill_alert_type()


def _backfill_alert_type() -> None:
    """Label existing alerts from the detector map in force today.

    Grouped by badge rather than row by row: the map is small and fixed, so
    this is one UPDATE per badge instead of one per alert. Anything whose
    detector the map does not know takes the same fallback `alert_type_for`
    applies — an unlabelled alert is one that no filter returns and no chip
    counts, invisible to the analyst working that type.
    """
    from collections import defaultdict

    from compliance.glossary import DETECTOR_ALERT_TYPE, alert_type_for

    by_type: dict[str, list[str]] = defaultdict(list)
    for detector, alert_type in DETECTOR_ALERT_TYPE.items():
        by_type[alert_type].append(detector)

    bind = op.get_bind()
    for alert_type, detectors in by_type.items():
        bind.execute(
            sa.text(
                "UPDATE alerts SET alert_type = :alert_type "
                "WHERE triggering_detectors -> 0 ->> 'detector' = ANY(:detectors)"
            ),
            {"alert_type": alert_type, "detectors": detectors},
        )
    bind.execute(
        sa.text("UPDATE alerts SET alert_type = :fallback WHERE alert_type IS NULL"),
        {"fallback": alert_type_for("")},
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_alerts_rank'), table_name='alerts')
    op.drop_index(op.f('ix_alerts_alert_type'), table_name='alerts')
    op.drop_column('alerts', 'alert_type')
