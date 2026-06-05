"""Settings API — LLM provider, API key, Ollama check."""

from flask import Blueprint, jsonify, request

from portal.services import llm

bp = Blueprint("settings", __name__, url_prefix="/api")


@bp.route("/check-ollama")
def check_ollama():
    return jsonify(llm.check_ollama())


@bp.route("/providers")
def providers():
    """Detect installed LLM providers (paths + keys). Used to render dynamic toggle."""
    return jsonify(llm.detect_providers())


@bp.route("/set-groq-key", methods=["POST"])
def set_groq_key():
    key = (request.json or {}).get("key", "").strip()
    if not key:
        return jsonify({"ok": False, "message": "No key provided"})
    llm._write_env_key("GROQ_API_KEY", key)
    try:
        import agents.llm_client as lc
        lc.GROQ_API_KEY = key
    except Exception:
        pass
    return jsonify({"ok": True})


@bp.route("/set-provider", methods=["POST"])
def set_provider():
    provider = (request.json or {}).get("provider", "gemini")
    return jsonify(llm.set_provider(provider))


@bp.route("/set-api-key", methods=["POST"])
def set_api_key():
    key = (request.json or {}).get("key", "").strip()
    if not key:
        return jsonify({"ok": False, "message": "No key provided"})
    llm.save_api_key(key)
    return jsonify({"ok": True, "message": "API key saved and activated"})
