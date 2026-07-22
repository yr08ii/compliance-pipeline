from collections.abc import Iterator
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from compliance.config import settings

engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
