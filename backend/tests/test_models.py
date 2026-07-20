from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from compliance.models import Base, Merchant, Alert


def test_create_merchant_and_alert():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        m = Merchant(merchant_id="M001", mcc="5411", lane="B")
        s.add(m)
        s.flush()
        a = Alert(
            merchant_id="M001",
            lane="B",
            blended_score=0.82,
            rank=1,
            triggering_detectors=[{"detector": "velocity_cap", "sub_score": 0.9}],
            feature_snapshot=[
                {"feature_name": "daily_volume", "merchant_value": 50000,
                 "baseline_value": 8000, "deviation": 5.25}
            ],
        )
        s.add(a)
        s.commit()
        assert s.get(Alert, a.id).feature_snapshot[0]["deviation"] == 5.25
