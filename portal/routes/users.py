"""User management API routes."""

from flask import Blueprint, jsonify, request, session

from portal.models import user as user_model
from portal.services import data as data_svc, pipeline

bp = Blueprint("users", __name__, url_prefix="/api/users")


@bp.route("")
def list_users():
    """List all users with enriched status."""
    users = user_model.get_all()
    uid = user_model.current_id()
    for u in users:
        status = pipeline.get_status(u["id"])
        u["pipeline_running"] = status.get("running", False)
        try:
            data = data_svc.load_results(u["id"])
            u["has_results"] = bool(data.get("jobs"))
            u["job_count"] = len(data.get("jobs", []))
        except Exception:
            u["has_results"] = False
            u["job_count"] = 0
    return jsonify({"users": users, "current_user_id": uid})


@bp.route("/create", methods=["POST"])
def create():
    """Create a new user and switch to them."""
    data = request.json or {}
    name = data.get("name", "New User").strip() or "New User"
    uid = user_model.create(name)
    user_model.set_current(uid)
    return jsonify({"ok": True, "user_id": uid, "redirect": "/start"})


@bp.route("/switch/<user_id>", methods=["POST"])
def switch(user_id):
    """Switch to an existing user."""
    if not user_model.exists(user_id):
        return jsonify({"ok": False, "message": "User not found"})

    user_model.set_current(user_id)
    try:
        has_results = bool(data_svc.load_results(user_id).get("jobs"))
    except Exception:
        has_results = False

    status = pipeline.get_status(user_id)
    if has_results:
        redirect_url = "/dashboard"
    elif status.get("running"):
        redirect_url = "/"
    else:
        redirect_url = "/start"

    return jsonify({"ok": True, "user_id": user_id, "redirect": redirect_url})


@bp.route("/delete/<user_id>", methods=["POST"])
def delete(user_id):
    """Delete a user profile. Cancel any running pipeline first."""
    # Cancel pipeline immediately so LLM workers stop
    status = pipeline.get_status(user_id)
    if status.get("running"):
        pipeline.request_cancel(user_id)

    current = user_model.current_id()
    user_model.delete(user_id)
    users = user_model.get_all()
    if current == user_id:
        if users:
            user_model.set_current(users[0]["id"])
        else:
            user_model.clear_current()
    return jsonify({"ok": True, "redirect": "/"})
