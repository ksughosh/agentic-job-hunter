"""SearchProfile model — multiple search configurations per user.

Each user can have many SearchProfiles, each with its own roles, work mode,
filters, and pipeline results. The resume + parsed scan are shared at the
user level; only pipeline inputs + outputs are per-profile.

File layout:
    data/<uid>/profiles/<pid>/search_profile.json   — config
    data/<uid>/profiles/<pid>/results.json           — pipeline output
    data/<uid>/profiles/<pid>/search_context.json    — search params
    data/<uid>/active_profile.txt                    — currently selected pid
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime

from portal.config import DATA_DIR


# ─── Helpers ─────────────────────────────────────────────────────


def _profiles_dir(user_id: str) -> str:
    d = os.path.join(DATA_DIR, user_id, "profiles")
    os.makedirs(d, exist_ok=True)
    return d


def _profile_dir(user_id: str, profile_id: str) -> str:
    d = os.path.join(_profiles_dir(user_id), profile_id)
    os.makedirs(d, exist_ok=True)
    return d


def _profile_path(user_id: str, profile_id: str) -> str:
    return os.path.join(_profile_dir(user_id, profile_id), "search_profile.json")


def _active_path(user_id: str) -> str:
    return os.path.join(DATA_DIR, user_id, "active_profile.txt")


# ─── CRUD ────────────────────────────────────────────────────────


def create(
    user_id: str,
    name: str = "Default",
    roles: list[str] | None = None,
    work_mode: str = "remote",
    scan_depth: str = "quick",
    location: str = "Global",
    salary_floor: int | None = None,
    seniority_override: str | None = None,
    exclude_keywords: list[str] | None = None,
) -> str:
    """Create a new SearchProfile under the given user. Returns profile_id."""
    pid = str(uuid.uuid4())[:8]
    profile = {
        "id": pid,
        "name": name,
        "created_at": datetime.now().isoformat(),
        "roles": roles or [],
        "work_mode": work_mode,
        "scan_depth": scan_depth,
        "location": location,
        "salary_floor": salary_floor,
        "seniority_override": seniority_override,
        "exclude_keywords": exclude_keywords or [],
    }
    path = _profile_path(user_id, pid)
    with open(path, "w") as f:
        json.dump(profile, f, indent=2)

    # If this is the first profile, make it active.
    if not get_active_id(user_id):
        set_active(user_id, pid)

    return pid


def get(user_id: str, profile_id: str) -> dict | None:
    """Load a single SearchProfile, or None if not found."""
    path = _profile_path(user_id, profile_id)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def list_all(user_id: str) -> list[dict]:
    """List all SearchProfiles for a user, sorted by created_at."""
    pdir = _profiles_dir(user_id)
    profiles = []
    if not os.path.isdir(pdir):
        return profiles
    for pid in os.listdir(pdir):
        sp = get(user_id, pid)
        if sp:
            profiles.append(sp)
    profiles.sort(key=lambda p: p.get("created_at", ""))
    return profiles


def update(user_id: str, profile_id: str, updates: dict) -> dict | None:
    """Update SearchProfile fields. Returns updated profile or None."""
    sp = get(user_id, profile_id)
    if not sp:
        return None
    # Only allow known fields to be updated.
    allowed = {
        "name", "roles", "work_mode", "scan_depth", "location",
        "salary_floor", "seniority_override", "exclude_keywords",
    }
    for k, v in updates.items():
        if k in allowed:
            sp[k] = v
    path = _profile_path(user_id, profile_id)
    with open(path, "w") as f:
        json.dump(sp, f, indent=2)
    return sp


def delete(user_id: str, profile_id: str):
    """Delete a SearchProfile directory."""
    import shutil
    d = os.path.join(_profiles_dir(user_id), profile_id)
    if os.path.isdir(d):
        shutil.rmtree(d)
    # If we deleted the active profile, switch to another.
    if get_active_id(user_id) == profile_id:
        remaining = list_all(user_id)
        if remaining:
            set_active(user_id, remaining[0]["id"])
        else:
            _clear_active(user_id)


# ─── Active profile ─────────────────────────────────────────────


def get_active_id(user_id: str) -> str | None:
    """Return the active profile ID, or None."""
    path = _active_path(user_id)
    if os.path.exists(path):
        with open(path) as f:
            pid = f.read().strip()
        if pid and os.path.isdir(os.path.join(_profiles_dir(user_id), pid)):
            return pid
    return None


def set_active(user_id: str, profile_id: str):
    path = _active_path(user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(profile_id)


def _clear_active(user_id: str):
    path = _active_path(user_id)
    if os.path.exists(path):
        os.remove(path)


def get_active(user_id: str) -> dict | None:
    """Convenience: load the active SearchProfile."""
    pid = get_active_id(user_id)
    return get(user_id, pid) if pid else None


# ─── Per-profile data I/O ────────────────────────────────────────


def load_profile_results(user_id: str, profile_id: str) -> dict:
    """Load pipeline results for a specific SearchProfile."""
    path = os.path.join(_profile_dir(user_id, profile_id), "results.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"jobs": [], "reviews": {}, "company_reviews": {}, "stats": {}, "profile": {}, "search_context": {}}


def save_profile_results(user_id: str, profile_id: str, results: dict):
    path = os.path.join(_profile_dir(user_id, profile_id), "results.json")
    with open(path, "w") as f:
        json.dump(results, f, indent=2, default=str)


def load_profile_search_context(user_id: str, profile_id: str) -> dict:
    path = os.path.join(_profile_dir(user_id, profile_id), "search_context.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_profile_search_context(user_id: str, profile_id: str, ctx: dict):
    path = os.path.join(_profile_dir(user_id, profile_id), "search_context.json")
    with open(path, "w") as f:
        json.dump(ctx, f, indent=2, default=str)


# ─── Migration ───────────────────────────────────────────────────


def ensure_default_profile(user_id: str) -> str:
    """Ensure a user has at least one SearchProfile.

    If none exist, create a "Default" profile and migrate any existing
    user-level results into it. Returns the active profile_id.
    """
    profiles = list_all(user_id)
    if profiles:
        pid = get_active_id(user_id) or profiles[0]["id"]
        set_active(user_id, pid)
        return pid

    # Create default profile from existing user data.
    from portal.models import user as user_model
    users = user_model.get_all()
    user = next((u for u in users if u["id"] == user_id), None)
    roles = user.get("desired_roles", []) if user else []

    # Read existing search_context for work_mode.
    sc_path = os.path.join(DATA_DIR, user_id, "search_context.json")
    work_mode = "remote"
    if os.path.exists(sc_path):
        try:
            with open(sc_path) as f:
                sc = json.load(f)
            work_mode = sc.get("work_mode", "remote")
        except Exception:
            pass

    pid = create(user_id, name="Default", roles=roles, work_mode=work_mode)

    # Migrate existing results.json into the new profile dir.
    for fname in ("results.json", "search_context.json"):
        src = os.path.join(DATA_DIR, user_id, fname)
        dst = os.path.join(_profile_dir(user_id, pid), fname)
        if os.path.exists(src):
            import shutil
            shutil.copy2(src, dst)

    return pid
