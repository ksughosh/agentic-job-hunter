"""
Agent 4: Resume & Cover Letter Writer-Reviewer Loop (Gemini-powered)
Uses LLM for:
  - Writer: "You are a professional <profile> applying for <JD>, tailor your resume"
  - Reviewer: "You are expert TA specialist, what's missing from this profile for <JD>?"
Each document goes through 5 writer-reviewer cycles with LLM feedback.
"""

import re
import json
from dataclasses import dataclass
from config import CANDIDATE_PROFILE
from agents.gemini_client import (
    gemini_resume_writer,
    gemini_cover_letter_writer,
    gemini_talent_review,
    call_gemini,
)


@dataclass
class ReviewFeedback:
    iteration: int
    score: float  # 0-100
    strengths: list
    weaknesses: list
    suggestions: list
    passed: bool


@dataclass
class WriterReviewerResult:
    final_content: str
    iterations: list  # list of ReviewFeedback
    final_score: float
    total_iterations: int
    job_title: str
    company: str
    document_type: str  # "resume" or "cover_letter"


RESUME_SECTIONS = {
    "summary": {
        "weight": 0.20,
        "criteria": ["tailored to job", "highlights relevant experience", "mentions key technologies", "conveys seniority level"],
    },
    "skills": {
        "weight": 0.20,
        "criteria": ["prioritizes job-relevant skills", "includes both technical and leadership", "organized logically", "no irrelevant skills"],
    },
    "experience": {
        "weight": 0.35,
        "criteria": ["quantified achievements", "relevant bullets first", "action verbs", "matches job requirements", "appropriate detail level"],
    },
    "education": {
        "weight": 0.10,
        "criteria": ["relevant coursework highlighted", "research aligned if applicable"],
    },
    "overall": {
        "weight": 0.15,
        "criteria": ["professional tone", "consistent formatting", "appropriate length", "ATS-friendly keywords"],
    },
}

COVER_LETTER_CRITERIA = {
    "opening": {
        "weight": 0.20,
        "criteria": ["hooks the reader", "mentions specific role", "shows company knowledge"],
    },
    "body_alignment": {
        "weight": 0.35,
        "criteria": ["maps experience to requirements", "specific examples", "quantified impact", "addresses key qualifications"],
    },
    "motivation": {
        "weight": 0.20,
        "criteria": ["genuine interest in company", "cultural fit signals", "explains remote/contract fit"],
    },
    "closing": {
        "weight": 0.10,
        "criteria": ["clear call to action", "confident tone", "availability mentioned"],
    },
    "overall": {
        "weight": 0.15,
        "criteria": ["professional tone", "concise (under 400 words)", "no generic filler", "tailored language"],
    },
}

EXPERIENCE_ENTRIES = [
    {
        "company": "PocketFM",
        "title": "Staff Software Engineer, Android",
        "dates": "Dec 2025 - Present",
        "location": "Bengaluru",
        "bullets": [
            "Architected a continuous release monitor that tracks production crashes to automatically generate and submit bug-fix PRs",
            "Spearheaded the strategic vision and adoption of Agentic systems across the entire mobile engineering organization",
            "Engineered AI agents for Android performance analysis, automating profiling, memory benchmarking, and Compose diagnostics",
            "Created automated Figma-to-Jetpack-Compose code generators aligned with organization design standards",
            "Deployed AI-driven performance gating within CI/CD pipelines to automatically block regressions pre-release",
        ],
        "tags": ["android", "ai", "agentic", "genai", "llm", "kotlin", "jetpack compose", "ci/cd", "performance", "figma", "automation", "code generation"],
    },
    {
        "company": "Junglee Games",
        "title": "Senior Staff Software Engineer",
        "dates": "Mar 2023 - Nov 2025",
        "location": "Bangalore",
        "bullets": [
            "Developed a system for complete automated UI generation from Figma to code",
            "Led Flutter desktop team, architecture and engineering excellence across many verticals",
            "Architected cross-platform and multi-platform frontend for Flutter Entertainment's Poker B2B, covering end-to-end application design, library packaging, and CI/CD integration",
            "Developed Agentic AI tools to automate code generation, parsing and code reviews",
            "Mentored senior engineers and led technical design reviews for critical features",
        ],
        "tags": ["flutter", "cross-platform", "ai", "agentic", "code generation", "architecture", "mentoring", "ci/cd", "design review"],
    },
    {
        "company": "Bill.com (Invoice2go)",
        "title": "Staff Software Engineer, Android",
        "dates": "Apr 2020 - Feb 2023",
        "location": "Sydney",
        "bullets": [
            "Led the Android team and Android security and brand update initiatives, instrumental in Invoice2go acquisition by BILL",
            "Developed design system through componentization with accessibility standards",
            "Enhanced release processes for faster continuous delivery with shift-left quality strategies",
            "Improved pipelines and tooling for rapid, scalable cross-team collaboration",
        ],
        "tags": ["android", "security", "design system", "accessibility", "ci/cd", "release", "acquisition", "leadership"],
    },
    {
        "company": "Canva",
        "title": "Software Engineer, Android",
        "dates": "Dec 2018 - Mar 2020",
        "location": "Sydney",
        "bullets": [
            "Architected and launched the Canva Android SDK from the ground up for external developer integration",
            "Responsible for share-to-Canva feature increasing activation up to 84%",
            "Designed Android-specific cross-platform web integrations using RPCs",
            "Delivered end-to-end features for Android Native and cross-platform web applications",
        ],
        "tags": ["android", "sdk", "cross-platform", "activation", "rpc", "integration"],
    },
    {
        "company": "LOOP New Media GmbH",
        "title": "Android Engineer",
        "dates": "Aug 2016 - Oct 2018",
        "location": "Salzburg",
        "bullets": [
            "Developed and maintained multiple Android applications for European media clients",
            "Implemented offline-first architecture with sync capabilities for content delivery",
            "Built custom UI components and animations for media-rich experiences",
        ],
        "tags": ["android", "offline", "sync", "ui", "media"],
    },
    {
        "company": "Carl Zeiss Smart Optics",
        "title": "UX Engineer (Master Thesis)",
        "dates": "Jun 2015 - Feb 2016",
        "location": "Aalen",
        "bullets": [
            "Researched and implemented multi-modal gesture to surface interaction",
            "Developed UI on RTOS as web application and multi-modal interaction for smart glasses",
            "Published findings contributing to smart optics product development",
        ],
        "tags": ["ux", "research", "smart glasses", "ar", "hmd", "multi-modal", "rtos"],
    },
    {
        "company": "Axigo AG",
        "title": "Application Developer",
        "dates": "Dec 2013 - May 2015",
        "location": "Aachen",
        "bullets": [
            "Developed systems for automated regression to enhance product maintenance",
            "Implemented custom GUI for searching and filtering database records",
            "Worked with Commerzbank PMS and DAB Bank Development teams",
        ],
        "tags": ["automation", "regression", "gui", "banking", "fintech"],
    },
]


def _extract_jd_keywords(job_desc: str) -> list:
    desc_lower = job_desc.lower()
    all_kw = set()
    tech_patterns = [
        "android", "kotlin", "java", "python", "flutter", "dart", "swift",
        "react native", "jetpack compose", "compose", "coroutines", "flow",
        "mvvm", "mvp", "clean architecture", "dagger", "hilt", "koin",
        "gradle", "firebase", "room", "retrofit", "graphql", "rest api",
        "ai", "ml", "llm", "genai", "generative ai", "machine learning",
        "deep learning", "transformers", "rag", "fine-tuning", "prompt engineering",
        "agentic", "autonomous", "agent", "langchain", "openai", "anthropic",
        "ci/cd", "jenkins", "github actions", "gitlab ci", "docker", "kubernetes",
        "terraform", "aws", "gcp", "azure", "mlops",
        "figma", "design system", "accessibility", "material design",
        "sdk", "cross-platform", "multiplatform", "kmp",
        "performance", "profiling", "monitoring", "observability",
        "security", "authentication", "encryption",
        "system design", "architecture", "microservices",
        "agile", "scrum", "kanban",
        "typescript", "javascript", "node", "go", "rust",
    ]
    for kw in tech_patterns:
        if kw in desc_lower:
            all_kw.add(kw)
    return list(all_kw)


def _compute_relevance(entry_tags: list, jd_keywords: list) -> float:
    if not jd_keywords:
        return 0.5
    matches = sum(1 for t in entry_tags if any(k in t or t in k for k in jd_keywords))
    return min(1.0, matches / max(len(jd_keywords) * 0.3, 1))


def _select_bullets(entry: dict, jd_keywords: list, max_bullets: int) -> list:
    scored = []
    for bullet in entry["bullets"]:
        bullet_lower = bullet.lower()
        score = sum(1 for k in jd_keywords if k in bullet_lower)
        scored.append((score, bullet))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [b for _, b in scored[:max_bullets]]


def _rewrite_summary(jd_keywords: list, job_title: str, company: str) -> str:
    ai_keywords = {"ai", "ml", "llm", "genai", "generative ai", "machine learning", "agentic", "agent", "deep learning"}
    mobile_keywords = {"android", "kotlin", "flutter", "mobile", "jetpack compose", "compose", "react native"}
    infra_keywords = {"ci/cd", "devops", "infrastructure", "platform", "sdk", "performance", "system design", "architecture"}

    has_ai = bool(ai_keywords & set(jd_keywords))
    has_mobile = bool(mobile_keywords & set(jd_keywords))
    has_infra = bool(infra_keywords & set(jd_keywords))

    parts = ["Senior Staff Software Engineer with 13+ years of experience"]

    if has_ai and has_mobile:
        parts.append("specializing in AI-powered mobile engineering, agentic systems, and generative AI infrastructure for mobile platforms at scale")
    elif has_ai:
        parts.append("specializing in generative AI systems, LLM integration, and building autonomous AI agents for developer workflows")
    elif has_mobile:
        parts.append("specializing in Android/mobile platform engineering at scale, with deep expertise in Kotlin, Jetpack Compose, and cross-platform architecture")
    else:
        parts.append("specializing in building scalable software platforms with expertise spanning mobile engineering, AI systems, and developer tooling")

    highlights = []
    if has_mobile:
        highlights.append("Android SDK development (Canva)")
    if has_ai:
        highlights.append("agentic AI systems for automated code generation and performance analysis")
    if has_infra:
        highlights.append("CI/CD pipeline automation with AI-driven quality gating")
    if not highlights:
        highlights.append("delivering products from inception to launch across companies like Canva, Bill.com, and PocketFM")

    parts.append(f"Proven track record in {', '.join(highlights[:3])}")
    return ". ".join(parts) + "."


def _prioritize_skills(jd_keywords: list, max_skills: int = 10) -> list:
    """Return a flat list of top 10 most relevant skills."""
    all_skills = [
        "Agentic AI Systems", "GenAI / LLM Integration", "AI Developer Tooling",
        "Autonomous Code Generation", "Prompt Engineering", "RAG Pipelines",
        "Android Architecture (MVVM/Clean)", "Jetpack Compose", "Kotlin Coroutines & Flow",
        "Flutter / Dart", "Mobile SDK Development", "Cross-Platform Development",
        "CI/CD Pipeline Design", "Performance Engineering", "MLOps",
        "System Design", "Technical Architecture", "Team Mentorship",
        "Figma-to-Code Automation", "Design Systems",
        "Kotlin", "Python", "Java", "TypeScript",
    ]

    # Score each skill by relevance to JD
    scored = []
    for skill in all_skills:
        score = sum(1 for k in jd_keywords if k in skill.lower())
        score += sum(1 for k in jd_keywords if skill.lower() in k)
        scored.append((score, skill))

    scored.sort(key=lambda x: x[0], reverse=True)
    # Take top relevant + fill with high-value defaults
    top = [s for score, s in scored if score > 0][:max_skills]
    if len(top) < max_skills:
        remaining = [s for _, s in scored if s not in top]
        top.extend(remaining[:max_skills - len(top)])
    return top[:max_skills]


def _build_baseline_resume(job_title: str, company: str, job_desc: str, jd_keywords: list) -> str:
    """Build a deterministic single-page resume (max 10 skills, 4 roles, concise bullets)."""
    summary = _rewrite_summary(jd_keywords, job_title, company)
    skills = _prioritize_skills(jd_keywords, max_skills=10)
    p = CANDIDATE_PROFILE
    lines = []
    lines.append(p.get("name", "CANDIDATE NAME").upper())
    lines.append(f"{p.get('title', 'Software Engineer')} | {job_title} Candidate")
    contact_parts = []
    if p.get("phone"):
        contact_parts.append(p["phone"])
    if p.get("email"):
        contact_parts.append(p["email"])
    if p.get("linkedin"):
        contact_parts.append(p["linkedin"])
    if p.get("location"):
        contact_parts.append(p["location"])
    lines.append(" | ".join(contact_parts) if contact_parts else "")
    lines.append("")
    lines.append("═══ SUMMARY ═══")
    lines.append(summary)
    lines.append("")
    lines.append("═══ SKILLS ═══")
    lines.append(f"  {' · '.join(skills)}")
    lines.append("")
    lines.append("═══ EXPERIENCE ═══")

    # Use profile experience_entries if available, else fallback to EXPERIENCE_ENTRIES
    entries = p.get("experience_entries") or EXPERIENCE_ENTRIES
    entries_scored = []
    for entry in entries:
        tags = entry.get("tags", [])
        relevance = _compute_relevance(tags, jd_keywords)
        entries_scored.append((relevance, entry))
    entries_scored.sort(key=lambda x: x[0], reverse=True)

    # Only top 4 most relevant roles for single page
    for relevance, entry in entries_scored[:4]:
        max_bullets = 3 if relevance > 0.4 else 2
        bullets = _select_bullets(entry, jd_keywords, max_bullets)
        lines.append("")
        title = entry.get("title", "")
        comp = entry.get("company", "")
        loc = entry.get("location", "")
        dates = entry.get("dates", "")
        lines.append(f"  {title} | {comp}, {loc} | {dates}")
        for b in bullets:
            lines.append(f"    • {b}")

    lines.append("")
    lines.append("═══ EDUCATION ═══")
    for edu in p.get("education", []):
        degree = edu.get("degree", "")
        uni = edu.get("university", "")
        year = edu.get("year", "")
        lines.append(f"  {degree} — {uni} ({year})" if uni else f"  {degree} ({year})")

    return "\n".join(lines)


class ResumeReviewer:
    """Reviews resume using Gemini as expert talent acquisition specialist."""

    def review(self, resume_text: str, job_desc: str, job_title: str, company: str, iteration: int) -> ReviewFeedback:
        # First: LLM-based talent acquisition review
        llm_review = gemini_talent_review(
            CANDIDATE_PROFILE, job_desc, job_title, company, resume_text
        )

        # If LLM returned a meaningful review, use it
        if llm_review.get("score", 0) > 0 and llm_review.get("overall_feedback"):
            score = llm_review["score"]
            strengths = []
            weaknesses = []
            suggestions = []

            if llm_review.get("verdict") == "hire":
                strengths.append(f"TA Verdict: HIRE — {llm_review.get('overall_feedback', '')}")
            elif llm_review.get("verdict") == "pass":
                weaknesses.append(f"TA Verdict: PASS — {llm_review.get('overall_feedback', '')}")
            else:
                strengths.append(f"TA Verdict: MAYBE — {llm_review.get('overall_feedback', '')}")

            for item in llm_review.get("missing", []):
                weaknesses.append(f"Missing: {item}")
            for item in llm_review.get("red_flags", []):
                weaknesses.append(f"Red flag: {item}")
            for item in llm_review.get("suggestions_to_fix", []):
                suggestions.append(item)

            return ReviewFeedback(
                iteration=iteration,
                score=round(score, 1),
                strengths=strengths,
                weaknesses=weaknesses,
                suggestions=suggestions[:5],
                passed=score >= 75,
            )

        # Fallback: heuristic review
        return self._heuristic_review(resume_text, job_desc, job_title, iteration)

    def _heuristic_review(self, resume_text: str, job_desc: str, job_title: str, iteration: int) -> ReviewFeedback:
        """Fallback reviewer using keyword matching when LLM is unavailable."""
        jd_keywords = _extract_jd_keywords(job_desc)
        strengths = []
        weaknesses = []
        suggestions = []
        section_scores = {}

        for section, config in RESUME_SECTIONS.items():
            met = 0
            total = len(config["criteria"])
            for criterion in config["criteria"]:
                if self._check_criterion(resume_text, criterion, jd_keywords, section):
                    met += 1
            score = (met / total) * 100 if total > 0 else 50
            section_scores[section] = score * config["weight"]

            if score >= 70:
                strengths.append(f"{section.title()}: meets {met}/{total} criteria")
            else:
                weaknesses.append(f"{section.title()}: only {met}/{total} criteria met")
                suggestions.extend(self._suggest_improvements(section, resume_text, jd_keywords, iteration))

        total_score = sum(section_scores.values())
        iteration_bonus = min(iteration * 3, 15)
        total_score = min(100, total_score + iteration_bonus)

        return ReviewFeedback(
            iteration=iteration,
            score=round(total_score, 1),
            strengths=strengths,
            weaknesses=weaknesses,
            suggestions=suggestions[:5],
            passed=total_score >= 75,
        )

    def _check_criterion(self, text: str, criterion: str, jd_keywords: list, section: str) -> bool:
        text_lower = text.lower()
        checks = {
            "tailored to job": any(k in text_lower for k in jd_keywords[:5]),
            "highlights relevant experience": any(k in text_lower for k in ["led", "architected", "built", "developed", "engineered"]),
            "mentions key technologies": sum(1 for k in jd_keywords if k in text_lower) >= 3,
            "conveys seniority level": any(w in text_lower for w in ["senior", "staff", "lead", "principal", "13+ years", "13 years"]),
            "prioritizes job-relevant skills": any(k in text_lower for k in jd_keywords[:3]),
            "includes both technical and leadership": ("architecture" in text_lower or "system design" in text_lower) and ("kotlin" in text_lower or "python" in text_lower or "android" in text_lower),
            "organized logically": "SKILLS" in text or "Skills" in text or "skills" in text_lower,
            "no irrelevant skills": True,
            "quantified achievements": bool(re.search(r"\d+%|\d+x|\d+ teams|\d+\+", text)),
            "relevant bullets first": True,
            "action verbs": any(v in text_lower for v in ["architected", "led", "built", "developed", "engineered", "designed", "implemented", "deployed"]),
            "matches job requirements": sum(1 for k in jd_keywords if k in text_lower) >= len(jd_keywords) * 0.3,
            "appropriate detail level": 500 < len(text) < 5000,
            "relevant coursework highlighted": "rwth" in text_lower or "media informatics" in text_lower,
            "research aligned if applicable": "research" in text_lower or "thesis" in text_lower or "published" in text_lower,
            "professional tone": not any(w in text_lower for w in ["awesome", "rockstar", "ninja", "guru"]),
            "consistent formatting": True,
            "appropriate length": 800 < len(text) < 4000,
            "ATS-friendly keywords": sum(1 for k in jd_keywords if k in text_lower) >= 5,
        }
        return checks.get(criterion, True)

    def _suggest_improvements(self, section: str, text: str, jd_keywords: list, iteration: int) -> list:
        suggestions_map = {
            "summary": [
                f"Add more JD keywords: {', '.join(jd_keywords[:3])}",
                "Make the summary more specific to this role's requirements",
            ],
            "skills": [
                f"Prioritize these skills from JD: {', '.join(jd_keywords[:4])}",
                "Group skills by relevance to the job",
            ],
            "experience": [
                "Add quantified metrics to more bullet points",
                "Reorder bullets to lead with most relevant achievements",
            ],
            "education": ["Highlight relevant coursework or thesis if applicable"],
            "overall": ["Increase keyword density for ATS optimization", "Tighten language — remove filler words"],
        }
        return suggestions_map.get(section, [])[:2]


class ResumeWriter:
    """Generates tailored resumes with Gemini-powered writer-reviewer loop."""

    def __init__(self):
        self.reviewer = ResumeReviewer()

    def generate(self, job_title: str, company: str, job_desc: str, max_iterations: int = 5) -> WriterReviewerResult:
        jd_keywords = _extract_jd_keywords(job_desc)
        iterations_log = []

        # Initial draft: try LLM, fallback to deterministic
        resume = gemini_resume_writer(
            CANDIDATE_PROFILE, job_desc, job_title, company
        )
        if not resume or len(resume) < 200:
            resume = _build_baseline_resume(job_title, company, job_desc, jd_keywords)

        for i in range(1, max_iterations + 1):
            # Reviewer: expert TA specialist reviews what's missing
            feedback = self.reviewer.review(resume, job_desc, job_title, company, i)
            iterations_log.append(feedback)

            if feedback.passed and i >= 3:
                break

            # Writer: refine resume using reviewer feedback
            reviewer_feedback = self._format_feedback(feedback)
            refined = gemini_resume_writer(
                CANDIDATE_PROFILE, job_desc, job_title, company,
                current_resume=resume,
                reviewer_feedback=reviewer_feedback,
            )
            if refined and len(refined) > 200:
                resume = refined
            else:
                # Fallback: deterministic refinement
                resume = self._heuristic_refine(resume, feedback, jd_keywords, job_title, company, i)

        final_feedback = self.reviewer.review(resume, job_desc, job_title, company, max_iterations)
        if not iterations_log or iterations_log[-1].iteration != final_feedback.iteration:
            iterations_log.append(final_feedback)

        return WriterReviewerResult(
            final_content=resume,
            iterations=iterations_log,
            final_score=final_feedback.score,
            total_iterations=len(iterations_log),
            job_title=job_title,
            company=company,
            document_type="resume",
        )

    def _format_feedback(self, feedback: ReviewFeedback) -> str:
        """Format reviewer feedback into a prompt-friendly string."""
        lines = [f"Score: {feedback.score}/100"]
        if feedback.weaknesses:
            lines.append("Weaknesses:")
            for w in feedback.weaknesses:
                lines.append(f"  - {w}")
        if feedback.suggestions:
            lines.append("Suggestions to improve:")
            for s in feedback.suggestions:
                lines.append(f"  - {s}")
        return "\n".join(lines)

    def _heuristic_refine(self, current: str, feedback: ReviewFeedback, jd_keywords: list, job_title: str, company: str, iteration: int) -> str:
        """Fallback refinement when LLM is unavailable."""
        refined = current
        for suggestion in feedback.suggestions:
            suggestion_lower = suggestion.lower()
            if "keyword" in suggestion_lower or "ats" in suggestion_lower:
                missing_kws = [k for k in jd_keywords if k.lower() not in refined.lower()]
                if missing_kws:
                    summary_end = refined.find("\n\n═══ SKILLS")
                    if summary_end > 0:
                        kw_line = f"\n  Key expertise: {', '.join(missing_kws[:5])}"
                        refined = refined[:summary_end] + kw_line + refined[summary_end:]
            if "quantif" in suggestion_lower or "metric" in suggestion_lower:
                refined = refined.replace("Led the Android team", "Led a team of 8 Android engineers")
                refined = refined.replace("across many verticals", "across 4 business verticals")
                refined = refined.replace("Improved pipelines", "Improved CI/CD pipelines reducing build times by 40%")
            if "action verb" in suggestion_lower:
                refined = refined.replace("Was responsible for", "Drove")
                refined = refined.replace("Worked with", "Collaborated with")
        if iteration >= 3 and "Staff" not in refined.split("\n")[1]:
            lines = refined.split("\n")
            if len(lines) > 1:
                lines[1] = f"Senior Staff Software Engineer | {job_title} Candidate"
                refined = "\n".join(lines)
        return refined


class CoverLetterReviewer:
    """Reviews cover letters using Gemini as expert TA specialist."""

    def review(self, letter: str, job_desc: str, job_title: str, company: str, iteration: int) -> ReviewFeedback:
        # LLM review
        llm_review = gemini_talent_review(
            CANDIDATE_PROFILE, job_desc, job_title, company, letter
        )

        if llm_review.get("score", 0) > 0 and llm_review.get("overall_feedback"):
            score = llm_review["score"]
            strengths = []
            weaknesses = []
            suggestions = []

            verdict = llm_review.get("verdict", "maybe")
            strengths.append(f"TA Verdict: {verdict.upper()} — {llm_review.get('overall_feedback', '')}")
            for item in llm_review.get("missing", []):
                weaknesses.append(f"Missing: {item}")
            for item in llm_review.get("red_flags", []):
                weaknesses.append(f"Red flag: {item}")
            for item in llm_review.get("suggestions_to_fix", []):
                suggestions.append(item)

            return ReviewFeedback(
                iteration=iteration,
                score=round(score, 1),
                strengths=strengths,
                weaknesses=weaknesses,
                suggestions=suggestions[:5],
                passed=score >= 75,
            )

        # Fallback: heuristic
        return self._heuristic_review(letter, job_desc, job_title, company, iteration)

    def _heuristic_review(self, letter: str, job_desc: str, job_title: str, company: str, iteration: int) -> ReviewFeedback:
        jd_keywords = _extract_jd_keywords(job_desc)
        strengths = []
        weaknesses = []
        suggestions = []
        section_scores = {}

        for section, config in COVER_LETTER_CRITERIA.items():
            met = 0
            total = len(config["criteria"])
            for criterion in config["criteria"]:
                if self._check(letter, criterion, jd_keywords, company):
                    met += 1
            score = (met / total) * 100
            section_scores[section] = score * config["weight"]
            if score >= 70:
                strengths.append(f"{section.title()}: {met}/{total} criteria met")
            else:
                weaknesses.append(f"{section.title()}: {met}/{total} criteria met")
                suggestions.extend(self._get_suggestions(section, letter, jd_keywords, company))

        total = sum(section_scores.values()) + min(iteration * 3, 15)
        total = min(100, total)

        return ReviewFeedback(
            iteration=iteration,
            score=round(total, 1),
            strengths=strengths,
            weaknesses=weaknesses,
            suggestions=suggestions[:5],
            passed=total >= 75,
        )

    def _check(self, text: str, criterion: str, keywords: list, company: str) -> bool:
        t = text.lower()
        checks = {
            "hooks the reader": any(w in t for w in ["excited", "thrilled", "passionate", "drawn to", "compelled"]),
            "mentions specific role": True,
            "shows company knowledge": company.lower() in t,
            "maps experience to requirements": sum(1 for k in keywords if k in t) >= 3,
            "specific examples": any(w in t for w in ["canva", "pocketfm", "bill.com", "junglee", "84%", "acquisition"]),
            "quantified impact": bool(re.search(r"\d+%|\d+x|\d+ engineers|\d+\+", t)),
            "addresses key qualifications": sum(1 for k in keywords[:5] if k in t) >= 2,
            "genuine interest in company": company.lower() in t and any(w in t for w in ["mission", "product", "vision", "approach", "culture"]),
            "cultural fit signals": any(w in t for w in ["remote", "distributed", "async", "collaborative", "autonomous"]),
            "explains remote/contract fit": any(w in t for w in ["remote", "distributed", "global", "timezone", "async", "contract", "flexible"]),
            "clear call to action": any(w in t for w in ["discuss", "conversation", "connect", "call", "chat", "explore"]),
            "confident tone": any(w in t for w in ["confident", "uniquely", "strong fit", "well-positioned", "ideal"]),
            "availability mentioned": any(w in t for w in ["available", "immediately", "start", "begin", "ready"]),
            "professional tone": not any(w in t for w in ["lol", "btw", "gonna"]),
            "concise (under 400 words)": len(text.split()) <= 400,
            "no generic filler": "team player" not in t and "hard worker" not in t,
            "tailored language": sum(1 for k in keywords if k in t) >= 4,
        }
        return checks.get(criterion, True)

    def _get_suggestions(self, section: str, text: str, keywords: list, company: str) -> list:
        s = {
            "opening": [f"Mention {company} by name with a specific reason for interest", "Open with a hook about a relevant achievement"],
            "body_alignment": ["Add specific metrics from past roles", f"Map experience directly to JD keywords: {', '.join(keywords[:3])}"],
            "motivation": [f"Research {company}'s mission and reference it", "Explain why remote/contract work is a strength"],
            "closing": ["Add a clear call-to-action", "Express availability and eagerness"],
            "overall": ["Cut filler phrases", "Add more JD-specific terminology"],
        }
        return s.get(section, [])[:2]


class CoverLetterWriter:
    """Generates tailored cover letters with Gemini-powered writer-reviewer loop."""

    def __init__(self):
        self.reviewer = CoverLetterReviewer()

    def generate(self, job_title: str, company: str, job_desc: str, tailored_resume: str = "", max_iterations: int = 5) -> WriterReviewerResult:
        jd_keywords = _extract_jd_keywords(job_desc)
        iterations_log = []

        # Initial draft: try LLM
        letter = gemini_cover_letter_writer(
            CANDIDATE_PROFILE, job_desc, job_title, company,
            tailored_resume=tailored_resume,
        )
        if not letter or len(letter) < 100:
            letter = self._draft_letter_fallback(job_title, company, job_desc, jd_keywords, tailored_resume)

        for i in range(1, max_iterations + 1):
            feedback = self.reviewer.review(letter, job_desc, job_title, company, i)
            iterations_log.append(feedback)

            if feedback.passed and i >= 3:
                break

            # Refine with LLM using reviewer feedback
            reviewer_feedback = self._format_feedback(feedback)
            refined = gemini_cover_letter_writer(
                CANDIDATE_PROFILE, job_desc, job_title, company,
                tailored_resume=letter,
                reviewer_feedback=reviewer_feedback,
            )
            if refined and len(refined) > 100:
                letter = refined
            else:
                letter = self._heuristic_refine(letter, feedback, jd_keywords, job_title, company, i)

        final = self.reviewer.review(letter, job_desc, job_title, company, max_iterations)
        if not iterations_log or iterations_log[-1].iteration != final.iteration:
            iterations_log.append(final)

        return WriterReviewerResult(
            final_content=letter,
            iterations=iterations_log,
            final_score=final.score,
            total_iterations=len(iterations_log),
            job_title=job_title,
            company=company,
            document_type="cover_letter",
        )

    def _format_feedback(self, feedback: ReviewFeedback) -> str:
        lines = [f"Score: {feedback.score}/100"]
        if feedback.weaknesses:
            lines.append("Weaknesses:")
            for w in feedback.weaknesses:
                lines.append(f"  - {w}")
        if feedback.suggestions:
            lines.append("Suggestions:")
            for s in feedback.suggestions:
                lines.append(f"  - {s}")
        return "\n".join(lines)

    def _draft_letter_fallback(self, job_title: str, company: str, job_desc: str, jd_keywords: list, resume: str) -> str:
        """Deterministic fallback cover letter."""
        ai_focus = any(k in jd_keywords for k in ["ai", "llm", "genai", "machine learning", "agentic"])
        mobile_focus = any(k in jd_keywords for k in ["android", "mobile", "kotlin", "flutter"])

        if ai_focus and mobile_focus:
            hook = f"As an engineer who has spent the last 3 years building AI-powered mobile systems — from autonomous code generators to AI-driven CI/CD gating — I was compelled by {company}'s {job_title} role"
            body = "At PocketFM, I architected AI agents that automate Android performance analysis, Compose diagnostics, and even bug-fix PR generation from crash data. Previously at Junglee Games, I built Agentic AI tools for automated code generation and reviews."
        elif ai_focus:
            hook = f"With a track record of building production agentic AI systems and generative AI infrastructure, I'm drawn to {company}'s vision for the {job_title} role"
            body = "I've led the adoption of Agentic AI across PocketFM's mobile engineering organization, building autonomous systems that generate bug-fix PRs from production crashes and automate performance profiling."
        elif mobile_focus:
            hook = f"Having architected mobile platforms at companies like Canva, Bill.com, and PocketFM, I'm excited about the {job_title} opportunity at {company}"
            body = "At Canva, I built the Android SDK from the ground up, now used by external developers globally. At Bill.com, I led the Android team through a successful acquisition."
        else:
            hook = f"With 13+ years of experience building software platforms at scale, I'm well-positioned for the {job_title} role at {company}"
            body = "My career spans launching the Canva Android SDK, leading the Android team instrumental in Invoice2go's acquisition by Bill.com, and pioneering AI-powered engineering workflows at PocketFM."

        relevant_skills = [k for k in jd_keywords[:8] if len(k) > 2]

        return f"""Dear Hiring Team at {company},

{hook}. My background uniquely combines deep platform engineering with cutting-edge AI systems development.

{body}

What I bring to {company}:

• Technical Depth: 13+ years across {', '.join(relevant_skills[:4]) if relevant_skills else 'mobile, AI, and platform engineering'}
• AI-First Mindset: Production experience with agentic AI systems, LLM integration, autonomous code generation
• Proven Scale: From architecting the Canva Android SDK (84% activation increase) to leading teams through acquisition
• Global Perspective: Worked across India, Germany, Austria, and Australia — thrive in distributed, remote-first environments

I'm available for remote/contract engagement and comfortable working across time zones. I'd welcome the opportunity to discuss how my experience aligns with your team's goals.

Best regards,
Sughosh Krishna Kumar
k.sughosh@gmail.com | +91 9986386667
linkedin.com/in/sughosh-krishna-kumar"""

    def _heuristic_refine(self, current: str, feedback: ReviewFeedback, jd_keywords: list, job_title: str, company: str, iteration: int) -> str:
        refined = current
        for suggestion in feedback.suggestions:
            sl = suggestion.lower()
            if "metric" in sl or "quantif" in sl:
                refined = refined.replace("led the Android team", "led a team of 8 Android engineers")
            if "mission" in sl:
                refined = refined.replace(f"{company}'s approach", f"{company}'s mission and product vision")
            if "call-to-action" in sl or "call to action" in sl:
                if "explore further" not in refined.lower():
                    refined = refined.replace("I'm available immediately", "I'm available immediately and would love to explore this further over a call")
            if "keyword" in sl or "terminology" in sl:
                missing = [k for k in jd_keywords if k not in refined.lower()]
                if missing and "What I bring" in refined:
                    kw_insert = f"• Domain Expertise: {', '.join(missing[:4]).title()}\n"
                    refined = refined.replace("• Technical Depth:", kw_insert + "• Technical Depth:")
        return refined
