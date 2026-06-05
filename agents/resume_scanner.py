"""Fast Resume Scanner — lightweight resume analysis for role recommendations.

Called immediately after file upload. Returns:
  - recommended_roles: personalized role suggestions with seniority
  - experience_years: detected years of experience
  - experience_level: junior/mid/senior/staff/principal
  - summary: one-line candidate summary
  - domain: primary work domain (mobile, backend, ML, etc.)
  - profile: structured candidate profile (name, title, skills, etc.)
  - search_queries: pre-built search queries ready for scraping

This single scan replaces both parse_resume_to_profile() and refine_search_context()
in the pipeline, saving ~5 min of LLM time on local models.
"""

from __future__ import annotations

import json
import re

from agents.llm_client import call_llm


def fast_scan_resume(raw_text: str, mode: str = "full") -> dict:
    """Resume scan — role recommendations + profile + search queries.

    Two modes (latency vs completeness):
      - mode="quick": minimal JSON for the onboarding UI (chips + scraping inputs).
        Drops verbose token-hog fields (experience_entries bullets, education).
        ~400 output tokens → ~5s on a local 4B model.
      - mode="full": complete profile incl. experience_entries + education.
        ~2500 output tokens → ~20s. Used to replace parse_resume_to_profile()
        and refine_search_context() in the pipeline.

    The route runs "quick" synchronously (fast chips) then "full" in a
    background thread to upgrade the cached result for the pipeline.
    """
    text = raw_text[:6000]
    prompt = _quick_prompt(text) if mode == "quick" else _full_prompt(text)
    max_tokens = 800 if mode == "quick" else 3000

    response = call_llm(prompt, max_tokens=max_tokens, temperature=0.2)

    # Heuristic fallback
    fallback = _heuristic_scan(raw_text)

    if not response:
        return fallback

    result = _parse_json_response(response)
    if not result:
        return fallback

    # Merge with heuristic fallback for any missing fields
    _fill_defaults(result, fallback, raw_text)

    return result


_SENIORITY_RULES = """CRITICAL SENIORITY RULES:
- 0-2 years → Junior/Associate prefix
- 3-5 years → no prefix (mid-level)
- 6-9 years → Senior prefix
- 10-14 years → Staff / Senior Lead prefix
- 15+ years → Principal / Distinguished prefix
- NEVER suggest roles below their seniority level"""


def _quick_prompt(text: str) -> str:
    """Minimal prompt — only fields the UI shows + scraper needs. Tiny output → fast.

    search_queries, seniority_keywords and exclude_keywords are derived in code
    by _fill_defaults, so the model does NOT emit them. Keeping the output small
    (~250 tokens) is what makes this fast on a local 4B model.
    """
    return f"""You are a senior tech recruiter. Analyze this resume quickly.

RESUME TEXT:
{text}

{_SENIORITY_RULES}

Respond ONLY in this compact JSON (no markdown, no explanation). Keep values SHORT:
{{
    "name": "<full name>",
    "title": "<current job title>",
    "experience_years": <number>,
    "experience_level": "<junior|mid|senior|staff|principal>",
    "domain": "<one of: mobile|backend|full-stack|ML-AI|DevOps|data|product>",
    "summary": "<short one-liner, max 12 words>",
    "primary_skills": ["<skill>", "<skill>", "<skill>", "<skill>", "<skill>", "<skill>"],
    "recommended_roles": ["<r1 w/ seniority>", "<r2>", "<r3>", "<r4>", "<r5>", "<r6>", "<r7>", "<r8>"]
}}"""


def _full_prompt(text: str) -> str:
    """Complete prompt — full structured profile incl. experience + education."""
    return f"""You are a senior tech recruiter and resume parser. Analyze this resume completely.

RESUME TEXT:
{text}

Do ALL of the following in ONE response:

A) PARSE the resume into a structured profile
B) DETERMINE experience level and recommend 8 job roles
C) GENERATE 12 optimized search queries for remote job boards

{_SENIORITY_RULES}

Respond ONLY in this exact JSON format (no markdown, no explanation):
{{
    "name": "<full name>",
    "title": "<current/most recent job title>",
    "experience_years": <number>,
    "experience_level": "<junior|mid|senior|staff|principal>",
    "location": "<city, country or empty>",
    "email": "<email or empty>",
    "phone": "<phone or empty>",
    "domain": "<primary domain: mobile/backend/full-stack/ML-AI/DevOps/data/product>",
    "summary": "<one-line: Name — Title, X years, key strengths>",
    "primary_skills": ["<skill1>", "<skill2>", ... up to 15 key skills],
    "domain_keywords": ["<kw1>", "<kw2>", ... up to 20 searchable keywords],
    "companies_worked": ["<company1>", "<company2>", ...],
    "experience_entries": [
        {{"company": "<name>", "title": "<title>", "dates": "<range>", "bullets": ["<b1>", "<b2>"]}}
    ],
    "education": [
        {{"degree": "<degree>", "university": "<university>", "year": <year>}}
    ],
    "recommended_roles": [
        "<role1 with seniority>", "<role2>", "<role3>", "<role4>",
        "<role5>", "<role6>", "<role7>", "<role8>"
    ],
    "search_queries": [
        "remote <role1>",
        "remote <role1> contract",
        "remote <role2>",
        "remote <role2> freelance",
        "remote <role3>",
        "remote <skill1> <skill2> engineer",
        "remote <domain> developer worldwide",
        "remote <seniority> <domain> engineer",
        "<seniority> <role1> work from anywhere",
        "remote <role1> global",
        "remote <domain> architect contract",
        "remote <seniority> engineer high paying"
    ],
    "seniority_keywords": ["<keyword1>", "<keyword2>"],
    "exclude_keywords": ["<keyword1>", "<keyword2>"]
}}"""


def _parse_json_response(response: str) :
    """Parse LLM JSON response with cleanup."""
    response = response.strip()
    if response.startswith("```"):
        lines = response.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        response = "\n".join(lines)

    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass

    # Extract the FIRST balanced {...} object. Models sometimes emit the object
    # twice ("}{") or add trailing prose — naive first-{/last-} grabs both and
    # fails with "Extra data".
    first = _first_json_object(response)
    if first is not None:
        return first

    # Repair truncated JSON (cut off by max_tokens): close open brackets so we
    # keep whatever fields arrived.
    return _repair_truncated_json(response)


def _first_json_object(response: str) :
    """Return the first complete, balanced JSON object in the string."""
    start = response.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(response)):
        c = response[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(response[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _repair_truncated_json(response: str) :
    """Best-effort close of a JSON object cut off mid-generation."""
    start = response.find("{")
    if start < 0:
        return None
    s = response[start:]
    # Drop a trailing partial token after the last complete value.
    last = max(s.rfind('"'), s.rfind("]"), s.rfind("}"))
    if last < 0:
        return None
    s = s[: last + 1]
    # Balance brackets.
    depth_obj = s.count("{") - s.count("}")
    depth_arr = s.count("[") - s.count("]")
    s = s.rstrip().rstrip(",")
    s += "]" * max(0, depth_arr) + "}" * max(0, depth_obj)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def _fill_defaults(result: dict, fallback: dict, raw_text: str):
    """Fill missing fields from heuristic fallback."""
    result.setdefault("name", "")
    result.setdefault("title", "")
    result.setdefault("experience_years", fallback["experience_years"])
    result.setdefault("experience_level", fallback["experience_level"])
    result.setdefault("location", "")
    result.setdefault("email", "")
    result.setdefault("phone", "")
    result.setdefault("domain", "software engineering")
    result.setdefault("summary", "")
    result.setdefault("primary_skills", [])
    # Quick scan omits domain_keywords — derive from skills + domain so the
    # scraper's keyword pre-filter still has signal until the full scan lands.
    if not result.get("domain_keywords"):
        kws = list(result.get("primary_skills", []))
        dom = result.get("domain", "")
        if dom:
            kws.append(dom)
        result["domain_keywords"] = kws
    result.setdefault("companies_worked", [])
    result.setdefault("experience_entries", [])
    result.setdefault("education", [])
    result.setdefault("recommended_roles", fallback["recommended_roles"])
    result.setdefault("search_queries", fallback.get("search_queries", []))
    result.setdefault("seniority_keywords", _seniority_keywords(result["experience_level"]))
    result.setdefault("exclude_keywords", _exclude_keywords(result["experience_level"]))

    # If name missing, try heuristic extraction
    if not result["name"]:
        name = _extract_name(raw_text)
        if name:
            result["name"] = name

    # Keep the model's personalized roles; pad (don't replace) if too few.
    roles = [r for r in result.get("recommended_roles", []) if r and r.strip()]
    if len(roles) < 4:
        seen = {r.lower() for r in roles}
        for r in fallback["recommended_roles"]:
            if r.lower() not in seen:
                roles.append(r)
                seen.add(r.lower())
            if len(roles) >= 8:
                break
    result["recommended_roles"] = roles[:8]

    # Ensure search queries exist
    if len(result.get("search_queries", [])) < 4:
        result["search_queries"] = _build_search_queries(
            result["recommended_roles"],
            result["experience_level"],
            result.get("primary_skills", []),
        )


def _extract_name(raw_text: str) -> str:
    """Quick regex name extraction from first few lines."""
    for line in raw_text.split("\n")[:5]:
        line = line.strip()
        if line and len(line) < 60 and not any(c in line for c in ["@", "http", "+", "(", ")"]):
            if re.match(r'^[A-Z][A-Za-z]+\s+[A-Z]', line):
                if not any(kw in line.lower() for kw in ["engineer", "developer", "manager", "senior", "staff", "skills"]):
                    return line.title() if line == line.upper() else line
    return ""


def _build_search_queries(roles: list[str], level: str, skills: list[str]) -> list[str]:
    """Build search queries from roles + skills when LLM didn't provide them."""
    queries = []
    for role in roles[:4]:
        queries.append(f"remote {role}")
        queries.append(f"remote {role} contract")
    if skills:
        top_skills = " ".join(skills[:3])
        queries.append(f"remote {top_skills} engineer")
        queries.append(f"remote {top_skills} developer worldwide")
    prefix = _seniority_prefix(level).strip()
    if prefix:
        queries.append(f"remote {prefix.lower()} engineer global")
        queries.append(f"remote {prefix.lower()} developer work from anywhere")
    return queries[:12]


def _heuristic_scan(raw_text: str) -> dict:
    """Regex fallback for experience detection."""
    # Detect years
    years = 0
    yr_match = re.search(r'(\d{1,2})\+?\s*(?:years|yrs)', raw_text, re.IGNORECASE)
    if yr_match:
        years = int(yr_match.group(1))
    else:
        # Count distinct year ranges in experience entries
        year_mentions = re.findall(r'\b(20\d{2}|19\d{2})\b', raw_text)
        if len(year_mentions) >= 2:
            year_ints = sorted(set(int(y) for y in year_mentions))
            years = max(year_ints) - min(year_ints)

    level = _level_from_years(years)
    prefix = _seniority_prefix(level)

    return {
        "experience_years": years,
        "experience_level": level,
        "domain": "software engineering",
        "summary": "",
        "recommended_roles": [
            f"{prefix}Software Engineer",
            f"{prefix}Full Stack Developer",
            f"{prefix}Backend Engineer",
            f"{prefix}Frontend Engineer",
            f"{prefix}Mobile Engineer",
            f"{prefix}DevOps Engineer",
            f"{prefix}Data Engineer",
            f"{prefix}Platform Engineer",
        ],
        "seniority_keywords": _seniority_keywords(level),
        "exclude_keywords": _exclude_keywords(level),
        "search_queries": _build_search_queries(
            [f"{prefix}Software Engineer", f"{prefix}Full Stack Developer",
             f"{prefix}Backend Engineer", f"{prefix}Frontend Engineer"],
            level, [],
        ),
    }


def _level_from_years(years: int) -> str:
    if years >= 15:
        return "principal"
    elif years >= 10:
        return "staff"
    elif years >= 6:
        return "senior"
    elif years >= 3:
        return "mid"
    else:
        return "junior"


def _seniority_prefix(level: str) -> str:
    return {
        "principal": "Principal ",
        "staff": "Staff ",
        "senior": "Senior ",
        "mid": "",
        "junior": "Junior ",
    }.get(level, "Senior ")


def _seniority_keywords(level: str) -> list[str]:
    """Keywords to ADD to search queries to target correct seniority."""
    return {
        "principal": ["principal", "distinguished", "director", "fellow", "VP"],
        "staff": ["staff", "lead", "architect", "senior staff", "tech lead"],
        "senior": ["senior", "lead", "sr"],
        "mid": [],
        "junior": ["junior", "entry level", "associate", "graduate"],
    }.get(level, ["senior"])


def _exclude_keywords(level: str) -> list[str]:
    """Title keywords that indicate too-junior roles for this level."""
    return {
        "principal": ["junior", "entry level", "associate", "intern", "graduate", "mid-level"],
        "staff": ["junior", "entry level", "associate", "intern", "graduate"],
        "senior": ["junior", "entry level", "intern", "graduate"],
        "mid": ["intern"],
        "junior": [],
    }.get(level, [])
