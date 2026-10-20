from fastapi.testclient import TestClient

from backend.app.main import app


def test_health() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_versioned_health() -> None:
    assert TestClient(app).get("/api/v1/health").status_code == 200
