"""
Job Hunter — Configuration
Profile and search queries are populated dynamically from:
  1. Uploaded resume (parsed by profile_parser)
  2. Desired role (from search input)
  3. LLM refinement (Gemini)

This file provides defaults that get overridden at runtime.
"""

CANDIDATE_PROFILE = {
    "name": "",
    "title": "",
    "years_experience": 0,
    "location": "",
    "email": "",
    "phone": "",
    "linkedin": "",
    "primary_skills": [],
    "domain_keywords": [],
    "preferred_role_types": ["full time"],
    "preferred_regions": ["Remote", "Global"],
    "min_experience_level": "senior",
    "education": [],
    "experience_highlights": [],
    "companies_worked": [],
    "experience_entries": [],
    "languages": ["English"],
}

JOB_SEARCH_QUERIES = [
    "remote software engineer",
    "remote developer",
]

TARGET_REGIONS = ["US", "Europe", "Australia", "Global", "Remote"]
