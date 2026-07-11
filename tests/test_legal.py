from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_privacy_policy_is_public_and_describes_optional_data_flows() -> None:
    response = client.get("/privacy")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "only\nits outward code" in response.text
    assert "Zero Data Retention" in response.text
    assert "no account system" in response.text


def test_support_page_has_a_working_public_contact_route() -> None:
    response = client.get("/support")

    assert response.status_code == 200
    assert "github.com/papajohn-123/50hz/issues" in response.text
    assert "informational" in response.text
