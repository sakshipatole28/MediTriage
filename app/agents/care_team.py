"""The MediTriage agent team.

Three Agno agents work one after another, each handing its output to the next:

    patient's symptoms
       -> 1. Symptom Analyst       (structures the complaint, picks the likely department)
       -> 2. Clinical Investigator (CALLS TOOLS: red flags, guideline search, department info)
       -> 3. Triage Coordinator    (decides urgency, department, care setting and advice)
       -> final triage summary

Safety net: before any LLM runs, find_red_flags() scans the patient's own words.
If it finds an emergency sign, the final urgency is forced to U1, whatever the
LLM wrote. An LLM can be wrong; a missed heart attack must not depend on it.

We wire the hand-offs ourselves in run_triage_workflow() instead of letting
an LLM "coordinator" decide, so the flow is predictable, cheaper (3 LLM steps)
and easy to test with fake agents.
"""

import re
from typing import Any, Dict, List, Optional

from agno.agent import Agent

from app import config
from app.agents.tools import check_red_flags, find_red_flags, get_department_info, search_medical_guidelines
from app.data import URGENCY_LEVELS, load_departments

AGENT_NAMES = ["Symptom Analyst", "Clinical Investigator", "Triage Coordinator"]

DEPARTMENT_LIST = (
    "emergency, cardiology, respiratory, neurology, gastroenterology, orthopedics, "
    "dermatology, ent, general-medicine, pediatrics, mental-health"
)


def create_model(api_key: Optional[str] = None, model_id: Optional[str] = None):
    """Create the LLM object for the selected provider (Gemini or Groq).

    Creating the object does not call the internet; the first call happens in agent.run().
    """
    if config.MODEL_PROVIDER == "google":
        from agno.models.google import Gemini

        return Gemini(id=model_id or config.GEMINI_MODEL, api_key=api_key or config.GEMINI_API_KEY)

    from agno.models.groq import Groq

    return Groq(id=model_id or config.GROQ_MODEL, api_key=api_key or config.GROQ_API_KEY)


def build_care_team(api_key: Optional[str] = None, model_id: Optional[str] = None) -> Dict[str, Agent]:
    """Build the three agents. Returns a dict so each step can be called by role."""
    model = create_model(api_key=api_key, model_id=model_id)

    analyst = Agent(
        name="Symptom Analyst",
        role="Turn a patient's own description into a clear, structured clinical summary.",
        model=model,
        telemetry=False,
        instructions=[
            "Read the patient's description and write these short sections:",
            "Chief complaint (one sentence), Onset and duration, Severity (as the patient describes it),",
            "Associated symptoms, Relevant history (age, conditions, medicines, pregnancy if given),",
            "Possible warning signs, Likely department, Missing information.",
            f"Likely department must be ONE of: {DEPARTMENT_LIST}.",
            "Only use facts from the description. Write 'not given' when something is missing.",
            "Do NOT diagnose. Describe what the patient reports.",
        ],
    )

    investigator = Agent(
        name="Clinical Investigator",
        role="Check for emergency red flags and find the hospital guidance that fits the symptoms.",
        model=model,
        tools=[check_red_flags, search_medical_guidelines, get_department_info],
        telemetry=False,
        instructions=[
            "You MUST call check_red_flags with the patient's ORIGINAL words.",
            "You MUST call search_medical_guidelines with 4-8 symptom keywords from the summary.",
            "You MUST call get_department_info with the most fitting department.",
            "Then report: red flags found (or none), matching guidelines (ID, typical urgency, why it fits",
            "or not), the department and contact, and the guideline's advice.",
            "Never invent guideline IDs. Only mention IDs returned by the tools.",
        ],
    )

    coordinator = Agent(
        name="Triage Coordinator",
        role="Make the final triage decision and explain it to the patient in plain language.",
        model=model,
        telemetry=False,
        instructions=[
            "Decide using ONLY the summary and investigation you are given.",
            "Start your answer with exactly these four lines:",
            "Urgency: <U1|U2|U3|U4|U5>",
            "Department: <department name>",
            "Care setting: <Emergency Department now | Urgent care within 2 hours | "
            "Same or next-day clinic | Routine appointment | Self-care at home>",
            "Matched guideline: <guideline ID, or 'None'>",
            "Then add markdown sections: ### Summary, ### Why this urgency, "
            "### What to do now (numbered), ### Go to Emergency immediately if, "
            "### Questions the doctor will ask.",
            "Urgency scale: " + " ".join(f"{k} = {v}" for k, v in URGENCY_LEVELS.items()),
            "If ANY red flag was found, Urgency MUST be U1 and Department MUST be Emergency Department.",
            "When unsure between two levels, choose the more urgent one.",
            "Never give a definite diagnosis and never name prescription medicines or doses.",
            "Write for a worried patient: short sentences, no jargon.",
        ],
    )

    return {"analyst": analyst, "investigator": investigator, "coordinator": coordinator}


def _content(run_output: Any) -> str:
    """Get the text out of an Agno RunOutput, and stop if the agent failed."""
    status = str(getattr(run_output, "status", "")).lower()
    text = str(getattr(run_output, "content", "") or "").strip()
    if "error" in status:
        raise RuntimeError(text or "The LLM provider returned an error.")
    return text


def _tool_calls(run_output: Any) -> List[Any]:
    """Tool calls the agent made during its run (empty list if none)."""
    return list(getattr(run_output, "tools", None) or [])


def extract_field(report: str, name: str) -> Optional[str]:
    """Read a 'Name: value' line from the report, e.g. extract_field(r, 'Urgency') -> 'U2'."""
    match = re.search(rf"^[\s*#-]*{name}\s*\**\s*:\s*\**\s*(.+?)\s*\**\s*$", report, re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip() if match else None


def _patient_block(symptoms: str, age: Optional[int], sex: Optional[str]) -> str:
    details = [f"Age: {age}" if age is not None else "Age: not given", f"Sex: {sex or 'not given'}"]
    return f"Patient details: {', '.join(details)}\nPatient's description:\n\"\"\"\n{symptoms}\n\"\"\""


def run_triage_workflow(
    symptoms: str,
    age: Optional[int] = None,
    sex: Optional[str] = None,
    team: Optional[Dict[str, Agent]] = None,
) -> Dict[str, Any]:
    """Run the 3 agents in order and return the final triage result."""
    team = team or build_care_team()
    patient = _patient_block(symptoms, age, sex)

    # Step 0: rule-based safety net on the patient's own words (no LLM involved).
    red_flags = find_red_flags(symptoms)

    # Step 1: Symptom Analyst structures the raw description.
    analysis = _content(team["analyst"].run(patient))

    # Step 2: Clinical Investigator gets the summary plus the original words and uses the tools.
    investigation_run = team["investigator"].run(
        f"{patient}\n\nSummary from the Symptom Analyst:\n{analysis}\n\n"
        "Check red flags, search the guidelines and look up the department."
    )
    investigation = _content(investigation_run)

    # Remember which tools were called and which guidelines they returned.
    tools_used: List[str] = []
    matched_guidelines: List[str] = []
    for call in _tool_calls(investigation_run):
        name = getattr(call, "tool_name", None)
        if name and name not in tools_used:
            tools_used.append(name)
        for gid in re.findall(r"GL-\d+", str(getattr(call, "result", "") or "")):
            if gid not in matched_guidelines:
                matched_guidelines.append(gid)

    # Step 3: Triage Coordinator sees everything, including the safety-net result.
    safety = (
        "Rule-based safety check found these red flags: " + ", ".join(f["name"] for f in red_flags)
        if red_flags
        else "Rule-based safety check found no red flags."
    )
    report = _content(
        team["coordinator"].run(
            f"{patient}\n\n{safety}\n\n"
            f"Symptom Analyst said:\n{analysis}\n\n"
            f"Clinical Investigator found:\n{investigation}"
        )
    )

    urgency = extract_field(report, "Urgency")
    urgency = urgency[:2].upper() if urgency else None
    department = extract_field(report, "Department")
    emergency = bool(red_flags) or urgency == "U1"

    # Safety override: a red flag always means U1 / Emergency, even if the LLM disagreed.
    if red_flags and urgency != "U1":
        urgency = "U1"
        department = load_departments()["emergency"]["name"]
        report = (
            "> **Safety override:** emergency warning signs were found "
            f"({', '.join(f['name'] for f in red_flags)}). Urgency raised to U1. "
            "Call 112 / 108 or go to the Emergency Department now.\n\n" + report
        )

    return {
        "report": report,
        "urgency": urgency,
        "urgency_label": URGENCY_LEVELS.get(urgency or ""),
        "department": department,
        "emergency": emergency,
        "red_flags": [f["name"] for f in red_flags],
        "matched_guidelines": matched_guidelines,
        "tools_used": tools_used,
        "analysis": analysis,
        "investigation": investigation,
    }
