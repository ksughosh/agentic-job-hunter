"""
Agent 3: Job Description Reviewer
1. Semantically matches JD requirements against resume with a match score
2. Uses Gemini LLM as expert headhunter for intelligent JD-profile matching
3. Reviews job listing quality (0-5) using company reviewer feedback
4. Sorts by salary with preference for contract roles + mobile/GenAI background
5. Filters by seniority level — targets Staff/Principal/Architect, penalizes junior/mid
"""

import re
from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from agents.gemini_client import gemini_jd_match, call_gemini
from config import CANDIDATE_PROFILE


@dataclass
class JDReviewResult:
    job_title: str
    company: str
    match_score: float  # 0-100
    listing_quality: float  # 0-5
    salary_score: float  # 0-100
    contract_bonus: float  # 0-20
    genai_mobile_bonus: float  # 0-20
    seniority_score: float  # 0-100
    composite_score: float  # 0-100
    skill_matches: list
    skill_gaps: list
    strengths: list
    salary_min: float
    salary_max: float
    job_url: str
    source: str
    job_type: str
    location: str
    company_rating: float
    seniority_level: str  # detected level
    source_quality: int = 50  # source conversion score 0-100
    location_score: float = 50  # 0-100, how well location matches
    work_mode_score: float = 50  # 0-100, how well work mode matches
    llm_reasoning: str = ""  # Gemini headhunter reasoning
    llm_strengths: list = None  # Gemini-identified strengths
    llm_gaps: list = None  # Gemini-identified gaps
    rank: int = 0


RESUME_SKILLS = {
    "core_technical": [
        "android", "kotlin", "java", "jetpack compose", "flutter", "dart",
        "python", "sdk development", "mobile architecture", "mvvm", "mvp",
        "clean architecture", "coroutines", "flow", "room", "retrofit",
        "dagger", "hilt", "koin", "gradle", "firebase",
    ],
    "ai_ml": [
        "generative ai", "genai", "llm", "large language model", "agentic ai",
        "machine learning", "deep learning", "ai agents", "autonomous systems",
        "code generation", "ai developer tools", "mlops", "ai infrastructure",
        "prompt engineering", "rag", "fine-tuning", "transformer",
    ],
    "infrastructure": [
        "ci/cd", "continuous integration", "continuous delivery",
        "performance engineering", "profiling", "memory optimization",
        "build systems", "release management", "devops", "docker",
        "kubernetes", "monitoring", "observability",
    ],
    "leadership": [
        "staff engineer", "senior staff", "tech lead", "team lead",
        "principal engineer", "architect", "engineering manager",
        "architecture", "system design", "mentoring", "code review",
        "design review", "cross-functional", "stakeholder management",
    ],
    "design_tools": [
        "figma", "design system", "component library", "accessibility",
        "ui/ux", "material design", "figma to code",
    ],
    "cross_platform": [
        "flutter", "react native", "cross-platform", "multi-platform",
        "kmp", "kotlin multiplatform",
    ],
}

SKILL_WEIGHTS = {
    "core_technical": 0.25,
    "ai_ml": 0.25,
    "infrastructure": 0.12,
    "leadership": 0.20,
    "design_tools": 0.08,
    "cross_platform": 0.10,
}

RESUME_TEXT = """
Senior Staff Software Engineer with 13+ years experience in mobile platforms at scale.
Specializing in architecting autonomous AI systems for mobile agentic workflow and
generative AI infrastructure for mobile teams. Expert in Android engineering, developer
tooling, and AI products from inception to launch.

Skills: Agentic AI Systems, GenAI/LLM, AI Developer Tooling, Autonomous Code Generation,
Mobile AI Infrastructure, Android Architecture, CI/CD & MLOps, Performance Engineering,
Kotlin, Python, System Design, Flutter, Jetpack Compose, SDK Development.

Experience at PocketFM: Architected continuous release monitor, AI agents for Android
performance analysis, Figma-to-Jetpack-Compose code generators, AI-driven performance
gating in CI/CD pipelines.

Experience at Junglee Games: Automated UI generation Figma to code, Led Flutter desktop
team, cross-platform architecture, Agentic AI tools for code generation and reviews.

Experience at Bill.com/Invoice2go: Led Android team, security initiatives, design system,
release processes, instrumental in acquisition.

Experience at Canva: Architected Canva Android SDK, share-to-Canva feature 84% activation,
cross-platform web integrations.

Education: MSc Media Informatics RWTH Aachen University Germany, BE Computer Science VTU.
Published research on gaze-contingent rendering at Carl Zeiss Smart Optics.
"""

SENIORITY_TIERS = {
    "principal": {
        "patterns": [
            r"\bprincipal\b", r"\bdistinguished\b", r"\bfellow\b",
            r"\bvp\s+of\s+engineering\b", r"\bvp\s+engineering\b",
            r"\bchief\s+(technology|architect|engineer)\b",
        ],
        "score": 100,
        "label": "Principal/Distinguished",
    },
    "staff": {
        "patterns": [
            r"\bstaff\b", r"\bsenior\s+staff\b", r"\barchitect\b",
            r"\bplatform\s+lead\b", r"\bengineering\s+lead\b",
            r"\btechnical\s+director\b", r"\btech\s+director\b",
            r"\bhead\s+of\s+(engineering|mobile|android|ai|platform)\b",
            r"\bdirector\s+of\s+engineering\b",
        ],
        "score": 95,
        "label": "Staff/Architect",
    },
    "senior_lead": {
        "patterns": [
            r"\bsenior\b.*\blead\b", r"\blead\b.*\bsenior\b",
            r"\btech\s*lead\b", r"\bteam\s*lead\b",
            r"\bengineering\s+manager\b",
        ],
        "score": 75,
        "label": "Senior Lead",
    },
    "senior": {
        "patterns": [
            r"\bsenior\b", r"\bsr\.?\b", r"\bsenior\s+(software|mobile|android|ai|ml|platform)\b",
        ],
        "score": 50,
        "label": "Senior",
    },
    "mid": {
        "patterns": [
            r"\bmid[\s-]?level\b", r"\bintermediate\b",
            r"(?:^|\b)(?:software|mobile|android|full[\s-]?stack)\s+engineer(?:ing)?\b",
        ],
        "score": 15,
        "label": "Mid-level",
    },
    "junior": {
        "patterns": [
            r"\bjunior\b", r"\bjr\.?\b", r"\bentry[\s-]?level\b",
            r"\bintern\b", r"\bassociate\b", r"\bgraduate\b",
        ],
        "score": 0,
        "label": "Junior/Entry",
    },
}

EXPERIENCE_YEAR_PATTERNS = [
    (r"\b1[2-9]\+?\s*(?:years|yrs)\b", 90),
    (r"\b[89]\+?\s*(?:years|yrs)\b", 70),
    (r"\b10\+?\s*(?:years|yrs)\b", 85),
    (r"\b[67]\+?\s*(?:years|yrs)\b", 55),
    (r"\b[45]\+?\s*(?:years|yrs)\b", 35),
    (r"\b[1-3]\+?\s*(?:years|yrs)\b", 10),
]


class JDReviewerAgent:
    """Reviews job descriptions against candidate profile, with LLM-powered headhunter matching."""

    def __init__(self, use_llm: bool = True, search_context=None, profile=None):
        """Init reviewer.

        profile: candidate profile dict from data_svc.load_profile() — used to
        build the TF-IDF corpus and skill buckets dynamically. If omitted the
        agent falls back to the legacy module-level RESUME_TEXT/RESUME_SKILLS
        constants (kept only for backward compatibility; should NOT be relied
        on in normal operation).
        """
        self._use_llm = use_llm
        self._ctx = search_context or {}
        self._profile = profile or {}
        # Parse work modes from comma-sep string or list.
        wm = self._ctx.get("work_mode", "remote")
        self._work_modes = set(
            m.strip().lower() for m in (wm.split(",") if isinstance(wm, str) else wm) if m.strip()
        )
        self._location = (self._ctx.get("location") or "Anywhere").strip()
        self._is_remote = "remote" in self._work_modes or "any" in self._work_modes
        self.vectorizer = TfidfVectorizer(
            stop_words="english",
            ngram_range=(1, 2),
            max_features=5000,
        )
        self._fit_vectorizer()
        self._llm_call_count = 0

    def _build_dynamic_corpus(self) -> tuple:
        """Build (resume_text, skill_buckets) from the candidate profile.

        Domain-agnostic: works equally for a Chartered Accountant, a doctor, a
        Staff Mobile Engineer, etc. We never inject hardcoded engineering
        skills here — the buckets are derived from whatever primary_skills /
        domain_keywords / experience_entries the resume actually contains.
        """
        p = self._profile or {}
        parts = []
        if p.get("summary"): parts.append(p["summary"])
        if p.get("title"): parts.append(p["title"])
        if p.get("name"): parts.append(p["name"])
        prims = [str(s) for s in (p.get("primary_skills") or []) if s]
        doms  = [str(s) for s in (p.get("domain_keywords") or []) if s]
        if prims: parts.append("Skills: " + ", ".join(prims))
        if doms:  parts.append("Domain: " + ", ".join(doms))
        for entry in (p.get("experience_entries") or []):
            if isinstance(entry, dict):
                bits = [entry.get("title", ""), entry.get("company", "")]
                bits += entry.get("bullets", []) or []
                parts.append(" ".join(str(b) for b in bits if b))
        for edu in (p.get("education") or []):
            if isinstance(edu, dict):
                parts.append(" ".join(str(edu.get(k, "")) for k in ("degree", "university", "field")))
        resume_text = "\n".join(p_ for p_ in parts if p_).strip()

        # Build skill buckets from the resume itself.
        buckets = {}
        if prims:
            buckets["primary"] = [s.lower() for s in prims]
        if doms:
            buckets["domain"] = [s.lower() for s in doms]
        return resume_text, buckets

    def _fit_vectorizer(self):
        dyn_text, dyn_buckets = self._build_dynamic_corpus()
        # Fall back to legacy constants only if profile produced nothing usable.
        if dyn_text:
            self._resume_text = dyn_text
            self._skill_buckets = dyn_buckets or {"primary": []}
            self._using_dynamic = True
        else:
            self._resume_text = RESUME_TEXT
            self._skill_buckets = RESUME_SKILLS
            self._using_dynamic = False
        all_skills = []
        for bucket in self._skill_buckets.values():
            all_skills.extend(bucket)
        corpus = [self._resume_text, " ".join(all_skills)] if all_skills else [self._resume_text]
        self.vectorizer.fit(corpus)
        self.resume_vector = self.vectorizer.transform([self._resume_text])

    def review_job(self, job, company_review=None) -> JDReviewResult:
        def _str(val):
            if isinstance(val, list):
                return " ".join(str(x) for x in val)
            return str(val) if val else ""

        title = _str(job.get("title", "") if isinstance(job, dict) else job.title)
        company = _str(job.get("company", "") if isinstance(job, dict) else job.company)
        desc = _str(job.get("description", "") if isinstance(job, dict) else job.description)
        url = _str(job.get("url", "") if isinstance(job, dict) else job.url)
        source = _str(job.get("source", "") if isinstance(job, dict) else job.source)
        job_type = _str(job.get("job_type", "") if isinstance(job, dict) else job.job_type)
        location = _str(job.get("location", "") if isinstance(job, dict) else job.location)
        salary_min = job.get("salary_min", 0) if isinstance(job, dict) else job.salary_min
        salary_max = job.get("salary_max", 0) if isinstance(job, dict) else job.salary_max
        tags = job.get("tags", []) if isinstance(job, dict) else job.tags
        if not isinstance(tags, list):
            tags = [str(tags)]

        match_score, skill_matches, skill_gaps = self._semantic_match(title, desc, tags)
        listing_quality = self._assess_listing_quality(job, company_review)
        salary_score = self._score_salary(salary_min, salary_max, job_type)
        contract_bonus = self._contract_bonus(job_type)
        genai_mobile_bonus = self._genai_mobile_bonus(title, desc, tags)
        seniority_score, seniority_level = self._score_seniority(title, desc)
        loc_score = self._score_location(location)
        wm_score = self._score_work_mode(job_type, location)

        # LLM-powered headhunter match (Gemini)
        llm_reasoning = ""
        llm_strengths = []
        llm_gaps = []
        llm_score = match_score  # fallback to TF-IDF score
        if self._use_llm:
            job_data = {
                "title": title, "company": company, "description": desc[:2000],
                "job_type": job_type, "location": location,
                "salary_min": salary_min, "salary_max": salary_max,
            }
            llm_result = gemini_jd_match(CANDIDATE_PROFILE, job_data)
            if llm_result.get("match_score", 0) > 0:
                llm_score = llm_result["match_score"]
                llm_reasoning = llm_result.get("reasoning", "")
                llm_strengths = llm_result.get("strengths", [])
                llm_gaps = llm_result.get("gaps", [])
                # Blend: 60% LLM + 40% TF-IDF
                match_score = llm_score * 0.6 + match_score * 0.4

        # Source quality score (0-100)
        from agents.job_scraper import SOURCE_QUALITY
        src_quality = job.get("source_quality", 0) if isinstance(job, dict) else getattr(job, "source_quality", 0)
        if not src_quality:
            src_quality = SOURCE_QUALITY.get(source, 50)

        # ── Weighted composite (user-specified priority order) ──
        #   1. match_score (keyword overlap + LLM) — strongest signal
        #   2. work_mode_score — does it match remote/hybrid/onsite preference
        #   3. salary_score — contract=hourly, fulltime=annual, context-aware
        #   4. location_score — anywhere+remote=high, resume-location=high
        #   5. seniority — level fit
        #   6. role_type (contract_bonus) — remote+contract(part-time)=high
        #   7. listing_quality + source_quality — tiebreakers
        composite = (
            match_score * 0.25 +        # keyword/LLM match is king
            wm_score * 0.15 +           # work mode alignment
            salary_score * 0.14 +       # pay, context-aware
            loc_score * 0.12 +          # location fit
            seniority_score * 0.12 +    # seniority match
            contract_bonus * 5 * 0.08 + # role type preference
            genai_mobile_bonus * 3 * 0.06 +  # domain bonus
            listing_quality * 15 * 0.04 +    # quality tiebreaker
            src_quality * 0.04                # source reputation
        )

        strengths = self._identify_strengths(title, desc, skill_matches, seniority_level)
        company_rating = 0
        if company_review:
            company_rating = company_review.weighted_score if hasattr(company_review, "weighted_score") else company_review.get("weighted_score", 0)

        return JDReviewResult(
            job_title=title,
            company=company,
            match_score=round(match_score, 1),
            listing_quality=round(listing_quality, 1),
            salary_score=round(salary_score, 1),
            contract_bonus=round(contract_bonus, 1),
            genai_mobile_bonus=round(genai_mobile_bonus, 1),
            seniority_score=round(seniority_score, 1),
            composite_score=round(composite, 1),
            skill_matches=skill_matches,
            skill_gaps=skill_gaps[:5],
            llm_reasoning=llm_reasoning,
            llm_strengths=llm_strengths,
            llm_gaps=llm_gaps,
            strengths=strengths,
            salary_min=salary_min,
            salary_max=salary_max,
            job_url=url,
            source=source,
            job_type=job_type,
            location=location,
            company_rating=round(company_rating, 1),
            seniority_level=seniority_level,
            source_quality=src_quality,
            location_score=round(loc_score, 1),
            work_mode_score=round(wm_score, 1),
        )

    def _score_seniority(self, title: str, description: str) -> tuple:
        title_lower = title.lower()
        desc_lower = description.lower()[:1000]

        for tier_name in ["principal", "staff", "senior_lead", "senior", "mid", "junior"]:
            tier = SENIORITY_TIERS[tier_name]
            for pattern in tier["patterns"]:
                if re.search(pattern, title_lower):
                    score = tier["score"]
                    for yr_pattern, yr_score in EXPERIENCE_YEAR_PATTERNS:
                        if re.search(yr_pattern, desc_lower):
                            score = (score * 0.7) + (yr_score * 0.3)
                            break
                    return score, tier["label"]

        for tier_name in ["principal", "staff", "senior_lead", "senior"]:
            tier = SENIORITY_TIERS[tier_name]
            for pattern in tier["patterns"]:
                if re.search(pattern, desc_lower):
                    return tier["score"] * 0.8, tier["label"] + " (from desc)"

        return 30, "Unknown"

    def _semantic_match(self, title: str, description: str, tags: list) -> tuple:
        combined = f"{title} {description} {' '.join(tags)}".lower()
        try:
            jd_vector = self.vectorizer.transform([combined])
            tfidf_sim = cosine_similarity(self.resume_vector, jd_vector)[0][0] * 100
        except Exception:
            tfidf_sim = 0

        # Score against the dynamic buckets built from THIS user's profile.
        # When dynamic buckets are empty we fall through to TF-IDF only — never
        # to the legacy engineering keyword list.
        buckets = self._skill_buckets or {}
        # Equal weight across whatever buckets the profile yielded.
        bucket_weight = 1.0 / max(len(buckets), 1)
        skill_matches, skill_gaps, category_scores = [], [], {}
        for category, skills in buckets.items():
            if not skills:
                continue
            matched = [s for s in skills if s and s.lower() in combined]
            if matched:
                score = len(matched) / max(len(skills), 1) * 100
                category_scores[category] = score
                skill_matches.extend(matched)
            else:
                skill_gaps.append(category)

        keyword_score = 0
        if category_scores:
            keyword_score = sum(category_scores.values()) / len(category_scores)

        # Blend: TF-IDF dominates when no skill buckets matched, otherwise
        # keyword score gets equal say. Prevents zero-skill profiles from
        # collapsing to 0 across the board.
        if category_scores:
            final_score = tfidf_sim * 0.4 + keyword_score * 0.6
        else:
            final_score = tfidf_sim
        return min(100, final_score), list(set(skill_matches)), skill_gaps

    def _assess_listing_quality(self, job, company_review) -> float:
        score = 2.5
        desc = job.get("description", "") if isinstance(job, dict) else getattr(job, "description", "")
        if len(desc) > 500:
            score += 0.5
        if len(desc) > 1000:
            score += 0.3

        salary_min = job.get("salary_min", 0) if isinstance(job, dict) else getattr(job, "salary_min", 0)
        if salary_min > 0:
            score += 0.5

        desc_lower = desc.lower()
        if any(w in desc_lower for w in ["responsibilities", "requirements", "qualifications"]):
            score += 0.3
        if any(w in desc_lower for w in ["benefits", "perks", "we offer"]):
            score += 0.2
        if any(w in desc_lower for w in ["equal opportunity", "diversity", "inclusion"]):
            score += 0.1

        if company_review:
            cr = company_review.overall_rating if hasattr(company_review, "overall_rating") else company_review.get("overall_rating", 3)
            score = score * 0.6 + cr * 0.4

        return min(5.0, score)

    def _score_salary(self, salary_min: float, salary_max: float, job_type: str = "") -> float:
        """Context-aware salary scoring.

        Contract/freelance roles: prefer higher per-hour rates (salary values often
        represent hourly or daily rates, so lower absolute numbers are expected).
        Full-time roles: prefer higher annual salary.
        """
        if salary_max <= 0 and salary_min <= 0:
            return 30  # no salary disclosed
        avg = (salary_min + salary_max) / 2 if salary_max > 0 else salary_min
        jt = job_type.lower()
        is_contract = any(k in jt for k in ["contract", "freelance", "contractor", "consulting", "part"])

        if is_contract:
            # Likely hourly/daily rate. $80+/hr = great, $50/hr = decent.
            # Also handle cases where annual-equivalent is posted (>10k = annual).
            if avg > 10000:
                avg = avg / 2080  # convert annual to approx hourly
            if avg >= 100: return 100
            elif avg >= 80: return 90
            elif avg >= 60: return 75
            elif avg >= 40: return 55
            elif avg >= 25: return 35
            return 20
        else:
            # Full-time annual salary
            if avg >= 250000: return 100
            elif avg >= 200000: return 90
            elif avg >= 150000: return 75
            elif avg >= 120000: return 60
            elif avg >= 100000: return 50
            elif avg >= 80000: return 35
            elif avg >= 50000: return 20
            return 10

    def _contract_bonus(self, job_type: str) -> float:
        """Role type preference — context-aware based on desired work modes.

        If user wants remote: contract/part-time → higher (flexibility),
            full-time → lower but still OK.
        If user wants onsite/hybrid: full-time → higher (stability).
        """
        jt = job_type.lower()
        is_contract = any(k in jt for k in ["contract", "freelance", "contractor", "consulting"])
        is_parttime = "part" in jt

        if self._is_remote:
            # Remote preference: contract > part-time > full-time
            if is_contract: return 20
            if is_parttime: return 15
            return 5  # full-time remote still fine
        else:
            # Onsite/hybrid preference: full-time > contract
            if is_contract: return 5
            if is_parttime: return 3
            return 15  # full-time preferred

    def _score_location(self, job_location: str) -> float:
        """Score how well the job location matches user preference.

        Rules:
        - location=Anywhere + remote → 100
        - location matches resume/desired location → 90
        - location=Anywhere + not remote → 60
        - different location → 20
        """
        jl = job_location.lower().strip()
        desired = self._location.lower().strip()
        is_anywhere = desired in ("anywhere", "global", "worldwide", "")
        job_is_remote = any(k in jl for k in ["remote", "anywhere", "worldwide", "global", "work from home", "distributed"])

        if is_anywhere and (self._is_remote or job_is_remote):
            return 100  # user wants anywhere + remote → perfect
        if job_is_remote:
            return 90  # job is remote regardless — good
        if is_anywhere:
            return 60  # user is flexible, job has a location

        # Check if job location matches desired location.
        if desired and desired in jl:
            return 90
        # Partial match (country/region).
        desired_parts = set(desired.replace(",", " ").split())
        jl_parts = set(jl.replace(",", " ").split())
        overlap = desired_parts & jl_parts
        if overlap:
            return 70

        return 20  # no match

    def _score_work_mode(self, job_type: str, job_location: str) -> float:
        """Score how well the job's work mode matches user preference.

        Infer work mode from job_type and location text.
        """
        jt = job_type.lower()
        jl = job_location.lower()
        combined = f"{jt} {jl}"

        # Infer job's work mode.
        job_modes = set()
        if any(k in combined for k in ["remote", "work from home", "distributed", "anywhere", "worldwide"]):
            job_modes.add("remote")
        if any(k in combined for k in ["hybrid", "flex"]):
            job_modes.add("hybrid")
        if any(k in combined for k in ["onsite", "on-site", "in-office", "in office"]):
            job_modes.add("onsite")
        if not job_modes:
            job_modes.add("unknown")

        if "any" in self._work_modes:
            return 80  # user accepts anything

        # Check overlap between desired and detected modes.
        overlap = self._work_modes & job_modes
        if overlap:
            return 100  # exact match
        if "unknown" in job_modes:
            return 50  # can't tell, neutral
        return 15  # mismatch

    def _genai_mobile_bonus(self, title: str, desc: str, tags: list) -> float:
        """Domain-match bonus — generic. Awards points proportional to how
        many of the candidate's domain_keywords / primary_skills appear in
        the JD. No hardcoded engineering or AI bias.

        Method name kept for backward compat with the composite formula.
        """
        combined = f"{title} {desc} {' '.join(tags)}".lower()
        keywords = []
        keywords.extend(self._skill_buckets.get("domain", []) or [])
        keywords.extend(self._skill_buckets.get("primary", []) or [])
        # De-dup + drop empties.
        keywords = [k for k in dict.fromkeys(keywords) if k]
        if not keywords:
            return 0.0
        hits = sum(1 for k in keywords if k in combined)
        if hits == 0:
            return 0.0
        # Cap at 20 like the legacy max; scale linearly to the % of profile
        # keywords present in the JD.
        return min(20.0, (hits / max(len(keywords), 1)) * 40.0)

    def _identify_strengths(self, title: str, desc: str, skill_matches: list, seniority: str) -> list:
        """Strengths now derive from skill_matches (which come from the user's
        own profile) instead of a hardcoded engineering checklist. No more
        'Strong Android/Mobile alignment' for a Chartered Accountant."""
        strengths = []
        if seniority in ["Staff/Architect", "Principal/Distinguished", "Senior Lead"]:
            strengths.append(f"Seniority match: {seniority}")

        # Take the top 4 matched skills as named strengths. They're the user's
        # actual primary/domain keywords, so the message is always specific.
        primary = self._skill_buckets.get("primary", []) or []
        domain  = self._skill_buckets.get("domain", []) or []
        named = []
        # Prefer primary skills, then domain keywords, in original profile order.
        for s in primary + domain:
            sl = s.lower()
            if sl in skill_matches and sl not in [n.lower() for n in named]:
                named.append(s)
            if len(named) >= 4:
                break
        if named:
            strengths.append("Skill match: " + ", ".join(named))

        if not strengths:
            strengths.append("General profile alignment")
        return strengths

    def batch_review(self, jobs: list, company_reviews: dict, progress_callback=None) -> list[JDReviewResult]:
        import time
        results = []
        total = len(jobs)
        for idx, job in enumerate(jobs):
            company = job.get("company", "") if isinstance(job, dict) else job.company
            title = job.get("job_title", "") if isinstance(job, dict) else job.job_title
            cr = company_reviews.get(company)
            result = self.review_job(job, cr)
            results.append(result)
            self._llm_call_count += 1
            # Progress update per job
            print(f"  [JD Reviewer] {idx+1}/{total}: {title[:40]} @ {company[:20]} — score {result.composite_score:.0f}", flush=True)
            if progress_callback:
                progress_callback(idx + 1, total)
            # Rate limit only for Gemini cloud (free tier = 15 req/min)
            # Gemma4 local has no rate limit
            if self._use_llm and self._llm_call_count % 14 == 0:
                from agents.llm_client import get_provider
                if get_provider() == "gemini":
                    print(f"  [JD Reviewer] Pausing 62s for Gemini rate limit...", flush=True)
                    time.sleep(62)

        results.sort(key=lambda r: r.composite_score, reverse=True)
        for i, r in enumerate(results):
            r.rank = i + 1
        print(f"  [JD Reviewer] Done: {len(results)} jobs reviewed, {self._llm_call_count} LLM calls made.")
        return results

    def to_dicts(self, results: list[JDReviewResult]) -> list[dict]:
        return [asdict(r) for r in results]
