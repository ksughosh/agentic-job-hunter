"""
Job Hunter — AI-Powered Multi-Agent Job Search Portal

Clean architecture:
    portal/
    ├── app.py              ← You are here (factory + entry point)
    ├── config.py           ← App settings, paths
    ├── models/user.py      ← User CRUD + session helpers
    ├── services/
    │   ├── data.py         ← Per-user file I/O
    │   ├── pipeline.py     ← Pipeline orchestration + status
    │   ├── analytics.py    ← Dashboard stats computation
    │   └── llm.py          ← LLM provider + API key management
    └── routes/
        ├── pages.py        ← Page routes (/, /start, /dashboard)
        ├── users.py        ← /api/users/*
        ├── pipeline.py     ← /api/start-search, /api/refresh
        ├── documents.py    ← /api/generate-resume, /api/generate-cover-letter
        └── settings.py     ← /api/set-provider, /api/set-api-key, /api/check-ollama
"""

import os
import sys

from flask import Flask

# Ensure project root is importable (for agents/, config, etc.)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _asset_version() -> str:
    """Short token used to cache-bust static assets. Prefers current git HEAD
    so each deploy invalidates the browser cache; falls back to mtime of app.py
    when git is unavailable (e.g. running from an extracted release zip)."""
    try:
        import subprocess
        out = subprocess.check_output(
            ["git", "rev-parse", "--short=10", "HEAD"],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        if out:
            return out
    except Exception:
        pass
    try:
        return str(int(os.path.getmtime(__file__)))
    except Exception:
        return "dev"


def create_app() -> Flask:
    """Application factory."""
    from portal.config import SECRET_KEY, MAX_UPLOAD_SIZE
    from portal.services.data import migrate_legacy_data
    from portal.routes import register_all

    app = Flask(__name__)
    app.secret_key = SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE

    # One-time migration of flat-file data → per-user dirs
    migrate_legacy_data()

    # Register all route blueprints
    register_all(app)

    # Cache-bust static URLs after each deploy (browser otherwise serves stale
    # JS/HTML, which is the most common cause of "AI Engine empty after
    # restart" / "scan returns generic chips" reports).
    asset_v = _asset_version()

    @app.context_processor
    def _inject_asset_v():
        return {"asset_v": asset_v}

    return app


# ─── Entry point ──────────────────────────────────────────────────

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, use_reloader=False, port=5050)
