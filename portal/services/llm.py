"""LLM provider and API key management.

Provider detection scans typical install paths (no hardcoded provider list in UI).
"""

import os
import shutil
from pathlib import Path

import requests as _req

from portal.config import ENV_PATH


# ─── Generic env-key helpers ─────────────────────────────────────────

def _read_env_key(key: str) -> str:
    if not os.path.exists(ENV_PATH):
        return ""
    with open(ENV_PATH) as f:
        for line in f:
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return ""


def _write_env_key(key: str, val: str):
    lines, found = [], False
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                if line.startswith(f"{key}="):
                    lines.append(f"{key}={val}\n")
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f"{key}={val}\n")
    with open(ENV_PATH, "w") as f:
        f.writelines(lines)
    os.environ[key] = val


# ─── Gemini-specific (kept for backward compat with /api/set-api-key) ───

def is_api_key_set() -> bool:
    val = _read_env_key("GEMINI_API_KEY")
    return bool(val) and val != "your-gemini-api-key-here"


def get_masked_key() -> str:
    val = _read_env_key("GEMINI_API_KEY")
    if val and val != "your-gemini-api-key-here":
        return val[:8] + "..." + val[-4:]
    return ""


def save_api_key(key: str):
    _write_env_key("GEMINI_API_KEY", key)
    try:
        import agents.llm_client as lc
        lc.GEMINI_API_KEY = key
    except Exception:
        pass


# ─── Provider detection ─────────────────────────────────────────────

def _ollama_running() -> tuple[bool, list[str]]:
    try:
        r = _req.get("http://localhost:11434/api/tags", timeout=2)
        if r.status_code == 200:
            return True, [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:
        pass
    return False, []


def _ollama_installed_path() -> bool:
    # Binary on PATH, or models dir at typical locations
    if shutil.which("ollama"):
        return True
    home = Path.home()
    for p in (home / ".ollama", Path("/usr/local/bin/ollama")):
        if p.exists():
            return True
    return False


def _lmstudio_installed_path() -> bool:
    home = Path.home()
    candidates = [
        home / ".lmstudio",
        home / ".cache" / "lm-studio",
        Path("/Applications/LM Studio.app"),
        home / "Applications" / "LM Studio.app",
    ]
    return any(p.exists() for p in candidates)


def _lmstudio_server_running() -> tuple[bool, list[str]]:
    base = os.environ.get("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")
    try:
        r = _req.get(f"{base}/models", timeout=2)
        if r.status_code == 200:
            return True, [m.get("id", "") for m in r.json().get("data", [])]
    except Exception:
        pass
    return False, []


def _mlx_installed() -> bool:
    try:
        import mlx_lm  # noqa: F401
        return True
    except Exception:
        return False


def check_ollama() -> dict:
    """Back-compat: check Ollama + Gemma."""
    running, models = _ollama_running()
    if not running:
        return {"ok": False, "message": "Ollama not running. Start with: ollama serve"}
    has_gemma = any("gemma" in m.lower() for m in models)
    return {
        "ok": has_gemma,
        "models": models,
        "message": "Gemma ready" if has_gemma else "Gemma not found in Ollama",
    }


def detect_providers() -> dict:
    """Detect which LLM providers are available on this machine.

    Returns: { active: str, providers: [ {id, label, icon, available, status, ...} ] }
    """
    from agents.llm_client import get_provider

    # Gemini: API key present
    gemini_key = _read_env_key("GEMINI_API_KEY")
    gemini_ok = bool(gemini_key) and gemini_key != "your-gemini-api-key-here"

    # Groq: API key present
    groq_key = _read_env_key("GROQ_API_KEY")
    groq_ok = bool(groq_key)

    # Ollama: installed + running
    ollama_path = _ollama_installed_path()
    ollama_run, ollama_models = _ollama_running()
    ollama_has_gemma = any("gemma" in m.lower() for m in ollama_models)

    # LM Studio: installed + server running
    lms_path = _lmstudio_installed_path()
    lms_run, lms_models = _lmstudio_server_running()

    # MLX: pip pkg present (Apple Silicon native)
    mlx_ok = _mlx_installed()

    providers = []

    if gemini_ok:
        providers.append({
            "id": "gemini", "label": "Gemini", "icon": "☁️",
            "available": True, "kind": "cloud",
            "status": "ready", "detail": "Google Gemini API",
        })

    if groq_ok:
        providers.append({
            "id": "groq", "label": "Groq", "icon": "⚡",
            "available": True, "kind": "cloud",
            "status": "ready", "detail": "Groq cloud (fast inference)",
        })

    if ollama_path or ollama_run:
        providers.append({
            "id": "gemma", "label": "Ollama", "icon": "🦙",
            "available": ollama_run,
            "kind": "local",
            "status": "ready" if (ollama_run and ollama_has_gemma) else ("running" if ollama_run else "installed"),
            "detail": f"{len(ollama_models)} model(s)" if ollama_run else "ollama serve to start",
            "models": ollama_models,
        })

    if lms_path or lms_run:
        providers.append({
            "id": "lmstudio", "label": "LM Studio", "icon": "🖥️",
            "available": lms_run,
            "kind": "local",
            "status": "ready" if lms_run else "installed",
            "detail": f"{len(lms_models)} model(s)" if lms_run else "Start LM Studio server",
            "models": lms_models,
        })

    if mlx_ok:
        providers.append({
            "id": "mlx", "label": "MLX", "icon": "🍎",
            "available": True, "kind": "local",
            "status": "ready", "detail": "Apple Silicon native (mlx-lm)",
        })

    # Always offer Gemini/Groq tiles even without keys (so user can configure)
    if not gemini_ok:
        providers.append({
            "id": "gemini", "label": "Gemini", "icon": "☁️",
            "available": False, "kind": "cloud",
            "status": "missing-key", "detail": "Set GEMINI_API_KEY in .env",
        })
    if not groq_ok:
        providers.append({
            "id": "groq", "label": "Groq", "icon": "⚡",
            "available": False, "kind": "cloud",
            "status": "missing-key", "detail": "Set GROQ_API_KEY in .env",
        })

    return {"active": get_provider(), "providers": providers}


def set_provider(provider: str) -> dict:
    from agents.llm_client import set_provider as _set, get_provider
    try:
        _set(provider)
        return {"ok": True, "provider": get_provider()}
    except ValueError as e:
        return {"ok": False, "message": str(e)}
