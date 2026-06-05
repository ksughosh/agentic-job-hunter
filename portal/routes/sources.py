"""Job sources management API."""

import json
import os
from flask import Blueprint, jsonify, request
from portal.config import DATA_DIR

bp = Blueprint("sources", __name__, url_prefix="/api/sources")

SOURCES_FILE = os.path.join(DATA_DIR, "custom_sources.json")


def _load_custom_sources() -> list[dict]:
    if os.path.exists(SOURCES_FILE):
        try:
            with open(SOURCES_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return []


def _save_custom_sources(sources: list[dict]):
    with open(SOURCES_FILE, "w") as f:
        json.dump(sources, f, indent=2)


@bp.route("")
def list_sources():
    """List all built-in and custom job sources."""
    from agents.job_scraper import SOURCE_QUALITY, JobSearchAgent
    agent = JobSearchAgent.__new__(JobSearchAgent)
    agent.__init__()

    builtin = []
    for s in agent.scrapers:
        builtin.append({
            "name": s.name,
            "quality": SOURCE_QUALITY.get(s.name, 50),
            "type": "builtin",
            "enabled": True,
        })

    custom = _load_custom_sources()
    return jsonify({"builtin": builtin, "custom": custom})


@bp.route("/add", methods=["POST"])
def add_source():
    """Add a custom RSS/API job source."""
    data = request.json or {}
    name = data.get("name", "").strip()
    url = data.get("url", "").strip()
    source_type = data.get("source_type", "rss").strip()
    quality = int(data.get("quality", 50))

    if not name or not url:
        return jsonify({"ok": False, "message": "Name and URL are required."})

    sources = _load_custom_sources()
    if any(s["name"].lower() == name.lower() for s in sources):
        return jsonify({"ok": False, "message": f"Source '{name}' already exists."})

    sources.append({
        "name": name,
        "url": url,
        "source_type": source_type,
        "quality": min(max(quality, 0), 100),
        "enabled": True,
    })
    _save_custom_sources(sources)
    return jsonify({"ok": True, "message": f"Source '{name}' added."})


@bp.route("/remove", methods=["POST"])
def remove_source():
    """Remove a custom source by name."""
    name = (request.json or {}).get("name", "").strip()
    sources = _load_custom_sources()
    before = len(sources)
    sources = [s for s in sources if s["name"].lower() != name.lower()]
    if len(sources) == before:
        return jsonify({"ok": False, "message": "Source not found."})
    _save_custom_sources(sources)
    return jsonify({"ok": True, "message": f"Source '{name}' removed."})


@bp.route("/toggle", methods=["POST"])
def toggle_source():
    """Enable or disable a custom source."""
    data = request.json or {}
    name = data.get("name", "").strip()
    enabled = data.get("enabled", True)
    sources = _load_custom_sources()
    for s in sources:
        if s["name"].lower() == name.lower():
            s["enabled"] = enabled
            _save_custom_sources(sources)
            return jsonify({"ok": True, "enabled": enabled})
    return jsonify({"ok": False, "message": "Source not found."})
