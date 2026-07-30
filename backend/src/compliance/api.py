from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from compliance.db import get_session
from compliance.models import Alert, Merchant, MerchantProfile
from datetime import date as _date

from compliance import diagnostics as diag
from compliance import glossary
from compliance.schemas import (
    AlertOut,
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

    @app.get("/api/alerts", response_model=list[AlertOut])
    def list_alerts(session: Session = Depends(get_session)) -> list[AlertOut]:
        alerts = session.scalars(select(Alert).order_by(Alert.rank))
        # Resolved once for the whole page rather than per row.
        names = diag.mcc_descriptions(session)
        return [_with_metadata(session, a, names) for a in alerts]

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
