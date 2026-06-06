"""Pipeline API routes — start search, refresh, status, resume scan."""

import os
import threading

from flask import Blueprint, jsonify, request

from portal.models import user as user_model
from portal.services import data as data_svc, pipeline as pipeline_svc, llm

bp = Blueprint("pipeline_api", __name__, url_prefix="/api")


@bp.route("/scan-resume", methods=["POST"])
def scan_resume():
    """Fast resume scan — returns role recommendations + profile + search queries.
    Called immediately after file upload. Result is cached per user so the pipeline
    can reuse it instead of re-parsing with LLM (saves ~5 min on local models).
    """
    resume_file = request.files.get("resume")
    if not resume_file:
        return jsonify({"ok": False, "message": "No file uploaded."})

    # Lazily create the user record on first successful scan — not on
    # "Create New Profile" click.  This prevents blank "New User" entries
    # from cluttering the sidebar before a resume is parsed.
    uid = user_model.current_id()
    if not uid:
        uid = user_model.create("Scanning…")
        user_model.set_current(uid)
        _user_is_provisional = True
    else:
        _user_is_provisional = False

    # "deep" = run the full scan synchronously (~20s) for a complete profile.
    # Default "quick" = fast chips (~3s) + full scan in the background.
    deep = request.form.get("mode", "quick").strip().lower() in ("full", "deep")

    user_dir = data_svc.uploads_dir(uid)
    resume_path = os.path.join(user_dir, "resume.pdf")
    resume_file.save(resume_path)

    try:
        from agents.profile_parser import extract_text_from_pdf
        from agents.resume_scanner import fast_scan_resume
        raw_text = extract_text_from_pdf(resume_path)
        if not raw_text:
            if _user_is_provisional:
                user_model.delete(uid)
                user_model.clear_current()
            return jsonify({"ok": False, "message": "Could not read PDF."})

        if deep:
            # DEEP scan (sync) — full profile incl. experience + education.
            result = fast_scan_resume(raw_text, mode="full")
            if not result or not result.get("name"):
                if _user_is_provisional:
                    user_model.delete(uid)
                    user_model.clear_current()
                return jsonify({"ok": False, "message": "LLM returned no profile. The active provider may be rate-limited (Groq free tier) or unreachable. Try switching to MLX/LM Studio/Ollama from the AI Engine toggle."})
            # Scan succeeded — name the user from the resume.
            user_model.update(uid, {"name": result.get("name", "New User")})
            data_svc.save_scan_result(result, uid)
            return jsonify({"ok": True, "mode": "deep", **result})

        # QUICK scan (sync) — minimal JSON, returns chips fast (~3s).
        result = fast_scan_resume(raw_text, mode="quick")
        if not result or not result.get("name"):
            if _user_is_provisional:
                user_model.delete(uid)
                user_model.clear_current()
            return jsonify({"ok": False, "message": "LLM returned no profile. The active provider may be rate-limited (Groq free tier) or unreachable. Try switching to MLX/LM Studio/Ollama from the AI Engine toggle."})

        # Scan succeeded — name the user from the resume.
        user_model.update(uid, {"name": result.get("name", "New User")})

        # Cache the quick result immediately so the pipeline can start even
        # before the full scan finishes.
        data_svc.save_scan_result(result, uid)

        # FULL scan (background) — upgrades the cache with experience_entries +
        # education so the pipeline's fast path has the complete profile. The
        # user is filling out the form meanwhile, so this latency is hidden.
        def _full_scan_bg():
            try:
                full = fast_scan_resume(raw_text, mode="full")
                if full and full.get("name"):
                    data_svc.save_scan_result(full, uid)
                    print(f"[ScanResume] Full scan cached for {uid}", flush=True)
            except Exception as e:
                print(f"[ScanResume] Full scan failed for {uid}: {e}", flush=True)

        threading.Thread(target=_full_scan_bg, daemon=True).start()

        return jsonify({"ok": True, "mode": "quick", **result})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"ok": False, "message": str(e)})


@bp.route("/start-search", methods=["POST"])
def start_search():
    """Onboarding submit: resume upload + roles → start full pipeline."""
    uid = user_model.current_id()
    if not uid:
        # User should already exist from scan_resume, but handle edge case.
        uid = user_model.create("New User")
        user_model.set_current(uid)

    status = pipeline_svc.get_status(uid)
    if status.get("running"):
        return jsonify({"status": "already_running", "message": status["message"]})

    resume_file = request.files.get("resume")
    desired_roles = request.form.get("desired_roles", "").strip()
    work_mode = request.form.get("work_mode", "remote").strip()  # comma-sep: "remote,hybrid"
    location = request.form.get("location", "Anywhere").strip() or "Anywhere"
    llm_provider = request.form.get("llm_provider", "gemini").strip()

    if not resume_file or not desired_roles:
        return jsonify({"status": "error", "message": "Please upload a resume and specify at least one role."})

    from agents.llm_client import set_provider
    set_provider(llm_provider)

    user_dir = data_svc.uploads_dir(uid)
    resume_path = os.path.join(user_dir, "resume.pdf")
    resume_file.save(resume_path)

    roles_list = [r.strip() for r in desired_roles.split(",") if r.strip()]
    user_model.update(uid, {"desired_roles": roles_list})

    threading.Thread(
        target=pipeline_svc.run_full,
        args=(uid, resume_path, roles_list, work_mode),
        kwargs={"location": location},
        daemon=True,
    ).start()
    return jsonify({"status": "started", "message": "Pipeline started..."})


@bp.route("/search-status")
def search_status():
    return jsonify(pipeline_svc.get_status(user_model.current_id()))


@bp.route("/refresh", methods=["POST"])
def refresh():
    """Re-run agent pipeline with existing profile."""
    uid = user_model.current_id()
    if not uid:
        return jsonify({"status": "error", "message": "No user selected."})

    status = pipeline_svc.get_status(uid)
    if status.get("running"):
        return jsonify({"status": "already_running", "message": status["message"]})

    profile = data_svc.load_profile(uid)
    if not profile.get("name"):
        return jsonify({"status": "error", "message": "No profile found. Please start from the beginning."})

    search_ctx = data_svc.load_search_context(uid)
    threading.Thread(
        target=pipeline_svc.run_agents,
        args=(
            uid, profile,
            search_ctx.get("search_queries", []),
            search_ctx.get("desired_role", ""),
            search_ctx.get("work_mode", "remote"),
        ),
        daemon=True,
    ).start()
    return jsonify({"status": "started", "message": "Refresh started..."})


@bp.route("/refresh-status")
def refresh_status():
    return jsonify(pipeline_svc.get_status(user_model.current_id()))


@bp.route("/cancel-pipeline", methods=["POST"])
def cancel_pipeline():
    """Cancel a running pipeline for the current user.

    If the user has no results yet (first-time onboarding cancelled),
    remove their sidebar entry so they don't leave a blank placeholder.
    """
    uid = user_model.current_id()
    if not uid:
        return jsonify({"ok": False, "message": "No user selected."})
    status = pipeline_svc.get_status(uid)
    if not status.get("running"):
        return jsonify({"ok": False, "message": "No pipeline is running."})
    pipeline_svc.request_cancel(uid)

    # Check if the user has any results — if not, clean up the placeholder.
    remove_user = False
    try:
        data = data_svc.load_results(uid)
        if not data.get("jobs"):
            remove_user = True
    except Exception:
        remove_user = True

    if remove_user:
        user_model.delete(uid)
        user_model.clear_current()
        return jsonify({"ok": True, "message": "Pipeline cancelled. Profile removed.", "removed": True})

    return jsonify({"ok": True, "message": "Cancellation requested.", "removed": False})
