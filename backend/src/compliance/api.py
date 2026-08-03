from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from compliance.db import get_session
from compliance.models import Alert, Disposition, Merchant, MerchantProfile
from datetime import date as _date

from compliance import cases as case_svc
from compliance import diagnostics as diag
from compliance import settings_store
from compliance import glossary
from compliance.schemas import (
    AlertOut,
    AlertPage,
    CaseDetail,
    CaseEventIn,
    CaseEventOut,
    CasePage,
    DispositionIn,
    DispositionOut,
    Diagnostics,
    Ledger,
    BaselineOverview,
    BaselineRow,
    Glossary,
)


def create_app() -> FastAPI:
    app = FastAPI(title="Compliance Monitoring Platform")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/glossary", response_model=Glossary)
    def read_glossary() -> Glossary:
        """Plain-English names for the internal identifiers.

        Served from the backend so the labels sit beside the detectors they
        describe and cannot drift from them.
        """
        return Glossary(
            detectors=glossary.as_dicts(glossary.DETECTORS),
            features=glossary.as_dicts(glossary.FEATURES),
            lanes=glossary.as_dicts(glossary.LANES),
            baseline_methods=glossary.as_dicts(glossary.BASELINE_METHODS),
            alert_types=glossary.as_dicts(glossary.ALERT_TYPES),
        )

    @app.get("/api/baselines", response_model=BaselineOverview)
    def baseline_overview(session: Session = Depends(get_session)) -> BaselineOverview:
        """What every merchant's baseline is currently built from.

        The lag means a specific past day rolls into the window on the next
        run; `next_inclusion_date` names it, so the team can see which day is
        still open for review before it becomes part of normal.
        """
        profiles = list(session.scalars(select(MerchantProfile)))
        lanes = {
            m.merchant_id: m.lane for m in session.scalars(select(Merchant))
        }

        rows = [
            BaselineRow(
                merchant_id=p.merchant_id,
                mcc=p.metrics.get("peer_mcc"),
                lane=lanes.get(p.merchant_id, "B"),
                method=p.metrics.get("baseline_method", "unknown"),
                usable=bool(p.metrics.get("baseline_usable")),
                center=p.metrics.get("baseline_center"),
                observations=int(p.metrics.get("baseline_n") or 0),
                quarantined_days=int(p.metrics.get("quarantined_days") or 0),
                peer_merchants=int(p.metrics.get("peer_merchants") or 0),
                peer_usable=bool(p.metrics.get("peer_usable")),
                volume_usable=bool(p.metrics.get("volume_usable")),
                velocity_usable=bool(p.metrics.get("velocity_usable")),
                is_ramp=bool(p.metrics.get("trend_is_ramp")),
            )
            for p in profiles
        ]
        rows.sort(key=lambda r: r.merchant_id)

        first = profiles[0].metrics if profiles else {}
        window_end = first.get("window_end")
        return BaselineOverview(
            window_start=first.get("window_start"),
            window_end=window_end,
            window_days=int(first.get("window_days") or 0),
            lag_days=int(first.get("lag_days") or 0),
            # The window is half-open, so its end date is precisely the day
            # that has not yet been included and rolls in next.
            next_inclusion_date=window_end[:10] if window_end else None,
            total_count=len(rows),
            usable_count=sum(1 for r in rows if r.usable),
            quarantined_total=sum(r.quarantined_days for r in rows),
            merchants=rows,
        )

    def _with_metadata(
        session: Session, alert: Alert, mcc_names: dict[str, str] | None = None
    ) -> AlertOut:
        """Join the merchant identity an analyst needs to act on the alert.

        Joined at read time rather than copied into the alert row: a corrected
        MCC description should show through, while `feature_snapshot` stays
        frozen because that is what the detector actually judged.
        """
        merchant = session.get(Merchant, alert.merchant_id)
        names = mcc_names if mcc_names is not None else diag.mcc_descriptions(session)
        description = None
        if merchant:
            description = merchant.mcc_description or names.get(merchant.mcc)
        return AlertOut(
            id=alert.id,
            merchant_id=alert.merchant_id,
            lane=alert.lane,
            blended_score=alert.blended_score,
            rank=alert.rank,
            created_at=alert.created_at,
            triggering_detectors=alert.triggering_detectors,
            feature_snapshot=alert.feature_snapshot,
            mcc=merchant.mcc if merchant else None,
            mcc_description=description,
            merchant_district=merchant.merchant_district if merchant else None,
            merchant_subdistrict=merchant.merchant_subdistrict if merchant else None,
            business_nature=merchant.business_nature if merchant else None,
            merchant_status=merchant.merchant_status if merchant else None,
            scored_date=diag.scored_date(alert),
            alert_type=diag.alert_type(alert),
        )

    @app.get("/api/alerts", response_model=AlertPage)
    def list_alerts(
        page: int = 1,
        page_size: int = 20,
        alert_type: str | None = None,
        session: Session = Depends(get_session),
    ) -> AlertPage:
        """Alerts still awaiting a decision, newest risk first.

        Paginated because the queue runs to thousands: loading it whole made
        the screen unusable before the first row appeared. Decided alerts drop
        out — an analyst's queue should hold only what still needs them.
        """
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)

        undecided = ~select(Disposition.id).where(
            Disposition.alert_id == Alert.id
        ).exists()
        conditions = [undecided]

        rows = list(session.scalars(select(Alert).where(*conditions).order_by(Alert.rank)))
        if alert_type:
            rows = [a for a in rows if diag.alert_type(a) == alert_type]

        total = len(rows)
        start = (page - 1) * page_size
        names = diag.mcc_descriptions(session)
        items = [_with_metadata(session, a, names) for a in rows[start : start + page_size]]

        return AlertPage(
            total=total,
            page=page,
            page_size=page_size,
            pages=max((total + page_size - 1) // page_size, 1),
            items=items,
        )

    @app.get("/api/alerts/counts")
    def alert_counts(session: Session = Depends(get_session)) -> dict:
        """Counts per alert type across the whole open queue.

        Computed server-side because the queue is paginated: counting only the
        visible page would make the filter chips lie about what is waiting.
        """
        undecided = ~select(Disposition.id).where(
            Disposition.alert_id == Alert.id
        ).exists()
        rows = list(session.scalars(select(Alert).where(undecided)))
        counts: dict[str, int] = {}
        for a in rows:
            key = diag.alert_type(a)
            counts[key] = counts.get(key, 0) + 1
        scored = diag.scored_date(rows[0]) if rows else None
        return {"total": len(rows), "by_type": counts, "scored_date": scored}

    @app.post("/api/alerts/{alert_id}/disposition", response_model=DispositionOut)
    def decide(
        alert_id: int, payload: DispositionIn, session: Session = Depends(get_session)
    ) -> Disposition:
        """Record an analyst's decision on an alert.

        A confirmed alert opens a case and quarantines its day from future
        baselines. A cleared one leaves the data in — the trading was
        legitimate, and removing it would teach the system otherwise.
        """
        alert = session.get(Alert, alert_id)
        if alert is None:
            raise HTTPException(status_code=404, detail="alert not found")

        existing = session.scalars(
            select(Disposition).where(Disposition.alert_id == alert_id)
        ).first()
        if existing is not None:
            # The first decision is part of the audit trail; overwriting it
            # silently would erase who concluded what, and when.
            raise HTTPException(
                status_code=409, detail="this alert has already been decided"
            )

        disposition = case_svc.record_disposition(session, alert, payload.model_dump())
        session.commit()
        return disposition

    @app.get("/api/cases", response_model=CasePage)
    def list_cases(
        page: int = 1,
        page_size: int = 20,
        resolved: bool | None = None,
        session: Session = Depends(get_session),
    ) -> CasePage:
        """Confirmed cases. Stale ones first — the board exists to surface
        what has been forgotten."""
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)
        rows = case_svc.list_cases(session, resolved=resolved)
        total = len(rows)
        start = (page - 1) * page_size
        return CasePage(
            total=total,
            page=page,
            page_size=page_size,
            pages=max((total + page_size - 1) // page_size, 1),
            items=rows[start : start + page_size],
        )

    @app.get("/api/cases/{disposition_id}", response_model=CaseDetail)
    def get_case(
        disposition_id: int, session: Session = Depends(get_session)
    ) -> CaseDetail:
        disposition = session.get(Disposition, disposition_id)
        if disposition is None:
            raise HTTPException(status_code=404, detail="case not found")
        return CaseDetail(**case_svc.case_detail(session, disposition))

    @app.post("/api/cases/{disposition_id}/events", response_model=CaseEventOut)
    def add_case_event(
        disposition_id: int,
        payload: CaseEventIn,
        session: Session = Depends(get_session),
    ) -> CaseEventOut:
        """Append a stage to a case timeline."""
        disposition = session.get(Disposition, disposition_id)
        if disposition is None:
            raise HTTPException(status_code=404, detail="case not found")

        valid = {t.key for t in glossary.CASE_STAGES}
        if payload.event_type not in valid:
            # Free-text stages would make the board unsortable and the
            # timeline unauditable.
            raise HTTPException(
                status_code=422,
                detail=f"unknown stage; expected one of {sorted(valid)}",
            )

        event = case_svc.add_event(session, disposition, payload.model_dump())
        session.commit()
        return CaseEventOut(
            id=event.id,
            event_type=event.event_type,
            label=glossary.stage_label(event.event_type),
            note=event.note,
            actor=event.actor,
            occurred_at=event.occurred_at,
        )

    @app.get("/api/settings")
    def read_settings(session: Session = Depends(get_session)) -> dict:
        """Current detection thresholds, with the shipped defaults alongside
        so the UI can show what has been changed from stock."""
        return {
            "current": settings_store.load_settings(session).as_dict(),
            "defaults": settings_store.DEFAULTS.as_dict(),
        }

    @app.put("/api/settings")
    def write_settings(payload: dict, session: Session = Depends(get_session)) -> dict:
        """Update detection thresholds.

        Takes effect on the next pipeline run, not retroactively: existing
        alerts were judged under the thresholds in force at the time, and
        rewriting that history would break the audit trail.
        """
        known = settings_store.DEFAULTS.as_dict()
        merged = {**settings_store.load_settings(session).as_dict()}
        for key, value in payload.items():
            if key in known:
                merged[key] = value
        settings = settings_store.DetectionSettings(**merged)
        settings_store.save_settings(session, settings)
        session.commit()
        return {"current": settings.as_dict(), "defaults": known}

    @app.get("/api/alerts/{alert_id}", response_model=AlertOut)
    def get_alert(alert_id: int, session: Session = Depends(get_session)) -> AlertOut:
        alert = session.get(Alert, alert_id)
        if alert is None:
            raise HTTPException(status_code=404, detail="alert not found")
        return _with_metadata(session, alert)

    @app.get("/api/alerts/{alert_id}/diagnostics", response_model=Diagnostics)
    def get_diagnostics(
        alert_id: int, session: Session = Depends(get_session)
    ) -> Diagnostics:
        """Why this alert fired: every detector's verdict, the statistics
        behind them, and the curves needed to plot the distributions."""
        alert = session.get(Alert, alert_id)
        if alert is None:
            raise HTTPException(status_code=404, detail="alert not found")
        return Diagnostics(**diag.diagnostics(session, alert))

    @app.get("/api/merchants/{merchant_id}/transactions", response_model=Ledger)
    def get_ledger(
        merchant_id: str, date: str, session: Session = Depends(get_session)
    ) -> Ledger:
        """Every transaction a merchant processed on one local day."""
        try:
            day = _date.fromisoformat(date)
        except ValueError:
            raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")
        return Ledger(**diag.ledger(session, merchant_id, day))

    import os
    from pathlib import Path

    dist = os.environ.get("FRONTEND_DIST")
    if dist:
        dist_path = Path(dist)
        index_path = dist_path / "index.html"
        if index_path.exists():

            @app.get("/{path:path}")
            def spa(path: str = "") -> FileResponse:
                if path.startswith("api/") or path == "api":
                    raise HTTPException(status_code=404, detail="not found")

                asset_path = dist_path / path
                if path and asset_path.is_file():
                    return FileResponse(asset_path)
                return FileResponse(index_path)

    return app


app = create_app()
