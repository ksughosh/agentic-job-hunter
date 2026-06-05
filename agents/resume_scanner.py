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

from agents.llm_client import (
    call_provider,
    get_provider,
    is_cloud,
    available_local_provider,
)


def fast_scan_resume(raw_text: str, mode: str = "full") -> dict:
    """Resume scan with explicit cascade:

      1. Local resume parsing without LM (always — heuristic baseline)
      2. Active provider's LLM (cloud OR local, whichever user picked)
      3. If active was cloud AND it failed AND a local LM is reachable
         → retry against that local LM
      4. Otherwise → return the heuristic-only result

    The LLM result, when present, is merged ON TOP of the heuristic baseline
    via _fill_defaults so partial responses still get useful defaults (years,
    seniority, name) from the regex parser.

    Two latency modes:
      - mode="quick": minimal JSON (~400 tok) for the onboarding UI.
      - mode="full":  complete profile (~2500 tok) for the pipeline.
    """
    text = raw_text[:6000]
    prompt = _quick_prompt(text) if mode == "quick" else _full_prompt(text)
    max_tokens = 800 if mode == "quick" else 3000

    # Step 1 — local heuristic baseline. Cheap, always succeeds.
    baseline = _heuristic_scan(raw_text)

    # Step 2 — active provider.
    active = get_provider()
    print(f"[ScanResume] cascade start: active={active}, mode={mode}", flush=True)
    response = call_provider(active, prompt, max_tokens=max_tokens, temperature=0.2)

    # Step 3 — fall through to local LM only if active was cloud.
    if not response and is_cloud(active):
        local = available_local_provider()
        if local:
            print(f"[ScanResume] {active} returned empty; trying local {local}", flush=True)
            response = call_provider(local, prompt, max_tokens=max_tokens, temperature=0.2)
        else:
            print(f"[ScanResume] {active} returned empty; no local LM available", flush=True)

    # Step 4 — if every LM path failed, return the heuristic-only result.
    if not response:
        print("[ScanResume] all LMs failed; returning heuristic baseline", flush=True)
        return baseline

    result = _parse_json_response(response)
    if not result:
        print("[ScanResume] LM response unparseable; returning heuristic baseline", flush=True)
        return baseline

    # Merge LM output on top of the heuristic baseline so missing fields get
    # a sensible default rather than disappearing.
    _fill_defaults(result, baseline, raw_text)
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
    return f"""You are an experienced recruiter. Analyze this resume quickly.
The candidate may be from ANY profession (engineering, finance, law, medicine,
design, marketing, sales, operations, etc.). Infer the actual profession from
the resume — do NOT assume software engineering.

RESUME TEXT:
{text}

{_SENIORITY_RULES}

Respond ONLY in this compact JSON (no markdown, no explanation). Keep values SHORT:
{{
    "name": "<full name>",
    "title": "<current job title — use candidate's actual title>",
    "experience_years": <number>,
    "experience_level": "<junior|mid|senior|staff|principal>",
    "domain": "<candidate's actual primary domain — open-ended, e.g. finance, audit, software, mobile, marketing, legal, medicine>",
    "summary": "<short one-liner, max 12 words>",
    "primary_skills": ["<skill>", "<skill>", "<skill>", "<skill>", "<skill>", "<skill>"],
    "recommended_roles": ["<r1 must match candidate's profession w/ seniority>", "<r2>", "<r3>", "<r4>", "<r5>", "<r6>", "<r7>", "<r8>"]
}}"""


def _full_prompt(text: str) -> str:
    """Complete prompt — full structured profile incl. experience + education.

    Domain-agnostic: works for any profession (engineering, accounting, law,
    medicine, design, marketing, etc.). The recruiter persona is generic and
    the domain field is open-ended.
    """
    return f"""You are an experienced recruiter and resume parser. Analyze this resume completely.
The candidate can be from ANY profession — software, finance, law, medicine, design,
marketing, operations, sales, etc. Do NOT assume software engineering. Infer the
candidate's actual profession from the resume content.

RESUME TEXT:
{text}

Do ALL of the following in ONE response:

A) PARSE the resume into a structured profile
B) DETERMINE experience level and recommend 8 job roles relevant to THIS candidate's profession
C) GENERATE 12 optimized search queries for job boards — use the candidate's own job
   titles and skill terminology, never default to "engineer" or "developer" unless the
   resume actually shows software engineering work

{_SENIORITY_RULES}

Respond ONLY in this exact JSON format (no markdown, no explanation):
{{
    "name": "<full name>",
    "title": "<current/most recent job title — use the candidate's actual title>",
    "experience_years": <number>,
    "experience_level": "<junior|mid|senior|staff|principal>",
    "location": "<city, country or empty>",
    "email": "<email or empty>",
    "phone": "<phone or empty>",
    "domain": "<candidate's actual primary domain — e.g. finance, audit, software, mobile, marketing, legal, medicine, design>",
    "summary": "<one-line: Name — Title, X years, key strengths>",
    "primary_skills": ["<skill1>", "<skill2>", ... up to 15 key skills from the resume],
    "domain_keywords": ["<kw1>", "<kw2>", ... up to 20 searchable keywords drawn from the resume's vocabulary],
    "companies_worked": ["<company1>", "<company2>", ...],
    "experience_entries": [
        {{"company": "<name>", "title": "<title>", "dates": "<range>", "bullets": ["<b1>", "<b2>"]}}
    ],
    "education": [
        {{"degree": "<degree>", "university": "<university>", "year": <year>}}
    ],
    "recommended_roles": [
        "<role1 with seniority — must match candidate's profession>", "<role2>", "<role3>", "<role4>",
        "<role5>", "<role6>", "<role7>", "<role8>"
    ],
    "search_queries": [
        "remote <role1>",
        "remote <role1> contract",
        "remote <role2>",
        "remote <role2> freelance",
        "remote <role3>",
        "remote <skill1> <skill2>",
        "remote <domain> <role1> worldwide",
        "remote <seniority> <domain>",
        "<seniority> <role1> work from anywhere",
        "remote <role1> global",
        "remote <domain> <role2> contract",
        "remote <seniority> <role1> high paying"
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
    # Domain stays empty unless the LLM (or a domain-agnostic heuristic) filled
    # it. We must NOT default to "software engineering" — that biases the whole
    # downstream pipeline (vectorizer corpus, query builder, JD reviewer) toward
    # tech jobs even for non-tech profiles (CA, doctor, designer, etc.).
    result.setdefault("domain", "")
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
    """Build domain-agnostic search queries from roles + skills.

    Used when the LLM doesn't return its own queries. Never hardcodes 'engineer'
    or 'developer' — we only know the candidate's actual roles and skills.
    """
    queries = []
    for role in roles[:6]:
        queries.append(f"remote {role}")
        queries.append(f"remote {role} contract")
    if skills:
        top_skills = " ".join(skills[:3])
        queries.append(f"remote {top_skills}")
        queries.append(f"remote {top_skills} worldwide")
    prefix = _seniority_prefix(level).strip()
    if prefix and roles:
        first_role = roles[0]
        # Strip any existing prefix to avoid "Senior Senior Auditor".
        plain = re.sub(r"^(senior|staff|principal|junior|lead)\s+", "", first_role, flags=re.IGNORECASE)
        queries.append(f"remote {prefix} {plain}".strip())
    # Deduplicate while preserving order.
    seen = set()
    out = []
    for q in queries:
        ql = q.lower()
        if ql not in seen:
            out.append(q)
            seen.add(ql)
    return out[:12]


def _heuristic_scan(raw_text: str) -> dict:
    """Regex fallback for experience detection.

    Domain-agnostic: we extract what we can (years, level) but do NOT inject
    engineering role defaults. If the LLM is unreachable the user sees an empty
    recommendation list rather than wrong recommendations (which is far worse
    for non-tech profiles — a Chartered Accountant should never see
    'Software Engineer' as a fallback).
    """
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

    return {
        "experience_years": years,
        "experience_level": level,
        "domain": "",
        "summary": "",
        "recommended_roles": [],
        "primary_skills": [],
        "domain_keywords": [],
        "seniority_keywords": _seniority_keywords(level),
        "exclude_keywords": _exclude_keywords(level),
        "search_queries": [],
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
