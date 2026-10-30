"""FastAPI application for MediTriage.

Routes:
- GET  /        -> the HTML/CSS/JS web page
- GET  /health  -> is the service up? (used by Docker, compose, Kubernetes and CI)
- POST /triage  -> run the 3-agent team on a patient's symptoms
"""

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.agents.care_team import AGENT_NAMES, run_triage_workflow
from app.data import load_departments, load_guidelines
from app.schemas import HealthResponse, TriageRequest, TriageResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("meditriage")

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title=config.APP_NAME,
    description=(
        "Three AI agents read a patient's symptoms, check red flags and hospital guidelines "
        "with real tools, and suggest how urgently and where the patient should be seen. "
        "Educational demo only, not medical advice."
    ),
    version="1.0.0",
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def serve_frontend() -> FileResponse:
    """Serve the single-page web UI."""
    return FileResponse(str(STATIC_DIR / "index.html"), media_type="text/html")


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Liveness/readiness check. Says whether a key exists, never shows the key itself."""
    return HealthResponse(
        app_name=config.APP_NAME,
        llm_configured=config.is_provider_configured(),
        model=config.get_active_model_name(),
        agents=AGENT_NAMES,
        guidelines=len(load_guidelines()),
        departments=len(load_departments()),
    )


@app.post("/triage", response_model=TriageResponse)
async def triage_patient(request: TriageRequest) -> TriageResponse:
    """Send symptoms through Analyst -> Investigator -> Coordinator and return the decision."""
    if not config.is_provider_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "LLM API key is not configured. Set GEMINI_API_KEY (https://aistudio.google.com/apikey) "
                "or GROQ_API_KEY (https://console.groq.com/keys) in .env or the environment. "
                "Kubernetes/Jenkins: the key comes from the Jenkins credential 'groq-api-key' "
                "(or GROQ_API_KEY=... ./scripts/deploy-k8s.sh <image>); the script restarts the pods to load it."
            ),
        )

    # Health data is sensitive: log only the size of the text, never the symptoms themselves.
    logger.info("Triaging symptom description (%d characters)", len(request.symptoms))
    try:
        # The agents are synchronous, so run them in a worker thread with a time limit.
        result = await asyncio.wait_for(
            asyncio.to_thread(run_triage_workflow, request.symptoms, request.age, request.sex),
            timeout=config.REQUEST_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=(
                f"The agents took longer than {config.REQUEST_TIMEOUT} seconds. Please try again. "
                "If this is an emergency, call 112 / 108 now."
            ),
        )
    except Exception as exc:
        message = config.redact_secrets(str(exc))
        logger.error("Agent workflow failed: %s", message)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"The agent team failed: {message}",
        )

    return TriageResponse(
        symptoms=request.symptoms,
        report=result["report"],
        urgency=result.get("urgency"),
        urgency_label=result.get("urgency_label"),
        department=result.get("department"),
        emergency=result.get("emergency", False),
        red_flags=result.get("red_flags", []),
        matched_guidelines=result.get("matched_guidelines", []),
        tools_used=result.get("tools_used", []),
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT)
