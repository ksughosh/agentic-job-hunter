"""
Backward-compatible re-export from llm_client.
All LLM logic lives in llm_client.py now (supports Gemini + Gemma4).
"""

from agents.llm_client import (
    call_llm as call_gemini,
    llm_jd_match as gemini_jd_match,
    llm_resume_writer as gemini_resume_writer,
    llm_cover_letter_writer as gemini_cover_letter_writer,
    llm_talent_review as gemini_talent_review,
    set_provider,
    get_provider,
    web_search,
    web_fetch,
    web_search_and_summarize,
    _parse_json_response,
    _format_profile,
    _format_job,
    GEMINI_API_KEY,
)
