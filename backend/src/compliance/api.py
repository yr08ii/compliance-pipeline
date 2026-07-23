from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from compliance.db import get_session
from compliance.models import Alert, Merchant, MerchantProfile
from compliance.schemas import AlertOut, BaselineOverview, BaselineRow


def create_app() -> FastAPI:
    app = FastAPI(title="Compliance Monitoring Platform")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

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

    @app.get("/api/alerts", response_model=list[AlertOut])
    def list_alerts(session: Session = Depends(get_session)) -> list[Alert]:
        return list(session.scalars(select(Alert).order_by(Alert.rank)))

    @app.get("/api/alerts/{alert_id}", response_model=AlertOut)
    def get_alert(alert_id: int, session: Session = Depends(get_session)) -> Alert:
        alert = session.get(Alert, alert_id)
        if alert is None:
            raise HTTPException(status_code=404, detail="alert not found")
        return alert

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
