import os
from pathlib import Path
from fastapi.testclient import TestClient
from compliance.api import create_app


def test_health_ok():
    client = TestClient(create_app())
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_spa_served_when_dist_exists(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>app</title>")
    monkeypatch.setenv("FRONTEND_DIST", str(dist))
    client = TestClient(create_app())
    resp = client.get("/")
    assert resp.status_code == 200
    assert "app" in resp.text
