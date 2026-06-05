from __future__ import annotations

"""Per-user data I/O — profiles, results, search context."""

import json
import os
import shutil
from datetime import datetime

from portal.config import DATA_DIR
from portal.models import user as user_model


def _user_dir(user_id: str | None = None) -> str:
    """Resolve and ensure the data directory for a user exists."""
    uid = user_id or user_model.current_id()
    if not uid:
        raise ValueError("No user selected")
    d = os.path.join(DATA_DIR, uid)
    os.makedirs(d, exist_ok=True)
    os.makedirs(os.path.join(d, "uploads"), exist_ok=True)
    return d


def uploads_dir(user_id: str | None = None) -> str:
    return os.path.join(_user_dir(user_id), "uploads")


# ─── JSON helpers (private) ───────────────────────────────────────


def _load_json(user_id: str | None, filename: str, default=None):
    path = os.path.join(_user_dir(user_id), filename)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default if default is not None else {}


def _save_json(user_id: str | None, filename: str, data):
    path = os.path.join(_user_dir(user_id), filename)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ─── Public API ───────────────────────────────────────────────────


def load_results(user_id: str | None = None) -> dict:
    default = {
        "jobs": [], "reviews": {}, "company_reviews": {},
        "stats": {}, "profile": {}, "search_context": {},
    }
    return _load_json(user_id, "results.json", default)


def save_results(results: dict, user_id: str | None = None):
    _save_json(user_id, "results.json", results)


def load_profile(user_id: str | None = None) -> dict:
    return _load_json(user_id, "profile.json", {})


def save_profile(profile: dict, user_id: str | None = None):
    _save_json(user_id, "profile.json", profile)


def load_search_context(user_id: str | None = None) -> dict:
    return _load_json(user_id, "search_context.json", {})


def save_search_context(ctx: dict, user_id: str | None = None):
    _save_json(user_id, "search_context.json", ctx)


def load_scan_result(user_id: str | None = None) -> dict:
    return _load_json(user_id, "scan_result.json", {})


def save_scan_result(result: dict, user_id: str | None = None):
    _save_json(user_id, "scan_result.json", result)


# ─── Legacy migration ────────────────────────────────────────────


def migrate_legacy_data():
    """Move old flat-file data into a 'default' user directory."""
    legacy_profile = os.path.join(DATA_DIR, "profile.json")
    default_dir = os.path.join(DATA_DIR, "default")
    if not os.path.exists(legacy_profile) or os.path.isdir(default_dir):
        return

    try:
        with open(legacy_profile) as f:
            profile = json.load(f)
        name = profile.get("name", "Default User")
    except Exception:
        name = "Default User"

    uid = "default"
    users = user_model.get_all()
    if not any(u["id"] == uid for u in users):
        users.append({
            "id": uid,
            "name": name,
            "created_at": datetime.now().isoformat(),
            "desired_roles": [],
        })
        user_model._save(users)

    os.makedirs(default_dir, exist_ok=True)
    os.makedirs(os.path.join(default_dir, "uploads"), exist_ok=True)

    for fname in ["results.json", "profile.json", "search_context.json"]:
        src = os.path.join(DATA_DIR, fname)
        if os.path.exists(src):
            shutil.move(src, os.path.join(default_dir, fname))

    legacy_uploads = os.path.join(DATA_DIR, "uploads")
    if os.path.exists(legacy_uploads):
        for f in os.listdir(legacy_uploads):
            src = os.path.join(legacy_uploads, f)
            if os.path.isfile(src):
                shutil.move(src, os.path.join(default_dir, "uploads", f))
