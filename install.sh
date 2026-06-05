#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────────
# Job Hunter — Interactive Setup Script
# Sets up Python deps, database, and .env configuration
# ─────────────────────────────────────────────────────────────────

BOLD='\033[1m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m' # No color

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
touch "$ENV_FILE"

# Helper: set/update a key in .env (must be defined before use)
_env_set() {
    local KEY="$1"
    local VALUE="$2"
    if grep -q "^${KEY}=" "$ENV_FILE" 2>/dev/null; then
        if [[ "$(uname)" == "Darwin" ]]; then
            sed -i '' "s|^${KEY}=.*|${KEY}=${VALUE}|" "$ENV_FILE"
        else
            sed -i "s|^${KEY}=.*|${KEY}=${VALUE}|" "$ENV_FILE"
        fi
    else
        echo "${KEY}=${VALUE}" >> "$ENV_FILE"
    fi
}

# Helper: configure Supabase interactively
_configure_supabase() {
    read -rp "  Supabase Project URL (https://xxx.supabase.co): " SB_URL
    read -rp "  Supabase Anon Key: " SB_KEY
    if [[ -z "$SB_URL" || -z "$SB_KEY" ]]; then
        err "URL and key are required. Run install.sh again to retry."
        exit 1
    fi
    _env_set "SUPABASE_URL" "$SB_URL"
    _env_set "SUPABASE_ANON_KEY" "$SB_KEY"
    _env_set "DB_BACKEND" "supabase"
    ok "Supabase configured"
}

banner() {
    echo ""
    echo -e "${CYAN}${BOLD}╔══════════════════════════════════════╗${NC}"
    echo -e "${CYAN}${BOLD}║   🔍 Job Hunter — Setup Wizard      ║${NC}"
    echo -e "${CYAN}${BOLD}╚══════════════════════════════════════╝${NC}"
    echo ""
}

step() { echo -e "\n${GREEN}${BOLD}[$1/$TOTAL_STEPS]${NC} ${BOLD}$2${NC}"; }
info() { echo -e "  ${CYAN}→${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
err()  { echo -e "  ${RED}✗${NC} $1"; }
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }

TOTAL_STEPS=5

banner

# ─── Step 1: Python dependencies ─────────────────────────────────

step 1 "Installing Python dependencies"

if ! command -v python3 &>/dev/null; then
    err "Python 3 not found. Please install Python 3.9+ first."
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
info "Python $PYTHON_VERSION detected"

pip3 install -r "$PROJECT_DIR/requirements.txt" --quiet 2>/dev/null
ok "Dependencies installed"

# ── JobSpy aggregator ──
# Indeed/LinkedIn/Glassdoor/Google/ZipRecruiter/Naukri/Bayt/BDJobs in one call.
# Requires Python 3.10+. We try the system python3 first; if it's 3.9 we look
# for python3.10+ on PATH and install JobSpy against that interpreter so the
# adapter picks it up when the project runs under it.

_install_jobspy_with() {
    local PY="$1"
    info "Installing python-jobspy against $($PY --version 2>&1)..."
    if "$PY" -m pip install --quiet --user python-jobspy 2>/dev/null; then
        ok "JobSpy installed"
        return 0
    fi
    if "$PY" -m pip install --quiet --user --break-system-packages python-jobspy 2>/dev/null; then
        ok "JobSpy installed (via --break-system-packages)"
        return 0
    fi
    warn "Could not install python-jobspy with $PY"
    return 1
}

_py_version_ge_310() {
    "$1" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null
}

JOBSPY_PY=""
for PY in python3 python3.13 python3.12 python3.11 python3.10; do
    if command -v "$PY" >/dev/null 2>&1 && _py_version_ge_310 "$PY"; then
        JOBSPY_PY="$PY"; break
    fi
done

if [[ -n "$JOBSPY_PY" ]]; then
    # Ensure the same interpreter that run.sh will pick has every dep, not
    # just JobSpy. Otherwise Flask/requests/etc. fail to import when run.sh
    # switches from system python3 (3.9) to python3.11+ for JobSpy.
    if ! "$JOBSPY_PY" -c "import flask" 2>/dev/null; then
        info "Installing project requirements against $($JOBSPY_PY --version 2>&1)..."
        if ! "$JOBSPY_PY" -m pip install --quiet --user -r "$PROJECT_DIR/requirements.txt" 2>/dev/null; then
            "$JOBSPY_PY" -m pip install --quiet --user --break-system-packages -r "$PROJECT_DIR/requirements.txt" 2>/dev/null \
                || warn "Could not install requirements against $JOBSPY_PY (manual: $JOBSPY_PY -m pip install -r requirements.txt)"
        fi
        "$JOBSPY_PY" -c "import flask" 2>/dev/null && ok "Project deps installed for $JOBSPY_PY"
    else
        ok "Project deps already present for $($JOBSPY_PY --version 2>&1)"
    fi

    if "$JOBSPY_PY" -c "import jobspy" 2>/dev/null; then
        ok "JobSpy already installed for $($JOBSPY_PY --version 2>&1)"
    else
        _install_jobspy_with "$JOBSPY_PY" || warn "Manual: pip3 install python-jobspy"
    fi
else
    warn "No Python 3.10+ found; JobSpy aggregator will be skipped."
    info "Install Python 3.10+ via: brew install python@3.12   (or pyenv/uv)"
    info "Then re-run install.sh."
fi

# ─── Step 2: Database configuration ──────────────────────────────

step 2 "Database configuration"

echo ""
echo -e "  How would you like to store job data?"
echo ""
echo -e "    ${BOLD}1)${NC} Supabase (cloud PostgreSQL — recommended, free tier)"
echo -e "    ${BOLD}2)${NC} Local PostgreSQL (requires pg installed)"
echo -e "    ${BOLD}3)${NC} Docker PostgreSQL (requires Docker)"
echo -e "    ${BOLD}4)${NC} Skip (use local JSON files only — no dedup)"
echo ""
read -rp "  Choose [1-4]: " DB_CHOICE

case "$DB_CHOICE" in
    1)
        info "Supabase selected"
        echo ""

        # Check if already configured
        if grep -q "SUPABASE_URL=" "$ENV_FILE" 2>/dev/null; then
            EXISTING_URL=$(grep "SUPABASE_URL=" "$ENV_FILE" | cut -d'=' -f2)
            echo -e "  Current: ${CYAN}$EXISTING_URL${NC}"
            read -rp "  Keep existing config? [Y/n]: " KEEP
            if [[ "${KEEP:-Y}" =~ ^[Yy]$ ]]; then
                ok "Keeping existing Supabase config"
            else
                _configure_supabase
            fi
        else
            _configure_supabase
        fi
        ;;

    2)
        info "Local PostgreSQL selected"

        if ! command -v psql &>/dev/null; then
            warn "psql not found. Installing PostgreSQL..."
            if command -v brew &>/dev/null; then
                brew install postgresql@16
                brew services start postgresql@16
                ok "PostgreSQL 16 installed and started"
            elif command -v apt-get &>/dev/null; then
                sudo apt-get update -qq && sudo apt-get install -y -qq postgresql postgresql-contrib
                sudo systemctl start postgresql
                ok "PostgreSQL installed and started"
            else
                err "Cannot auto-install PostgreSQL. Please install it manually."
                exit 1
            fi
        else
            ok "PostgreSQL found: $(psql --version | head -1)"
        fi

        # Create database
        DB_NAME="job_hunter"
        DB_USER="${USER:-postgres}"
        info "Creating database '$DB_NAME'..."

        if psql -lqt 2>/dev/null | cut -d \| -f 1 | grep -qw "$DB_NAME"; then
            ok "Database '$DB_NAME' already exists"
        else
            createdb "$DB_NAME" 2>/dev/null || sudo -u postgres createdb "$DB_NAME" 2>/dev/null
            ok "Database '$DB_NAME' created"
        fi

        # Apply schema
        if [[ -f "$PROJECT_DIR/schema.sql" ]]; then
            psql -d "$DB_NAME" -f "$PROJECT_DIR/schema.sql" -q 2>/dev/null || true
            ok "Schema applied"
        fi

        DB_URL="postgresql://${DB_USER}@localhost:5432/${DB_NAME}"
        _env_set "DATABASE_URL" "$DB_URL"
        _env_set "DB_BACKEND" "local_pg"
        ok "Local PostgreSQL configured: $DB_URL"
        ;;

    3)
        info "Docker PostgreSQL selected"

        if ! command -v docker &>/dev/null; then
            err "Docker not found. Please install Docker Desktop first."
            exit 1
        fi

        CONTAINER_NAME="job-hunter-pg"
        PG_PASSWORD="jobhunter123"

        if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
            if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
                ok "Container '$CONTAINER_NAME' already running"
            else
                docker start "$CONTAINER_NAME"
                ok "Container '$CONTAINER_NAME' started"
            fi
        else
            docker run -d \
                --name "$CONTAINER_NAME" \
                -e POSTGRES_PASSWORD="$PG_PASSWORD" \
                -e POSTGRES_DB=job_hunter \
                -p 5432:5432 \
                postgres:16-alpine
            ok "PostgreSQL container '$CONTAINER_NAME' started"
            info "Waiting for PostgreSQL to be ready..."
            sleep 3
        fi

        DB_URL="postgresql://postgres:${PG_PASSWORD}@localhost:5432/job_hunter"
        _env_set "DATABASE_URL" "$DB_URL"
        _env_set "DB_BACKEND" "docker_pg"

        # Apply schema
        if [[ -f "$PROJECT_DIR/schema.sql" ]]; then
            PGPASSWORD="$PG_PASSWORD" psql -h localhost -U postgres -d job_hunter -f "$PROJECT_DIR/schema.sql" -q 2>/dev/null || true
            ok "Schema applied"
        fi

        ok "Docker PostgreSQL configured"
        ;;

    4)
        info "Skipping database — using local JSON files"
        _env_set "DB_BACKEND" "json"
        warn "No dedup across runs. Jobs will be re-analyzed each time."
        ;;

    *)
        err "Invalid choice. Run install.sh again."
        exit 1
        ;;
esac

# ─── Step 3: LLM providers ──────────────────────────────────────
# Multi-select: detect local installs by typical paths, ask about cloud keys.

step 3 "LLM Provider configuration"

# Helper: open a URL in the default browser (best effort, cross-platform)
_open_url() {
    local URL="$1"
    if [[ "$(uname)" == "Darwin" ]]; then open "$URL" 2>/dev/null || true
    elif command -v xdg-open &>/dev/null; then xdg-open "$URL" 2>/dev/null || true
    elif command -v start &>/dev/null; then start "$URL" 2>/dev/null || true
    fi
}

# ── Detect local LMs via typical install paths (no hardcoding) ──
LMSTUDIO_PATHS=("$HOME/.lmstudio" "$HOME/.cache/lm-studio" "/Applications/LM Studio.app" "$HOME/Applications/LM Studio.app")
OLLAMA_PATHS=("$HOME/.ollama")

LMSTUDIO_FOUND=0
for p in "${LMSTUDIO_PATHS[@]}"; do
    if [[ -e "$p" ]]; then LMSTUDIO_FOUND=1; break; fi
done

OLLAMA_FOUND=0
if command -v ollama &>/dev/null; then OLLAMA_FOUND=1; fi
for p in "${OLLAMA_PATHS[@]}"; do
    if [[ -e "$p" ]]; then OLLAMA_FOUND=1; break; fi
done

MLX_FOUND=0
if python3 -c "import mlx_lm" 2>/dev/null; then MLX_FOUND=1; fi

echo ""
info "Local LM scan:"
[[ $LMSTUDIO_FOUND -eq 1 ]] && ok "LM Studio detected" || warn "LM Studio not found (install from https://lmstudio.ai)"
[[ $OLLAMA_FOUND -eq 1 ]] && ok "Ollama detected" || warn "Ollama not found (install from https://ollama.ai)"
[[ $MLX_FOUND -eq 1 ]] && ok "mlx-lm Python package installed" || warn "mlx-lm not installed (pip install mlx-lm — Apple Silicon only)"

# ── Gemini ──
echo ""
read -rp "  Include Google Gemini (cloud)? [y/N]: " WANT_GEMINI
if [[ "${WANT_GEMINI:-N}" =~ ^[Yy]$ ]]; then
    EXISTING=$(grep "^GEMINI_API_KEY=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2)
    if [[ -n "$EXISTING" && "$EXISTING" != "your-gemini-api-key-here" ]]; then
        echo -e "  Existing key: ${CYAN}${EXISTING:0:8}...${NC}"
        read -rp "  Keep existing? [Y/n]: " KEEP
        if [[ ! "${KEEP:-Y}" =~ ^[Yy]$ ]]; then
            info "Opening https://aistudio.google.com/apikey ..."
            _open_url "https://aistudio.google.com/apikey"
            read -rp "  Paste Gemini API Key: " GEMINI_KEY
            [[ -n "$GEMINI_KEY" ]] && _env_set "GEMINI_API_KEY" "$GEMINI_KEY" && ok "Gemini key saved"
        fi
    else
        info "Opening https://aistudio.google.com/apikey ..."
        _open_url "https://aistudio.google.com/apikey"
        read -rp "  Paste Gemini API Key: " GEMINI_KEY
        [[ -n "$GEMINI_KEY" ]] && _env_set "GEMINI_API_KEY" "$GEMINI_KEY" && ok "Gemini key saved"
    fi
fi

# ── Groq ──
echo ""
read -rp "  Include Groq (cloud, very fast)? [y/N]: " WANT_GROQ
if [[ "${WANT_GROQ:-N}" =~ ^[Yy]$ ]]; then
    EXISTING=$(grep "^GROQ_API_KEY=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2)
    if [[ -n "$EXISTING" ]]; then
        echo -e "  Existing key: ${CYAN}${EXISTING:0:8}...${NC}"
        read -rp "  Keep existing? [Y/n]: " KEEP
        if [[ ! "${KEEP:-Y}" =~ ^[Yy]$ ]]; then
            info "Opening https://console.groq.com/keys ..."
            _open_url "https://console.groq.com/keys"
            read -rp "  Paste Groq API Key: " GROQ_KEY
            [[ -n "$GROQ_KEY" ]] && _env_set "GROQ_API_KEY" "$GROQ_KEY" && ok "Groq key saved"
        fi
    else
        info "Opening https://console.groq.com/keys ..."
        _open_url "https://console.groq.com/keys"
        read -rp "  Paste Groq API Key: " GROQ_KEY
        [[ -n "$GROQ_KEY" ]] && _env_set "GROQ_API_KEY" "$GROQ_KEY" && ok "Groq key saved"
    fi
fi

# ── Ollama model setup ──
if [[ $OLLAMA_FOUND -eq 1 ]]; then
    echo ""
    read -rp "  Pull a Gemma model into Ollama now? [y/N]: " PULL_GEMMA
    if [[ "${PULL_GEMMA:-N}" =~ ^[Yy]$ ]]; then
        if curl -s http://localhost:11434/api/tags 2>/dev/null | grep -qi "gemma"; then
            ok "Gemma already present in Ollama"
        else
            info "Pulling gemma2 (may take a few minutes)..."
            ollama pull gemma2 2>/dev/null || warn "Pull failed. Run: ollama pull gemma2"
        fi
    fi
fi

# Default active provider: prefer MLX > LM Studio > Ollama > Groq > Gemini
DEFAULT_PROVIDER=""
[[ $MLX_FOUND -eq 1 ]] && DEFAULT_PROVIDER="mlx"
[[ -z "$DEFAULT_PROVIDER" && $LMSTUDIO_FOUND -eq 1 ]] && DEFAULT_PROVIDER="lmstudio"
[[ -z "$DEFAULT_PROVIDER" && $OLLAMA_FOUND -eq 1 ]] && DEFAULT_PROVIDER="gemma"
[[ -z "$DEFAULT_PROVIDER" ]] && grep -q "^GROQ_API_KEY=" "$ENV_FILE" 2>/dev/null && DEFAULT_PROVIDER="groq"
[[ -z "$DEFAULT_PROVIDER" ]] && grep -q "^GEMINI_API_KEY=" "$ENV_FILE" 2>/dev/null && DEFAULT_PROVIDER="gemini"
if [[ -n "$DEFAULT_PROVIDER" ]]; then
    _env_set "LLM_PROVIDER" "$DEFAULT_PROVIDER"
    ok "Default provider: $DEFAULT_PROVIDER (change anytime in the UI)"
else
    warn "No LLM provider configured. Install MLX/Ollama/LM Studio or set Gemini/Groq key."
fi

# ─── Step 4: Verify configuration ────────────────────────────────

step 4 "Verifying configuration"

if [[ -f "$ENV_FILE" ]]; then
    ok ".env file exists at $ENV_FILE"
    # Count configured keys
    KEYS=$(grep -c "=" "$ENV_FILE" 2>/dev/null || echo 0)
    info "$KEYS configuration keys set"
else
    warn "No .env file found — creating one"
    touch "$ENV_FILE"
fi

# Verify Python imports
python3 -c "import flask; import requests; import bs4" 2>/dev/null && ok "Core Python packages OK" || err "Missing packages — run: pip3 install -r requirements.txt"

# ─── Step 5: Ready ───────────────────────────────────────────────

step 5 "Setup complete!"

echo ""
echo -e "${GREEN}${BOLD}  ┌─────────────────────────────────────────┐${NC}"
echo -e "${GREEN}${BOLD}  │  🚀 Job Hunter is ready to launch!      │${NC}"
echo -e "${GREEN}${BOLD}  └─────────────────────────────────────────┘${NC}"
echo ""
echo -e "  Start the app:     ${BOLD}./run.sh${NC}  (or python3 portal/app.py)"
echo -e "  Open in browser:   ${BOLD}http://localhost:5050${NC}"
echo ""
echo -e "  Optional:"
echo -e "    Start Ollama:    ${BOLD}ollama serve${NC}"
echo -e "    View logs:       ${BOLD}python3 portal/app.py 2>&1 | tee /tmp/jobhunter.log${NC}"
echo ""

