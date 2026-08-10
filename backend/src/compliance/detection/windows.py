"""Fitting merchant baselines from the local transaction store.

Baselines are recomputed on every nightly run from a rolling window, not
refreshed periodically: a merchant that legitimately grows would otherwise be
scored against a stale baseline — and flagged for it — until the next refresh.

Every merchant is fitted in one pass over the window rather than fetched
individually, because per-merchant queries scale with the merchant count and
cannot build the peer cohorts that Family A's peer baselines need.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from compliance.detection.baselines import (
    MIN_COHORT_MERCHANTS,
    MIN_COUNT_DISPERSION,
    MIN_RATIO_DISPERSION,
    Baseline,
    PeerBaseline,
    Trend,
    detect_ramp,
    fit_baseline,
    fit_peer_baseline,
)
from compliance.models import Alert, Disposition, Merchant, Transaction

# Where the portfolio trades. A card issued here is a home card; everything
# else is overseas.
HOME_COUNTRY = "HK"

# Outcomes where no value reached the merchant. Enumerated rather than defined
# as "not SUCCESS": the extract also carries AUTHORIZED, PENDING, and rows with
# no status at all, whose outcome is unknown. Unknown is not failed, and
# counting it as failed would be a claim the data does not make.
UNSETTLED_STATUSES = ("DECLINED", "CANCELLED", "REVERSED", "VOIDED")


def settled_sale():
    """Rows where value actually moved to the merchant.

    Every baseline is a statement about what this merchant's trade normally
    looks like, and an attempt that failed is not trade. The distortion was not
    even-handed: a declined transaction averages HKD 1,355 against HKD 253 for
    a successful one, so failures pulled ticket baselines upward — and they
    counted as ordinary volume, which taught the volume baseline that a burst
    of declines is a normal day. That burst is the card-testing pattern the
    decline rule exists to catch.

    Rows with no status are kept. They are 8,078 of the extract and their
    outcome is simply not recorded; dropping them would discard data on the
    strength of a guess, in the direction that quietly shrinks every baseline.
    """
    return (
        Transaction.is_refund.is_(False),
        or_(
            Transaction.transaction_status.is_(None),
            Transaction.transaction_status.not_in(UNSETTLED_STATUSES),
        ),
    )


def has_card_origin():
    """Rows that name a real issuing country.

    Over half the extract is a wallet rail — Alipay, Octopus, WeChat Pay,
    PayMe — and a wallet has no card to have been issued anywhere, so the
    source writes `card_issuing_country` as an empty string. An empty string
    is not NULL: it passed every null check and then compared unequal to "HK",
    so each wallet tap counted as a foreign-issued card. On the scored day that
    put the portfolio's mean foreign share at 53% against a true 10%.

    Keyed on the absent country rather than on a list of wallet rails, so a
    rail added to the extract tomorrow is handled without a code change — and
    so the card transactions whose origin the extract simply does not carry are
    excluded too, which is the same statement: origin unknown, no vote.
    """
    return (
        Transaction.card_issuing_country.is_not(None),
        Transaction.card_issuing_country != "",
    )


def _window_bounds(
    as_of: datetime, window_days: int, lag_days: int = 0
) -> tuple[datetime, datetime]:
    """The half-open baseline window [start, end).

    `lag_days` holds the window back from the present. This is not a tuning
    knob: a disposition takes days to arrive, so the lag is the interval in
    which analysts can still rule on activity before it is absorbed into the
    baseline. Without it, data is normalised before anyone has judged it.
    """
    end = as_of - timedelta(days=lag_days)
    return end - timedelta(days=window_days), end


def _cohort_column(dimension: str):
    """Which merchant attribute groups a cohort.

    Trade and place are separate questions: a HKD 800 ticket is ordinary for a
    Central restaurant and remarkable for one in Sham Shui Po, and neither is
    visible from MCC alone. They are kept as independent dimensions rather than
    a combined MCC-by-district key, which would shatter into cohorts too small
    to be distributions.
    """
    if dimension == "mcc":
        return Merchant.mcc
    if dimension == "subdistrict":
        return Merchant.merchant_subdistrict
    raise ValueError(f"unknown cohort dimension: {dimension!r}")


def quarantined_days(session: Session) -> set[tuple[str, date]]:
    """(merchant, day) pairs confirmed as genuinely bad.

    Transactions from these days must never shape a future baseline, or the
    system absorbs its own confirmed findings as normal. Only TRUE_POSITIVE
    counts — a cleared alert means the behaviour was legitimate and belongs
    in the baseline.
    """
    rows = session.execute(
        select(Alert.merchant_id, Alert.created_at)
        .join(Disposition, Disposition.alert_id == Alert.id)
        .where(Disposition.verdict == "TRUE_POSITIVE")
    )
    return {(merchant_id, created_at.date()) for merchant_id, created_at in rows}


def amounts_in_window(
    session: Session, merchant_id: str, as_of: datetime, window_days: int
) -> list[float]:
    """Gross transaction amounts for one merchant over [as_of - window, as_of).

    `as_of` is exclusive so a nightly run scoring the prior day cannot leak the
    day being scored into the baseline it is scored against.

    Refunds are excluded: they are value moving the other way, and mixing them
    into the ticket baseline distorts the merchant's normal. The refund-ratio
    rule covers them instead.
    """
    start, end = _window_bounds(as_of, window_days)
    stmt = (
        select(Transaction.total_amount)
        .where(
            Transaction.merchant_id == merchant_id,
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
            *settled_sale(),
        )
        .order_by(Transaction.occurred_at)
    )
    return list(session.scalars(stmt))


def fit_all_baselines(
    session: Session,
    as_of: datetime,
    window_days: int,
    *,
    min_observations: int,
    min_span_days: int = 0,
    lag_days: int = 0,
    exclude_quarantined: bool = True,
) -> dict[str, Baseline]:
    """Fit an amount baseline for every merchant in one pass.

    Maturity is *count and elapsed days*, not count alone. A merchant trading
    heavily for three days can clear an observation threshold while having no
    weekly or seasonal shape at all — its "normal" is not yet a normal, and
    scoring against it manufactures confident nonsense.

    Merchants with too little history are returned with an explicitly unusable
    baseline rather than omitted — the router needs to see them to send them
    down Lane B, and a silently missing merchant is a merchant nobody monitors.
    """
    start, end = _window_bounds(as_of, window_days, lag_days)
    rows = session.execute(
        select(Transaction.merchant_id, Transaction.total_amount, Transaction.occurred_at)
        .where(
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
            *settled_sale(),
        )
        .order_by(Transaction.merchant_id, Transaction.occurred_at)
    )

    quarantined = quarantined_days(session) if exclude_quarantined else set()

    amounts: dict[str, list[float]] = {}
    first_seen: dict[str, datetime] = {}
    last_seen: dict[str, datetime] = {}
    for merchant_id, amount, occurred_at in rows:
        if (merchant_id, occurred_at.date()) in quarantined:
            continue
        amounts.setdefault(merchant_id, []).append(amount)
        first_seen.setdefault(merchant_id, occurred_at)
        last_seen[merchant_id] = occurred_at

    def _fit(merchant_id: str) -> Baseline:
        values = amounts.get(merchant_id, [])
        if merchant_id in first_seen:
            span = (last_seen[merchant_id] - first_seen[merchant_id]).total_seconds() / 86400
            if span < min_span_days:
                # Enough transactions, not enough elapsed time.
                return fit_baseline(values, min_observations=len(values) + 1)
        return fit_baseline(values, min_observations=min_observations)

    return {
        merchant_id: _fit(merchant_id)
        for merchant_id in session.scalars(select(Merchant.merchant_id))
    }


def fit_payment_method_baselines(
    session: Session,
    as_of: datetime,
    window_days: int,
    *,
    min_observations: int,
    lag_days: int = 0,
    exclude_quarantined: bool = True,
) -> dict[str, dict[str, Baseline]]:
    """Fit a separate amount baseline per merchant per payment rail.

    Rails carry genuinely different amount patterns. Octopus is a stored-value
    card for transit and small retail, so HKD 3,000 on it is remarkable; the
    same amount on Visa is unremarkable. Pooling them gives one baseline with a
    spread wide enough to swallow the Octopus outlier entirely — the rail that
    most needed watching is the one the pooled view hides.

    Returns {merchant_id: {card_type: Baseline}}. A rail the merchant barely
    uses comes back unusable rather than fitted, since a merchant that takes
    Alipay twice a month has no Alipay pattern to be judged against.
    """
    start, end = _window_bounds(as_of, window_days, lag_days)
    rows = session.execute(
        select(
            Transaction.merchant_id,
            Transaction.card_type,
            Transaction.total_amount,
            Transaction.occurred_at,
        ).where(
            Transaction.card_type.is_not(None),
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
            *settled_sale(),
        )
    )

    quarantined = quarantined_days(session) if exclude_quarantined else set()

    grouped: dict[str, dict[str, list[float]]] = {}
    for merchant_id, card_type, amount, occurred_at in rows:
        if (merchant_id, occurred_at.date()) in quarantined:
            continue
        grouped.setdefault(merchant_id, {}).setdefault(card_type, []).append(amount)

    return {
        merchant_id: {
            rail: fit_baseline(amounts, min_observations=min_observations)
            for rail, amounts in rails.items()
        }
        for merchant_id, rails in grouped.items()
    }


def fit_peer_baselines(
    session: Session,
    as_of: datetime,
    window_days: int,
    *,
    lag_days: int = 0,
    min_cohort_merchants: int = 3,
    exclude_quarantined: bool = True,
) -> dict[str, PeerBaseline]:
    """Fit one amount distribution per MCC cohort.

    The cohort is built from each member's *median ticket* — one vote per
    merchant — not from pooled raw transactions. Pooling weights merchants by
    transaction volume, so a single high-volume launderer can dominate its
    cohort and drag the fence up to cover itself, which is precisely the
    contamination peer comparison exists to resist.

    With equal weighting it is usable even on unaudited history: a dirty
    merchant is one point among many, and the IQR is built from quartiles that
    ignore the tail it sits in. Dilution plus robustness is what lets a launch
    lean on peer comparison before any history is vetted.
    """
    from statistics import median as _median
    start, end = _window_bounds(as_of, window_days, lag_days)
    rows = session.execute(
        select(Merchant.mcc, Transaction.merchant_id, Transaction.total_amount,
               Transaction.occurred_at)
        .join(Transaction, Transaction.merchant_id == Merchant.merchant_id)
        .where(
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
            *settled_sale(),
        )
    )

    quarantined = quarantined_days(session) if exclude_quarantined else set()

    per_merchant: dict[str, dict[str, list[float]]] = {}
    for mcc, merchant_id, amount, occurred_at in rows:
        if (merchant_id, occurred_at.date()) in quarantined:
            continue
        per_merchant.setdefault(mcc, {}).setdefault(merchant_id, []).append(amount)

    cohorts: dict[str, PeerBaseline] = {}
    for mcc, members in per_merchant.items():
        centers = [_median(values) for values in members.values() if values]
        cohorts[mcc] = fit_peer_baseline(centers, n_merchants=len(centers))
    return cohorts


def _daily_counts(rows: list[datetime]) -> list[int]:
    """Transactions per active day.

    Only days the merchant actually traded are counted. Padding quiet days
    with zeros would drag a sporadic merchant's median to zero and make every
    trading day an outlier; a merchant waking up after a long silence is the
    dormant-reactivation typology, handled by rules rather than here.
    """
    per_day: dict[date, int] = {}
    for occurred_at in rows:
        per_day[occurred_at.date()] = per_day.get(occurred_at.date(), 0) + 1
    return [per_day[day] for day in sorted(per_day)]


def daily_counts_in_window(
    session: Session,
    merchant_id: str,
    as_of: datetime,
    window_days: int,
    lag_days: int = 0,
) -> list[int]:
    """One transaction count per active day, for the lagged baseline window."""
    start, end = _window_bounds(as_of, window_days, lag_days)
    rows = list(
        session.scalars(
            select(Transaction.occurred_at).where(
                Transaction.merchant_id == merchant_id,
                Transaction.occurred_at >= start,
                Transaction.occurred_at < end,
                *settled_sale(),
            )
        )
    )
    return _daily_counts(rows)


def fit_volume_baselines(
    session: Session,
    as_of: datetime,
    window_days: int,
    *,
    min_observations: int,
    lag_days: int = 0,
    exclude_quarantined: bool = True,
) -> dict[str, Baseline]:
    """Fit each merchant's daily transaction-count baseline.

    Transacting far more than usual is a signal in its own right, independent
    of ticket size — and it is what makes cohort manipulation self-defeating:
    the volume needed to drag a peer distribution is itself an outlier here.
    """
    start, end = _window_bounds(as_of, window_days, lag_days)
    rows = session.execute(
        select(Transaction.merchant_id, Transaction.occurred_at)
        .where(
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
            *settled_sale(),
        )
        .order_by(Transaction.merchant_id, Transaction.occurred_at)
    )

    quarantined = quarantined_days(session) if exclude_quarantined else set()

    times: dict[str, list[datetime]] = {}
    for merchant_id, occurred_at in rows:
        if (merchant_id, occurred_at.date()) in quarantined:
            continue
        times.setdefault(merchant_id, []).append(occurred_at)

    return {
        merchant_id: fit_baseline(
            [float(c) for c in _daily_counts(times.get(merchant_id, []))],
            min_observations=min_observations,
            min_dispersion=MIN_COUNT_DISPERSION,
        )
        for merchant_id in session.scalars(select(Merchant.merchant_id))
    }


def fit_peer_volume_baselines(
    session: Session,
    as_of: datetime,
    window_days: int,
    *,
    lag_days: int = 0,
    exclude_quarantined: bool = True,
) -> dict[str, PeerBaseline]:
    """Fit each cohort's distribution of typical daily transaction counts.

    One vote per merchant, so the answer to "is this merchant transacting far
    more than its peers?" cannot be skewed by the very merchant being asked
    about.
    """
    from statistics import median as _median

    start, end = _window_bounds(as_of, window_days, lag_days)
    rows = session.execute(
        select(Merchant.mcc, Transaction.merchant_id, Transaction.occurred_at)
        .join(Transaction, Transaction.merchant_id == Merchant.merchant_id)
        .where(
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
            *settled_sale(),
        )
    )

    quarantined = quarantined_days(session) if exclude_quarantined else set()

    times: dict[str, dict[str, list[datetime]]] = {}
    for mcc, merchant_id, occurred_at in rows:
        if (merchant_id, occurred_at.date()) in quarantined:
            continue
        times.setdefault(mcc, {}).setdefault(merchant_id, []).append(occurred_at)

    cohorts: dict[str, PeerBaseline] = {}
    for mcc, members in times.items():
        centers = [
            _median(_daily_counts(stamps)) for stamps in members.values() if stamps
        ]
        cohorts[mcc] = fit_peer_baseline(
            [float(c) for c in centers],
            n_merchants=len(centers),
            min_dispersion=MIN_COUNT_DISPERSION,
        )
    return cohorts


def _peak_rate(stamps: list[datetime], window_minutes: int) -> int:
    """The most transactions falling inside any window of that length.

    A sliding window over sorted timestamps, so a burst is measured wherever
    it happens rather than against clock-hour boundaries that could split it
    in half.
    """
    if not stamps:
        return 0
    ordered = sorted(stamps)
    span = timedelta(minutes=window_minutes)
    peak = 1
    start = 0
    for end in range(len(ordered)):
        # Half-open [t, t+span), matching every other window in this module:
        # transactions exactly one span apart fall in different windows.
        while ordered[end] - ordered[start] >= span:
            start += 1
        peak = max(peak, end - start + 1)
    return peak


def daily_peak_rate(
    session: Session,
    merchant_id: str,
    as_of: datetime,
    window_days: int,
    lag_days: int = 0,
    window_minutes: int = 60,
) -> list[int]:
    """Per active day, the busiest `window_minutes` that day."""
    start, end = _window_bounds(as_of, window_days, lag_days)
    rows = list(
        session.scalars(
            select(Transaction.occurred_at).where(
                Transaction.merchant_id == merchant_id,
                Transaction.occurred_at >= start,
                Transaction.occurred_at < end,
                *settled_sale(),
            )
        )
    )
    per_day: dict[date, list[datetime]] = {}
    for occurred_at in rows:
        per_day.setdefault(occurred_at.date(), []).append(occurred_at)
    return [_peak_rate(per_day[day], window_minutes) for day in sorted(per_day)]


def fit_velocity_baselines(
    session: Session,
    as_of: datetime,
    window_days: int,
    *,
    min_observations: int,
    lag_days: int = 0,
    window_minutes: int = 60,
    exclude_quarantined: bool = True,
) -> dict[str, Baseline]:
    """Fit each merchant's baseline for how tightly it transacts in time.

    Distinct from daily volume: a structuring burst can put many transactions
    through in minutes while the day's total stays unremarkable. Only the
    within-day rate exposes that shape.
    """
    start, end = _window_bounds(as_of, window_days, lag_days)
    rows = session.execute(
        select(Transaction.merchant_id, Transaction.occurred_at)
        .where(
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
            *settled_sale(),
        )
        .order_by(Transaction.merchant_id, Transaction.occurred_at)
    )

    quarantined = quarantined_days(session) if exclude_quarantined else set()

    per_merchant: dict[str, dict[date, list[datetime]]] = {}
    for merchant_id, occurred_at in rows:
        if (merchant_id, occurred_at.date()) in quarantined:
            continue
        per_merchant.setdefault(merchant_id, {}).setdefault(
            occurred_at.date(), []
        ).append(occurred_at)

    def _fit(merchant_id: str) -> Baseline:
        days = per_merchant.get(merchant_id, {})
        peaks = [float(_peak_rate(days[d], window_minutes)) for d in sorted(days)]
        return fit_baseline(
            peaks,
            min_observations=min_observations,
            min_dispersion=MIN_COUNT_DISPERSION,
        )

    return {
        merchant_id: _fit(merchant_id)
        for merchant_id in session.scalars(select(Merchant.merchant_id))
    }


def _capped(values: list[float], cap: int) -> list[float]:
    """Evenly sample down to `cap` items, deterministically.

    Evenly spaced rather than head-or-tail so the sample represents the whole
    window instead of one end of it.
    """
    if len(values) <= cap:
        return values
    step = len(values) / cap
    return [values[int(i * step)] for i in range(cap)]


def fit_peer_transaction_baselines(
    session: Session,
    as_of: datetime,
    window_days: int,
    *,
    lag_days: int = 0,
    per_merchant_cap: int = 200,
    dimension: str = "mcc",
    exclude_quarantined: bool = True,
) -> dict[str, Baseline]:
    """Fit a cohort's *transaction* distribution per MCC.

    Answers "is this ticket unusual for this trade?" — distinct from the
    merchant-level cohort test, which asks whether the merchant's typical
    ticket is unusual. The distinction matters most for cold-start merchants:
    a median does not move for a single huge transaction, so only a
    transaction-level test can catch one large ticket at a merchant that has
    no baseline of its own.

    Each member's contribution is capped so a high-volume merchant cannot pull
    the cohort distribution far enough to cover its own behaviour.
    """
    key = _cohort_column(dimension)
    start, end = _window_bounds(as_of, window_days, lag_days)
    rows = session.execute(
        select(key, Transaction.merchant_id, Transaction.total_amount,
               Transaction.occurred_at)
        .join(Transaction, Transaction.merchant_id == Merchant.merchant_id)
        .where(
            key.is_not(None),
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
            *settled_sale(),
        )
        .order_by(key, Transaction.merchant_id, Transaction.occurred_at)
    )

    quarantined = quarantined_days(session) if exclude_quarantined else set()

    by_member: dict[str, dict[str, list[float]]] = {}
    for mcc, merchant_id, amount, occurred_at in rows:
        if (merchant_id, occurred_at.date()) in quarantined:
            continue
        by_member.setdefault(mcc, {}).setdefault(merchant_id, []).append(amount)

    cohorts: dict[str, Baseline] = {}
    for mcc, members in by_member.items():
        pooled: list[float] = []
        for values in members.values():
            pooled.extend(_capped(values, per_merchant_cap))
        # A cohort needs several merchants before its spread means anything.
        required = 1 if len(members) >= MIN_COHORT_MERCHANTS else len(pooled) + 1
        cohorts[mcc] = fit_baseline(pooled, min_observations=required)
    return cohorts


def fit_peer_foreign_ratio_baselines(
    session: Session,
    as_of: datetime,
    window_days: int,
    *,
    lag_days: int = 0,
    dimension: str = "subdistrict",
    home_country: str = HOME_COUNTRY,
    exclude_quarantined: bool = True,
) -> dict[str, Baseline]:
    """Fit each district's normal share of foreign-issued cards.

    Foreignness is not itself the signal — an airport shop sees tourists all
    day and a residential grocer does not. What matters is a merchant sitting
    far from the norm *for where it trades*, which only a district cohort can
    say. One ratio per merchant, so a busy member cannot define the district.

    Built over card transactions only, exactly as `merchant_foreign_ratio` is.
    The two must share a definition: a norm assembled one way and a merchant
    value assembled another are not the same quantity, and the comparison
    between them means nothing.
    """
    from statistics import median as _median

    key = _cohort_column(dimension)
    start, end = _window_bounds(as_of, window_days, lag_days)
    rows = session.execute(
        select(key, Transaction.merchant_id, Transaction.card_issuing_country,
               Transaction.occurred_at)
        .join(Transaction, Transaction.merchant_id == Merchant.merchant_id)
        .where(
            key.is_not(None),
            *has_card_origin(),
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
            *settled_sale(),
        )
    )

    quarantined = quarantined_days(session) if exclude_quarantined else set()

    tally: dict[str, dict[str, list[int]]] = {}
    for group, merchant_id, country, occurred_at in rows:
        if (merchant_id, occurred_at.date()) in quarantined:
            continue
        seen = tally.setdefault(group, {}).setdefault(merchant_id, [0, 0])
        seen[0] += 1
        if country != home_country:
            seen[1] += 1

    cohorts: dict[str, Baseline] = {}
    for group, members in tally.items():
        ratios = [foreign / total for total, foreign in members.values() if total]
        required = 1 if len(ratios) >= MIN_COHORT_MERCHANTS else len(ratios) + 1
        cohorts[group] = fit_baseline(
            ratios, min_observations=required, min_dispersion=MIN_RATIO_DISPERSION
        )
    return cohorts


def merchant_foreign_ratio(
    session: Session,
    merchant_id: str,
    as_of: datetime,
    home_country: str = HOME_COUNTRY,
) -> float | None:
    """Share of the scored day's *card* transactions on foreign-issued cards.

    Wallet rails take no part, on either side of the division. They are not
    cards and have no issuing country, so counting them in the denominator
    understates the share and counting them in the numerator — which is what
    the missing empty-string guard used to do — inverts the measure entirely.

    None, not zero, for a merchant that took no cards at all: roughly a fifth
    of the portfolio trades on wallets alone on any given day, and a fabricated
    zero would enter those merchants into a district comparison on a quantity
    nobody measured.
    """
    from compliance.pipeline.stages import scored_day_bounds

    start, end = scored_day_bounds(as_of)
    countries = list(
        session.scalars(
            select(Transaction.card_issuing_country).where(
                Transaction.merchant_id == merchant_id,
                Transaction.occurred_at >= start,
                Transaction.occurred_at < end,
                *settled_sale(),
                *has_card_origin(),
            )
        )
    )
    if not countries:
        return None
    return sum(1 for c in countries if c != home_country) / len(countries)


def _unsettled_attempts():
    """Every attempt to move value, settled or not.

    The denominator of the unsettled share, and deliberately not
    `settled_sale()` — this is the one measure whose subject is the failures
    themselves, so filtering them out would leave it measuring nothing.
    """
    return ()


def _daily_unsettled_ratios(
    rows: list[tuple[datetime, str | None]],
) -> list[float]:
    """One unsettled share per day the merchant attempted anything.

    Per day rather than pooled over the window: a merchant's failure rate is a
    daily property, and pooling would let one heavy day set the level for all
    of them. Quiet days are simply absent — a day with no attempts has no
    share, which is not the same as a share of zero.
    """
    per_day: dict[date, list[bool]] = {}
    for occurred_at, status in rows:
        per_day.setdefault(occurred_at.date(), []).append(
            status in UNSETTLED_STATUSES
        )
    return [
        sum(outcomes) / len(outcomes)
        for outcomes in per_day.values()
        if outcomes
    ]


def fit_unsettled_ratio_baselines(
    session: Session,
    as_of: datetime,
    window_days: int,
    *,
    min_observations: int,
    lag_days: int = 0,
    exclude_quarantined: bool = True,
) -> dict[str, Baseline]:
    """Fit each merchant's own daily share of attempts that did not settle.

    Family B already carries a fixed threshold on the absolute decline rate.
    This is the complement and answers the other question: not whether the rate
    is bad, but whether it is a change. A merchant whose trade has always run a
    fifth declined is not news; the same merchant at four fifths is, and a
    single portfolio-wide threshold sees only one of those.

    Floored dispersion, as the other ratio baselines are: a merchant whose
    failure rate is genuinely identical every day has zero spread, which is
    ordinary rather than degenerate, and without the floor nothing would ever
    be measurable against it.
    """
    start, end = _window_bounds(as_of, window_days, lag_days)
    rows = session.execute(
        select(
            Transaction.merchant_id,
            Transaction.occurred_at,
            Transaction.transaction_status,
        ).where(
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
            *_unsettled_attempts(),
        )
    )

    quarantined = quarantined_days(session) if exclude_quarantined else set()

    per_merchant: dict[str, list[tuple[datetime, str | None]]] = {}
    for merchant_id, occurred_at, status in rows:
        if (merchant_id, occurred_at.date()) in quarantined:
            continue
        per_merchant.setdefault(merchant_id, []).append((occurred_at, status))

    return {
        merchant_id: fit_baseline(
            _daily_unsettled_ratios(per_merchant.get(merchant_id, [])),
            min_observations=min_observations,
            min_dispersion=MIN_RATIO_DISPERSION,
        )
        for merchant_id in session.scalars(select(Merchant.merchant_id))
    }


def fit_peer_unsettled_ratio_baselines(
    session: Session,
    as_of: datetime,
    window_days: int,
    *,
    lag_days: int = 0,
    dimension: str = "mcc",
    exclude_quarantined: bool = True,
) -> dict[str, Baseline]:
    """Fit each cohort's normal share of attempts that did not settle.

    Trades differ in how much they legitimately fail — a card-not-present
    business declines far more than a supermarket till — so a portfolio-wide
    number would either excuse the first or condemn the second. One ratio per
    merchant, so a high-volume member cannot define its own cohort.
    """
    key = _cohort_column(dimension)
    start, end = _window_bounds(as_of, window_days, lag_days)
    rows = session.execute(
        select(
            key,
            Transaction.merchant_id,
            Transaction.occurred_at,
            Transaction.transaction_status,
        )
        .join(Transaction, Transaction.merchant_id == Merchant.merchant_id)
        .where(
            key.is_not(None),
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
            *_unsettled_attempts(),
        )
    )

    quarantined = quarantined_days(session) if exclude_quarantined else set()

    tally: dict[str, dict[str, list[int]]] = {}
    for group, merchant_id, occurred_at, status in rows:
        if (merchant_id, occurred_at.date()) in quarantined:
            continue
        seen = tally.setdefault(group, {}).setdefault(merchant_id, [0, 0])
        seen[0] += 1
        seen[1] += status in UNSETTLED_STATUSES

    cohorts: dict[str, Baseline] = {}
    for group, members in tally.items():
        ratios = [
            unsettled / attempts
            for attempts, unsettled in members.values()
            if attempts
        ]
        cohorts[group] = fit_baseline(
            ratios,
            min_observations=MIN_COHORT_MERCHANTS,
            min_dispersion=MIN_RATIO_DISPERSION,
        )
    return cohorts


def merchant_unsettled_ratio(
    session: Session, merchant_id: str, as_of: datetime
) -> float | None:
    """Share of the scored day's attempts that did not settle.

    None for a merchant that attempted nothing. Not zero: a silent day is the
    absence of a measurement, and a fabricated zero would enter the merchant
    into a cohort comparison on a number nobody observed.
    """
    from compliance.pipeline.stages import scored_day_bounds

    start, end = scored_day_bounds(as_of)
    statuses = list(
        session.scalars(
            select(Transaction.transaction_status).where(
                Transaction.merchant_id == merchant_id,
                Transaction.occurred_at >= start,
                Transaction.occurred_at < end,
                *_unsettled_attempts(),
            )
        )
    )
    if not statuses:
        return None
    return sum(1 for s in statuses if s in UNSETTLED_STATUSES) / len(statuses)


def fit_trend(
    session: Session,
    merchant_id: str,
    as_of: datetime,
    *,
    short_days: int,
    long_days: int,
    lag_days: int = 0,
    min_observations: int = 6,
) -> Trend:
    """Compare a merchant's recent level against its long-run level.

    Catches the one thing a trailing self-baseline structurally cannot: a slow
    ramp, where no single day is an outlier against its own recent history but
    the level multiplies over months.
    """
    from statistics import median

    short = amounts_in_window(session, merchant_id, as_of - timedelta(days=lag_days), short_days)
    long = amounts_in_window(session, merchant_id, as_of - timedelta(days=lag_days), long_days)

    if len(short) < min_observations or len(long) < min_observations:
        return Trend(0.0, 0.0, 0.0, False)

    return detect_ramp(median(short), median(long))
