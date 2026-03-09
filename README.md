# VoiceBuddy

**AI-powered voice booking agent for small service businesses.**

Small service businesses — HVAC, plumbing, dental, physio, law firms — miss inbound calls every day. A missed call is a missed customer. Hiring a receptionist is expensive and doesn't scale. VoiceBuddy answers every call, understands what the caller needs, and books an appointment directly into Google Calendar — all in under a second of latency, with a voice indistinguishable from a human receptionist.

> **Live demo:** Call **+1-318-568-8982** (CoolBreeze HVAC)

---

## What It Does

- **Answers inbound calls 24/7** — never a busy line, never voicemail
- **Books appointments end-to-end** — checks calendar availability, confirms a slot, creates a Google Calendar event
- **Handles interruptions naturally** — barge-in detection cancels bot speech mid-sentence
- **Identifies returning callers** — looks up customers by phone number
- **Sends post-call SMS** — appointment confirmation via Twilio
- **Transfers to human** — on request, or outside business hours
- **Multi-tenant** — one deployment serves multiple businesses, routed by phone number

---

## Architecture

### Voice Pipeline (per call)

```
Twilio PSTN
    │  (mulaw audio, WebSocket)
    ▼
aiohttp HTTP server (port 8766)
    ├── POST /incoming-call  → TwiML + <Stream> response
    └── WS   /twilio-media   → MediaStream handler
                │
                ▼
        VoiceBuddy Server (server.py)
                │
    ┌───────────┼───────────────────────────────┐
    │           │                               │
    ▼           ▼                               ▼
Silero VAD  Deepgram Flux v2 (STT)      State Machine
(barge-in)  Real-time transcript        IDLE → USER_SPEAKING
                │                       → PROCESSING
                ▼                       → FILLER_RESPONSE
        LLM Orchestrator                → BOT_SPEAKING
        ├── Claude Haiku (filler)       → IDLE
        │   Fast ack, <600ms
        └── Claude Sonnet (full)
            + Booking Tools
                │
                ▼
        Sentence Splitter
        (stream tokens → sentences)
                │
                ▼
        Cartesia Sonic 3 (TTS)
        Raw PCM → mulaw encode
                │
                ▼
        Twilio MediaStream (audio back to caller)
```

### Booking Pipeline

```
Caller: "I need a tune-up tomorrow at 10 AM"
    │
    ▼
Claude Sonnet detects booking intent
    │
    ├── check_availability(date, service)
    │       → Google Calendar freebusy API
    │       → returns open slots in tenant timezone
    │
    └── book_appointment(name, phone, date, time, service)
            → Creates Google Calendar event
            → Writes Appointment record to PostgreSQL
            → Returns confirmation to TTS
```

### Multi-Tenant Routing

```
Twilio "To" number → tenant lookup
    +13185688982  → coolbreeze_hvac (HVAC, America/Vancouver)
    +1xxxxxxxxxx  → dental_clinic   (Dental, America/Toronto)
    ...

Each tenant has:
  - tenants/{id}.yaml     (config: services, hours, voice, prompt)
  - tokens/{id}.json      (Google OAuth credentials)
```

---

## Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| Phone | Twilio | Inbound PSTN calls, MediaStream, SMS |
| STT | Deepgram Flux v2 | Real-time speech-to-text via WebSocket |
| LLM (filler) | Claude Haiku 3.5 | Fast acknowledgment (<600ms, 5–15 words) |
| LLM (full) | Claude Sonnet 4.5 | Full response + booking tool use |
| TTS | Cartesia Sonic 3 | Voice synthesis, raw PCM streaming |
| VAD | Silero VAD v5 (ONNX) | Server-side barge-in detection |
| Calendar | Google Calendar API | Availability check + event creation |
| Database | PostgreSQL (Neon) / SQLite (local) | Customers, calls, appointments |
| HTTP server | aiohttp | Twilio webhooks + MediaStream WebSocket |
| Browser WS | websockets 16.0 | Browser demo UI |
| Migrations | Alembic | Schema versioning |
| Deploy | GKE (Google Kubernetes Engine) | Production hosting |

### Audio Format

16 kHz, 16-bit signed PCM, mono. Twilio sends 8 kHz µ-law (mulaw); the server decodes to PCM before VAD/STT and re-encodes to mulaw for playback.

---

## Latency Profile

| Stage | Target |
|---|---|
| End-of-turn detection | <350ms |
| Filler response (Haiku) | <600ms |
| Full response first word | <1.2s |
| End-to-end (caller hears audio) | <1.5s p75 |

Filler words ("One moment…", "Sure, let me check…") bridge the gap while Claude Sonnet processes the full request.

---

## State Machine

```
IDLE ──────────────► USER_SPEAKING
                           │
                           ▼
                      PROCESSING ──► FILLER_RESPONSE
                           │               │
                           └───────────────► BOT_SPEAKING
                                               │
                           ◄───────────────────┘ (turn end)
                      IDLE

BARGE_IN:  BOT_SPEAKING → cancel pipeline → USER_SPEAKING
```

All transitions are pre-defined and validated. Invalid transitions are logged and silently dropped — never crash the session.

---

## Project Structure

```
src/
├── server.py              # Main server: event queue, pipeline, call orchestration
├── http_server.py         # aiohttp: /incoming-call webhook + /twilio-media WebSocket
├── state_machine.py       # Event-driven state machine (IDLE→SPEAKING→BOT_SPEAKING…)
├── deepgram_client.py     # Deepgram Flux v2 STT wrapper
├── llm_orchestrator.py    # Dual-layer LLM: Haiku filler + Sonnet full + tool use
├── booking_tools.py       # Claude tool definitions (check_availability, book_appointment)
├── booking_service.py     # Booking orchestration: calendar + DB write
├── calendar_service.py    # Google Calendar OAuth + freebusy + event CRUD
├── post_call_service.py   # Async post-call: transcript, AI summary, SMS
├── customer_service.py    # Caller ID lookup + new/returning customer flow
├── tenant_config.py       # TenantConfig dataclass + YAML loader + registry
├── twilio_handler.py      # TwiML builder + Twilio signature validation
├── human_handoff.py       # Business hours + Twilio <Dial> transfer
├── database.py            # SQLAlchemy async engine + session factory
├── models.py              # ORM models: Customer, Call, Appointment
├── prompts.py             # System prompts (cached across turns)
├── sentence_splitter.py   # Streaming sentence boundary detection
├── tts_client.py          # Cartesia Sonic 3 TTS wrapper
├── vad_detector.py        # Silero VAD v5 ONNX (barge-in)
├── latency_logger.py      # Per-session JSONL latency logging
├── voice_config.py        # Audio format constants (16kHz PCM)
└── static/
    └── index.html         # Browser demo client (AudioWorklet)

tenants/
└── coolbreeze_hvac.yaml   # Tenant config (services, hours, voice, prompt, timezone)

tokens/
└── {tenant_id}.json       # Per-tenant Google OAuth tokens (gitignored)

migrations/                # Alembic migrations
k8s/                       # Kubernetes manifests (GKE deployment)
scripts/
├── authorize_tenant.py    # Google OAuth flow for new tenants
└── test_booking_integration.py  # Standalone booking integration test

test/
├── test_integration_e2e.py      # Production E2E: health, DB, calendar, LLM booking
├── test_booking_service.py      # Booking engine unit tests
├── test_calendar_service.py     # Calendar service unit tests
├── test_state_machine_sim.py    # State transition tests
├── test_sentence_splitter.py    # Sentence splitting tests
├── test_twilio_webhook.py       # TwiML + signature validation tests
└── ...                          # 264 unit tests total
```

---

## Local Development

### Prerequisites

- Python 3.11+
- [ngrok](https://ngrok.com) (free account, one tunnel)
- Twilio account + phone number
- Anthropic, Deepgram, Cartesia API keys
- Google Cloud project (Calendar API enabled)

### Install

```bash
git clone https://github.com/ChendongLi/VoiceBuddy
cd VoiceBuddy
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-server.txt
pip install -r requirements-dev.txt  # test dependencies
```

### Configure

```bash
cp .env.example .env
```

Edit `.env`:

```env
DEEPGRAM_API_KEY=...
CLAUDE_API_KEY=...
CARTESIA_API_KEY=...
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_WEBHOOK_HOST=your-tunnel.ngrok-free.dev   # ngrok subdomain
DATABASE_URL=sqlite+aiosqlite:///./voicebuddy_dev.db
SKIP_TWILIO_VALIDATION=true                       # LOCAL ONLY — never in prod
```

### Add a tenant

1. Copy the example config:
   ```bash
   cp tenants/coolbreeze_hvac.yaml tenants/my_business.yaml
   ```

2. Edit `tenants/my_business.yaml` with your business details, services, hours, and timezone.

3. Authorize Google Calendar:
   ```bash
   python scripts/authorize_tenant.py --tenant-id my_business
   ```
   This opens a browser OAuth flow and saves `tokens/my_business.json`.

4. Point a Twilio number at your ngrok URL:
   ```
   https://your-tunnel.ngrok-free.dev/incoming-call
   ```

### Run locally

```bash
./run_local.sh
# or:
PORT=8765 HTTP_PORT=8766 .venv/bin/python src/server.py
```

Start ngrok (single tunnel for both HTTP and WebSocket):

```bash
ngrok http 8766
```

### Run tests

```bash
# Unit tests (264, fast, no network)
PYTHONPATH=src pytest test/ \
  --ignore=test/test_cartesia.py \
  --ignore=test/test_claude_api.py

# Integration tests (hit real APIs + production DB)
PYTHONPATH=src pytest test/test_integration_e2e.py -m integration -v
```

---

## Production Deployment (GKE)

### Infrastructure

- **Cluster:** GKE `voicebuddy-standard`, `us-west1-b`, `e2-medium`
- **Domain:** `voicebuddy.agentlens.net` (GCP managed SSL cert)
- **Database:** Neon PostgreSQL (free tier, serverless)
- **Secrets:** Kubernetes `voicebuddy-secrets` + `voicebuddy-calendar-sa`

### Deploy

```bash
SHA=$(git rev-parse HEAD)
gcloud builds submit --config cloudbuild.yaml . \
  --project=agentlens-489006 \
  --substitutions=COMMIT_SHA=$SHA
```

Cloud Build:
1. Builds Docker image → pushes to Artifact Registry
2. Applies k8s manifests (`k8s/`)
3. Rolls out new image to the deployment

### Required k8s secrets

`voicebuddy-secrets` must contain:

| Key | Description |
|---|---|
| `DEEPGRAM_API_KEY` | Deepgram API key |
| `CLAUDE_API_KEY` | Anthropic API key |
| `CARTESIA_API_KEY` | Cartesia API key |
| `CARTESIA_VOICE_ID` | Cartesia voice UUID (optional) |
| `TWILIO_ACCOUNT_SID` | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | Twilio auth token |
| `DATABASE_URL` | PostgreSQL connection string |

`voicebuddy-calendar-sa` must contain:

| Key | Description |
|---|---|
| `service_account.json` | Google service account JSON key |

### Database migrations

```bash
DATABASE_URL="postgresql+asyncpg://..." alembic upgrade head
```

### Monitor

```bash
# Live logs
kubectl logs -n voicebuddy -l app=voicebuddy -f

# Pod status
kubectl get pods -n voicebuddy

# Health check
curl https://voicebuddy.agentlens.net/health
```

---

## Tenant Configuration

Each tenant is a YAML file in `tenants/`:

```yaml
tenant_id: coolbreeze_hvac
phone_number: "+13185688982"
business_name: CoolBreeze HVAC
timezone: America/Vancouver

system_prompt: |
  You are a friendly, professional AI receptionist for CoolBreeze HVAC...

services:
  - name: maintenance
    duration_min: 120
    upsell: "Would you like to sign up for our annual maintenance plan?"
  - name: repair
    duration_min: 90

providers:
  - name: CoolBreeze Dispatch
    calendar_id: lichendong@gmail.com

buffer_min: 15
cancellation_policy: "Please give us 24 hours notice to cancel or reschedule."

business_hours:
  mon_fri: "8am-6pm"
  saturday: "9am-2pm"
  sunday: closed

fallback_number: "+16045550000"   # human handoff

voice_id: f786b574-daa5-4673-aa0c-cbe3e8534c02   # Cartesia voice

filler_phrases:
  - "One moment while I check that for you."
  - "Sure, let me look at our availability."
  - "Give me just a second."
```

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| Single port (8766) for HTTP + WebSocket | ngrok free tier allows only one tunnel |
| aiohttp for Twilio webhooks | Existing `websockets` server kept for browser UI; aiohttp wraps Twilio MediaStream in `_WsAdapter` |
| Non-streaming for tool-use responses | Claude streaming API is incompatible with `tool_use` blocks; text queued to TTS directly when no tokens stream |
| SQLite locally / PostgreSQL in prod | Zero-dependency local dev; `aiosqlite` + `asyncpg` swap via `DATABASE_URL` |
| Neon over Cloud SQL | Free tier, no sidecar proxy needed, standard connection string |
| Per-tenant Google OAuth tokens | Each business authorizes its own calendar; tokens stored in `tokens/{tenant_id}.json` |
| `SKIP_TWILIO_VALIDATION=true` local only | Twilio signs against the public ngrok URL; local validation always fails |
| Deployment strategy `Recreate` | Single `e2-medium` node can't fit two pods simultaneously |

---

## Implemented Features (MVP)

| Ticket | Feature | Status |
|---|---|---|
| AGE-11 | Twilio inbound call + MediaStream WebSocket | ✅ |
| AGE-12 | YAML tenant config + phone number routing | ✅ |
| AGE-13 | Twilio µ-law ↔ PCM audio bridge | ✅ |
| AGE-14 | PostgreSQL schema + SQLAlchemy + Alembic | ✅ |
| AGE-15 | Customer profiles — caller ID + new/returning flow | ✅ |
| AGE-16 | Google Calendar OAuth + availability + booking tools | ✅ |
| AGE-17 | Booking engine — check_availability + book_appointment | ✅ |
| AGE-18 | In-call UX — filler audio, confirmations, upsells | ✅ |
| AGE-19 | Human handoff — Twilio transfer + business hours | ✅ |
| AGE-20 | Post-call — transcript + AI summary + SMS | ✅ |
| AGE-21 | Resilience — circuit breaker + call drop guard | ✅ |
| AGE-22 | Timezone — bookings in tenant local time | ✅ |

---

## License

MIT
