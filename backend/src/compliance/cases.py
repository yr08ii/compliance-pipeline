"""Deciding alerts, and following confirmed cases to resolution.

The decision is where the system earns its keep. Everything upstream exists to
put a merchant in front of an analyst; everything downstream depends on what
they concluded — including the baselines themselves, since a confirmed-bad day
must never shape what the system considers normal.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from compliance import glossary
from compliance.models import Alert, CaseEvent, Disposition, Merchant

HKT = timezone(timedelta(hours=8))

# A case with no update for this long is surfaced on the board. Chasing a
# merchant is easy to start and easy to forget, and an aged open case is the
# accountability gap the follow-through board exists to close.
STALE_AFTER_DAYS = 7


def record_disposition(session: Session, alert: Alert, payload: dict) -> Disposition:
    """Record a decision, and open a case if the alert was confirmed.

    A true alert needs following up, so it opens a case with its first stage.
    A false alert does not — there is nothing to chase, the trading was
    legitimate, and its data stays in the baseline.
    """
    disposition = Disposition(
        alert_id=alert.id,
        verdict=payload["verdict"],
        reason_code=payload["reason_code"],
        risk_axis=payload["risk_axis"],
        action_taken=payload.get("action_taken", "NONE"),
        analyst_id=payload["analyst_id"],
        notes=payload.get("notes"),
    )
    session.add(disposition)
    session.flush()

    if disposition.verdict == "TRUE_POSITIVE":
        session.add(
            CaseEvent(
                disposition_id=disposition.id,
                event_type="OPENED",
                note=payload.get("notes"),
                actor=payload["analyst_id"],
            )
        )
        session.flush()

    return disposition


def _events(session: Session, disposition_id: int) -> list[CaseEvent]:
    return list(
        session.scalars(
            select(CaseEvent)
            .where(CaseEvent.disposition_id == disposition_id)
            .order_by(CaseEvent.occurred_at, CaseEvent.id)
        )
    )


def _summary(session: Session, disposition: Disposition, events: list[CaseEvent]) -> dict:
    alert = session.get(Alert, disposition.alert_id)
    merchant = session.get(Merchant, alert.merchant_id) if alert else None
    latest = events[-1] if events else None
    stage = latest.event_type if latest else "OPENED"
    last_update = latest.occurred_at if latest else disposition.decided_at

    now = datetime.now(timezone.utc)
    reference = last_update if last_update.tzinfo else last_update.replace(tzinfo=timezone.utc)
    days = max((now - reference).days, 0)
    resolved = stage in glossary.RESOLVED_STAGES

    scored = None
    if alert:
        stamp = alert.as_of or alert.created_at
        scored = (stamp.astimezone(HKT) - timedelta(days=1)).date().isoformat()

    return {
        "disposition_id": disposition.id,
        "alert_id": disposition.alert_id,
        "merchant_id": alert.merchant_id if alert else "",
        "mcc": merchant.mcc if merchant else None,
        "mcc_description": merchant.mcc_description if merchant else None,
        "reason_code": disposition.reason_code,
        "risk_axis": disposition.risk_axis,
        "stage": stage,
        "stage_label": glossary.stage_label(stage),
        "is_resolved": resolved,
        "opened_at": disposition.decided_at,
        "last_update": last_update,
        "days_since_update": days,
        # Resolved cases cannot go stale; only open ones need chasing.
        "is_stale": (not resolved) and days >= STALE_AFTER_DAYS,
        "notes": disposition.notes,
        "analyst_id": disposition.analyst_id,
        "scored_date": scored,
    }


def list_cases(session: Session, *, resolved: bool | None = None) -> list[dict]:
    """Confirmed cases, most recently updated first.

    Only true alerts appear: a false alert has nothing to follow through.
    """
    dispositions = list(
        session.scalars(
            select(Disposition).where(Disposition.verdict == "TRUE_POSITIVE")
        )
    )
    summaries = [_summary(session, d, _events(session, d.id)) for d in dispositions]
    if resolved is not None:
        summaries = [s for s in summaries if s["is_resolved"] == resolved]
    # Stale cases first — the board exists to surface what has been forgotten.
    summaries.sort(key=lambda s: (not s["is_stale"], s["last_update"]), reverse=False)
    summaries.sort(key=lambda s: s["is_stale"], reverse=True)
    return summaries


def case_detail(session: Session, disposition: Disposition) -> dict:
    events = _events(session, disposition.id)
    detail = _summary(session, disposition, events)
    detail["events"] = [
        {
            "id": e.id,
            "event_type": e.event_type,
            "label": glossary.stage_label(e.event_type),
            "note": e.note,
            "actor": e.actor,
            "occurred_at": e.occurred_at,
        }
        for e in events
    ]
    return detail


def add_event(session: Session, disposition: Disposition, payload: dict) -> CaseEvent:
    """Append a stage. Stages append rather than replace — the timeline is the
    record, and overwriting a status would erase how the case got here."""
    event = CaseEvent(
        disposition_id=disposition.id,
        event_type=payload["event_type"],
        note=payload.get("note"),
        actor=payload["actor"],
    )
    session.add(event)
    session.flush()
    return event
