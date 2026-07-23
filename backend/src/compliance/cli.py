"""Local bootstrap: generate synthetic history and run the pipeline once."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from compliance.db import SessionLocal
from compliance.models import (
    Alert,
    CaseEvent,
    Disposition,
    Merchant,
    MerchantProfile,
    TrainingBatch,
    Transaction,
)
from compliance.pipeline.flow import run_pipeline
from compliance.synthetic import generate_history


def _reset_demo_data(session) -> None:
    for model in (CaseEvent, Disposition, Alert, TrainingBatch, MerchantProfile,
                  Transaction, Merchant):
        session.execute(delete(model))


# The scored day is the Hong Kong business day. Anchoring to UTC midnight
# would cut the day at 08:00 local, splitting a merchant's trading in two.
HKT = timezone(timedelta(hours=8))


def main() -> None:
    as_of = datetime.now(HKT).replace(hour=0, minute=0, second=0, microsecond=0)
    with SessionLocal() as session:
        _reset_demo_data(session)
        session.commit()
        generate_history(session, as_of=as_of)
        session.commit()
        alert_count = run_pipeline(session, as_of=as_of)
        session.commit()
        print(f"pipeline complete: {alert_count} alert(s) written")


if __name__ == "__main__":
    main()
