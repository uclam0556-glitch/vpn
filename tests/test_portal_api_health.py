from fastapi.testclient import TestClient

from hamalivpn.api import app


def test_portal_api_health_is_json_not_spa_redirect() -> None:
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["app"] == "hamalivpn-portal"

