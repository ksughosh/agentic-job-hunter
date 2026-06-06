"""Parallel execution utilities for the pipeline.

Thread-safe wrappers for:
  - Scraping N sources concurrently
  - Heuristic pre-filtering (top-N for LLM)
  - JD matching in parallel
  - Company reviews in parallel

All functions are pure — they don't mutate pipeline status directly.
The caller (pipeline.py) handles status updates.
"""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable


# ═══════════════════════════════════════════════════════════════════
# 1. PARALLEL SCRAPING
# ═══════════════════════════════════════════════════════════════════


@dataclass
class ScrapeResult:
    """Result from one scraper source."""
    source_name: str
    jobs: list = field(default_factory=list)
    ok: bool = True
    error: str = ""


def scrape_parallel(
    scrapers: list,
    keywords: list[str],
    max_workers: int = 8,
    on_source_done: Callable | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[list[ScrapeResult], list[str]]:
    """Scrape all sources in parallel using ThreadPoolExecutor.

    Args:
        scrapers: list of scraper objects with .name and .scrape(keywords) method
        keywords: filter keywords passed to each scraper
        max_workers: max concurrent scrapers
        on_source_done: callback(source_name, count, ok, completed, total) per source
        cancel_event: if set, pending scrapers will be skipped

    Returns:
        (results, errors) — results is list of ScrapeResult, errors is list of error strings
    """
    if not scrapers:
        return [], []

    results: list[ScrapeResult] = []
    errors: list[str] = []
    lock = threading.Lock()
    completed_count = 0
    total = len(scrapers)

    def _scrape_one(scraper) -> ScrapeResult:
        """Run a single scraper. Called inside thread pool."""
        # Check cancel before starting
        if cancel_event and cancel_event.is_set():
            return ScrapeResult(source_name=scraper.name, jobs=[], ok=False, error="cancelled")

        try:
            jobs = scraper.scrape(keywords)
            return ScrapeResult(source_name=scraper.name, jobs=jobs, ok=True)
        except Exception as e:
            return ScrapeResult(source_name=scraper.name, jobs=[], ok=False, error=str(e))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_scraper = {
            executor.submit(_scrape_one, s): s for s in scrapers
        }

        for future in as_completed(future_to_scraper):
            result = future.result()

            with lock:
                completed_count += 1
                results.append(result)
                if not result.ok and result.error and result.error != "cancelled":
                    errors.append(f"{result.source_name}: {result.error}")

                if on_source_done:
                    on_source_done(
                        result.source_name,
                        len(result.jobs),
                        result.ok,
                        completed_count,
                        total,
                        error=result.error,
                    )

    return results, errors


# ═══════════════════════════════════════════════════════════════════
# 2. HEURISTIC PRE-FILTER
# ═══════════════════════════════════════════════════════════════════


# Seniority tiers for scoring
_SENIORITY_SCORES = {
    "principal": {"principal", "distinguished", "fellow", "vp", "chief", "director"},
    "staff": {"staff", "architect", "lead", "head"},
    "senior": {"senior", "sr"},
    "mid": set(),
    "junior": {"junior", "jr", "intern", "entry", "associate", "graduate", "fresher"},
}

_SENIORITY_RANK = {"principal": 5, "staff": 4, "senior": 3, "mid": 2, "junior": 1}


def heuristic_top_n(
    jobs: list[dict],
    profile_keywords: list[str],
    n: int = 40,
    experience_level: str = "senior",
) -> list[dict]:
    """Fast heuristic scoring to pick top N jobs for LLM review.

    Scoring dimensions:
      - Keyword overlap with profile (0-50 pts)
      - Seniority match (0-30 pts)
      - Listing quality signals (0-20 pts)

    Returns copies of job dicts with _heuristic_score attached.
    """
    if not jobs:
        return []

    keywords_lower = {k.lower() for k in profile_keywords}
    scored = []

    for job in jobs:
        score = _score_job(job, keywords_lower, experience_level)
        job_copy = dict(job)
        job_copy["_heuristic_score"] = score
        scored.append(job_copy)

    scored.sort(key=lambda j: j["_heuristic_score"], reverse=True)
    return scored[:n]


def _score_job(job: dict, keywords: set[str], exp_level: str) -> float:
    """Score a single job heuristically (no LLM needed)."""
    title = (job.get("title") or "").lower()
    desc = (job.get("description") or "").lower()[:1000]
    combined = f"{title} {desc}"
    score = 0.0

    # ── Keyword overlap (0-50) ──
    if keywords:
        matches = sum(1 for kw in keywords if kw in combined)
        score += min(50.0, (matches / max(len(keywords), 1)) * 50)

    # ── Seniority match (0-30) ──
    job_level = _detect_seniority(title)
    target_rank = _SENIORITY_RANK.get(exp_level, 3)
    job_rank = _SENIORITY_RANK.get(job_level, 2)

    if job_rank == target_rank:
        score += 30  # exact match
    elif job_rank == target_rank + 1 or job_rank == target_rank - 1:
        score += 20  # one level off
    elif job_rank < target_rank - 1:
        score -= 10  # too junior — penalize

    # ── Listing quality signals (0-20) ──
    # Has salary info
    if job.get("salary_min", 0) > 0 or job.get("salary_max", 0) > 0:
        score += 5
    # Has description
    if len(desc) > 200:
        score += 5
    # Has company name
    if job.get("company"):
        score += 3
    # Source quality
    sq = job.get("source_quality", 50)
    score += (sq / 100) * 7

    return round(score, 1)


def _detect_seniority(title: str) -> str:
    """Quick regex seniority detection from job title."""
    title_lower = title.lower()
    for level, keywords in _SENIORITY_SCORES.items():
        for kw in keywords:
            if re.search(r'\b' + re.escape(kw) + r'\b', title_lower):
                return level
    return "mid"  # default if no signals


# ═══════════════════════════════════════════════════════════════════
# 3. PARALLEL JD MATCHING
# ═══════════════════════════════════════════════════════════════════


def jd_match_parallel(
    reviewer,
    jobs: list[dict],
    company_reviews: dict,
    max_workers: int = 4,
    on_progress: Callable | None = None,
    cancel_event: threading.Event | None = None,
) -> list:
    """Run JD review_job() calls in parallel.

    Args:
        reviewer: JDReviewerAgent instance with .review_job(job, cr) method
        jobs: list of job dicts to review
        company_reviews: dict mapping company name → review data
        max_workers: concurrent review threads
        on_progress: callback(done, total) — called with monotonically increasing done
        cancel_event: if set, pending jobs will be skipped

    Returns:
        list of JDReviewResult objects (order not guaranteed)
    """
    if not jobs:
        return []

    results = []
    errors = []
    lock = threading.Lock()
    done_count = 0
    total = len(jobs)

    def _review_one(job: dict):
        """Review a single job. Called inside thread pool."""
        if cancel_event and cancel_event.is_set():
            return None

        company = job.get("company", "")
        cr = company_reviews.get(company)
        return reviewer.review_job(job, cr)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_job = {executor.submit(_review_one, j): j for j in jobs}

        for future in as_completed(future_to_job):
            if cancel_event and cancel_event.is_set():
                # Cancel remaining futures
                for f in future_to_job:
                    f.cancel()
                break

            try:
                result = future.result()
                if result is not None:
                    with lock:
                        results.append(result)
                        done_count += 1
                        if on_progress:
                            on_progress(done_count, total)
            except Exception as e:
                with lock:
                    done_count += 1
                    errors.append(str(e))
                    if on_progress:
                        on_progress(done_count, total)

    if errors:
        print(f"  ⚠️ {len(errors)} JD review errors: {errors[:3]}", flush=True)

    return results


# ═══════════════════════════════════════════════════════════════════
# 4. PARALLEL COMPANY REVIEW
# ═══════════════════════════════════════════════════════════════════


def company_review_parallel(
    reviewer,
    jobs: list[dict],
    max_workers: int = 4,
    cancel_event: threading.Event | None = None,
) -> dict:
    """Run company reviews in parallel, one per unique company.

    Args:
        reviewer: CompanyReviewerAgent with .review(name, desc, job_type, loc) method
        jobs: list of job dicts (may have duplicate companies)
        max_workers: concurrent review threads
        cancel_event: if set, stop early

    Returns:
        dict mapping company_name → review object
    """
    # Deduplicate: one representative job per company
    company_jobs: dict[str, dict] = {}
    for j in jobs:
        company = j.get("company", "")
        if company and company not in company_jobs:
            company_jobs[company] = j

    if not company_jobs:
        return {}

    results: dict[str, Any] = {}
    lock = threading.Lock()

    def _review_one(company_name: str, job: dict):
        if cancel_event and cancel_event.is_set():
            return None, None
        desc = job.get("description", "")
        jtype = job.get("job_type", "")
        loc = job.get("location", "")
        review = reviewer.review(company_name, desc, jtype, loc)
        return company_name, review

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_review_one, name, job): name
            for name, job in company_jobs.items()
        }

        for future in as_completed(futures):
            try:
                name, review = future.result()
                if name is not None and review is not None:
                    with lock:
                        results[name] = review
            except Exception as e:
                company_name = futures[future]
                print(f"  ⚠️ Company review failed for {company_name}: {e}", flush=True)

    return results
