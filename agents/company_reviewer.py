"""
Agent 2: Company Reviewer
Rates companies on work culture, remote support, freelancer benefits,
compensation reputation, and overall desirability.
"""

import re
import time
from dataclasses import dataclass, asdict
from typing import Optional

import requests


@dataclass
class CompanyReview:
    company_name: str
    overall_rating: float  # 0-5
    remote_culture: float  # 0-5
    freelancer_support: float  # 0-5
    compensation_rep: float  # 0-5
    benefits_perks: float  # 0-5
    engineering_culture: float  # 0-5
    work_life_balance: float  # 0-5
    company_size: str
    industry: str
    headquarters: str
    description: str
    confidence: str  # "high", "medium", "low"
    data_source: str

    @property
    def weighted_score(self) -> float:
        weights = {
            "remote_culture": 0.25,
            "freelancer_support": 0.20,
            "compensation_rep": 0.20,
            "engineering_culture": 0.15,
            "work_life_balance": 0.10,
            "benefits_perks": 0.10,
        }
        return round(sum(
            getattr(self, k) * w for k, w in weights.items()
        ), 2)


KNOWN_COMPANIES = {
    "google": CompanyReview("Google", 4.5, 3.5, 2.0, 5.0, 5.0, 4.8, 3.5, "100000+", "Tech", "Mountain View, CA", "Leading tech company", "high", "Known"),
    "meta": CompanyReview("Meta", 4.2, 3.0, 2.0, 4.8, 4.5, 4.5, 3.0, "50000+", "Tech", "Menlo Park, CA", "Social media tech giant", "high", "Known"),
    "apple": CompanyReview("Apple", 4.3, 2.0, 1.5, 5.0, 4.8, 4.5, 3.0, "100000+", "Tech", "Cupertino, CA", "Consumer electronics & software", "high", "Known"),
    "microsoft": CompanyReview("Microsoft", 4.4, 4.0, 3.0, 4.8, 4.8, 4.5, 4.0, "100000+", "Tech", "Redmond, WA", "Enterprise & consumer tech", "high", "Known"),
    "amazon": CompanyReview("Amazon", 3.8, 2.5, 2.0, 4.5, 4.0, 4.0, 2.5, "100000+", "Tech/E-commerce", "Seattle, WA", "E-commerce and cloud", "high", "Known"),
    "netflix": CompanyReview("Netflix", 4.5, 4.5, 3.0, 5.0, 4.5, 4.8, 3.5, "10000+", "Entertainment/Tech", "Los Gatos, CA", "Streaming entertainment", "high", "Known"),
    "spotify": CompanyReview("Spotify", 4.3, 4.5, 3.5, 4.2, 4.5, 4.5, 4.5, "5000+", "Music/Tech", "Stockholm, Sweden", "Music streaming platform", "high", "Known"),
    "stripe": CompanyReview("Stripe", 4.5, 4.5, 3.0, 5.0, 4.5, 4.8, 3.5, "5000+", "Fintech", "San Francisco, CA", "Payment infrastructure", "high", "Known"),
    "canva": CompanyReview("Canva", 4.6, 4.0, 3.0, 4.5, 4.8, 4.5, 4.5, "3000+", "Design/Tech", "Sydney, Australia", "Design platform", "high", "Known"),
    "gitlab": CompanyReview("GitLab", 4.5, 5.0, 4.0, 4.2, 4.5, 4.5, 4.8, "2000+", "DevTools", "Remote", "All-remote DevOps platform", "high", "Known"),
    "automattic": CompanyReview("Automattic", 4.3, 5.0, 4.5, 4.0, 4.0, 4.2, 4.8, "1500+", "Tech", "Remote", "Fully distributed WordPress parent", "high", "Known"),
    "toptal": CompanyReview("Toptal", 3.8, 5.0, 4.5, 3.5, 3.0, 3.5, 4.0, "1000+", "Freelance Platform", "Remote", "Top freelancer network", "high", "Known"),
    "upwork": CompanyReview("Upwork", 3.5, 4.5, 4.0, 3.0, 2.5, 3.0, 4.0, "500+", "Freelance Platform", "Remote", "Freelance marketplace", "high", "Known"),
    "shopify": CompanyReview("Shopify", 4.2, 4.5, 3.0, 4.5, 4.5, 4.5, 4.0, "10000+", "E-commerce/Tech", "Ottawa, Canada", "E-commerce platform", "high", "Known"),
    "airbnb": CompanyReview("Airbnb", 4.4, 4.5, 2.5, 4.5, 4.8, 4.5, 4.0, "5000+", "Travel/Tech", "San Francisco, CA", "Travel marketplace", "high", "Known"),
    "datadog": CompanyReview("Datadog", 4.2, 3.5, 2.5, 4.5, 4.2, 4.5, 3.5, "5000+", "Cloud/DevOps", "New York, NY", "Cloud monitoring", "high", "Known"),
    "twilio": CompanyReview("Twilio", 4.0, 4.0, 3.0, 4.2, 4.0, 4.2, 4.0, "5000+", "Cloud Communications", "San Francisco, CA", "Communication APIs", "high", "Known"),
    "hubspot": CompanyReview("HubSpot", 4.5, 4.5, 3.0, 4.2, 4.8, 4.3, 4.5, "5000+", "SaaS/Marketing", "Cambridge, MA", "CRM and marketing platform", "high", "Known"),
    "notion": CompanyReview("Notion", 4.4, 4.0, 3.0, 4.5, 4.5, 4.5, 4.0, "500+", "Productivity/Tech", "San Francisco, CA", "All-in-one workspace", "high", "Known"),
    "figma": CompanyReview("Figma", 4.6, 4.0, 3.0, 4.8, 4.8, 4.8, 4.0, "1000+", "Design/Tech", "San Francisco, CA", "Collaborative design tool", "high", "Known"),
    "vercel": CompanyReview("Vercel", 4.3, 5.0, 3.5, 4.5, 4.2, 4.8, 4.0, "500+", "DevTools/Cloud", "Remote", "Frontend cloud platform", "high", "Known"),
    "linear": CompanyReview("Linear", 4.5, 5.0, 3.5, 4.5, 4.0, 4.8, 4.5, "100+", "DevTools", "Remote", "Project management for teams", "high", "Known"),
    "deel": CompanyReview("Deel", 4.0, 5.0, 5.0, 4.0, 4.0, 3.8, 4.0, "3000+", "HR/Fintech", "Remote", "Global HR and payroll", "high", "Known"),
    "remote.com": CompanyReview("Remote.com", 4.2, 5.0, 5.0, 4.0, 4.2, 4.0, 4.5, "1000+", "HR/Tech", "Remote", "Global employment platform", "high", "Known"),
    "anthropic": CompanyReview("Anthropic", 4.7, 3.5, 2.5, 5.0, 4.5, 5.0, 3.5, "500+", "AI/ML", "San Francisco, CA", "AI safety company", "high", "Known"),
    "openai": CompanyReview("OpenAI", 4.5, 3.0, 2.0, 5.0, 4.5, 5.0, 3.0, "1000+", "AI/ML", "San Francisco, CA", "AI research company", "high", "Known"),
}

REMOTE_FRIENDLY_KEYWORDS = [
    "remote-first", "fully remote", "distributed", "work from anywhere",
    "async", "remote friendly", "global team", "no office",
]

CONTRACT_FRIENDLY_KEYWORDS = [
    "contract", "freelance", "contractor", "consulting", "1099",
    "independent contractor", "b2b", "flexible engagement",
]


class CompanyReviewerAgent:
    """Reviews and rates companies based on available data."""

    def __init__(self):
        self.cache = {}
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        })

    def review(self, company_name: str, job_description: str = "", job_type: str = "", location: str = "") -> CompanyReview:
        key = re.sub(r"[^a-z0-9]", "", company_name.lower())
        if key in self.cache:
            return self.cache[key]
        if key in KNOWN_COMPANIES:
            review = KNOWN_COMPANIES[key]
            self.cache[key] = review
            return review
        review = self._infer_review(company_name, job_description, job_type, location)
        self.cache[key] = review
        return review

    def _infer_review(self, name: str, desc: str, job_type: str, location: str) -> CompanyReview:
        if isinstance(job_type, list):
            job_type = " ".join(str(x) for x in job_type)
        if isinstance(location, list):
            location = " ".join(str(x) for x in location)
        combined = f"{desc} {job_type} {location}".lower()
        remote_score = 3.0
        for kw in REMOTE_FRIENDLY_KEYWORDS:
            if kw in combined:
                remote_score = min(5.0, remote_score + 0.4)
        freelancer_score = 2.0
        jt = job_type.lower()
        if any(k in jt for k in ["contract", "freelance", "contractor"]):
            freelancer_score = 4.0
        for kw in CONTRACT_FRIENDLY_KEYWORDS:
            if kw in combined:
                freelancer_score = min(5.0, freelancer_score + 0.3)

        has_benefits = any(w in combined for w in ["health", "insurance", "wellness", "stipend", "equipment", "home office"])
        benefits_score = 3.5 if has_benefits else 2.5

        eng_signals = ["engineering blog", "open source", "tech talks", "hackathon", "conferences"]
        eng_score = 3.0
        for s in eng_signals:
            if s in combined:
                eng_score = min(5.0, eng_score + 0.3)

        wlb_score = 3.5
        if any(w in combined for w in ["flexible hours", "async", "work-life", "unlimited pto", "unlimited vacation"]):
            wlb_score = 4.5
        comp_score = 3.0
        if any(w in combined for w in ["competitive", "above market", "top of market", "equity", "stock"]):
            comp_score = 4.0

        size = "Unknown"
        if any(w in combined for w in ["startup", "early stage", "seed", "series a"]):
            size = "Startup (<50)"
        elif any(w in combined for w in ["growing", "scale-up", "series b", "series c"]):
            size = "Scale-up (50-500)"
        elif any(w in combined for w in ["enterprise", "fortune", "global presence"]):
            size = "Enterprise (1000+)"

        industry = "Tech"
        if any(w in combined for w in ["fintech", "finance", "banking", "payments"]):
            industry = "Fintech"
        elif any(w in combined for w in ["health", "medical", "biotech"]):
            industry = "HealthTech"
        elif any(w in combined for w in ["ecommerce", "retail", "marketplace"]):
            industry = "E-commerce"
        elif any(w in combined for w in ["ai", "machine learning", "ml", "llm"]):
            industry = "AI/ML"
        elif any(w in combined for w in ["gaming", "game"]):
            industry = "Gaming"

        overall = round((remote_score + freelancer_score + benefits_score + eng_score + wlb_score + comp_score) / 6, 1)

        return CompanyReview(
            company_name=name,
            overall_rating=overall,
            remote_culture=round(remote_score, 1),
            freelancer_support=round(freelancer_score, 1),
            compensation_rep=round(comp_score, 1),
            benefits_perks=round(benefits_score, 1),
            engineering_culture=round(eng_score, 1),
            work_life_balance=round(wlb_score, 1),
            company_size=size,
            industry=industry,
            headquarters=location or "Unknown",
            description=f"{name} - {industry} company",
            confidence="low" if overall < 3 else "medium",
            data_source="Inferred from JD",
        )

    def batch_review(self, jobs: list) -> dict:
        reviews = {}
        for job in jobs:
            company = job.company if hasattr(job, "company") else job.get("company", "")
            if not company or company in reviews:
                continue
            desc = job.description if hasattr(job, "description") else job.get("description", "")
            jtype = job.job_type if hasattr(job, "job_type") else job.get("job_type", "")
            loc = job.location if hasattr(job, "location") else job.get("location", "")
            reviews[company] = self.review(company, desc, jtype, loc)
        return reviews

    def to_dicts(self, reviews: dict) -> dict:
        return {k: asdict(v) for k, v in reviews.items()}
