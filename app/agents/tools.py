"""Tools (plain Python functions) that the Clinical Investigator agent can call.

Agno reads each function's name, type hints and docstring and sends them to
the LLM as a "function calling" schema. The LLM decides when to call a tool,
Agno runs the Python function, and the result goes back to the LLM.

find_red_flags() is also called directly by the workflow (not only by the LLM),
so an emergency is never missed just because the model forgot to call a tool.
"""

import re
from typing import Dict, List, Tuple

from app.data import URGENCY_LEVELS, load_departments, load_guidelines, load_red_flags

# Common words that say nothing about which condition it is.
STOPWORDS = {
    "the", "and", "for", "with", "when", "this", "that", "from", "have", "has",
    "not", "but", "are", "was", "were", "can", "since", "been", "feel", "feeling",
    "then", "into", "our", "your", "you", "they", "them", "its", "any", "all",
    "get", "got", "does", "did", "just", "also", "very", "some", "there", "my",
    "days", "day", "today", "yesterday", "having", "really", "bit", "lot",
}

# Words that cancel a red flag phrase right after them: "no chest pain", "denies chest pain".
NEGATIONS = {"no", "not", "without", "denies", "never", "nor"}

# A guideline must score at least this much to count as relevant.
MIN_SCORE = 4


def _words(text: str) -> set:
    """Split text into lowercase words, dropping short and common words."""
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return {t for t in tokens if len(t) > 2 and t not in STOPWORDS}


def _is_negated(text_before: str) -> bool:
    """True if one of the 3 words just before a phrase is a negation, in the same clause.

    "I have no chest pain"  -> negated.
    "No fever but chest pain" -> NOT negated ("but" starts a new clause).
    """
    clause = re.split(r"[.,;:!?]|\bbut\b", text_before.lower())[-1]
    return bool(set(clause.split()[-3:]) & NEGATIONS)


def find_red_flags(text: str) -> List[Dict[str, str]]:
    """Return the emergency red flags found in free text, e.g. [{'id': 'RF-CARDIAC', ...}]."""
    lower = (text or "").lower().replace("’", "'")
    found = []
    for rule in load_red_flags():
        for phrase in rule["phrases"]:
            for match in re.finditer(rf"\b{re.escape(phrase)}\b", lower):
                if not _is_negated(lower[: match.start()]):
                    found.append({"id": rule["id"], "name": rule["name"], "phrase": phrase})
                    break
            else:
                continue
            break  # one hit per rule is enough
    return found


def score_guideline(query: str, guideline: dict) -> int:
    """Give a guideline a relevance score for the query (higher = more similar)."""
    query_lower = query.lower()
    query_words = _words(query)
    score = 0
    # Keyword hits are the strongest signal. \b makes "ear" not match "year".
    for keyword in guideline.get("keywords", []):
        if re.search(rf"\b{re.escape(keyword.lower())}\b", query_lower):
            score += 3
    score += 2 * len(query_words & _words(guideline.get("title", "")))
    score += len(query_words & _words(guideline.get("description", "")))
    return score


def find_matching_guidelines(query: str, limit: int = 3) -> List[Tuple[int, dict]]:
    """Return up to `limit` (score, guideline) pairs, best match first."""
    scored = [(score_guideline(query, g), g) for g in load_guidelines()]
    scored = [pair for pair in scored if pair[0] >= MIN_SCORE]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[:limit]


def search_medical_guidelines(query: str) -> str:
    """Search the CityCare Hospital guideline library for conditions matching the symptoms.

    Use this to ground the triage decision in the hospital's own guidance.

    Args:
        query: Short description of the main symptoms, e.g. "high fever joint pain pain behind eyes".

    Returns:
        Matching guidelines with ID, title, department, typical urgency, red flags and advice,
        or a message saying nothing matched.
    """
    if not query or not query.strip():
        return "No query provided. Describe the symptoms in a few keywords."

    matches = find_matching_guidelines(query)
    if not matches:
        return f"No matching guideline found for: '{query}'. Use general-medicine and clinical judgement."

    lines = []
    for score, g in matches:
        lines.append(
            f"[{g['id']}] {g['title']}\n"
            f"  department: {g['department']} | typical urgency: {g['typical_urgency']} | match score: {score}\n"
            f"  typical picture: {g['description']}\n"
            f"  red flags: {'; '.join(g['red_flags'])}\n"
            f"  advice: {g['advice']}"
        )
    return "\n".join(lines)


def check_red_flags(symptoms: str) -> str:
    """Check the patient's own words for emergency red flags (chest pain, stroke signs, etc.).

    Args:
        symptoms: The patient's symptom description, ideally the original words.

    Returns:
        The red flags found and what to do, or a message saying none were found.
    """
    flags = find_red_flags(symptoms)
    if not flags:
        return "No emergency red flags found in the description."
    listed = "\n".join(f"  [{f['id']}] {f['name']} (matched: '{f['phrase']}')" for f in flags)
    return f"EMERGENCY RED FLAGS FOUND:\n{listed}\nRequired urgency: U1 - {URGENCY_LEVELS['U1']}"


def get_department_info(department: str) -> str:
    """Look up a CityCare Hospital department: its name, contact, location and the urgency scale.

    Args:
        department: Department key, one of: emergency, cardiology, respiratory, neurology,
            gastroenterology, orthopedics, dermatology, ent, general-medicine, pediatrics, mental-health.

    Returns:
        The department's details and what each urgency level means.
    """
    departments = load_departments()
    key = (department or "").strip().lower()
    if key not in departments:
        return f"Unknown department '{department}'. Valid departments: {', '.join(sorted(departments))}."

    info = departments[key]
    scale = "\n".join(f"  {level}: {rule}" for level, rule in URGENCY_LEVELS.items())
    return (
        f"Department '{key}' is {info['name']} (contact: {info['contact']}, location: {info['location']}).\n"
        f"Handles: {info['description']}\nUrgency scale:\n{scale}"
    )
