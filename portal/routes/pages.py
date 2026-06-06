from __future__ import annotations

"""Page routes — index, start (onboarding), dashboard."""

from flask import Blueprint, render_template, redirect, url_for

from portal.models import user as user_model
from portal.services import data as data_svc, analytics, pipeline, llm

bp = Blueprint("pages", __name__)


def _onboarding_ctx(uid: str | None, running=False, status=None):
    """Common context dict for the onboarding template."""
    return {
        "api_key_set": llm.is_api_key_set(),
        "api_key_masked": llm.get_masked_key(),
        "users": user_model.get_all(),
        "current_user_id": uid,
        "pipeline_running": running,
        "pipeline_status": status or {},
    }


@bp.route("/")
def index():
    """Landing — route to the appropriate view."""
    users = user_model.get_all()
    uid = user_model.current_id()

    if uid:
        # Check pipeline first — it must not be swallowed by a load_results error
        status = pipeline.get_status(uid)
        if status.get("running"):
            return render_template("onboarding.html",
                                   **_onboarding_ctx(uid, True, status))
        try:
            data = data_svc.load_results(uid)
            if data.get("jobs"):
                return redirect(url_for("pages.dashboard"))
        except Exception:
            pass

    # Auto-select first user if none active
    if users and not uid:
        uid = users[0]["id"]
        user_model.set_current(uid)
        try:
            if data_svc.load_results(uid).get("jobs"):
                return redirect(url_for("pages.dashboard"))
        except Exception:
            pass

    return render_template("onboarding.html", **_onboarding_ctx(uid))


@bp.route("/start")
def start():
    """Force-show the onboarding page (new search).

    ?new=1 clears the current user so the scan-resume route creates a
    fresh profile lazily on first successful parse.
    """
    from flask import request
    uid = user_model.current_id()
    if request.args.get("new"):
        # Don't clear current user if their pipeline is still running —
        # that would orphan the pipeline thread and create a duplicate
        # user on the next resume upload.
        if uid:
            status = pipeline.get_status(uid)
            if status.get("running"):
                # Stay on the running pipeline instead of creating a new profile
                return render_template("onboarding.html",
                                       **_onboarding_ctx(uid, True, status))
        user_model.clear_current()
        uid = None
        status = {}
    else:
        status = pipeline.get_status(uid) if uid else {}
    return render_template("onboarding.html",
                           **_onboarding_ctx(uid, status.get("running", False), status))


@bp.route("/dashboard")
def dashboard():
    """Main dashboard with job results."""
    uid = user_model.current_id()
    if not uid:
        return redirect(url_for("pages.index"))

    data = data_svc.load_results(uid)
    jobs = data.get("jobs", [])
    if not jobs:
        return redirect(url_for("pages.index"))

    top_jobs = sorted(jobs, key=lambda j: j.get("composite_score", 0), reverse=True)[:50]
    stats = analytics.compute_dashboard_stats(jobs)
    p_status = pipeline.get_status(uid)

    return render_template(
        "index.html",
        jobs=top_jobs,
        total_jobs=len(jobs),
        stats=data.get("stats", {}),
        score_distribution=stats["score_distribution"],
        source_counts=stats["source_counts"],
        type_counts=stats["type_counts"],
        top_skills=stats["top_skills"],
        salary_ranges=stats["salary_ranges"],
        avg_match=stats["avg_match"],
        avg_composite=stats["avg_composite"],
        contract_count=stats["contract_count"],
        high_match=stats["high_match"],
        company_reviews=data.get("company_reviews", {}),
        profile=data.get("profile", {}),
        search_context=data.get("search_context", {}),
        users=user_model.get_all(),
        current_user_id=uid,
        source_quality_data=stats["source_quality_data"],
        pipeline_running=p_status.get("running", False),
        pipeline_status=p_status,
    )
