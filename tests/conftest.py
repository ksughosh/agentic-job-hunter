"""Shared test fixtures and helpers for the job-hunter test suite."""

from __future__ import annotations

import sys
import os

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest


# ─── Fake scraper for testing parallel scraping ─────────────────

class FakeScraper:
    """Simulates a real scraper with configurable delay, results, and failure mode."""

    def __init__(self, name: str, jobs: list = None, delay: float = 0.05,
                 fail: bool = False, fail_type: str = "exception"):
        self.name = name
        self._jobs = jobs or []
        self._delay = delay
        self._fail = fail
        self._fail_type = fail_type
        self.stats = {"attempts": 0, "successes": 0, "failures": 0, "healed": 0}
        self.scrape_count = 0  # track how many times scrape() was called

    def scrape(self, keywords: list) -> list:
        import time
        self.scrape_count += 1
        time.sleep(self._delay)

        if self._fail:
            if self._fail_type == "timeout":
                time.sleep(10)  # will be killed by test timeout
            raise RuntimeError(f"{self.name} scrape failed (simulated)")

        self.stats["successes"] += 1
        return list(self._jobs)


def make_fake_job(title: str = "Software Engineer", company: str = "TestCo",
                  source_url: str = "", source: str = "FakeSource",
                  description: str = "Build things.", location: str = "Remote",
                  job_type: str = "Full-Time", salary_min: float = 0,
                  salary_max: float = 0, tags: list = None) -> dict:
    """Create a realistic job dict for testing."""
    import uuid
    return {
        "title": title,
        "company": company,
        "source_url": source_url or f"https://example.com/jobs/{uuid.uuid4().hex[:8]}",
        "source": source,
        "description": description,
        "location": location,
        "job_type": job_type,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "tags": tags or [],
        "posted_date": "2026-06-01",
        "remote": True,
        "region": "",
        "currency": "USD",
        "source_quality": 70,
    }


@pytest.fixture
def sample_jobs():
    """Generate 50 diverse sample jobs for testing."""
    jobs = []
    companies = ["Google", "Meta", "Stripe", "Canva", "Atlassian",
                 "Shopify", "Airbnb", "Notion", "Figma", "Linear"]
    titles = [
        "Staff Android Engineer", "Senior Backend Developer",
        "Principal ML Engineer", "Staff Full Stack Developer",
        "Senior iOS Engineer", "Junior Frontend Developer",
        "Mid-Level DevOps Engineer", "Staff Platform Engineer",
        "Intern Software Developer", "Associate Data Analyst",
    ]
    for i in range(50):
        company = companies[i % len(companies)]
        title = titles[i % len(titles)]
        jobs.append(make_fake_job(
            title=f"{title} #{i}",
            company=company,
            description=f"Job {i}: We need a {title} to work on {company}'s platform. "
                        f"Requirements: 5+ years experience, Python, distributed systems. "
                        f"This is a {'remote' if i % 2 == 0 else 'hybrid'} position.",
            source=f"Source{i % 5}",
        ))
    return jobs


@pytest.fixture
def fake_scrapers():
    """Create 10 fake scrapers with varying speeds and outcomes."""
    jobs_per = [make_fake_job(title=f"Job from S{i}", source=f"Scraper{i}") for i in range(10)]
    scrapers = []
    for i in range(10):
        # Scrapers 0-6: succeed with 1-3 jobs, varying delay
        # Scraper 7: slow (simulates timeout-prone source)
        # Scraper 8: fails with exception
        # Scraper 9: returns 0 jobs (empty source)
        if i == 7:
            scrapers.append(FakeScraper(f"SlowSource", [jobs_per[i]], delay=0.3))
        elif i == 8:
            scrapers.append(FakeScraper(f"FailSource", fail=True))
        elif i == 9:
            scrapers.append(FakeScraper(f"EmptySource", jobs=[], delay=0.02))
        else:
            scrapers.append(FakeScraper(
                f"Source{i}",
                jobs=[jobs_per[i]] * (i % 3 + 1),
                delay=0.02 + (i * 0.01),
            ))
    return scrapers
