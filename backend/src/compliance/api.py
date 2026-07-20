from fastapi import FastAPI, Depends, HTTPException
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

    return app


app = create_app()
