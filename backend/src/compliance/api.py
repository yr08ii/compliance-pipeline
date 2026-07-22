from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from compliance.db import get_session
from compliance.models import Alert
from compliance.schemas import AlertOut


def create_app() -> FastAPI:
    app = FastAPI(title="Compliance Monitoring Platform")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

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
