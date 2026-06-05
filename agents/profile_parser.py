"""
Resume Profile Parser
Extracts structured candidate profile from uploaded PDF resume.
Uses:
  1. PyPDF2 for text extraction
  2. Regex heuristics for structured fields (name, email, phone, education)
  3. Gemini LLM for intelligent profile extraction (skills, experience, summary)
"""

import re
import json
from pathlib import Path

from agents.gemini_client import call_gemini


def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract raw text from a PDF file."""
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(pdf_path)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return text.strip()
    except Exception as e:
        print(f"[ProfileParser] PDF extraction error: {e}")
        return ""


def parse_resume_to_profile(pdf_path: str) -> dict:
    """
    Parse a resume PDF into a structured candidate profile.
    Returns a dict compatible with the agent system.
    """
    raw_text = extract_text_from_pdf(pdf_path)
    if not raw_text:
        return _empty_profile()

    # Try LLM-powered extraction first
    profile = _llm_extract_profile(raw_text)
    if profile and profile.get("name"):
        return profile

    # Fallback: heuristic extraction
    return _heuristic_extract_profile(raw_text)


def _llm_extract_profile(raw_text: str) -> dict:
    """Use Gemini to intelligently parse resume text into structured profile."""
    # Truncate to avoid token limits
    text = raw_text[:6000]

    prompt = f"""You are an expert resume parser. Extract a structured candidate profile from this resume text.

RESUME TEXT:
{text}

Extract the following and respond ONLY in this exact JSON format (no markdown, no explanation):
{{
    "name": "<full name>",
    "title": "<current/most recent job title and specialization>",
    "years_experience": <number>,
    "location": "<city, country>",
    "email": "<email address or empty string>",
    "phone": "<phone number or empty string>",
    "linkedin": "<linkedin url or empty string>",
    "primary_skills": ["<skill1>", "<skill2>", ... up to 15 most important skills],
    "domain_keywords": ["<keyword1>", "<keyword2>", ... up to 30 searchable keywords from the resume],
    "preferred_role_types": ["full time"],
    "preferred_regions": ["Remote", "Global"],
    "min_experience_level": "<junior/mid/senior/staff/principal>",
    "education": [
        {{"degree": "<degree name>", "university": "<university name>", "year": <graduation year>}}
    ],
    "experience_highlights": ["<highlight1>", "<highlight2>", ... up to 8 key achievements],
    "companies_worked": ["<company1>", "<company2>", ...],
    "experience_entries": [
        {{
            "company": "<company name>",
            "title": "<job title>",
            "dates": "<date range>",
            "location": "<location>",
            "bullets": ["<bullet1>", "<bullet2>", ...],
            "tags": ["<tag1>", "<tag2>", ...]
        }}
    ],
    "languages": ["<language1>", "<language2>", ...]
}}

Be precise. Extract actual data from the resume — do not fabricate anything."""

    response = call_gemini(prompt, max_tokens=4000, temperature=0.2)
    if not response:
        return {}

    # Parse JSON
    response = response.strip()
    if response.startswith("```"):
        lines = response.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        response = "\n".join(lines)

    try:
        profile = json.loads(response)
    except json.JSONDecodeError:
        start = response.find("{")
        end = response.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                profile = json.loads(response[start:end])
            except json.JSONDecodeError:
                return {}
        else:
            return {}

    # Validate minimum fields
    if not profile.get("name"):
        return {}

    # Ensure required fields have defaults
    profile.setdefault("title", "Software Engineer")
    profile.setdefault("years_experience", 0)
    profile.setdefault("location", "")
    profile.setdefault("email", "")
    profile.setdefault("phone", "")
    profile.setdefault("linkedin", "")
    profile.setdefault("primary_skills", [])
    profile.setdefault("domain_keywords", [])
    profile.setdefault("preferred_role_types", ["full time"])
    profile.setdefault("preferred_regions", ["Remote", "Global"])
    profile.setdefault("min_experience_level", "senior")
    profile.setdefault("education", [])
    profile.setdefault("experience_highlights", [])
    profile.setdefault("companies_worked", [])
    profile.setdefault("experience_entries", [])
    profile.setdefault("languages", ["English"])

    return profile


def _heuristic_extract_profile(raw_text: str) -> dict:
    """Fallback regex-based profile extraction."""
    lines = raw_text.split("\n")
    profile = _empty_profile()

    # Name: usually first non-empty line — can be ALL CAPS or Title Case
    for line in lines[:5]:
        line = line.strip()
        if line and len(line) < 60 and not any(c in line for c in ["@", "http", "+", "(", ")"]):
            # Match "JOHN DOE" or "John Doe" patterns (2+ words, starts with letter)
            if re.match(r'^[A-Z][A-Za-z]+\s+[A-Z]', line) and not any(kw in line.lower() for kw in ["engineer", "developer", "manager", "senior", "staff", "skills", "education"]):
                profile["name"] = line.title() if line == line.upper() else line
                break

    # Email
    email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', raw_text)
    if email_match:
        profile["email"] = email_match.group()

    # Phone
    phone_match = re.search(r'[\+]?[\d\s\-()]{10,}', raw_text)
    if phone_match:
        profile["phone"] = phone_match.group().strip()

    # LinkedIn
    linkedin_match = re.search(r'linkedin\.com/in/[\w-]+', raw_text)
    if linkedin_match:
        profile["linkedin"] = linkedin_match.group()

    # Education
    edu_patterns = [
        r'((?:B\.?S\.?|B\.?E\.?|B\.?Tech|M\.?S\.?|M\.?Sc|M\.?Tech|MBA|Ph\.?D)[\w\s,]+?)(?:\d{4})',
    ]
    for pattern in edu_patterns:
        for match in re.finditer(pattern, raw_text):
            year_match = re.search(r'\b(19|20)\d{2}\b', match.group())
            profile["education"].append({
                "degree": match.group(1).strip(),
                "university": "",
                "year": int(year_match.group()) if year_match else 0,
            })

    # Skills: look for skills section
    skills_section = ""
    in_skills = False
    for line in lines:
        if re.search(r'\bSKILLS?\b', line, re.IGNORECASE):
            in_skills = True
            continue
        if in_skills:
            if re.search(r'\b(EXPERIENCE|EDUCATION|PROJECTS|WORK)\b', line, re.IGNORECASE):
                break
            skills_section += " " + line

    if skills_section:
        # Split on common delimiters
        skill_tokens = re.split(r'[,·•|/\n]', skills_section)
        profile["primary_skills"] = [s.strip() for s in skill_tokens if 2 < len(s.strip()) < 40][:15]

    # If skills section was empty, try to extract from full text
    if not profile["primary_skills"]:
        known_skills = [
            "python", "java", "kotlin", "javascript", "typescript", "react", "node",
            "android", "ios", "flutter", "docker", "kubernetes", "aws", "gcp", "azure",
            "machine learning", "deep learning", "ai", "sql", "postgresql", "mongodb",
            "git", "ci/cd", "agile", "scrum", "rest api", "graphql", "redis",
            "spring", "django", "flask", "fastapi", "terraform", "jenkins",
        ]
        raw_lower = raw_text.lower()
        found = [s for s in known_skills if s in raw_lower]
        profile["primary_skills"] = found[:15]

    # Extract title from second line if available
    if not profile.get("title"):
        for line in lines[1:5]:
            line = line.strip()
            if line and any(kw in line.lower() for kw in ["engineer", "developer", "manager", "architect", "scientist", "lead"]):
                profile["title"] = line
                break

    # Extract years of experience
    yr_match = re.search(r'(\d{1,2})\+?\s*(?:years|yrs)', raw_text, re.IGNORECASE)
    if yr_match:
        profile["years_experience"] = int(yr_match.group(1))

    # Extract companies from text
    company_patterns = re.findall(r'(?:at|@)\s+([A-Z][A-Za-z\s.]+?)(?:\s*[,|]|\s*\d)', raw_text)
    if company_patterns:
        profile["companies_worked"] = [c.strip() for c in company_patterns[:10]]

    profile["domain_keywords"] = [s.lower() for s in profile["primary_skills"]]

    return profile


def refine_search_context(profile: dict, desired_role: str) -> dict:
    """
    Use LLM to refine the parsed profile + desired role into
    optimized search queries. Enforces:
    - Remote roles accessible from the applicant's location (e.g. India → global)
    - Contract/freelance preference
    - High-paying targets
    - Correct seniority level
    """
    location = profile.get("location", "India")
    prompt = f"""You are a job search strategist. Given this candidate profile and desired role, generate optimized job search parameters.

CANDIDATE PROFILE:
Name: {profile.get('name', 'Unknown')}
Title: {profile.get('title', '')}
Experience: {profile.get('years_experience', 0)} years
Location: {location}
Skills: {', '.join(profile.get('primary_skills', [])[:15])}
Experience highlights: {'; '.join(profile.get('experience_highlights', [])[:5])}

DESIRED ROLE: {desired_role}

CONSTRAINTS:
- The candidate is based in {location} and wants REMOTE roles working for global companies in US, Europe, Australia, New Zealand, Singapore, UK, Canada
- Preference for CONTRACT / FREELANCE roles (but include full-time remote too)
- Target HIGH-PAYING roles (senior/staff/principal level compensation)
- Exclude jobs that require US/EU residency or visa sponsorship
- All search queries must include "remote" to ensure remote-only results

Generate search parameters. Respond ONLY in this exact JSON format:
{{
    "search_queries": ["<query1>", "<query2>", ... exactly 10 diverse search queries for remote job APIs, each must include "remote"],
    "target_regions": ["<region1>", "<region2>", ... target regions accessible from {location}],
    "role_keywords": ["<kw1>", "<kw2>", ... 10 most relevant role keywords],
    "seniority_target": "<junior/mid/senior/staff/principal>",
    "role_type_preference": ["contract", "freelance", "full time"],
    "refined_title": "<a clear, searchable job title>",
    "search_summary": "<one-line summary of what we're searching for>"
}}"""

    response = call_gemini(prompt, max_tokens=2000, temperature=0.3)
    default = {
        "search_queries": [
            f"remote {desired_role}",
            f"remote {desired_role} contract",
            f"remote senior {desired_role}",
            f"remote staff {desired_role}",
            f"remote {desired_role} worldwide",
            f"remote {desired_role} global",
            f"remote {desired_role} freelance",
            f"{desired_role} work from anywhere",
            f"remote {desired_role} high paying",
            f"remote contract {desired_role} international",
        ],
        "target_regions": ["US", "USA", "Europe", "EU", "Australia", "New Zealand", "Singapore", "UK", "Canada", "Global", "Worldwide"],
        "role_keywords": desired_role.lower().split(),
        "seniority_target": "senior",
        "role_type_preference": ["contract", "freelance", "full time"],
        "refined_title": desired_role,
        "search_summary": f"Searching for remote {desired_role} roles (contract preferred) accessible from {location}",
    }

    if not response:
        return default

    # Parse
    response = response.strip()
    if response.startswith("```"):
        lines = response.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        response = "\n".join(lines)

    try:
        result = json.loads(response)
        # Merge with defaults for any missing keys
        for k, v in default.items():
            result.setdefault(k, v)
        return result
    except json.JSONDecodeError:
        start = response.find("{")
        end = response.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                result = json.loads(response[start:end])
                for k, v in default.items():
                    result.setdefault(k, v)
                return result
            except json.JSONDecodeError:
                pass
    return default


def _empty_profile() -> dict:
    return {
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
