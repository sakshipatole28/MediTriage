"""Tests for POST /triage: validation, missing key, errors and mocked end-to-end runs."""

from unittest.mock import patch

GOOD = "I have had a high fever for 3 days with severe joint pain and pain behind my eyes."


def test_empty_body_is_rejected(client):
    assert client.post("/triage", json={}).status_code == 422


def test_short_description_is_rejected(client):
    assert client.post("/triage", json={"symptoms": "I feel ill"}).status_code == 422


def test_whitespace_only_is_rejected(client):
    assert client.post("/triage", json={"symptoms": " " * 50}).status_code == 422


def test_invalid_age_and_sex_are_rejected(client):
    assert client.post("/triage", json={"symptoms": GOOD, "age": 150}).status_code == 422
    assert client.post("/triage", json={"symptoms": GOOD, "sex": "robot"}).status_code == 422


def test_missing_api_key_returns_503(client, without_keys):
    res = client.post("/triage", json={"symptoms": GOOD})
    assert res.status_code == 503
    assert "API key is not configured" in res.json()["detail"]


def test_triage_with_mocked_workflow(client, with_key):
    """Replace the whole workflow with a fixed answer and check the API response shape."""
    fake_result = {
        "report": "Urgency: U3\nDepartment: General Medicine (OPD)",
        "urgency": "U3",
        "urgency_label": "Soon: same-day or next-day clinic appointment.",
        "department": "General Medicine (OPD)",
        "emergency": False,
        "red_flags": [],
        "matched_guidelines": ["GL-206"],
        "tools_used": ["search_medical_guidelines"],
    }
    with patch("app.main.run_triage_workflow", return_value=fake_result) as workflow:
        res = client.post("/triage", json={"symptoms": GOOD, "age": 22, "sex": "female"})
    assert res.status_code == 200
    workflow.assert_called_once_with(GOOD, 22, "female")
    data = res.json()
    assert data["urgency"] == "U3"
    assert data["matched_guidelines"] == ["GL-206"]
    assert data["symptoms"] == GOOD
    assert "not a medical device" in data["disclaimer"]


def test_triage_end_to_end_with_fake_agents(client, with_key, fake_team):
    """Run the REAL workflow and REAL tools; only the LLM agents are fake."""
    with patch("app.agents.care_team.build_care_team", return_value=fake_team):
        res = client.post("/triage", json={"symptoms": GOOD})
    assert res.status_code == 200
    data = res.json()
    assert data["urgency"] == "U3"
    assert data["department"] == "General Medicine (OPD)"
    assert data["emergency"] is False
    assert data["matched_guidelines"][0] == "GL-206"
    assert data["tools_used"] == ["check_red_flags", "search_medical_guidelines", "get_department_info"]


def test_emergency_end_to_end_is_forced_to_u1(client, with_key, fake_team):
    """The fake coordinator says U3, but the patient describes stroke signs."""
    stroke = "My mother's face is drooping on one side and her speech is slurred since 10 minutes."
    with patch("app.agents.care_team.build_care_team", return_value=fake_team):
        data = client.post("/triage", json={"symptoms": stroke, "age": 67}).json()
    assert data["urgency"] == "U1"
    assert data["emergency"] is True
    assert data["red_flags"] == ["Possible stroke"]


def test_agent_error_returns_500_and_hides_key(client, with_key):
    error = RuntimeError("401 Unauthorized for key test-key-not-real-123")
    with patch("app.main.run_triage_workflow", side_effect=error):
        res = client.post("/triage", json={"symptoms": GOOD})
    assert res.status_code == 500
    assert "test-key-not-real-123" not in res.text
    assert "[REDACTED]" in res.json()["detail"]
