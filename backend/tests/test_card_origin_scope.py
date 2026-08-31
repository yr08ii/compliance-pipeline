"""What counts as a card origin.

Over half the real extract is a wallet rail — Alipay, Octopus, WeChat Pay,
PayMe — and a wallet has no issuing country, so the source writes an empty
string. An empty string is not NULL, so it passed every null check and then
behaved as a country in its own right:

  * `merchant_foreign_ratio` asked `country != 'HK'`, which an empty string
    satisfies. Every wallet tap counted as a foreign-issued card. Across the
    portfolio's scored day that reported a mean foreign share of 53% where the
    true figure is 10%.
  * `fit_origin_mix` admitted it as an origin, so the modal "country" for a
    wallet-heavy merchant was the absence of one.

A transaction now carries a card origin only when it names a real issuing
country. That covers today's wallet rails without naming them, and covers the
card transactions whose origin the extract simply does not have.

Separately, and by instruction: the origin *mix* is now foreign cards only.
Hong Kong is home, and its share drowned the distribution the detector exists
to watch — a change in which overseas countries are paying.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from compliance.detection.profiles import fit_origin_mix
from compliance.detection.windows import (
    fit_peer_foreign_ratio_baselines,
    merchant_foreign_ratio,
)
from compliance.models import Base, Merchant, Transaction

HKT = timezone(timedelta(hours=8))
AS_OF = datetime(2026, 5, 1, tzinfo=HKT)
SCORED_DAY = AS_OF - timedelta(days=1)

# The wallet rails in the extract. Named here only to build realistic
# fixtures — the code under test keys on the absent issuing country, not on
# this list, so a new rail needs no code change.
WALLETS = ("ALIPAY", "OCTOPUS", "WECHAT", "PAYME")


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s


def _txn(s, merchant_id, txn_id, *, country, card_type, at, amount=100.0):
    s.add(
        Transaction(
            source_txn_id=txn_id,
            merchant_id=merchant_id,
            total_amount=amount,
            occurred_at=at,
            is_refund=False,
            card_type=card_type,
            card_issuing_country=country,
        )
    )


class TestForeignRatio:
    def test_wallets_are_not_foreign_cards(self, session):
        """Nine Octopus taps and one Japanese card is a 10% foreign day."""
        session.add(Merchant(merchant_id="M1", mcc="5411", merchant_subdistrict="TST"))
        session.flush()
        for i in range(9):
            _txn(
                session, "M1", f"W{i}",
                country="", card_type="OCTOPUS",
                at=SCORED_DAY.replace(hour=10),
            )
        for i in range(9):
            _txn(
                session, "M1", f"H{i}",
                country="HK", card_type="VISA",
                at=SCORED_DAY.replace(hour=11),
            )
        _txn(
            session, "M1", "F1",
            country="JP", card_type="VISA",
            at=SCORED_DAY.replace(hour=12),
        )
        session.commit()

        # Ten cards, one of them overseas. The nine wallet taps are not cards
        # and take no part in the ratio.
        assert merchant_foreign_ratio(session, "M1", AS_OF) == pytest.approx(0.1)

    def test_wallet_only_merchant_has_no_ratio(self, session):
        """A merchant that took no cards has no foreign-card share.

        Not zero and certainly not one: the quantity is undefined, and saying
        either would put a merchant into a district comparison on the strength
        of a number nobody measured.
        """
        session.add(Merchant(merchant_id="M2", mcc="5411", merchant_subdistrict="TST"))
        session.flush()
        for i in range(12):
            _txn(
                session, "M2", f"W{i}",
                country="", card_type=WALLETS[i % len(WALLETS)],
                at=SCORED_DAY.replace(hour=10),
            )
        session.commit()

        assert merchant_foreign_ratio(session, "M2", AS_OF) is None

    def test_district_baseline_ignores_wallets(self, session):
        """The cohort norm must be built the same way as the value judged
        against it, or a merchant is measured against a different quantity."""
        for n in range(6):
            merchant_id = f"D{n}"
            session.add(
                Merchant(merchant_id=merchant_id, mcc="5411", merchant_subdistrict="TST")
            )
            session.flush()
            for i in range(20):
                _txn(
                    session, merchant_id, f"{merchant_id}-W{i}",
                    country="", card_type="OCTOPUS",
                    at=AS_OF - timedelta(days=10, hours=i % 12),
                )
            for i in range(8):
                _txn(
                    session, merchant_id, f"{merchant_id}-H{i}",
                    country="HK", card_type="VISA",
                    at=AS_OF - timedelta(days=10, hours=i % 12),
                )
            for i in range(2):
                _txn(
                    session, merchant_id, f"{merchant_id}-F{i}",
                    country="US", card_type="VISA",
                    at=AS_OF - timedelta(days=10, hours=i % 12),
                )
        session.commit()

        cohorts = fit_peer_foreign_ratio_baselines(session, AS_OF, 30, lag_days=0)

        # Each member is 2 foreign of 10 cards. Counting the 20 wallet taps as
        # foreign would put the district norm above 0.7 instead.
        assert cohorts["TST"].center == pytest.approx(0.2)


class TestOriginMix:
    def test_mix_counts_only_foreign_issuers(self, session):
        """Home cards and wallets are both outside the distribution."""
        session.add(Merchant(merchant_id="M3", mcc="5411"))
        session.flush()
        for i in range(50):
            _txn(
                session, "M3", f"H{i}",
                country="HK", card_type="VISA",
                at=AS_OF - timedelta(days=5),
            )
        for i in range(30):
            _txn(
                session, "M3", f"W{i}",
                country="", card_type="ALIPAY",
                at=AS_OF - timedelta(days=5),
            )
        for i in range(20):
            _txn(
                session, "M3", f"J{i}",
                country="JP", card_type="VISA",
                at=AS_OF - timedelta(days=5),
            )
        for i in range(4):
            _txn(
                session, "M3", f"U{i}",
                country="US", card_type="VISA",
                at=AS_OF - timedelta(days=5),
            )
        session.commit()

        mix = fit_origin_mix(session, "M3", AS_OF, 30)

        assert set(mix.counts) == {"JP", "US"}
        assert mix.n == 24
        assert mix.share("JP") == pytest.approx(20 / 24)
        # Neither is a country this merchant sees.
        assert mix.counts.get("HK") is None
        assert mix.counts.get("") is None

    def test_usability_is_measured_on_foreign_cards(self, session):
        """A merchant with a thousand home cards and four foreign ones has no
        foreign-origin pattern to compare against, and must not be scored as
        though it did."""
        session.add(Merchant(merchant_id="M4", mcc="5411"))
        session.flush()
        for i in range(1000):
            _txn(
                session, "M4", f"H{i}",
                country="HK", card_type="VISA",
                at=AS_OF - timedelta(days=5),
            )
        for i in range(4):
            _txn(
                session, "M4", f"F{i}",
                country="TH", card_type="VISA",
                at=AS_OF - timedelta(days=5),
            )
        session.commit()

        mix = fit_origin_mix(session, "M4", AS_OF, 30)

        assert mix.n == 4
        assert not mix.usable
