from __future__ import annotations

"""User model — CRUD operations and session management."""

import json
import os
import uuid
from datetime import datetime

from flask import session

from portal.config import DATA_DIR, USERS_FILE


# ─── CRUD ─────────────────────────────────────────────────────────


def get_all() -> list[dict]:
    """Load all registered users."""
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return []


def _save(users: list[dict]):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)


def create(name: str = "New User") -> str:
    """Create a new user and return their ID."""
    uid = str(uuid.uuid4())[:8]
    users = get_all()
    users.append({
        "id": uid,
        "name": name,
        "created_at": datetime.now().isoformat(),
        "desired_roles": [],
    })
    _save(users)
    user_dir = os.path.join(DATA_DIR, uid)
    os.makedirs(user_dir, exist_ok=True)
    os.makedirs(os.path.join(user_dir, "uploads"), exist_ok=True)
    return uid


def update(user_id: str, updates: dict):
    """Update user metadata."""
    users = get_all()
    for u in users:
        if u["id"] == user_id:
            u.update(updates)
            break
    _save(users)


def delete(user_id: str):
    """Remove a user from the registry (data directory kept for safety)."""
    users = get_all()
    _save([u for u in users if u["id"] != user_id])


def exists(user_id: str) -> bool:
    return any(u["id"] == user_id for u in get_all())


# ─── Session helpers ──────────────────────────────────────────────


def current_id() -> str | None:
    """Current user ID from the Flask session."""
    return session.get("user_id")


def set_current(user_id: str):
    session["user_id"] = user_id


def clear_current():
    session.pop("user_id", None)
