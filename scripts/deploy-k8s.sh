#!/usr/bin/env bash
# Deploys a MediTriage image to the local kind cluster and checks that it works.
# Used by BOTH pipelines (Jenkinsfile and .github/workflows/ci.yml), and by hand:
#   GROQ_API_KEY=gsk_... ./scripts/deploy-k8s.sh meditriage:1.0
#
# Needs on the machine: docker, kind, kubectl, curl.
# Reads (optional) GROQ_API_KEY / GEMINI_API_KEY from the environment for the Secret.
set -euo pipefail

IMAGE="${1:?usage: deploy-k8s.sh <image>   e.g. meditriage:1.0}"
CLUSTER="${KIND_CLUSTER:-meditriage}"
NAMESPACE="meditriage"
NODE_PORT="${NODE_PORT:-30090}"
cd "$(dirname "$0")/.."                       # run from the project root

# 1. Create the cluster the first time (later runs reuse it).
if ! kind get clusters | grep -qx "$CLUSTER"; then
    echo ">> Creating kind cluster '$CLUSTER'"
    kind create cluster --name "$CLUSTER" --config k8s/kind-cluster.yaml --wait 180s
fi

# kind writes the cluster's address and certificates; any user in the docker group can read them.
# A private kubeconfig file means this works for the jenkins user too.
KUBECONFIG="$(mktemp)"
export KUBECONFIG
trap 'rm -f "$KUBECONFIG"' EXIT
kind get kubeconfig --name "$CLUSTER" > "$KUBECONFIG"

# 2. Copy the image from the host's Docker into the cluster node (no registry needed).
echo ">> Loading $IMAGE into the cluster"
kind load docker-image "$IMAGE" --name "$CLUSTER"

# 3. Namespace first, because the Secret lives inside it.
kubectl apply -f k8s/namespace.yaml

# 4. API keys come from the pipeline's credentials, never from git.
#    No key given: keep the existing Secret, or create an empty one so pods can start
#    (the app then answers /health but /triage returns 503).
KEY_GIVEN=false
if [[ -n "${GROQ_API_KEY:-}" || -n "${GEMINI_API_KEY:-}" ]]; then
    KEY_GIVEN=true
    echo ">> Updating Secret meditriage-secrets"
    kubectl -n "$NAMESPACE" create secret generic meditriage-secrets \
        --from-literal=GROQ_API_KEY="${GROQ_API_KEY:-}" \
        --from-literal=GEMINI_API_KEY="${GEMINI_API_KEY:-}" \
        --dry-run=client -o yaml | kubectl apply -f -
elif ! kubectl -n "$NAMESPACE" get secret meditriage-secrets >/dev/null 2>&1; then
    echo ">> No API key given; creating an empty Secret"
    kubectl -n "$NAMESPACE" create secret generic meditriage-secrets \
        --from-literal=GROQ_API_KEY= --from-literal=GEMINI_API_KEY=
fi

# 5. Apply all manifests, swapping in the image this pipeline just built.
#    A new tag changes the Deployment, so Kubernetes does a rolling update.
echo ">> Applying manifests with image $IMAGE"
kubectl kustomize k8s/ | sed "s|image: meditriage:1.0|image: $IMAGE|" | kubectl apply -f -

# 5b. Pods read the Secret into their environment only when they START. So we stamp a
#     fingerprint (sha256) of the Secret on the pod template. When the key changes, the
#     fingerprint changes, and Kubernetes does a rolling restart, even if the image tag
#     is the same as last time. Same key = same fingerprint = no restart.
SECRET_HASH="$(kubectl -n "$NAMESPACE" get secret meditriage-secrets -o jsonpath='{.data}' | sha256sum | cut -c1-16)"
CURRENT_HASH="$(kubectl -n "$NAMESPACE" get deployment meditriage-api \
    -o jsonpath='{.spec.template.metadata.annotations.meditriage/secret-hash}')"
if [[ "$SECRET_HASH" != "$CURRENT_HASH" ]]; then
    echo ">> Secret changed since the pods started: rolling restart to load it"
    kubectl -n "$NAMESPACE" patch deployment meditriage-api --type merge \
        -p "{\"spec\":{\"template\":{\"metadata\":{\"annotations\":{\"meditriage/secret-hash\":\"$SECRET_HASH\"}}}}}"
fi

# 6. Wait until the new pods pass their readiness probe.
if ! kubectl -n "$NAMESPACE" rollout status deployment/meditriage-api --timeout=180s; then
    echo ">> Rollout failed"
    kubectl -n "$NAMESPACE" get pods -o wide
    kubectl -n "$NAMESPACE" describe pods -l app=meditriage-api | tail -40
    kubectl -n "$NAMESPACE" logs -l app=meditriage-api --tail=50 || true
    exit 1
fi
kubectl -n "$NAMESPACE" get pods,svc

# 7. Smoke test through the NodePort, the same way a user reaches it.
#    When a key was given, also check the pods really loaded it (llm_configured: true),
#    so a missing key fails the pipeline instead of showing up later as HTTP 503.
for i in $(seq 1 30); do
    if HEALTH="$(curl -fsS "http://localhost:$NODE_PORT/health")"; then
        echo "$HEALTH"
        if $KEY_GIVEN && ! grep -q '"llm_configured":true' <<<"$HEALTH"; then
            echo ">> Pods are up but report llm_configured=false although a key was given"
            exit 1
        fi
        $KEY_GIVEN || echo ">> WARNING: no API key given; /triage will return 503 (set GROQ_API_KEY)"
        echo ">> Deployed: http://localhost:$NODE_PORT"
        exit 0
    fi
    sleep 2
done
echo ">> /health did not answer on port $NODE_PORT"
exit 1
