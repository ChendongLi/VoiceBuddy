#!/usr/bin/env bash
# run_local.sh — Start VoiceBuddy locally, killing any existing instance first.
set -euo pipefail

PORT="${VOICEBUDDY_PORT:-8765}"
SERVER="src/server.py"
VENV_DIR=".venv"

# ── Colours ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

info()    { echo -e "${GREEN}[VoiceBuddy]${NC} $*"; }
warn()    { echo -e "${YELLOW}[VoiceBuddy]${NC} $*"; }
error()   { echo -e "${RED}[VoiceBuddy]${NC} $*" >&2; }

# ── 1. Kill any existing process on the port ───────────────────────────────────
kill_existing() {
  local pids
  pids=$(lsof -ti tcp:"$PORT" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    warn "Found existing process(es) on port $PORT (PID: $pids) — killing..."
    echo "$pids" | xargs kill -9 2>/dev/null || true
    sleep 1
    info "Killed."
  else
    info "No existing process on port $PORT."
  fi
}

# ── 2. Check .env exists ───────────────────────────────────────────────────────
check_env() {
  if [[ ! -f .env ]]; then
    error ".env not found. Copy .env.example and fill in your API keys."
    exit 1
  fi
}

# ── 3. Activate venv (create if missing) ──────────────────────────────────────
activate_venv() {
  if [[ ! -d "$VENV_DIR" ]]; then
    warn "No .venv found — creating..."
    python3 -m venv "$VENV_DIR"
    info ".venv created."
  fi
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
}

# ── 4. Install/sync dependencies ──────────────────────────────────────────────
install_deps() {
  if [[ -f pyproject.toml ]]; then
    info "Installing dependencies..."
    pip install -e ".[dev]" --quiet 2>&1 | tail -3 || \
    pip install -e . --quiet 2>&1 | tail -3
  elif [[ -f requirements.txt ]]; then
    pip install -r requirements.txt --quiet 2>&1 | tail -3
  fi
}

# ── 5. Start Postgres via docker-compose (only if not using SQLite) ────────────
start_postgres() {
  local db_url="${DATABASE_URL:-}"
  if [[ "$db_url" == sqlite* ]]; then
    info "Using SQLite — no Docker needed. (${db_url})"
    return
  fi
  if [[ -f docker-compose.yml ]] && command -v docker &>/dev/null; then
    if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q "postgres"; then
      info "Starting Postgres via docker-compose..."
      docker compose up -d postgres 2>&1 | tail -3
      sleep 2
    else
      info "Postgres already running."
    fi
  else
    warn "No DATABASE_URL set and no docker-compose.yml found."
    warn "Tip: set DATABASE_URL=sqlite+aiosqlite:///./voicebuddy_dev.db in .env for zero-setup local dev."
  fi
}

# ── 6. Run DB migrations (if alembic is set up) ───────────────────────────────
run_migrations() {
  if [[ -f alembic.ini ]]; then
    info "Running DB migrations..."
    alembic upgrade head
  fi
}

# ── Main ───────────────────────────────────────────────────────────────────────
cd "$(dirname "$0")"

# Load .env so DATABASE_URL etc. are available to this script
if [[ -f .env ]]; then
  set -o allexport
  # shellcheck disable=SC1091
  source .env
  set +o allexport
fi

info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
info "  VoiceBuddy — Local Dev Runner"
info "  Port: $PORT"
info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

kill_existing
check_env
activate_venv
install_deps
start_postgres
run_migrations

HTTP_PORT="${HTTP_PORT:-8766}"
info "Starting server..."
info "Browser UI:      http://localhost:$PORT"
info "WebSocket:       ws://localhost:$PORT/ws"
info "Twilio webhook:  POST http://localhost:$HTTP_PORT/incoming-call"
info "(websockets only accepts GET — Twilio HTTP runs on a separate port)"
info "Press Ctrl+C to stop."
echo ""

exec python3 "$SERVER"
