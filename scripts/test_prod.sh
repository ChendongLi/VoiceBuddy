#!/usr/bin/env bash
# test_prod.sh — Run all integration tests against production stack
#
# Usage:
#   ./scripts/test_prod.sh           # full suite (health + DB + calendar + LLM booking)
#   ./scripts/test_prod.sh -k db     # DB tests only
#   ./scripts/test_prod.sh -k cal    # calendar tests only
#
# Pulls DATABASE_URL from k8s secret automatically.
# Requires: kubectl context pointing at voicebuddy cluster, .venv active or venv found.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# Activate venv if not already active
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  if [[ -f ".venv/bin/activate" ]]; then
    source .venv/bin/activate
  else
    echo "❌ No .venv found. Run: python3 -m venv .venv && pip install -r requirements-server.txt -r requirements-dev.txt"
    exit 1
  fi
fi

# Pull prod DB URL from k8s
echo "🔑 Fetching DATABASE_URL from k8s secret..."
export DATABASE_URL
DATABASE_URL="$(kubectl get secret voicebuddy-secrets -n voicebuddy -o jsonpath='{.data.DATABASE_URL}' | base64 -d)"

echo "🚀 Running integration tests against https://voicebuddy.agentlens.net ..."
echo ""

PYTHONPATH=src pytest test/test_integration_e2e.py -m integration -v "$@"
