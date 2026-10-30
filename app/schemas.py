"""Pydantic models: they validate what comes in and describe what goes out."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app import config


class TriageRequest(BaseModel):
    """A patient's symptom description that needs triage."""

    symptoms: str = Field(
        ...,
        min_length=20,
        max_length=4000,
        description="The symptoms in the patient's own words: what, since when, how bad.",
        examples=[
            "I have had a high fever for 3 days with severe joint pain and pain behind my eyes. "
            "There are a lot of mosquitoes near my house."
        ],
    )
    age: Optional[int] = Field(default=None, ge=0, le=120, description="Patient age in years (optional).")
    sex: Optional[Literal["female", "male", "other"]] = Field(default=None, description="Optional.")

    @field_validator("symptoms")
    @classmethod
    def must_have_real_text(cls, value: str) -> str:
        """Reject descriptions that are only spaces, and strip extra whitespace."""
        trimmed = value.strip()
        if len(trimmed) < 20:
            raise ValueError("Symptom description must contain at least 20 non-whitespace characters.")
        return trimmed


class TriageResponse(BaseModel):
    """The final triage decision produced by the agent team."""

    status: str = "success"
    symptoms: str = Field(..., description="The description that was submitted.")
    report: str = Field(..., description="Final triage summary written by the Triage Coordinator (markdown).")
    urgency: Optional[str] = Field(default=None, description="U1 (emergency) to U5 (self-care).")
    urgency_label: Optional[str] = Field(default=None, description="What the urgency level means.")
    department: Optional[str] = Field(default=None, description="Department the patient should go to.")
    emergency: bool = Field(default=False, description="True when the patient should get emergency care now.")
    red_flags: List[str] = Field(default_factory=list, description="Emergency signs found by the safety check.")
    matched_guidelines: List[str] = Field(default_factory=list, description="Guideline IDs the tools found.")
    tools_used: List[str] = Field(default_factory=list, description="Tools the Clinical Investigator called.")
    disclaimer: str = config.DISCLAIMER


class HealthResponse(BaseModel):
    """Answer of GET /health. Never contains secrets."""

    status: str = "healthy"
    app_name: str
    llm_configured: bool
    model: str
    agents: List[str]
    guidelines: int
    departments: int
