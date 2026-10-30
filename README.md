# MediTriage: Multi-Agent Patient Triage Assistant 🩺

MediTriage reads a patient's symptoms (in their own words) for a sample hospital ("CityCare Hospital")
and hands them through three AI agents. The **Symptom Analyst** structures the complaint. The
**Clinical Investigator** calls real tools to check emergency red flags, search the hospital's guideline
library and look up the right department. The **Triage Coordinator** decides the urgency (U1 emergency
to U5 self-care), the department and what the patient should do now.

A rule-based **safety net** also scans the patient's words for emergency signs (chest pain, stroke signs,
breathing trouble, self-harm...). If it finds one, the urgency is forced to **U1 / Emergency**, whatever
the LLM said.

Built with FastAPI and Agno, and runs on the free tier of **Gemini** or **Groq**. It is containerised with
Docker and Docker Compose, deployed to Kubernetes (kind), and automated with GitHub Actions and Jenkins.

> ⚠️ Educational demo only. Not a medical device and not medical advice. In an emergency call 112 / 108.

See **[EXPLAINED.md](EXPLAINED.md)** for the architecture, a file-by-file guide, viva questions and the demo script.

## Quick start (Ubuntu)

```bash
# 1. Python setup
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Add ONE free API key
cp .env.example .env
nano .env          # set GROQ_API_KEY=... or GEMINI_API_KEY=...

# 3. Tests (no key or internet needed)
pytest -v

# 4. Run
uvicorn app.main:app --host 127.0.0.1 --port 8000
# open http://127.0.0.1:8000  (Swagger docs at /docs)
```

## Easiest way: one command

```bash
./scripts/start.sh      # frees the ports, builds, starts api + ui, waits until healthy, prints the URLs
./scripts/stop.sh       # stops it again
```

`start.sh` stops anything from **this project** that is holding the ports (a local uvicorn started here,
an old `meditriage` container, an old compose stack). If another program owns a port, it tells you and
stops. Then just use different ports:

```bash
API_PORT=8003 UI_PORT=8503 ./scripts/start.sh     # or put API_PORT / UI_PORT in .env
```

## Docker (step by step)

Only one thing can use a port at a time. If uvicorn from step 4 is still running, stop it first
(`Ctrl+C` in its terminal), or Docker fails with `address already in use`.

```bash
ss -ltnp | grep -E ':(8000|8501)\b'   # nothing printed = ports are free

docker build --network=host -t meditriage:1.0 .
docker rm -f meditriage 2>/dev/null   # remove an old container with the same name, if any
docker run -d --name meditriage -p 8000:8000 --env-file .env meditriage:1.0
curl http://localhost:8000/health
docker rm -f meditriage               # stop it before using compose (same port)

docker compose up -d --build     # api on :8000, Streamlit ui on :8501
docker compose ps                # both should show (healthy)
docker compose down
```

Want to keep uvicorn running at the same time? Publish on other host ports:
`docker run -d --name meditriage -p 8003:8000 --env-file .env meditriage:1.0` (then http://localhost:8003),
or for compose `API_PORT=8003 UI_PORT=8503 docker compose up -d --build`.
In `-p 8003:8000` the left number is the port on your laptop and the right one is inside the container.

## Kubernetes (kind)

```bash
docker build --network=host -t meditriage:1.0 .
GROQ_API_KEY=your_real_key ./scripts/deploy-k8s.sh meditriage:1.0
# creates the kind cluster "meditriage" (first time), loads the image, creates the Secret,
# applies k8s/, waits for the rollout and curls /health
# open http://localhost:30090
```

Port 30090 (not 30080) is used so MediTriage can run next to the TriageBot cluster on the same laptop.

## CI/CD

Both pipelines run the tests, build the image, check the container, then deploy it to Kubernetes
with the same script, `scripts/deploy-k8s.sh`.

| Pipeline | Deploys to | Key comes from |
|---|---|---|
| GitHub Actions (`.github/workflows/ci.yml`) | a throwaway kind cluster in the runner; on `main` the image is also pushed to `ghcr.io/<owner>/meditriage` | repo secret `GROQ_API_KEY` (optional) |
| Jenkins (`Jenkinsfile`) | the kind cluster on this machine, so the app stays live at http://localhost:30090 | Jenkins credential `groq-api-key` (Secret text) |

## API

| Method | Path | What it does |
|---|---|---|
| GET | `/` | Web UI |
| GET | `/health` | Status, model, agent names, number of guidelines and departments (never the key) |
| POST | `/triage` | Body `{"symptoms": "...", "age": 22, "sex": "female"}` (symptoms 20 to 4000 chars; age and sex optional). Returns urgency, department, emergency flag, red flags, matched guidelines, tools used, the full summary and a disclaimer |

Error codes: `422` invalid input, `503` no API key configured, `504` the agents took too long, `500` the LLM provider failed (keys are redacted).

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `failed to bind host port 0.0.0.0:8000/tcp: address already in use` | Something already listens on 8000: the local uvicorn, the compose `api` container or another `docker run`. | Easiest: `./scripts/start.sh` (stops this project's leftovers), or `API_PORT=8003 UI_PORT=8503 ./scripts/start.sh`. By hand: find it with `ss -ltnp \| grep :8000` and `docker ps`, then stop it (`Ctrl+C`, `kill <pid>`, `docker compose down`, `docker rm -f meditriage`). |
| `Conflict. The container name "/meditriage" is already in use` | An earlier `docker run` failed but left a container with that name (status `Created`). | `docker rm -f meditriage`, then run again. |
| `env file .env not found` | `.env` does not exist yet. | `cp .env.example .env` and add a key. |
| `docker compose up` stops with containers in state `Created` | The port error above; compose made the containers but could not start them. | `./scripts/start.sh` (it runs `docker compose down` first). |
| `/triage` returns `503` | No API key in `.env` (Docker/Compose) or in the Secret (Kubernetes). | Add the key, then `./scripts/start.sh` (or `docker compose up -d --force-recreate`), or rerun `GROQ_API_KEY=... ./scripts/deploy-k8s.sh meditriage:1.0`. |
| `503: LLM API key is not configured` on **http://localhost:30090** (Kubernetes / Jenkins) | The pods have no key. Either the Jenkins credential `groq-api-key` is missing/empty, or the pods started before the key was put in the Secret (pods read a Secret only when they start). | Check: `curl localhost:30090/health` shows `"llm_configured":false`. Fix: add/repair the Jenkins credential (Secret text, ID `groq-api-key`) and **Build Now**, or by hand `GROQ_API_KEY=your_key ./scripts/deploy-k8s.sh meditriage:1.0`. The script stamps a fingerprint of the Secret on the pods, so a new key always triggers a rolling restart, and it fails if the pods still say `llm_configured:false`. |
| `pip ... ReadTimeoutError` during `docker build` | Slow network through Docker's bridge. | Build with `--network=host` (already in the commands above); just run it again, the pip cache keeps what was downloaded. |
| `permission denied ... docker.sock` | Your user is not in the `docker` group. | `sudo usermod -aG docker $USER`, then `newgrp docker` (or log out and in). |
