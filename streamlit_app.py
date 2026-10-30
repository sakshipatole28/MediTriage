"""Optional Streamlit UI for MediTriage.

This UI does NOT import the agents. It is a thin client that calls the FastAPI
backend over HTTP. That is why docker compose can run it as a separate
container: it only needs to know API_URL (e.g. http://api:8000).
"""

import os

import httpx
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")

EXAMPLE = (
    "I have had a high fever for 3 days with severe joint and body pain and pain behind my eyes. "
    "Lots of mosquitoes near my hostel. No bleeding."
)

st.set_page_config(page_title="MediTriage", page_icon="🩺")
st.title("🩺 MediTriage")
st.caption("Symptom Analyst → Clinical Investigator (tools) → Triage Coordinator")
st.warning(
    "Emergency? Do not wait for this app. Call 112 or 108 (ambulance). "
    "MediTriage is an educational demo, not medical advice."
)

# Sidebar: backend health
with st.sidebar:
    st.subheader("Backend")
    st.code(API_URL)
    try:
        health = httpx.get(f"{API_URL}/health", timeout=5).json()
        if health["llm_configured"]:
            st.success(f"Ready: {health['model']}")
        else:
            st.warning("Backend up, but no API key is set.")
        st.write("Agents:", ", ".join(health["agents"]))
        st.write("Guidelines in library:", health["guidelines"])
        st.write("Departments:", health["departments"])
    except httpx.HTTPError:
        st.error("Backend not reachable.")

symptoms = st.text_area("Symptoms", value=EXAMPLE, height=150, max_chars=4000)
col_age, col_sex = st.columns(2)
age = col_age.number_input("Age (0 = not given)", min_value=0, max_value=120, value=22)
sex = col_sex.selectbox("Sex", ["not given", "female", "male", "other"])

if st.button("Triage these symptoms", type="primary"):
    body = {"symptoms": symptoms}
    if age:
        body["age"] = int(age)
    if sex != "not given":
        body["sex"] = sex

    with st.spinner("Three agents are working (this can take 20-60 seconds)..."):
        try:
            res = httpx.post(f"{API_URL}/triage", json=body, timeout=120)
        except httpx.HTTPError as exc:
            st.error(f"Could not reach the backend: {exc}")
            st.stop()

    if res.status_code != 200:
        detail = res.json().get("detail")
        if isinstance(detail, list):  # 422 validation errors come as a list
            detail = "; ".join(d["msg"] for d in detail)
        st.error(f"{res.status_code}: {detail}")
    else:
        data = res.json()
        if data["emergency"]:
            st.error(
                "EMERGENCY: call 112 / 108 or go to the Emergency Department now. "
                + (f"Warning signs: {', '.join(data['red_flags'])}" if data["red_flags"] else "")
            )
        col1, col2 = st.columns(2)
        col1.metric("Urgency", data["urgency"] or "?")
        col2.metric("Department", data["department"] or "?")
        if data["urgency_label"]:
            st.info(data["urgency_label"])
        st.write("**Matched guidelines:**", ", ".join(data["matched_guidelines"]) or "none")
        st.write("**Tools called:**", ", ".join(data["tools_used"]) or "none")
        st.divider()
        st.markdown(data["report"])
        st.caption(data["disclaimer"])
