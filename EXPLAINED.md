# MediTriage Explained (viva guide)

## 1. The problem in simple words

**The problem.** A busy hospital OPD and its helpline get hundreds of patients a day. Many are not
sure where to go: "Is this an emergency? Which department? Can I wait until tomorrow?". Someone at the
front desk has to listen to each patient, pick out the important facts, check for danger signs, match
the complaint to the right department and decide how soon the patient must be seen. When the queue is
long, a dangerous case (a heart attack that "feels like acidity") can wait too long, and simple cases
crowd the Emergency Department.

**What the app does.** MediTriage does that first pass automatically. A patient types their symptoms
in their own words. Three AI agents work on it in turn, and the patient gets back:
an **urgency level** (U1 emergency to U5 self-care), the **department** to go to, the matching
**hospital guideline**, any **emergency red flags**, clear "what to do now" steps, warning signs to watch
for, and a disclaimer.

**Why it is safe enough for a demo.** Three layers:
1. A **rule-based red-flag check** in plain Python scans the patient's own words *before* any LLM runs.
   If it finds chest pain, stroke signs, breathing trouble, collapse, severe bleeding or self-harm, the
   final urgency is **forced to U1 / Emergency Department**, even if the LLM said something milder.
2. The agents are told to **ground** their answer in the hospital's guideline library (via tools), to
   never give a definite diagnosis, never name prescription medicines or doses, and to choose the more
   urgent level when unsure.
3. Every answer carries a **disclaimer** and the UI always shows "Emergency? Call 112 / 108".
   Symptom text is **never written to the logs** (only its length), because health data is sensitive.

**Why containers help.** The app needs Python 3.11, about ten pinned libraries and the right settings.
Without containers, "it works on my laptop" often fails on the lab PC or the server. A Docker image
packs the code, Python and all libraries into one unit that runs the same everywhere. Docker Compose
starts the API and the UI together with one command, and Kubernetes runs several copies and restarts
them if they crash. A hospital system must not go down because one process crashed, which is exactly
what Kubernetes' replicas and self-healing give us. CI/CD builds, tests and deploys that same image
automatically on every push.

## 2. Architecture

```
                    Browser (HTML/JS UI on :8000)  or  Streamlit UI container (:8501)
                                     |
                                     |  POST /triage {"symptoms": "...", "age": 58, "sex": "male"}
                                     v
  +------------------------------------------------------------------------------+
  |  FastAPI (app/main.py)                                                        |
  |   - Pydantic validates input (422 if too short, bad age/sex)                  |
  |   - no API key?  -> 503                                                       |
  |   - runs the workflow in a thread with a timeout (504)                        |
  +-------------------------------------+----------------------------------------+
                                        |
                                        v   run_triage_workflow()  (app/agents/care_team.py)
  +------------------------------------------------------------------------------+
  |  Step 0: Safety net (plain Python, no LLM)                                    |
  |    find_red_flags(patient's words) -> e.g. ["Possible heart attack"]          |
  +-------------------------------------+----------------------------------------+
                                        v
  +------------------------------------------------------------------------------+
  |  Agent 1: Symptom Analyst                                                     |
  |    raw words -> chief complaint, onset, severity, history, likely department  |
  +-------------------------------------+----------------------------------------+
                                        | summary (text)
                                        v
  +------------------------------------------------------------------------------+
  |  Agent 2: Clinical Investigator              function calling                 |
  |    LLM decides to call  ------------------>  check_red_flags(symptoms)        |
  |                         ------------------>  search_medical_guidelines(query) |
  |                         ------------------>  get_department_info(department)  |
  |                         <------------------  results from app/data/*.json     |
  +-------------------------------------+----------------------------------------+
                                        | investigation (text) + tool results
                                        v
  +------------------------------------------------------------------------------+
  |  Agent 3: Triage Coordinator                                                  |
  |    words + safety result + summary + investigation ->                         |
  |    Urgency / Department / Care setting / Matched guideline /                  |
  |    Summary / Why / What to do now / Go to Emergency if / Questions            |
  +-------------------------------------+----------------------------------------+
                                        v
  +------------------------------------------------------------------------------+
  |  Safety override: red flag found but LLM said not U1?  -> force U1 + banner   |
  +-------------------------------------+----------------------------------------+
                                        v
     JSON: urgency, urgency_label, department, emergency, red_flags,
           matched_guidelines, tools_used, report, disclaimer

  LLM provider: Gemini or Groq (chosen by environment variables)
```

**Urgency scale** (defined once in `app/data/__init__.py`):

| Level | Meaning |
|---|---|
| U1 | Emergency: go to the Emergency Department now or call 112 / 108. |
| U2 | Urgent: be seen by a doctor within 1-2 hours. |
| U3 | Soon: same-day or next-day clinic appointment. |
| U4 | Routine: regular appointment within 1-2 weeks. |
| U5 | Self-care: manage at home and watch for warning signs. |

**Why a fixed pipeline and not an LLM "team leader"?** LegalBot uses Agno's `Team` in coordinate mode,
where an LLM decides which member runs next. For triage we want the same three steps every time, in the
same order, so the flow is predictable, costs exactly three LLM calls, and is easy to test with fake
agents. The hand-off is plain Python: the output text of one agent becomes part of the next agent's prompt.

## 3. File-by-file

| File | What it is for |
|---|---|
| `app/main.py` | FastAPI app with three routes (`/`, `/health`, `/triage`) and the error handling (422/503/504/500). Logs only the length of the symptoms. |
| `app/config.py` | Reads all settings and API keys from environment variables, holds the disclaimer, hides keys in error messages. |
| `app/schemas.py` | Pydantic models that check the incoming symptoms, age (0-120) and sex, and describe the JSON responses. |
| `app/agents/care_team.py` | Creates the three Agno agents, runs them in order passing each output to the next, and applies the red-flag safety override. |
| `app/agents/tools.py` | The three real tools the Investigator can call (red flags, guideline search, department lookup) and the red-flag matcher with negation handling. |
| `app/agents/__init__.py` | Makes the agent functions importable as one package. |
| `app/data/guidelines.json` | 14 simplified sample clinical guidelines (chest pain, stroke, asthma, dengue, sprain...), the library the search tool reads. |
| `app/data/departments.json` | 11 hospital departments with contact extension and location. |
| `app/data/red_flags.json` | 9 groups of emergency warning phrases (cardiac, stroke, breathing, anaphylaxis, bleeding, collapse, headache, meningitis, self-harm). |
| `app/data/__init__.py` | Loads the JSON files and holds the U1-U5 urgency scale. |
| `app/static/index.html` | The web page: emergency notice, symptoms box, optional age/sex, the 3 agent steps, the result. |
| `app/static/style.css` | Styling with light and dark mode; urgency badges are coloured red (U1) to green (U4/U5). |
| `app/static/app.js` | Calls `/health` and `/triage`, shows an emergency banner, and shows the result safely (HTML is escaped). |
| `streamlit_app.py` | Optional second UI that talks to the API over HTTP, so it runs as its own container. |
| `.streamlit/config.toml` | Streamlit settings (headless, no usage stats). |
| `tests/conftest.py` | Fake agents and shared fixtures so tests never call a real LLM. |
| `tests/test_health.py` | Tests `/health`, `/`, and that the key never appears in responses. |
| `tests/test_api.py` | Tests validation, the 503 without a key, a mocked run, a real-tools run, the emergency override through the API, and key redaction. |
| `tests/test_agents.py` | Tests team construction, agent hand-offs, the safety override, red flags with negation, all tools and the field parser. |
| `pytest.ini` | Tells pytest where the tests are and hides one harmless library warning. |
| `requirements.txt` | Exact versions of every Python library. |
| `.env.example` | Template showing which settings exist, with blank keys. |
| `.gitignore` | Stops `.env`, venvs and caches from being committed to git. |
| `.dockerignore` | Stops `.env`, venvs, git, tests and docs from being copied into the image. |
| `Dockerfile` | Recipe for building the container image. |
| `docker-compose.yml` | Runs the API and Streamlit containers together with healthchecks and a read-only data volume. |
| `k8s/namespace.yaml` | Creates the `meditriage` namespace. |
| `k8s/configmap.yaml` | Non-secret settings (provider, model names, timeout). |
| `k8s/secret.yaml` | Placeholder API keys, for reference only. Not applied; the deploy script creates the real Secret. |
| `k8s/deployment.yaml` | Runs 2 pods with probes, resource limits and a non-root security context. |
| `k8s/service.yaml` | Gives the pods one stable address, load-balances between them, and opens NodePort 30090. |
| `k8s/ingress.yaml` | Exposes the service outside the cluster at `meditriage.local`. |
| `k8s/kustomization.yaml` | Lets `kubectl apply -k k8s/` apply all manifests at once (not the Secret). |
| `k8s/kind-cluster.yaml` | Settings for the local kind cluster: maps host port 30090 to the NodePort. |
| `scripts/deploy-k8s.sh` | The deploy step both pipelines share: cluster, image load, Secret, apply, rollout, `/health`. |
| `scripts/start.sh` | One-command local start: frees the ports from this project's leftovers, runs compose, waits until healthy, prints the URLs. |
| `scripts/stop.sh` | Stops the compose stack and any single `meditriage` container (Kubernetes is left running). |
| `.github/workflows/ci.yml` | GitHub Actions: test, build, check `/health`, push to GHCR (main), deploy to a kind cluster. |
| `Jenkinsfile` | Jenkins pipeline: test, build, check, then deploy to the local kind cluster (live on :30090). |
| `README.md` | Short how-to-run guide. |
| `EXPLAINED.md` | This file. |

## 4. Line by line

### The agents (app/agents/care_team.py)

| Code | Meaning |
|---|---|
| `AGENT_NAMES = [...]` | The three agent names, also returned by `/health` so you can see the team is loaded. |
| `create_model()` | Returns a `Gemini` or `Groq` model object depending on `MODEL_PROVIDER`. Creating it does not call the internet. |
| `Agent(name=..., role=..., instructions=[...])` | An Agno agent: an LLM plus a role and a list of rules it must follow. |
| `tools=[check_red_flags, search_medical_guidelines, get_department_info]` | Only the Investigator gets tools. Agno turns each Python function (name, type hints, docstring) into a function-calling schema. |
| `"You MUST call check_red_flags with the patient's ORIGINAL words."` | The summary may soften the patient's words, so the red-flag check gets the original text. |
| Coordinator: `"Start your answer with exactly these four lines: Urgency: ..."` | A fixed format, so `extract_field()` can read the urgency and department from the text. |
| `"If ANY red flag was found, Urgency MUST be U1"` / `"When unsure ... choose the more urgent one"` | Clinical safety rule: over-triage is safer than under-triage. |
| `"Never give a definite diagnosis and never name prescription medicines or doses."` | The app guides, it does not treat. |
| `red_flags = find_red_flags(symptoms)` | Step 0. Plain Python, runs before any LLM, cannot "forget" or hallucinate. |
| `team["analyst"].run(patient)` | Step 1. `.run()` sends the prompt to the LLM and returns a `RunOutput` whose `.content` is the text. |
| `team["investigator"].run(f"{patient} ... {analysis}")` | Step 2. The hand-off: the analyst's output is pasted into the investigator's prompt. |
| `for call in _tool_calls(investigation_run)` | Agno records each tool call (`tool_name`, `result`). We collect tool names and `GL-xxx` IDs from the **real tool results**, so the API never reports an ID the LLM invented. |
| `team["coordinator"].run(...)` | Step 3. Gets the words, the safety-net result, and both earlier outputs. |
| `if red_flags and urgency != "U1": urgency = "U1" ...` | The safety override. Adds a "Safety override" note to the top of the report. |
| `_content()` raises if `status` contains "error" | If the provider fails (wrong key, rate limit), we raise, and `main.py` turns it into a clean HTTP 500. |

### The red-flag matcher (app/agents/tools.py)

| Code | Meaning |
|---|---|
| `re.finditer(rf"\b{re.escape(phrase)}\b", lower)` | Find the phrase as whole words, so "fit" does not match "benefit". `re.escape` makes characters like `'` safe in the regex. |
| `.replace("’", "'")` | Phones often type a curly apostrophe; "can’t breathe" must match "can't breathe". |
| `_is_negated()` | Looks at the 3 words before the phrase. "no chest pain", "denies chest pain" are skipped. |
| `re.split(r"[.,;:!?]\|\bbut\b", ...)[-1]` | Only look inside the same clause: in "No fever but chest pain", the "no" belongs to "fever", so chest pain still counts. |
| `score_guideline()` | Keyword hit = 3 points, word shared with the title = 2, word shared with the description = 1. Below `MIN_SCORE = 4` it does not count. Simple, explainable, no extra libraries. |

### Dockerfile

| Line | Meaning |
|---|---|
| `FROM python:3.11-slim` | Start from the official Python 3.11 image, "slim" Debian variant (small, no compilers). |
| `ENV PYTHONDONTWRITEBYTECODE=1` | Do not write `.pyc` files; they are useless in an image. |
| `PYTHONUNBUFFERED=1` | Print logs immediately, so `docker logs` shows them in real time. |
| `PIP_NO_CACHE_DIR=1` | Do not keep pip's download cache, which keeps the image smaller. |
| `PIP_DISABLE_PIP_VERSION_CHECK=1` | Skip pip's "new version available" check. |
| `PIP_DEFAULT_TIMEOUT=300`, `PIP_RETRIES=10` | Wait up to 5 minutes per download and retry 10 times, so a slow network doesn't fail the build with "Read timed out". |
| `WORKDIR /app` | All following commands run in `/app`, and it is created if missing. |
| `RUN groupadd ... useradd ...` | Create user `appuser` with UID 10001 and no login shell. |
| `COPY requirements.txt .` | Copy ONLY the dependency list first. |
| `RUN --mount=type=cache,target=/root/.cache/pip` | Give pip a download cache that lives on the build machine, not in the image. If a build fails halfway, the next try reuses what already downloaded. |
| `env -u PIP_NO_CACHE_DIR pip install -r requirements.txt` | Install libraries. `env -u` switches off the "no cache" setting for this one command so the cache mount is used. This layer is reused until `requirements.txt` changes, so code edits rebuild in seconds. |
| `COPY --chown=appuser:appuser app ./app` | Copy the code, owned by appuser. |
| `COPY ... streamlit_app.py .` / `.streamlit` | Copy the optional UI and its config. |
| `USER appuser` | Everything from here on, including the running app, is not root. |
| `EXPOSE 8000 8501` | Documents which ports the container listens on (it does not publish them; `-p` does). |
| `HEALTHCHECK ... CMD python -c "urllib...(/health)"` | Every 30s Docker calls `/health`. After 3 failures the container is marked `unhealthy`. Python is used instead of curl, so nothing extra is installed. |
| `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]` | Default start command. `0.0.0.0` means "accept connections from outside the container", which port mapping needs. |

### docker-compose.yml

| Line | Meaning |
|---|---|
| `services:` | List of containers to run. |
| `api:` / `build: context: . network: host` | Build the image from the Dockerfile in this folder, using the host's network for pip. |
| `image: meditriage:1.0` | Name and tag of the built image. |
| `container_name: meditriage-api` | Fixed container name, which makes `docker logs meditriage-api` easy. |
| `env_file: - path: .env, required: false` | Load API keys from `.env` on the host. If `.env` is missing the stack still starts and `/triage` returns 503. (Needs Compose 2.24 or newer.) |
| `ports: "${API_PORT:-8000}:8000"` | Host port goes to container port 8000. `${API_PORT:-8000}` means "use the variable `API_PORT` from the shell or `.env`, or 8000 if it is not set", so a busy port is fixed with `API_PORT=8003` without editing the file. The UI does the same with `UI_PORT`. |
| `volumes: ./app/data:/app/app/data:ro` | Mount the guidelines, departments and red flags from the host, read-only. A doctor can update a guideline in the JSON and the tools see it on the next request, no rebuild. |
| `healthcheck: test/interval/timeout/retries/start_period` | Same `/health` check as the Dockerfile: every 15s, 5s timeout, 3 failures means unhealthy, failures in the first 20s don't count. |
| `restart: unless-stopped` | If the container crashes, Docker restarts it (unless you stopped it yourself). |
| `ui:` / `command: streamlit run ...` | Same image, but starts Streamlit instead of uvicorn. |
| `API_URL: http://api:8000` | Compose has built-in DNS, so the name `api` resolves to the api container. |
| `depends_on: api: condition: service_healthy` | Start the UI only after the API is healthy. |
| (no `env_file` on ui) | The UI never sees the API keys (least privilege). |

### k8s/deployment.yaml

| Line | Meaning |
|---|---|
| `apiVersion: apps/v1`, `kind: Deployment` | The object type that manages identical pods. |
| `metadata: name/namespace/labels` | Name it `meditriage-api` in the `meditriage` namespace. |
| `replicas: 2` | Always keep 2 pods running. If one dies, a new one is created, and the other keeps serving patients meanwhile. |
| `selector.matchLabels: app: meditriage-api` | The deployment owns pods with this label. |
| `template:` | The blueprint for each pod. |
| `securityContext: runAsNonRoot / runAsUser: 10001` | Kubernetes refuses to run the pod as root, and uses the same UID as the Dockerfile. |
| `image: meditriage:1.0`, `imagePullPolicy: IfNotPresent` | Use the image loaded into the cluster by `kind load` instead of pulling from a registry. |
| `containerPort: 8000` (named `http`) | The port the app listens on. |
| `envFrom: configMapRef / secretRef` | Every key in the ConfigMap and the Secret becomes an environment variable, and `config.py` reads them. |
| `readinessProbe: httpGet /health` | "Can this pod take traffic?" If not, the Service stops sending it requests. |
| `livenessProbe: httpGet /health` | "Is this pod still alive?" If it fails 3 times, Kubernetes restarts the container. |
| `initialDelaySeconds / periodSeconds / failureThreshold` | Wait before the first check, how often to check, and how many failures count as "down". |
| `resources.requests: cpu 100m, memory 128Mi` | Minimum guaranteed (100m = 0.1 CPU). The scheduler uses this to pick a node. |
| `resources.limits: cpu 500m, memory 512Mi` | Hard maximum. Above 512Mi the container is killed (OOMKilled), and CPU is throttled at 0.5. |
| `allowPrivilegeEscalation: false`, `drop: ["ALL"]` | The process cannot gain extra Linux privileges. |

### k8s/service.yaml and k8s/kind-cluster.yaml

| Line | Meaning |
|---|---|
| `type: NodePort` | Open the Service on a fixed port of the cluster node, so it can be reached from outside the cluster. |
| `port: 80`, `targetPort: http` | Inside the cluster the Service listens on 80 and forwards to the pod's port named `http` (8000). |
| `nodePort: 30090` | The port opened on the node. NodePorts must be between 30000 and 32767. TriageBot already uses 30080, so we use 30090. |
| `extraPortMappings: containerPort 30090 -> hostPort 30090` | (kind config) The kind "node" is a Docker container; this publishes its port 30090 on your laptop, so `http://localhost:30090` reaches the Service. |

### k8s/configmap.yaml, secret.yaml, ingress.yaml, kustomization.yaml

| File | Meaning |
|---|---|
| `configmap.yaml` | `MODEL_PROVIDER`, model names and `REQUEST_TIMEOUT`: settings that are safe to show. |
| `secret.yaml` | Placeholder keys only, kept as an example of the format. Not applied. |
| `ingress.yaml` | Routes `http://meditriage.local` to the Service when an ingress controller (nginx) is installed. `proxy-read-timeout: 120` because three LLM calls can take a while. |
| `kustomization.yaml` | The list of files `kubectl apply -k k8s/` applies, in order. The Secret is deliberately left out. |

### scripts/deploy-k8s.sh (the deploy step both pipelines run)

| Line | Meaning |
|---|---|
| `set -euo pipefail` | Stop at the first failing command (`-e`), treat unset variables as errors (`-u`), and fail a pipe if any part fails (`pipefail`). A half-finished deploy never reports success. |
| `IMAGE="${1:?usage...}"` | The first argument is the image to deploy, e.g. `meditriage:42`. If it is missing, print the usage and stop. |
| `kind get clusters \| grep -qx "$CLUSTER"` / `kind create cluster ...` | Create the cluster only the first time; later runs reuse it. |
| `kind get kubeconfig ... > "$KUBECONFIG"` | Write the cluster's address and certificates to a temporary file, so kubectl knows where to connect. Works for any user in the docker group, including `jenkins`. |
| `trap 'rm -f "$KUBECONFIG"' EXIT` | Delete that temporary file when the script ends, even on failure. |
| `kind load docker-image "$IMAGE"` | Copy the image from your Docker into the cluster node. No registry needed. |
| `kubectl apply -f k8s/namespace.yaml` | Create the namespace first, because the Secret lives inside it. |
| `kubectl create secret generic ... --dry-run=client -o yaml \| kubectl apply -f -` | Build the Secret from the pipeline's key without sending it (`--dry-run=client -o yaml` prints it), then `apply` creates or updates it. Works on every run. |
| `kubectl kustomize k8s/ \| sed "s\|image: meditriage:1.0\|image: $IMAGE\|" \| kubectl apply -f -` | Combine all manifests, swap in the image tag this build made, and apply them. The changed tag starts a rolling update. |
| `SECRET_HASH=... sha256sum` / `kubectl patch ... annotations.meditriage/secret-hash` | Pods copy a Secret into their environment **only when they start**. If the key changes but the image tag does not (e.g. running the script twice with `meditriage:1.0`), `kubectl apply` says "unchanged" and the old pods keep the old (or empty) key, so `/triage` gives 503. We put a short fingerprint of the Secret on the pod template; a new key means a new fingerprint, which makes Kubernetes do a rolling restart. Same key, same fingerprint, no restart. |
| `kubectl rollout status ... --timeout=180s` | Wait until the new pods pass their readiness probe. If not within 3 minutes, print pod details and logs and fail the pipeline. |
| `curl -fsS http://localhost:30090/health` (retry loop) | Final smoke test through the NodePort, the same way a user reaches the app. `-f` makes curl fail on an HTTP error. If a key was given, it also requires `"llm_configured":true`, so a missing key fails the pipeline right away instead of showing up later as a 503. |

### .github/workflows/ci.yml

| Line | Meaning |
|---|---|
| `on: push, pull_request` | Run on every push to any branch and on every pull request. |
| `permissions: packages: write` | Let the built-in `GITHUB_TOKEN` push images to GitHub Container Registry. |
| `jobs: test` | Fresh Ubuntu machine: checkout, install Python 3.11, install requirements, run pytest. |
| `deploy: needs: test` | Only runs if `test` passed. |
| `docker build -t "$IMAGE"` | Build the image, tagged with the commit id (`github.sha`), so every version is unique. |
| `docker run ... curl /health` | Prove the container starts and answers before deploying it. |
| `if: github.event_name == 'push' && github.ref == 'refs/heads/main'` | Push the image to `ghcr.io` only for pushes to `main`. |
| `${GITHUB_REPOSITORY_OWNER,,}` | Bash for "lowercase this", because registry names must be lowercase. |
| `uses: helm/kind-action@v1` | Ready-made step that installs kind and kubectl and creates a cluster from `k8s/kind-cluster.yaml`. |
| `GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}` | Read the key from the repo's encrypted secrets. GitHub masks it as `***` in the logs. |
| `run: ./scripts/deploy-k8s.sh "$IMAGE"` | Deploy to that cluster and check it. The cluster is thrown away when the job ends; it proves the deploy works. |

### Jenkinsfile

| Line | Meaning |
|---|---|
| `pipeline { agent any` | Declarative pipeline that may run on any Jenkins machine (here, your laptop). |
| `triggers { pollSCM('H/5 * * * *') }` | Check GitHub for new commits about every 5 minutes. `H` spreads jobs out. Webhooks can't reach a laptop, so polling is used. |
| `environment { IMAGE = "meditriage:${BUILD_NUMBER}" }` | Each build gets its own image tag: build 7 makes `meditriage:7`. |
| `HOST_PORT = "8002"` | The test container's port; 8000 is compose and 8001 is TriageBot's Jenkins job. |
| `stage('Checkout') { checkout scm }` | Download the code from the repo configured in the job. |
| `stage('Test')` | Make a venv, install requirements, run pytest and save a JUnit XML report. |
| `stage('Docker Build')` | `docker build --network=host` so pip uses the laptop's network directly. |
| `stage('Run & Health Check')` | Start the image on port 8002 and curl `/health`. |
| `withCredentials([string(credentialsId: 'groq-api-key', ...)])` | Load the key from Jenkins' credential store into `$GROQ_API_KEY` for this step only, masked in the log. Same credential ID as TriageBot, so it can be reused. |
| `sh './scripts/deploy-k8s.sh "$IMAGE"'` | Deploy to the kind cluster on this machine. It stays running at `localhost:30090`. |
| `post { always { junit ...; docker rm -f ... } }` | Always publish the test report and remove the test container, even if a stage failed. |

## 5. Step-by-step: GitHub and Jenkins setup

### GitHub (once)

1. Create an empty repository on github.com named `MediTriage` (no README, no .gitignore).
2. In this folder:
   ```bash
   cd "/home/deadend/Desktop/Medical Healthcare"
   git init
   git add .
   git status                      # make sure .env and .venv are NOT listed
   git commit -m "MediTriage: multi-agent patient triage with Docker, K8s, Jenkins, GitHub Actions"
   git branch -M main
   git remote add origin https://github.com/<your-user>/MediTriage.git
   git push -u origin main         # password = personal access token with scopes repo + workflow
   ```
3. Optional: repo **Settings → Secrets and variables → Actions → New repository secret**,
   name `GROQ_API_KEY`. Without it the pipeline still passes; the deployed pods just answer 503 on `/triage`.
4. Open the **Actions** tab: the `CI/CD` run should go green (test → deploy). On `main`, the image
   appears under your profile's **Packages** as `meditriage`.

### Jenkins (once; Jenkins is already installed on this laptop for TriageBot)

1. The machine setup from TriageBot is reused: `jenkins` in the `docker` group, `python3-venv`, `kind`, `kubectl`.
2. Credential: if `groq-api-key` (Secret text) already exists from TriageBot, nothing to do. Otherwise
   **Manage Jenkins → Credentials → System → Global → Add Credentials → Secret text**, ID `groq-api-key`.
3. **New Item** → name `MediTriage` → **Pipeline** → **Pipeline script from SCM** → Git,
   URL `https://github.com/<your-user>/MediTriage.git`, branch `*/main`, script path `Jenkinsfile` → Save.
4. **Build Now**. All 5 stages should be green, and the app is live at http://localhost:30090.
   From now on every push is picked up within about 5 minutes by `pollSCM`.

## 6. Likely viva questions

1. **What does each agent do?** The Symptom Analyst turns the patient's words into a structured summary and picks a likely department. The Clinical Investigator calls `check_red_flags`, `search_medical_guidelines` and `get_department_info` to ground the decision in hospital data. The Triage Coordinator decides urgency, department and care setting and explains it in plain words.
2. **Why is there a rule-based check if we have an LLM?** LLMs are probabilistic and can under-triage. A missed heart attack is the worst possible error. Plain keyword rules are predictable and testable, so they act as a safety net that can only make the answer *more* urgent, never less.
3. **How do you avoid false alarms like "no chest pain"?** `_is_negated()` checks the three words before the phrase in the same clause. "No fever but chest pain" still counts, because "but" starts a new clause. Some false alarms remain, and that is acceptable: over-triage is safer than under-triage.
4. **What is function calling?** We give the LLM the tool's name, parameters and docstring. The LLM replies "call `search_medical_guidelines('high fever joint pain')`", Agno runs our Python function, and the result goes back to the LLM. The API's `tools_used` field proves it happened.
5. **What is grounding / why the guideline library?** The LLM must base its advice on the hospital's own guidance, not on whatever it remembers. `matched_guidelines` comes from real tool results, so an invented ID can never appear there.
6. **How is patient privacy handled?** Symptoms are never logged (only their length), not stored, the keys are never logged or returned, and the UI container never sees the keys. A real system would also need consent, encryption and compliance with health-data laws (e.g. India's DPDP Act, HIPAA in the US).
7. **Is this a medical device?** No. It is an educational demo. Every response includes a disclaimer, it never diagnoses or prescribes, and the UI always tells the patient to call 112 / 108 in an emergency.
8. **Image vs container?** An image is a read-only template (code, Python, libraries). A container is a running instance of an image. One image can run many containers.
9. **Why `python:3.11-slim`?** It is official and much smaller than the full image, so builds, pushes and the attack surface are all smaller.
10. **Why copy `requirements.txt` before the code?** Layer caching. The slow `pip install` layer is reused until dependencies change, so code edits rebuild in seconds.
11. **Why a non-root user?** If someone exploits the app, they get a powerless user, not root. Kubernetes `runAsNonRoot` enforces it. For health data this matters even more.
12. **Why a healthcheck?** "Process is running" does not mean "app works". `/health` proves the web server responds. Docker, compose and Kubernetes all act on it.
13. **Readiness vs liveness probe?** Readiness decides whether a pod gets traffic. Liveness decides whether the pod should be restarted.
14. **ConfigMap vs Secret?** A ConfigMap holds normal settings (model name, timeout). A Secret holds sensitive values (API keys); it is stored separately, can be encrypted and access-controlled, and is not shown by `kubectl describe`. Base64 is encoding, not encryption.
15. **Why environment variables for keys?** The same image runs everywhere with different keys, and keys never end up in git or inside the image. That is also why `.env` is in `.gitignore` and `.dockerignore`.
16. **Why mock the LLM in tests?** Tests must be fast, free, repeatable and run in CI without a key. Our fake Investigator still runs the real tools, and the safety-override test uses a fake coordinator that deliberately answers "U4" for chest pain, to prove the override works.
17. **What happens without an API key?** `/health` still returns 200 with `llm_configured: false`, and `/triage` returns 503 with a clear message.
18. **What does docker compose add over `docker run`?** Both containers, their ports, volume, env file, healthchecks, restart policy and start order in one file, started with one command on a shared network with DNS.
19. **Why mount the data folder as a volume?** Guidelines change. A clinician can edit `guidelines.json` on the host and the next request uses it, with no rebuild. `:ro` means the container can't modify it.
20. **What does the CI/CD pipeline do?** On every push: run pytest. Only if that passes: build the image, start it and curl `/health`, then deploy it to Kubernetes with `scripts/deploy-k8s.sh` (load image, create Secret, apply manifests with the new tag, wait for rollout, curl `/health`). GitHub Actions also pushes the image to GHCR on `main`. Jenkins deploys to the cluster on this laptop, so the app stays live at `localhost:30090`.
21. **What is a rolling update?** Each build has a new image tag, so the Deployment changes. Kubernetes starts new pods, waits for their readiness probe, then removes old pods, so there is no downtime. A bad image never becomes ready, the rollout times out, the pipeline fails and the old pods keep serving.
22. **Kubernetes is a deployment tool, so why also Jenkins?** Kubernetes *runs and heals* the app; it never sees the code, runs tests or builds images. Jenkins/GitHub Actions *test, build and roll out* each new version to Kubernetes.
    ```
    git push -> Jenkins / GitHub Actions: pytest -> docker build -> container check -> kubectl apply (new tag)
                                                                                           |
                                                       Kubernetes: rolling update -> keep 2 pods healthy
    ```
23. **Why kind and not minikube?** kind runs the whole cluster as Docker containers, so any user who can use Docker (the jenkins user, the GitHub runner) can create it and load images into it. Same Kubernetes API as any real cluster.
24. **Why is secret.yaml not in kustomization?** Otherwise every deploy would overwrite the real key with the placeholder.
25. **CI vs CD?** Continuous Integration: every push is automatically tested and built. Continuous Deployment: every passing build is automatically released. We do both.
26. **Jenkins was green but `/triage` gave 503. Why?** The pods were started before the key was in the Secret. Environment variables from a Secret are read only at pod start, and re-applying the same image tag changes nothing, so no restart happened. Fix in `deploy-k8s.sh`: a fingerprint of the Secret on the pod template forces a rolling restart whenever the key changes, and the smoke test now checks `llm_configured` too. (Alternative: mount the Secret as a file; mounted Secrets update inside running pods, env vars do not.)
27. **(Bonus) Why do 504 and 500 exist?** 504: the three LLM calls took longer than `REQUEST_TIMEOUT` (the message also reminds the user to call 112 if it's an emergency). 500: the provider failed; the key is replaced with `[REDACTED]`.

## 7. Demo script (under 3 minutes)

```bash
cd "/home/deadend/Desktop/Medical Healthcare"
source .venv/bin/activate
pytest -v                                   # 22 passed, no key needed
./scripts/start.sh                          # frees ports, builds, starts, waits: api and ui "(healthy)"
curl http://localhost:8000/health
```

Open **http://localhost:8000** and try the three example buttons:

| Example | Expected |
|---|---|
| **Fever with joint pain** (age 22) | U3, General Medicine (OPD), matched guideline **GL-206** (suspected dengue), advice to get a blood test and avoid ibuprofen/aspirin. |
| **Chest pain and sweating** (age 58) | Red **EMERGENCY** banner, U1, Emergency Department, red flag "Possible heart attack", GL-201. |
| **Twisted ankle** (age 19) | U4, Orthopaedics, GL-209, RICE advice. |

The LLM's wording varies between runs; the urgency for the chest-pain case is always U1 because of the safety net.

From the terminal:

```bash
curl -s -X POST http://localhost:8000/triage -H "Content-Type: application/json" \
  -d '{"symptoms":"My mother face is drooping on one side and her speech is slurred since 10 minutes.","age":67}'
# "urgency":"U1", "emergency":true, "red_flags":["Possible stroke"]
```

Show the second container: **http://localhost:8501** (Streamlit UI calling the API over the compose network).

Show Kubernetes (deployed by Jenkins or by hand with `./scripts/deploy-k8s.sh meditriage:1.0`):

```bash
kubectl --context kind-meditriage -n meditriage get pods,svc    # 2 pods Running, NodePort 30090
curl http://localhost:30090/health
```

Show self-healing:

```bash
kubectl --context kind-meditriage -n meditriage delete pod -l app=meditriage-api --wait=false
kubectl --context kind-meditriage -n meditriage get pods -w     # new pods appear; Ctrl+C to stop
```

Show the pipelines: the green Jenkins build (all 5 stages) and the green GitHub Actions run.
Finish with `./scripts/stop.sh` (or `docker compose down`).

## 8. Every command explained

### Python and tests

| Command | What it does |
|---|---|
| `python3 -m venv .venv` | Create a private Python folder `.venv` for this project. |
| `source .venv/bin/activate` | Use that venv in this terminal (the prompt shows `(.venv)`). `deactivate` leaves it. |
| `pip install -r requirements.txt` | Install every library at the exact version listed. |
| `pytest -v` | Run all tests; `-v` prints each test name and PASSED/FAILED. |
| `uvicorn app.main:app --host 127.0.0.1 --port 8000` | Start the API without Docker. `app.main:app` = "the `app` object in `app/main.py`". |

### Docker

| Command | What it does |
|---|---|
| `docker build --network=host -t meditriage:1.0 .` | Build an image from the `Dockerfile` in `.`. `-t` names it. `--network=host` avoids pip timeouts. |
| `docker run -d --name meditriage -p 8000:8000 --env-file .env meditriage:1.0` | Start a container: `-d` background, `--name` fixed name, `-p host:container` publish the port, `--env-file` pass the keys. |
| `docker ps` / `docker ps -a` | List running / all containers. |
| `docker logs meditriage` (`-f` to follow) | Show what the app printed. |
| `docker exec meditriage-api id` | Run a command inside the container; shows it runs as `appuser` (uid 10001), not root. |
| `docker rm -f meditriage` | Stop and delete the container (`-f` = even if running). The image stays. Needed before reusing the name. |
| `ss -ltnp \| grep :8000` | Show which program is listening on port 8000 (`-l` listening, `-t` TCP, `-n` numbers, `-p` process). Nothing printed = free. |
| `docker images` | List images. |

Errors you may see:

- **"failed to bind host port 0.0.0.0:8000/tcp: address already in use"** (or **"port is already allocated"**): only one program can listen on a port. Usually it is the local `uvicorn` from the quick start, the compose `api` container, or an earlier `docker run`. Find it with `ss -ltnp | grep :8000` (a process on the laptop) and `docker ps` (a container). Stop it with `Ctrl+C`, `docker compose down` or `docker rm -f meditriage`, or publish on another host port (`-p 8003:8000`; only the left, host-side number has to be free).
- **"Conflict. The container name "/meditriage" is already in use"**: a failed `docker run` still creates the container (status `Created`) before the port error. Remove it with `docker rm -f meditriage` and run again.

### Docker Compose

| Command | What it does |
|---|---|
| `docker compose up -d --build` | Build if needed and start every service in the background. |
| `docker compose up -d --force-recreate` | Recreate the containers, e.g. after editing `.env` (containers only read it when created). |
| `docker compose ps` | Show the services and their health. |
| `docker compose logs -f api` | Follow the api service's logs. |
| `docker compose down` | Stop and remove the containers and the network. |
| `API_PORT=8003 UI_PORT=8503 docker compose up -d` | Same stack on other host ports, when 8000/8501 are busy. |
| `./scripts/start.sh` / `./scripts/stop.sh` | Our wrappers: clean up leftovers, start, wait for healthy, print URLs / stop everything. |

### Kubernetes (kind and kubectl)

| Command | What it does |
|---|---|
| `kind create cluster --name meditriage --config k8s/kind-cluster.yaml` | Create a one-node cluster that runs as a Docker container (the deploy script does this the first time). |
| `kind get clusters` | List kind clusters (`meditriage`, and `triagebot` if it exists). |
| `kind load docker-image meditriage:1.0 --name meditriage` | Copy an image from your Docker into the cluster. |
| `kind delete cluster --name meditriage` | Delete the cluster and everything in it. |
| `./scripts/deploy-k8s.sh meditriage:1.0` | Our full deploy. Prefix with `GROQ_API_KEY=...` to set the key. |
| `kubectl config get-contexts` | List the clusters kubectl knows. `kind-meditriage` is ours. |
| `kubectl config use-context kind-meditriage` | Make it the default, so `--context` is not needed. |
| `kubectl -n meditriage get pods,svc` | List pods and services in the namespace (`-n`). |
| `kubectl -n meditriage get pods -w` | Watch pods change. |
| `kubectl -n meditriage describe pod <name>` | Full details and events of one pod. First place to look when a pod won't start. |
| `kubectl -n meditriage logs -l app=meditriage-api` | Logs of all pods with that label. |
| `kubectl apply -k k8s/` | Create or update everything in `k8s/kustomization.yaml`. |
| `kubectl -n meditriage rollout status deployment/meditriage-api` | Wait until a rolling update finishes. |
| `kubectl -n meditriage rollout undo deployment/meditriage-api` | Go back to the previous version. |
| `kubectl -n meditriage scale deployment/meditriage-api --replicas=3` | Run 3 pods instead of 2. |
| `kubectl -n meditriage delete pod -l app=meditriage-api` | Delete the pods; the Deployment recreates them (self-healing demo). |

### Git and GitHub

| Command | What it does |
|---|---|
| `git init` | Make this folder a git repository. |
| `git add .` | Stage every file except what `.gitignore` excludes (`.env`, `.venv`). |
| `git status` | Show what is staged. Check that `.env` is NOT listed. |
| `git commit -m "message"` | Save a snapshot. |
| `git branch -M main` | Rename the branch to `main`. |
| `git remote add origin <url>` | Remember the GitHub repo under the name `origin`. |
| `git push -u origin main` | Upload commits. The password is a **personal access token** (scopes `repo`, `workflow`). |
| `git log --oneline` | List commits, one per line. |

### Jenkins

| Command / action | What it does |
|---|---|
| `sudo usermod -aG docker jenkins` | Let the `jenkins` user run docker (and therefore kind). |
| `sudo systemctl restart jenkins` | Restart Jenkins so it picks up the new group. |
| `sudo apt install -y python3-venv` | Needed for the Test stage's `python3 -m venv`. |
| Manage Jenkins → Credentials → Secret text, ID `groq-api-key` | Stores the key encrypted; the Jenkinsfile reads it by that ID. |
| New Item → Pipeline → "Pipeline script from SCM" | Jenkins reads the pipeline from the repo, so the pipeline itself is version-controlled. |
| Build Now | Run the pipeline by hand. After that, `pollSCM` runs it on every new commit. |

### curl

| Command | What it does |
|---|---|
| `curl http://localhost:8000/health` | Send a GET request and print the reply. |
| `curl -s -X POST <url> -H "Content-Type: application/json" -d '{"symptoms":"..."}'` | Send a POST with a JSON body. `-s` hides the progress bar. |
| `curl -fsS` | `-f` fails on HTTP errors (so scripts notice), `-sS` silent but still shows errors. |

## 9. Tools used and easier alternatives

| Job | We used | Easier alternative | Trade-off |
|---|---|---|---|
| Web API | FastAPI | Flask | Fewer concepts; FastAPI gives input validation and `/docs` for free. |
| Agents | Agno | Plain Groq/Gemini SDK calls, or CrewAI | Plain calls are easy to read but you write tool calling yourself. CrewAI has a similar "agents + tasks" style. |
| LLM | Groq / Gemini free tier | Ollama (local model) | No key and no data leaves the laptop (good for health data), but needs a strong laptop and gives weaker answers. |
| Red-flag safety net | keyword rules + negation | a medical NER model (e.g. scispaCy) | Rules are simple and testable; a model catches more phrasings but is heavier and less predictable. |
| Guideline search | keyword scoring | vector search / RAG (embeddings + FAISS or Chroma) | Better for a big library and synonyms; needs an embedding model and a vector store. |
| Second UI | Streamlit | Gradio | Even less code for a simple input/output form. |
| Containers | Docker | Docker Desktop (GUI) or Podman | Docker Desktop shows everything in a window; Podman needs no daemon or root. |
| Local Kubernetes | kind | Docker Desktop Kubernetes, minikube, k3d | kind is the easiest for CI because it only needs Docker. |
| Manifests | plain YAML + Kustomize | Helm | Helm is popular for sharing apps but not simpler. |
| Cluster UI | `kubectl` | k9s, Lens / Headlamp | Nicer for demos. |
| CI/CD (cloud) | GitHub Actions | GitLab CI | Nothing to install; one YAML file in the repo. |
| CI/CD (self-hosted) | Jenkins | Woodpecker / Drone | Jenkins needs plugins, credentials and a job set up by hand, but is very common in companies. |
| Image registry | GHCR | Docker Hub | GHCR needs no extra account in Actions, because `GITHUB_TOKEN` already has access. |
| Secrets | Kubernetes Secret + pipeline credentials | `.env` only (Compose) | Simplest, but only works on one machine. |
