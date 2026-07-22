from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from compliance.models import Base, Merchant, Transaction
from compliance.seed import seed


def test_seed_inserts_merchants_and_transactions():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    with S() as s:
        seed(s)
        s.commit()
        merchants = s.scalar(select(func.count()).select_from(Merchant))
        txns = s.scalar(select(func.count()).select_from(Transaction))
        assert merchants >= 3
        assert txns >= 20
