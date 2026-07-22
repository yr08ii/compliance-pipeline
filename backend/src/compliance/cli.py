"""Local bootstrap: seed synthetic data and run the pipeline once."""

from sqlalchemy import delete

from compliance.db import SessionLocal
from compliance.models import Alert, CaseEvent, Disposition, Merchant, MerchantProfile, Transaction, TrainingBatch
from compliance.pipeline.flow import run_pipeline
from compliance.seed import seed


def _reset_demo_data(session) -> None:
    for model in (CaseEvent, Disposition, Alert, TrainingBatch, MerchantProfile, Transaction, Merchant):
        session.execute(delete(model))


def main() -> None:
    with SessionLocal() as session:
        _reset_demo_data(session)
        session.commit()
        seed(session)
        session.commit()
        alert_count = run_pipeline(session)
        session.commit()
        print(f"pipeline complete: {alert_count} alert(s) written")


if __name__ == "__main__":
    main()