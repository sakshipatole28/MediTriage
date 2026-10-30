"""Shared test helpers.

FakeAgent replaces a real Agno agent, so tests never call an LLM or the internet.
It records every prompt it receives, so we can check the hand-off between agents.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agents.tools import check_red_flags, get_department_info, search_medical_guidelines
from app.main import app


class FakeAgent:
    """Stand-in for agno.agent.Agent with the same .run(prompt) method."""

    def __init__(self, reply, tool_calls=None):
        self.reply = reply
        self.tool_calls = tool_calls or []
        self.prompts = []

    def run(self, prompt):
        self.prompts.append(prompt)
        return SimpleNamespace(content=self.reply, tools=self.tool_calls, status="COMPLETED")


class FakeInvestigator(FakeAgent):
    """Pretends to be the LLM deciding to call all three tools, but runs the REAL tool code."""

    def run(self, prompt):
        self.prompts.append(prompt)
        calls = [
            SimpleNamespace(tool_name="check_red_flags", result=check_red_flags(prompt)),
            SimpleNamespace(tool_name="search_medical_guidelines",
                            result=search_medical_guidelines("high fever joint pain pain behind eyes dengue")),
            SimpleNamespace(tool_name="get_department_info", result=get_department_info("general-medicine")),
        ]
        return SimpleNamespace(content="Best match GL-206 (suspected dengue). Department: General Medicine.",
                               tools=calls, status="COMPLETED")


COORDINATOR_REPLY = (
    "Urgency: U3\n"
    "Department: General Medicine (OPD)\n"
    "Care setting: Same or next-day clinic\n"
    "Matched guideline: GL-206\n\n"
    "### Summary\nHigh fever with joint pain, possible dengue.\n"
    "### What to do now\n1. Get a blood test today or tomorrow."
)


def make_team(coordinator_reply=COORDINATOR_REPLY):
    """A full fake team: analyst -> investigator (real tools) -> coordinator."""
    return {
        "analyst": FakeAgent("Chief complaint: fever with joint pain. Likely department: general-medicine"),
        "investigator": FakeInvestigator(None),
        "coordinator": FakeAgent(coordinator_reply),
    }


@pytest.fixture
def fake_team():
    return make_team()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def with_key(monkeypatch):
    """Pretend a Groq key is configured (the key is fake and never used)."""
    monkeypatch.setattr("app.config.MODEL_PROVIDER", "groq")
    monkeypatch.setattr("app.config.GROQ_API_KEY", "test-key-not-real-123")


@pytest.fixture
def without_keys(monkeypatch):
    """Pretend no API key is configured at all."""
    monkeypatch.setattr("app.config.GEMINI_API_KEY", "")
    monkeypatch.setattr("app.config.GROQ_API_KEY", "")
