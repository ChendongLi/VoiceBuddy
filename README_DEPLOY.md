# VoiceBuddy — Deployment Guide

## Architecture

```
Client (audio I/O)
    │  WebSocket /ws/voice  (16kHz PCM → raw PCM)
    ▼
FastAPI Server (GKE)
    │
    ├─ Deepgram STT (nova-2)  ← streams mic audio
    ├─ Claude LLM             ← Haiku filler + Sonnet response
    └─ Cartesia TTS (sonic-2) ← streams PCM back to client
```

## Prerequisites

| Tool | Version |
|------|---------|
| `gcloud` CLI | ≥ 460 |
| `kubectl` | ≥ 1.29 |
| `docker` | ≥ 24 |
| Python | 3.11 |

## GCP Setup

### 1. Authenticate

```bash
gcloud auth login
gcloud config set project agentlens-489006
gcloud config set compute/region us-west1
```

### 2. Enable required APIs

```bash
gcloud services enable \
  container.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com
```

### 3. Create Artifact Registry repository

```bash
gcloud artifacts repositories create voicebuddy \
  --repository-format=docker \
  --location=us-west1
```

### 4. Create GKE Autopilot cluster

```bash
gcloud container clusters create-auto voicebuddy-cluster \
  --region=us-west1
```

### 5. Store secrets in Secret Manager

```bash
echo -n "YOUR_DEEPGRAM_KEY"  | gcloud secrets create DEEPGRAM_API_KEY  --data-file=-
echo -n "YOUR_CLAUDE_KEY"    | gcloud secrets create CLAUDE_API_KEY    --data-file=-
echo -n "YOUR_CARTESIA_KEY"  | gcloud secrets create CARTESIA_API_KEY  --data-file=-
echo -n "YOUR_VOICE_ID"      | gcloud secrets create CARTESIA_VOICE_ID --data-file=-
```

### 6. Create Kubernetes secret from Secret Manager values

```bash
# Fetch secrets and create a k8s Secret
kubectl create namespace voicebuddy --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic voicebuddy-secrets \
  --namespace voicebuddy \
  --from-literal=DEEPGRAM_API_KEY="$(gcloud secrets versions access latest --secret=DEEPGRAM_API_KEY)" \
  --from-literal=CLAUDE_API_KEY="$(gcloud secrets versions access latest --secret=CLAUDE_API_KEY)" \
  --from-literal=CARTESIA_API_KEY="$(gcloud secrets versions access latest --secret=CARTESIA_API_KEY)" \
  --from-literal=CARTESIA_VOICE_ID="$(gcloud secrets versions access latest --secret=CARTESIA_VOICE_ID)"
```

### 7. Grant Cloud Build access to GKE

```bash
PROJECT_NUMBER=$(gcloud projects describe agentlens-489006 --format='value(projectNumber)')
CB_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"

gcloud projects add-iam-policy-binding agentlens-489006 \
  --member="serviceAccount:${CB_SA}" \
  --role="roles/container.developer"
```

---

## Local Development

```bash
# Install dependencies
pip install -r server/requirements.txt

# Copy and fill in env vars
cp .env.example .env   # or set manually

# Run with hot reload
uvicorn server.main:app --reload --host 0.0.0.0 --port 8080
```

Health check: http://localhost:8080/health

### WebSocket test (requires `websocat`)

```bash
# Stream a WAV file (convert to raw PCM first)
ffmpeg -i test.wav -f s16le -ar 16000 -ac 1 - | websocat ws://localhost:8080/ws/voice
```

---

## Manual Deployment

```bash
# 1. Authenticate Docker to Artifact Registry
gcloud auth configure-docker us-west1-docker.pkg.dev

# 2. Build & push
docker build -t us-west1-docker.pkg.dev/agentlens-489006/voicebuddy/voicebuddy:latest .
docker push us-west1-docker.pkg.dev/agentlens-489006/voicebuddy/voicebuddy:latest

# 3. Apply k8s manifests
kubectl apply -f k8s/ --namespace voicebuddy

# 4. Verify rollout
kubectl rollout status deployment/voicebuddy --namespace voicebuddy
kubectl get pods --namespace voicebuddy
```

---

## CI/CD Pipeline

**GitHub Actions** (`.github/workflows/ci.yml`) runs on every PR:
- Installs Python dependencies
- Checks that all server modules import without errors
- Does **not** deploy (Cloud Build handles that)

**Cloud Build** (`cloudbuild.yaml`) triggers on push to `main`:
1. `docker build` — builds the image tagged with `$COMMIT_SHA`
2. `docker push` — pushes to Artifact Registry
3. `kubectl apply -f k8s/` — applies manifests (idempotent)
4. `kubectl set image` — rolls out the new image

### Set up Cloud Build trigger

```bash
gcloud builds triggers create github \
  --name=voicebuddy-deploy \
  --repo-name=VoiceBuddy \
  --repo-owner=<YOUR_GITHUB_ORG> \
  --branch-pattern=^main$ \
  --build-config=cloudbuild.yaml
```

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DEEPGRAM_API_KEY` | Yes | — | Deepgram API key |
| `CLAUDE_API_KEY` | Yes | — | Anthropic API key |
| `CARTESIA_API_KEY` | Yes | — | Cartesia API key |
| `CARTESIA_VOICE_ID` | No | `a0e99841-438c-4a64-b679-ae501e7d6091` | Cartesia voice ID |
| `AUDIO_SAMPLE_RATE` | No | `16000` | PCM sample rate (Hz) |
| `AUDIO_ENCODING` | No | `pcm_s16le` | Audio encoding |
| `HOST` | No | `0.0.0.0` | Server bind host |
| `PORT` | No | `8080` | Server port |

---

## Monitoring

```bash
# Logs
kubectl logs -f deployment/voicebuddy --namespace voicebuddy

# HPA status
kubectl get hpa --namespace voicebuddy

# Pod status
kubectl get pods --namespace voicebuddy -o wide
```
