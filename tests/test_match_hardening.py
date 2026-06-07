"""Tests for the match-score hardening blend in the JD-match step.

The full ``_run_jd_match`` flow is heavyweight (LLM client, DB, parallel
workers), so we test the hardening math directly against
``agents.job_enricher.harden_match_score`` and verify the blend rules used
in the pipeline.
"""

from __future__ import annotations

from agents.job_enricher import harden_match_score, keyword_overlap_score


def test_hardening_pulls_down_hallucinated_high_score():
    """LLM says 95, but description has zero overlap with the profile —
    hardened score should be notably lower."""
    profile = {"primary_skills": ["python", "django", "rest"]}
    desc = "We are a finance team looking for an actuarial analyst."  # no skill words
    overlap = keyword_overlap_score(profile, desc)
    hardened = harden_match_score(95, overlap)
    assert overlap == 0.0
    assert hardened < 70.0, f"expected hardening to pull score down, got {hardened}"


def test_hardening_boosts_genuine_match_llm_missed():
    """Profile and description fully aligned but LLM gave 40 — hardened
    pulls up toward the overlap signal."""
    profile = {"primary_skills": ["python", "django", "rest"]}
    desc = "Senior Python developer needed. Strong Django and REST experience required."
    overlap = keyword_overlap_score(profile, desc)
    assert overlap == 100.0
    hardened = harden_match_score(40, overlap)
    assert hardened > 55.0, f"expected hardening to lift score, got {hardened}"


def test_hardening_preserves_aligned_scores():
    """When LLM and overlap agree, hardened ≈ either signal."""
    profile = {"primary_skills": ["python"]}
    desc = "Python role."
    overlap = keyword_overlap_score(profile, desc)
    hardened = harden_match_score(100, overlap)
    assert hardened == 100.0


def test_hardening_idempotent_on_zero():
    """Both signals zero → hardened zero."""
    assert harden_match_score(0, 0) == 0.0


def test_overlap_uses_all_profile_axes():
    """Overlap must read primary_skills, domain_keywords, desired_roles."""
    profile = {
        "primary_skills": [],
        "domain_keywords": ["audit"],
        "desired_roles": ["compliance manager"],
    }
    desc = "Audit and compliance manager role."
    overlap = keyword_overlap_score(profile, desc)
    assert overlap == 100.0
