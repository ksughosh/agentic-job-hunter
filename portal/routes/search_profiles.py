"""Search Profile API routes — CRUD + switch active profile."""

from flask import Blueprint, jsonify, request

from portal.models import user as user_model, search_profile as sp_model
from portal.services import pipeline

bp = Blueprint("search_profiles", __name__, url_prefix="/api/search-profiles")


@bp.route("")
def list_profiles():
    """List all search profiles for the current user."""
    uid = user_model.current_id()
    if not uid:
        return jsonify({"profiles": [], "active_id": None})

    profiles = sp_model.list_all(uid)
    active_id = sp_model.get_active_id(uid)

    # Enrich with result counts + pipeline status.
    for p in profiles:
        try:
            results = sp_model.load_profile_results(uid, p["id"])
            p["job_count"] = len(results.get("jobs", []))
            p["has_results"] = p["job_count"] > 0
        except Exception:
            p["job_count"] = 0
            p["has_results"] = False
        p["is_active"] = p["id"] == active_id

    return jsonify({"profiles": profiles, "active_id": active_id})


@bp.route("/create", methods=["POST"])
def create_profile():
    """Create a new search profile."""
    uid = user_model.current_id()
    if not uid:
        return jsonify({"ok": False, "message": "No user selected."})

    data = request.json or {}
    pid = sp_model.create(
        uid,
        name=data.get("name", "New Search"),
        roles=data.get("roles", []),
        work_mode=data.get("work_mode", "remote"),
        scan_depth=data.get("scan_depth", "quick"),
        location=data.get("location", "Global"),
        salary_floor=data.get("salary_floor"),
        seniority_override=data.get("seniority_override"),
        exclude_keywords=data.get("exclude_keywords", []),
    )
    return jsonify({"ok": True, "profile_id": pid})


@bp.route("/update/<profile_id>", methods=["POST"])
def update_profile(profile_id):
    """Update a search profile."""
    uid = user_model.current_id()
    if not uid:
        return jsonify({"ok": False, "message": "No user selected."})

    data = request.json or {}
    sp = sp_model.update(uid, profile_id, data)
    if not sp:
        return jsonify({"ok": False, "message": "Profile not found."})
    return jsonify({"ok": True, "profile": sp})


@bp.route("/switch/<profile_id>", methods=["POST"])
def switch_profile(profile_id):
    """Switch the active search profile."""
    uid = user_model.current_id()
    if not uid:
        return jsonify({"ok": False, "message": "No user selected."})

    sp = sp_model.get(uid, profile_id)
    if not sp:
        return jsonify({"ok": False, "message": "Profile not found."})

    sp_model.set_active(uid, profile_id)
    return jsonify({"ok": True, "active_id": profile_id})


@bp.route("/delete/<profile_id>", methods=["POST"])
def delete_profile(profile_id):
    """Delete a search profile."""
    uid = user_model.current_id()
    if not uid:
        return jsonify({"ok": False, "message": "No user selected."})

    sp_model.delete(uid, profile_id)
    return jsonify({"ok": True, "active_id": sp_model.get_active_id(uid)})
