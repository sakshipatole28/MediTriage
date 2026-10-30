"""Tests for the agent team, the hand-offs between agents, the safety net and the tools."""

from app.agents.care_team import AGENT_NAMES, build_care_team, extract_field, run_triage_workflow
from app.agents.tools import check_red_flags, find_red_flags, get_department_info, search_medical_guidelines
from tests.conftest import make_team

DENGUE = "High fever for 3 days with severe joint pain and pain behind my eyes."


def test_build_team_has_three_agents_and_tools():
    # A fake key is fine: building agents does not contact the LLM.
    team = build_care_team(api_key="fake-key")
    assert [agent.name for agent in team.values()] == AGENT_NAMES

    tool_names = [tool.__name__ for tool in team["investigator"].tools]
    assert tool_names == ["check_red_flags", "search_medical_guidelines", "get_department_info"]
    assert not team["analyst"].tools and not team["coordinator"].tools


def test_workflow_hands_output_from_agent_to_agent(fake_team):
    result = run_triage_workflow(DENGUE, age=22, team=fake_team)

    # Agent 1 got the raw description and the age.
    assert "pain behind my eyes" in fake_team["analyst"].prompts[0]
    assert "Age: 22" in fake_team["analyst"].prompts[0]
    # Agent 2 got Agent 1's summary AND the original words (for check_red_flags).
    investigator_prompt = fake_team["investigator"].prompts[0]
    assert "Likely department: general-medicine" in investigator_prompt
    assert "pain behind my eyes" in investigator_prompt
    # Agent 3 got both earlier outputs and the safety-net result.
    coordinator_prompt = fake_team["coordinator"].prompts[0]
    assert "Likely department: general-medicine" in coordinator_prompt
    assert "Best match GL-206" in coordinator_prompt
    assert "found no red flags" in coordinator_prompt

    assert result["urgency"] == "U3"
    assert result["department"] == "General Medicine (OPD)"
    assert result["emergency"] is False
    assert result["matched_guidelines"][0] == "GL-206"
    assert result["tools_used"] == ["check_red_flags", "search_medical_guidelines", "get_department_info"]


def test_red_flag_overrides_llm_urgency():
    """Even if the LLM says U4, chest pain in the patient's words forces U1 / Emergency."""
    team = make_team(coordinator_reply="Urgency: U4\nDepartment: Cardiology\n\n### Summary\nProbably acidity.")
    result = run_triage_workflow("Crushing chest pain going to my left arm, sweating for 20 minutes.", team=team)
    assert result["urgency"] == "U1"
    assert result["emergency"] is True
    assert result["department"] == "Emergency Department"
    assert "Possible heart attack" in result["red_flags"]
    assert result["report"].startswith("> **Safety override:**")
    assert "Possible heart attack" in team["coordinator"].prompts[0]


def test_red_flags_detects_emergencies():
    names = [f["name"] for f in find_red_flags("My father's face is drooping and his speech is slurred")]
    assert names == ["Possible stroke"]
    assert find_red_flags("I can’t breathe and my lips turning blue")[0]["id"] == "RF-BREATHING"
    assert find_red_flags("I want to end my life")[0]["id"] == "RF-SELFHARM"


def test_red_flags_respects_negation():
    assert find_red_flags("Mild cough, no chest pain and no fever.") == []
    assert find_red_flags("Patient denies chest pain.") == []
    # "but" starts a new clause, so the second phrase still counts.
    assert find_red_flags("No fever but chest pain since morning")[0]["id"] == "RF-CARDIAC"


def test_check_red_flags_tool_text():
    assert "No emergency red flags" in check_red_flags("itchy rash on my arm")
    assert "Required urgency: U1" in check_red_flags("he had a seizure and is hard to wake")


def test_guideline_search_finds_the_right_condition():
    assert search_medical_guidelines(DENGUE).startswith("[GL-206]")
    assert search_medical_guidelines("twisted ankle swelling playing football").startswith("[GL-209]")


def test_guideline_search_with_no_match_and_empty_query():
    assert "No matching guideline" in search_medical_guidelines("quantum banana printer")
    assert "No query provided" in search_medical_guidelines("   ")


def test_department_info_known_and_unknown():
    assert "General Medicine (OPD)" in get_department_info("General-Medicine")
    assert "U1:" in get_department_info("emergency")
    assert "Unknown department" in get_department_info("billing")


def test_extract_field_handles_markdown_bold():
    report = "**Urgency:** U2\nDepartment: Respiratory Medicine\nCare setting: Urgent care within 2 hours"
    assert extract_field(report, "Urgency") == "U2"
    assert extract_field(report, "Department") == "Respiratory Medicine"
    assert extract_field(report, "Matched guideline") is None
