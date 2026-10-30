"""Local data for MediTriage: a small clinical guideline library, a department map
and a list of emergency red flags.

The data describes a made-up hospital called "CityCare Hospital". It is simplified
teaching material, not real clinical guidance.
Files are read every time a tool runs, so if you edit the JSON through the
docker compose volume mount, the change is visible without a rebuild.
"""

import json
from pathlib import Path
from typing import Any, Dict, List

DATA_DIR = Path(__file__).resolve().parent

# Urgency level -> how quickly the patient should be seen and where.
URGENCY_LEVELS = {
    "U1": "Emergency: go to the Emergency Department now or call 112 / 108.",
    "U2": "Urgent: be seen by a doctor within 1-2 hours.",
    "U3": "Soon: same-day or next-day clinic appointment.",
    "U4": "Routine: regular appointment within 1-2 weeks.",
    "U5": "Self-care: manage at home and watch for warning signs.",
}


def _load(name: str) -> Any:
    with open(DATA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def load_guidelines() -> List[Dict[str, Any]]:
    """Return the list of clinical guidelines from guidelines.json."""
    return _load("guidelines.json")


def load_departments() -> Dict[str, Dict[str, str]]:
    """Return the department key -> details map from departments.json."""
    return _load("departments.json")


def load_red_flags() -> List[Dict[str, Any]]:
    """Return the emergency red flag rules from red_flags.json."""
    return _load("red_flags.json")
