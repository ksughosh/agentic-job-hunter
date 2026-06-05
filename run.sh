#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# Job Hunter — run script
# Starts the Flask backend (which also serves the frontend templates).
# Optionally auto-starts local LLM backends (LM Studio CLI / Ollama) if installed.
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
info() { echo -e "  ${CYAN}→${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
err()  { echo -e "  ${RED}✗${NC} $1"; }

PORT="${PORT:-5050}"
HOST="${HOST:-127.0.0.1}"

# Read LLM_PROVIDER from .env (best effort)
ACTIVE_PROVIDER=""
if [[ -f "$PROJECT_DIR/.env" ]]; then
    ACTIVE_PROVIDER=$(grep "^LLM_PROVIDER=" "$PROJECT_DIR/.env" 2>/dev/null | cut -d'=' -f2 || true)
fi

echo ""
echo -e "${CYAN}${BOLD}── Job Hunter ──${NC}"
[[ -n "$ACTIVE_PROVIDER" ]] && info "LLM provider: $ACTIVE_PROVIDER"

# ── Optional: start Ollama if user picked it and binary exists ──
if [[ "$ACTIVE_PROVIDER" == "gemma" ]] && command -v ollama &>/dev/null; then
    if ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
        info "Starting Ollama server in background..."
        nohup ollama serve >/tmp/jobhunter-ollama.log 2>&1 &
        sleep 2
        ok "Ollama started (PID $!)"
    else
        ok "Ollama already running"
    fi
fi

# ── LM Studio: just verify it's up, can't auto-launch GUI ──
if [[ "$ACTIVE_PROVIDER" == "lmstudio" || "$ACTIVE_PROVIDER" == "mlx" ]]; then
    if curl -s http://localhost:1234/v1/models >/dev/null 2>&1; then
        ok "LM Studio server reachable on :1234"
    else
        warn "LM Studio server not reachable on :1234 — open LM Studio and start the server"
    fi
fi

# ── Pick Python ──
PY=python3
if [[ -d "$PROJECT_DIR/.venv" ]]; then
    # shellcheck disable=SC1091
    source "$PROJECT_DIR/.venv/bin/activate"
    PY=python
    ok "Using virtualenv .venv"
fi

# ── Start Flask backend (also serves frontend) ──
export PORT HOST
info "Starting Flask backend on http://${HOST}:${PORT}"
exec "$PY" portal/app.py
