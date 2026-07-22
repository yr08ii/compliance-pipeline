from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from compliance.models import Merchant, Transaction

_DAY = datetime(2026, 7, 19, tzinfo=timezone.utc)


def seed(session: Session) -> None:
    merchants = [
        Merchant(merchant_id="M001", mcc="5411", lane="A"),   # grocery, mature
        Merchant(merchant_id="M002", mcc="5944", lane="A"),   # jewelry, mature
        Merchant(merchant_id="M003", mcc="5732", lane="B"),   # electronics, new
    ]
    session.add_all(merchants)
    txn_id = 0
    for m in merchants:
        base_amount = 120.0 if m.merchant_id != "M002" else 4000.0
        count = 10 if m.merchant_id != "M003" else 3
        for i in range(count):
            txn_id += 1
            session.add(Transaction(
                source_txn_id=f"T{txn_id:05d}",
                merchant_id=m.merchant_id,
                amount=base_amount * (1 + 0.1 * i),
                occurred_at=_DAY + timedelta(hours=i),
                is_refund=(i % 7 == 0),
                terminal_id=f"TERM-{m.merchant_id}",
                card_bin="457896",
                geo="HK",
            ))
