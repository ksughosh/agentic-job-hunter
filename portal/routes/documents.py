"""Document generation API — tailored resumes and cover letters."""

from flask import Blueprint, jsonify, request

from portal.models import user as user_model
from portal.services import pipeline as pipeline_svc

bp = Blueprint("documents", __name__, url_prefix="/api")


def _serialize_result(result, doc_type: str) -> dict:
    return {
        "content": result.final_content,
        "score": result.final_score,
        "iterations": [
            {
                "iteration": fb.iteration,
                "score": fb.score,
                "strengths": fb.strengths,
                "weaknesses": fb.weaknesses,
                "suggestions": fb.suggestions,
                "passed": fb.passed,
            }
            for fb in result.iterations
        ],
        "total_iterations": result.total_iterations,
        "document_type": doc_type,
    }


@bp.route("/generate-resume", methods=["POST"])
def generate_resume():
    uid = user_model.current_id()
    data = request.json
    pipeline_svc.inject_profile(uid)

    from agents.resume_writer import ResumeWriter
    result = ResumeWriter().generate(
        data.get("job_title", ""),
        data.get("company", ""),
        data.get("description", ""),
        max_iterations=5,
    )
    return jsonify(_serialize_result(result, "resume"))


@bp.route("/generate-cover-letter", methods=["POST"])
def generate_cover_letter():
    uid = user_model.current_id()
    data = request.json
    pipeline_svc.inject_profile(uid)

    from agents.resume_writer import CoverLetterWriter
    result = CoverLetterWriter().generate(
        data.get("job_title", ""),
        data.get("company", ""),
        data.get("description", ""),
        data.get("tailored_resume", ""),
        max_iterations=5,
    )
    return jsonify(_serialize_result(result, "cover_letter"))
