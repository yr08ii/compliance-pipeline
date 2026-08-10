import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from compliance.ingest import IngestResult, ingest_payload, parse_json
from compliance.models import Base, Merchant, Transaction


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


ROW = {
    "payment_id": "P0001",
    "merchant_id": "M001",
    "total_amount": "250.50",
    "net_amount": "243.00",
    "hkt_transaction_time": "2026-07-19 14:32:00",
    "transaction_status": "SUCCESS",
    "card_type": "VISA",
    "card_origin": "LOCAL",
    "card_issuing_country": "HK",
    "card_issuing_bank": "HSBC",
    "payment_gateway": "GW1",
    "currency": "HKD",
    "hashed_pan": "abc123",
    "masked_pan": "457896******1234",
    "mcc": "5411",
    "mcc_description": "Grocery",
    "agent_id": "AG7",
    "hashed_merchant_name": "hmn1",
    "hashed_br_number": "hbr1",
    "hashed_merchant_address": "hma1",
    "city": "Hong Kong",
    "merchant_area": "Kowloon",
    "merchant_district": "Yau Tsim Mong",
    "merchant_subdistrict": "Mong Kok",
    "business_plan": "STANDARD",
    "business_nature": "Retail",
    "ownership_or_business_type": "LIMITED",
    "merchant_status": "ACTIVE",
}


class TestParse:
    def test_reads_a_json_array(self):
        rows = parse_json(json.dumps([ROW]))
        assert rows[0]["payment_id"] == "P0001"

    def test_reads_a_json_lines_payload(self):
        rows = parse_json(json.dumps(ROW) + "\n" + json.dumps({**ROW, "payment_id": "P2"}))
        assert [r["payment_id"] for r in rows] == ["P0001", "P2"]

    def test_reads_a_wrapped_payload(self):
        rows = parse_json(json.dumps({"data": [ROW]}))
        assert len(rows) == 1


class TestIngest:
    def test_creates_merchant_and_transaction(self, session):
        result = ingest_payload(session, json.dumps([ROW]))
        session.flush()

        assert result == IngestResult(inserted=1, skipped=0, merchants=1)
        txn = session.scalars(select(Transaction)).one()
        assert txn.total_amount == 250.50
        assert txn.card_issuing_country == "HK"
        assert txn.occurred_at.hour == 14
        merchant = session.scalars(select(Merchant)).one()
        assert merchant.merchant_subdistrict == "Mong Kok"
        assert merchant.hashed_br_number == "hbr1"

    def test_is_idempotent_on_payment_id(self, session):
        """A re-delivered or re-run file must not double-count."""
        ingest_payload(session, json.dumps([ROW]))
        session.flush()
        again = ingest_payload(session, json.dumps([ROW]))
        session.flush()

        assert again.inserted == 0
        assert again.skipped == 1
        assert len(list(session.scalars(select(Transaction)))) == 1

    def test_does_not_store_masked_pan(self, session):
        """Display-only and never an identifier, so it is not copied at all."""
        ingest_payload(session, json.dumps([ROW]))
        session.flush()
        txn = session.scalars(select(Transaction)).one()
        assert not hasattr(txn, "masked_pan")

    def test_marks_refunds_from_status(self, session):
        ingest_payload(session, json.dumps([{**ROW, "payment_id": "R1",
                                             "transaction_status": "REFUNDED"}]))
        session.flush()
        assert session.scalars(select(Transaction)).one().is_refund is True

    def test_marks_refunds_from_a_negative_amount(self, session):
        ingest_payload(session, json.dumps([{**ROW, "payment_id": "R2",
                                             "total_amount": "-80.00"}]))
        session.flush()
        txn = session.scalars(select(Transaction)).one()
        assert txn.is_refund is True
        assert txn.total_amount == 80.0, "amount is stored as magnitude"

    def test_updates_merchant_metadata_on_later_files(self, session):
        ingest_payload(session, json.dumps([ROW]))
        session.flush()
        ingest_payload(session, json.dumps([{**ROW, "payment_id": "P2",
                                             "merchant_status": "SUSPENDED"}]))
        session.flush()
        assert session.scalars(select(Merchant)).one().merchant_status == "SUSPENDED"


class TestBlankFieldsBecomeNull:
    """A blank in the source means "absent", and it has to be stored that way.

    The real extract carries `hashed_pan=''` on every wallet rail — Alipay,
    Octopus, WeChat Pay, PayMe — because a wallet has no card number to hash.
    Stored as an empty string, those rows are all *equal* to one another, so a
    card-linkage rule reads more than half the portfolio as a single card
    visiting thousands of merchants. That is not a tuning problem; it is one
    value pretending to be an identifier.
    """

    def test_a_blank_pan_is_stored_as_null(self, session):
        ingest_payload(session, json.dumps([{**ROW, "payment_id": "W1",
                                             "card_type": "OCTOPUS",
                                             "hashed_pan": ""}]))
        session.flush()
        assert session.scalars(select(Transaction)).one().hashed_pan is None

    def test_a_whitespace_pan_is_stored_as_null(self, session):
        ingest_payload(session, json.dumps([{**ROW, "payment_id": "W2",
                                             "hashed_pan": "  "}]))
        session.flush()
        assert session.scalars(select(Transaction)).one().hashed_pan is None

    def test_two_wallet_rows_do_not_share_an_identifier(self, session):
        rows = [
            {**ROW, "payment_id": "W3", "merchant_id": "M1",
             "card_type": "ALIPAY", "hashed_pan": ""},
            {**ROW, "payment_id": "W4", "merchant_id": "M2",
             "card_type": "WECHAT", "hashed_pan": ""},
        ]
        ingest_payload(session, json.dumps(rows))
        session.flush()
        pans = [t.hashed_pan for t in session.scalars(select(Transaction))]
        assert pans == [None, None]

    def test_a_blank_merchant_hash_is_stored_as_null(self, session):
        """Same failure on the identity side: every merchant with no business
        registration on file would otherwise join one enormous ring."""
        ingest_payload(session, json.dumps([{**ROW, "payment_id": "B1",
                                             "hashed_br_number": ""}]))
        session.flush()
        assert session.scalars(select(Merchant)).one().hashed_br_number is None

    def test_a_real_value_is_untouched(self, session):
        ingest_payload(session, json.dumps([ROW]))
        session.flush()
        assert session.scalars(select(Transaction)).one().hashed_pan == "abc123"
