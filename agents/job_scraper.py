"""
Agent 1: Job Search Scraper with Auto-Healing
Searches 25+ remote job APIs and boards, deduplicates results,
and self-heals when selectors or API structures change.

Each source has a quality score (0-100) estimating lead-to-opportunity
conversion rate, used to weight composite scores on the dashboard.
"""

import hashlib
import json
import re
import time
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup


# ─── Source Quality Scores ─────────────────────────────────────────
# Estimated lead-to-opportunity conversion rate (0-100) based on:
#   - Direct employer postings vs aggregated/scraped
#   - Recruiter response rate
#   - Job freshness / active hiring signal
#   - India-remote friendliness
#   - Community reputation for actual hires
SOURCE_QUALITY = {
    # Tier 1: Highest conversion — direct employer postings, high response
    "LinkedIn":         92,  # #1 globally, direct recruiter contact, InMail
    "Wellfound":        88,  # Startup founders post directly, quick response
    "Turing":           87,  # Pre-vetted, US companies specifically hiring India
    "Instahyre":        85,  # AI-matched, India-focused, employer-initiated
    "Cutshort":         84,  # India tech, direct company chat
    "Toptal":           83,  # Vetted network, high-paying contracts guaranteed
    # Tier 2: Strong conversion — curated boards, good response
    "WeWorkRemotely":   80,  # Largest remote board, companies pay to post
    "Remotive":         78,  # Curated, quality postings, good remote culture
    "Arc.dev":          77,  # WWR partner, timezone-matched
    "Gun.io":           76,  # Vetted freelance, screened projects
    "Contra":           75,  # 0% fee, direct client relationships
    "Indeed":           74,  # Massive volume, direct apply, variable quality
    "Naukri":           73,  # India's largest, direct employer postings
    # Tier 3: Good lead source — APIs with volume
    "RemoteOK":         70,  # Large volume, some stale listings
    "Himalayas":        68,  # Good remote-first data
    "Jobicy":           67,  # Salary data, smaller but focused
    "WorkingNomads":    65,  # Nomad-friendly, good for contract
    "Glassdoor":        65,  # Company insights, some listings gated
    "FlexJobs":         64,  # Hand-screened but membership-gated
    "Monster":          62,  # Legacy board, declining relevance
    # Tier 4: Supplementary — aggregators, lower signal
    "Jobgether":        58,  # AI-matched but newer platform
    "Arbeitnow":        55,  # EU-heavy, limited global
    "Remote.co":        55,  # Small curated list
    "JustRemote":       52,  # Smaller board
    "RemoteRocketship": 50,  # Aggregator, may have stale data
    "Truelancer":       48,  # Freelance, lower-end projects typical
}


@dataclass
class JobListing:
    title: str
    company: str
    location: str
    salary: str
    job_type: str  # contract, full-time, freelance
    description: str
    url: str
    source: str
    tags: list = field(default_factory=list)
    posted_date: str = ""
    remote: bool = True
    region: str = ""
    currency: str = "USD"
    salary_min: float = 0
    salary_max: float = 0
    source_quality: int = 50
    uid: str = ""

    def __post_init__(self):
        if not self.uid:
            raw = f"{self.title.lower().strip()}|{self.company.lower().strip()}"
            self.uid = hashlib.md5(raw.encode()).hexdigest()
        if not self.source_quality:
            self.source_quality = SOURCE_QUALITY.get(self.source, 50)


class AutoHealingScraper:
    """Base scraper with retry, fallback selectors, and structural adaptation."""

    def __init__(self, name: str):
        self.name = name
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/html, */*",
        })
        self.stats = {"attempts": 0, "successes": 0, "failures": 0, "healed": 0}
        self.selector_versions = {}

    def fetch(self, url: str, retries: int = 3, delay: float = 2.0) -> Optional[requests.Response]:
        for attempt in range(retries):
            self.stats["attempts"] += 1
            try:
                resp = self.session.get(url, timeout=15)
                if resp.status_code == 200:
                    self.stats["successes"] += 1
                    return resp
                if resp.status_code == 429:
                    wait = delay * (2 ** attempt)
                    print(f"  [{self.name}] Rate limited, waiting {wait:.0f}s...")
                    time.sleep(wait)
                    continue
                if resp.status_code >= 500:
                    time.sleep(delay)
                    continue
                print(f"  [{self.name}] HTTP {resp.status_code} for {url}")
                self.stats["failures"] += 1
                return None
            except requests.RequestException as e:
                print(f"  [{self.name}] Request error (attempt {attempt+1}): {e}")
                time.sleep(delay)
        self.stats["failures"] += 1
        return None

    def heal_json_structure(self, data: dict, expected_keys: list, parent_key: str = "") -> dict:
        """Auto-detect renamed or restructured JSON keys."""
        healed = {}
        flat_keys = self._flatten_keys(data)
        for expected in expected_keys:
            if expected in data:
                healed[expected] = data[expected]
            else:
                for fk in flat_keys:
                    if expected.lower() in fk.lower():
                        healed[expected] = self._get_nested(data, fk)
                        self.stats["healed"] += 1
                        print(f"  [{self.name}] Healed: '{expected}' -> '{fk}'")
                        break
                else:
                    healed[expected] = ""
        return healed

    def _flatten_keys(self, d, prefix=""):
        keys = []
        if isinstance(d, dict):
            for k, v in d.items():
                full = f"{prefix}.{k}" if prefix else k
                keys.append(full)
                keys.extend(self._flatten_keys(v, full))
        return keys

    def _get_nested(self, d, key_path):
        parts = key_path.split(".")
        cur = d
        for p in parts:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                return ""
        return cur

    @staticmethod
    def _clean_html(html: str) -> str:
        if not html:
            return ""
        soup = BeautifulSoup(html, "html.parser")
        return soup.get_text(separator=" ", strip=True)[:2000]

    @staticmethod
    def _parse_salary_text(text: str) -> tuple:
        if not text:
            return 0, 0, "USD"
        currency = "USD"
        if "€" in text or "EUR" in text:
            currency = "EUR"
        elif "£" in text or "GBP" in text:
            currency = "GBP"
        elif "AUD" in text or "A$" in text:
            currency = "AUD"
        numbers = re.findall(r"[\d,]+(?:\.\d+)?", text.replace(",", ""))
        nums = [float(n) for n in numbers if float(n) > 100]
        if len(nums) >= 2:
            return min(nums), max(nums), currency
        elif len(nums) == 1:
            return nums[0], nums[0], currency
        return 0, 0, currency


# ─── API-based Scrapers ────────────────────────────────────────────


class RemotiveScraper(AutoHealingScraper):
    """Scrapes Remotive.com API for remote jobs."""

    BASE_URL = "https://remotive.com/api/remote-jobs"
    CATEGORIES = [
        "software-dev", "data", "devops", "product",
        "machine-learning", "mobile",
    ]

    def __init__(self):
        super().__init__("Remotive")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        for category in self.CATEGORIES:
            url = f"{self.BASE_URL}?category={category}&limit=50"
            resp = self.fetch(url)
            if not resp:
                continue
            try:
                data = resp.json()
                listings = data.get("jobs", data) if isinstance(data, dict) else data
                if not isinstance(listings, list):
                    for key in data:
                        if isinstance(data[key], list):
                            listings = data[key]
                            self.stats["healed"] += 1
                            break
                    else:
                        continue
                for item in listings:
                    job = self._parse_job(item, keywords)
                    if job and job.uid not in seen:
                        seen.add(job.uid)
                        jobs.append(job)
            except (json.JSONDecodeError, KeyError) as e:
                print(f"  [{self.name}] Parse error: {e}")
            time.sleep(0.5)
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_job(self, item: dict, keywords: list) -> Optional[JobListing]:
        title = item.get("title", "")
        desc = item.get("description", "")
        tags = item.get("tags", [])
        combined = f"{title} {desc} {' '.join(tags)}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None
        salary_text = item.get("salary", "") or ""
        smin, smax, currency = self._parse_salary_text(salary_text)
        job_type = item.get("job_type", "full_time") or "full_time"
        return JobListing(
            title=title,
            company=item.get("company_name", ""),
            location=item.get("candidate_required_location", "Anywhere"),
            salary=salary_text,
            job_type=job_type.replace("_", " "),
            description=self._clean_html(desc),
            url=item.get("url", ""),
            source="Remotive",
            tags=tags if isinstance(tags, list) else [],
            posted_date=item.get("publication_date", ""),
            region=item.get("candidate_required_location", "Global"),
            salary_min=smin,
            salary_max=smax,
            currency=currency,
        )


class RemoteOKScraper(AutoHealingScraper):
    """Scrapes RemoteOK API."""

    BASE_URL = "https://remoteok.com/api"

    def __init__(self):
        super().__init__("RemoteOK")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        resp = self.fetch(self.BASE_URL)
        if not resp:
            return jobs
        try:
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                if isinstance(data[0], dict) and "legal" in str(data[0]).lower():
                    data = data[1:]
            for item in data:
                if not isinstance(item, dict):
                    continue
                job = self._parse_job(item, keywords)
                if job and job.uid not in seen:
                    seen.add(job.uid)
                    jobs.append(job)
        except (json.JSONDecodeError, KeyError) as e:
            print(f"  [{self.name}] Parse error: {e}")
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_job(self, item: dict, keywords: list) -> Optional[JobListing]:
        title = item.get("position", "") or item.get("title", "")
        company = item.get("company", "")
        desc = item.get("description", "")
        tags = item.get("tags", []) or []
        combined = f"{title} {desc} {company} {' '.join(tags)}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None
        location = item.get("location", "Remote")
        salary_text = ""
        smin = float(item.get("salary_min", 0) or 0)
        smax = float(item.get("salary_max", 0) or 0)
        if smin or smax:
            salary_text = f"${smin:,.0f} - ${smax:,.0f}"
        slug = item.get("slug", "") or item.get("id", "")
        url = item.get("url", f"https://remoteok.com/remote-jobs/{slug}")
        return JobListing(
            title=title,
            company=company,
            location=location or "Remote",
            salary=salary_text,
            job_type=item.get("type", "full time") or "full time",
            description=desc[:2000] if desc else "",
            url=url,
            source="RemoteOK",
            tags=tags if isinstance(tags, list) else [],
            posted_date=item.get("date", ""),
            salary_min=smin,
            salary_max=smax,
        )


class ArbeitnowScraper(AutoHealingScraper):
    """Scrapes Arbeitnow API for remote jobs."""

    BASE_URL = "https://www.arbeitnow.com/api/job-board-api"

    def __init__(self):
        super().__init__("Arbeitnow")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        page = 1
        max_pages = 3
        while page <= max_pages:
            url = f"{self.BASE_URL}?page={page}"
            resp = self.fetch(url)
            if not resp:
                break
            try:
                data = resp.json()
                listings = data.get("data", [])
                if not listings:
                    break
                for item in listings:
                    if not item.get("remote", False):
                        continue
                    job = self._parse_job(item, keywords)
                    if job and job.uid not in seen:
                        seen.add(job.uid)
                        jobs.append(job)
                if not data.get("links", {}).get("next"):
                    break
                page += 1
                time.sleep(0.5)
            except (json.JSONDecodeError, KeyError) as e:
                print(f"  [{self.name}] Parse error: {e}")
                break
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_job(self, item: dict, keywords: list) -> Optional[JobListing]:
        title = item.get("title", "")
        desc = item.get("description", "")
        tags = item.get("tags", []) or []
        combined = f"{title} {desc} {' '.join(tags)}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None
        return JobListing(
            title=title,
            company=item.get("company_name", ""),
            location=item.get("location", "Remote"),
            salary="",
            job_type="full time",
            description=self._clean_html(desc),
            url=item.get("url", ""),
            source="Arbeitnow",
            tags=tags if isinstance(tags, list) else [],
            posted_date=item.get("created_at", ""),
            region="Europe",
        )


class HimalayasScraper(AutoHealingScraper):
    """Scrapes Himalayas.app API for remote jobs."""

    BASE_URL = "https://himalayas.app/jobs/api"

    def __init__(self):
        super().__init__("Himalayas")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        params = {"limit": 50, "offset": 0}
        for page in range(3):
            params["offset"] = page * 50
            resp = self.fetch(f"{self.BASE_URL}?limit={params['limit']}&offset={params['offset']}")
            if not resp:
                break
            try:
                data = resp.json()
                listings = data.get("jobs", [])
                if not listings:
                    break
                for item in listings:
                    job = self._parse_job(item, keywords)
                    if job and job.uid not in seen:
                        seen.add(job.uid)
                        jobs.append(job)
                time.sleep(0.5)
            except (json.JSONDecodeError, KeyError) as e:
                print(f"  [{self.name}] Parse error: {e}")
                break
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_job(self, item: dict, keywords: list) -> Optional[JobListing]:
        title = item.get("title", "")
        desc = item.get("description", "") or item.get("excerpt", "")
        categories = item.get("categories", []) or []
        combined = f"{title} {desc} {' '.join(categories)}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None
        smin = float(item.get("minSalary", 0) or 0)
        smax = float(item.get("maxSalary", 0) or 0)
        salary_text = f"${smin:,.0f} - ${smax:,.0f}" if (smin or smax) else ""
        company_data = item.get("companyName", "") or ""
        slug = item.get("slug", "")
        return JobListing(
            title=title,
            company=company_data if isinstance(company_data, str) else str(company_data),
            location=item.get("location", "Remote"),
            salary=salary_text,
            job_type=item.get("type", "full time") or "full time",
            description=desc[:2000] if desc else "",
            url=f"https://himalayas.app/jobs/{slug}" if slug else "",
            source="Himalayas",
            tags=categories,
            posted_date=item.get("pubDate", ""),
            salary_min=smin,
            salary_max=smax,
        )


class JobIcyScraper(AutoHealingScraper):
    """Scrapes Jobicy API for remote jobs."""

    BASE_URL = "https://jobicy.com/api/v2/remote-jobs"

    def __init__(self):
        super().__init__("Jobicy")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        for industry in ["tech", "engineering", "data-science"]:
            url = f"{self.BASE_URL}?count=50&industry={industry}"
            resp = self.fetch(url)
            if not resp:
                continue
            try:
                data = resp.json()
                listings = data.get("jobs", [])
                if not listings:
                    continue
                for item in listings:
                    job = self._parse_job(item, keywords)
                    if job and job.uid not in seen:
                        seen.add(job.uid)
                        jobs.append(job)
                time.sleep(0.5)
            except (json.JSONDecodeError, KeyError) as e:
                print(f"  [{self.name}] Parse error: {e}")
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_job(self, item: dict, keywords: list) -> Optional[JobListing]:
        title = item.get("jobTitle", "")
        desc = item.get("jobDescription", "")
        combined = f"{title} {desc}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None
        salary_text = ""
        smin = float(item.get("annualSalaryMin", 0) or 0)
        smax = float(item.get("annualSalaryMax", 0) or 0)
        if smin or smax:
            salary_text = f"${smin:,.0f} - ${smax:,.0f}"
        return JobListing(
            title=title,
            company=item.get("companyName", ""),
            location=item.get("jobGeo", "Remote"),
            salary=salary_text,
            job_type=item.get("jobType", "full time") or "full time",
            description=self._clean_html(desc),
            url=item.get("url", ""),
            source="Jobicy",
            tags=item.get("jobIndustry", []) or [],
            posted_date=item.get("pubDate", ""),
            salary_min=smin,
            salary_max=smax,
            region=item.get("jobGeo", "Global"),
        )


class WorkingNomadsScraper(AutoHealingScraper):
    """Scrapes Working Nomads public JSON API."""

    BASE_URL = "https://www.workingnomads.com/api/exposed_jobs/"

    def __init__(self):
        super().__init__("WorkingNomads")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        resp = self.fetch(self.BASE_URL)
        if not resp:
            return jobs
        try:
            data = resp.json()
            if not isinstance(data, list):
                data = data.get("jobs", data.get("results", []))
            for item in data:
                if not isinstance(item, dict):
                    continue
                job = self._parse_job(item, keywords)
                if job and job.uid not in seen:
                    seen.add(job.uid)
                    jobs.append(job)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            print(f"  [{self.name}] Parse error: {e}")
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_job(self, item: dict, keywords: list) -> Optional[JobListing]:
        title = item.get("title", "")
        desc = item.get("description", "") or item.get("content", "")
        company = item.get("company_name", "") or item.get("company", "")
        tags_raw = item.get("tags", "") or ""
        tags = tags_raw.split(",") if isinstance(tags_raw, str) else (tags_raw or [])
        tags = [t.strip() for t in tags if t.strip()]
        category = item.get("category_name", "") or ""
        combined = f"{title} {desc} {company} {category} {' '.join(tags)}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None
        location = item.get("location", "") or "Remote"
        return JobListing(
            title=title,
            company=company,
            location=location,
            salary="",
            job_type=item.get("job_type", "full time") or "full time",
            description=self._clean_html(desc),
            url=item.get("url", "") or item.get("apply_url", ""),
            source="WorkingNomads",
            tags=tags,
            posted_date=item.get("pub_date", "") or item.get("published", ""),
            region="Global",
        )


# ─── RSS / Web Scrapers ───────────────────────────────────────────


class WeWorkRemotelyScraper(AutoHealingScraper):
    """Scrapes We Work Remotely RSS feeds — largest remote-only job board."""

    RSS_FEEDS = [
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
        "https://weworkremotely.com/categories/remote-management-finance-jobs.rss",
        "https://weworkremotely.com/categories/remote-design-jobs.rss",
        "https://weworkremotely.com/categories/remote-front-end-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
    ]

    def __init__(self):
        super().__init__("WeWorkRemotely")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        for feed_url in self.RSS_FEEDS:
            resp = self.fetch(feed_url)
            if not resp:
                continue
            try:
                root = ET.fromstring(resp.content)
                channel = root.find("channel")
                if channel is None:
                    continue
                for item in channel.findall("item"):
                    job = self._parse_rss_item(item, keywords)
                    if job and job.uid not in seen:
                        seen.add(job.uid)
                        jobs.append(job)
            except ET.ParseError as e:
                print(f"  [{self.name}] RSS parse error: {e}")
            time.sleep(0.3)
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_rss_item(self, item, keywords: list) -> Optional[JobListing]:
        title_el = item.find("title")
        title = title_el.text.strip() if title_el is not None and title_el.text else ""
        link_el = item.find("link")
        url = link_el.text.strip() if link_el is not None and link_el.text else ""
        desc_el = item.find("description")
        desc = desc_el.text.strip() if desc_el is not None and desc_el.text else ""
        pubdate_el = item.find("pubDate")
        pubdate = pubdate_el.text.strip() if pubdate_el is not None and pubdate_el.text else ""

        # WWR titles are formatted as "Company: Job Title"
        company = ""
        job_title = title
        if ": " in title:
            parts = title.split(": ", 1)
            company = parts[0].strip()
            job_title = parts[1].strip()

        combined = f"{job_title} {desc} {company}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None

        return JobListing(
            title=job_title,
            company=company,
            location="Remote",
            salary="",
            job_type="full time",
            description=self._clean_html(desc),
            url=url,
            source="WeWorkRemotely",
            tags=[],
            posted_date=pubdate,
            region="Global",
        )


class RemoteRocketshipScraper(AutoHealingScraper):
    """Scrapes RemoteRocketship — large aggregator with India page."""

    BASE_URL = "https://www.remoterocketship.com"

    def __init__(self):
        super().__init__("RemoteRocketship")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        # Scrape both global and India-specific pages
        pages = [
            f"{self.BASE_URL}/country/india/",
            f"{self.BASE_URL}/jobs/software-engineer",
            f"{self.BASE_URL}/jobs/machine-learning",
        ]
        for page_url in pages:
            resp = self.fetch(page_url)
            if not resp:
                continue
            try:
                soup = BeautifulSoup(resp.text, "html.parser")
                # Find job listing cards/rows
                for card in soup.select("a[href*='/job/'], .job-card, .job-listing, tr.job-row, div[class*='job']"):
                    job = self._parse_card(card, keywords, page_url)
                    if job and job.uid not in seen:
                        seen.add(job.uid)
                        jobs.append(job)
            except Exception as e:
                print(f"  [{self.name}] Parse error on {page_url}: {e}")
            time.sleep(0.5)
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_card(self, card, keywords: list, page_url: str) -> Optional[JobListing]:
        # Extract text content
        text = card.get_text(separator=" ", strip=True)
        if len(text) < 10:
            return None

        # Try to find title and company from structure
        title = ""
        company = ""
        url = ""

        # Get link
        if card.name == "a" and card.get("href"):
            url = card["href"]
            if not url.startswith("http"):
                url = self.BASE_URL + url
        else:
            link = card.find("a", href=True)
            if link:
                url = link["href"]
                if not url.startswith("http"):
                    url = self.BASE_URL + url

        # Try heading tags for title
        heading = card.find(["h2", "h3", "h4", "strong"])
        if heading:
            title = heading.get_text(strip=True)

        # Try to extract company from secondary text
        spans = card.find_all(["span", "p", "div"])
        for s in spans:
            s_text = s.get_text(strip=True)
            if s_text and s_text != title and len(s_text) < 60 and not company:
                company = s_text
                break

        if not title:
            # Fall back to first meaningful text chunk
            title = text[:80]

        combined = f"{title} {text}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None

        return JobListing(
            title=title[:120],
            company=company[:60],
            location="Remote",
            salary="",
            job_type="full time",
            description=text[:2000],
            url=url,
            source="RemoteRocketship",
            tags=[],
            region="Global",
        )


class JustRemoteScraper(AutoHealingScraper):
    """Scrapes JustRemote.co — worldwide remote jobs, timezone-aware."""

    BASE_URL = "https://justremote.co"

    def __init__(self):
        super().__init__("JustRemote")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        pages = [
            f"{self.BASE_URL}/remote-developer-jobs",
            f"{self.BASE_URL}/remote-engineering-jobs",
            f"{self.BASE_URL}/remote-devops-jobs",
        ]
        for page_url in pages:
            resp = self.fetch(page_url)
            if not resp:
                continue
            try:
                soup = BeautifulSoup(resp.text, "html.parser")
                # JustRemote uses job cards with links
                for card in soup.select("a[href*='/remote-jobs/'], .job-card, div[class*='job'], article"):
                    job = self._parse_card(card, keywords)
                    if job and job.uid not in seen:
                        seen.add(job.uid)
                        jobs.append(job)
            except Exception as e:
                print(f"  [{self.name}] Parse error: {e}")
            time.sleep(0.5)
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_card(self, card, keywords: list) -> Optional[JobListing]:
        text = card.get_text(separator=" ", strip=True)
        if len(text) < 10:
            return None

        url = ""
        if card.name == "a" and card.get("href"):
            url = card["href"]
            if not url.startswith("http"):
                url = self.BASE_URL + url
        else:
            link = card.find("a", href=True)
            if link:
                url = link["href"]
                if not url.startswith("http"):
                    url = self.BASE_URL + url

        title = ""
        company = ""
        heading = card.find(["h2", "h3", "h4", "strong"])
        if heading:
            title = heading.get_text(strip=True)
        if not title:
            title = text[:80]

        spans = card.find_all(["span", "p"])
        for s in spans:
            s_text = s.get_text(strip=True)
            if s_text and s_text != title and len(s_text) < 60:
                company = s_text
                break

        combined = f"{title} {text}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None

        return JobListing(
            title=title[:120],
            company=company[:60],
            location="Remote",
            salary="",
            job_type="full time",
            description=text[:2000],
            url=url,
            source="JustRemote",
            tags=[],
            region="Worldwide",
        )


class ArcDevScraper(AutoHealingScraper):
    """Scrapes Arc.dev — remote jobs for Indian developers, partners with WWR."""

    BASE_URL = "https://arc.dev"

    def __init__(self):
        super().__init__("Arc.dev")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        pages = [
            f"{self.BASE_URL}/en-in/remote-jobs",
            f"{self.BASE_URL}/remote-jobs/software-engineer",
            f"{self.BASE_URL}/remote-jobs/machine-learning",
            f"{self.BASE_URL}/remote-jobs/mobile-developer",
        ]
        for page_url in pages:
            resp = self.fetch(page_url)
            if not resp:
                continue
            try:
                soup = BeautifulSoup(resp.text, "html.parser")
                for card in soup.select("a[href*='/company/'], a[href*='/remote-jobs/'], div[class*='job'], article"):
                    job = self._parse_card(card, keywords)
                    if job and job.uid not in seen:
                        seen.add(job.uid)
                        jobs.append(job)
            except Exception as e:
                print(f"  [{self.name}] Parse error: {e}")
            time.sleep(0.5)
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_card(self, card, keywords: list) -> Optional[JobListing]:
        text = card.get_text(separator=" ", strip=True)
        if len(text) < 15:
            return None

        url = ""
        if card.name == "a" and card.get("href"):
            url = card["href"]
            if not url.startswith("http"):
                url = self.BASE_URL + url
        else:
            link = card.find("a", href=True)
            if link:
                url = link["href"]
                if not url.startswith("http"):
                    url = self.BASE_URL + url

        title = ""
        company = ""
        heading = card.find(["h2", "h3", "h4", "strong"])
        if heading:
            title = heading.get_text(strip=True)
        if not title:
            title = text[:80]

        combined = f"{title} {text}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None

        return JobListing(
            title=title[:120],
            company=company[:60] if company else "",
            location="Remote — India eligible",
            salary="",
            job_type="full time",
            description=text[:2000],
            url=url,
            source="Arc.dev",
            tags=[],
            region="Global",
        )


class WellfoundScraper(AutoHealingScraper):
    """Scrapes Wellfound (AngelList) — startup remote roles."""

    BASE_URL = "https://wellfound.com"

    def __init__(self):
        super().__init__("Wellfound")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        pages = [
            f"{self.BASE_URL}/jobs?remote=true",
            f"{self.BASE_URL}/role/software-engineer?remote=true",
            f"{self.BASE_URL}/role/mobile-developer?remote=true",
        ]
        for page_url in pages:
            resp = self.fetch(page_url)
            if not resp:
                continue
            try:
                soup = BeautifulSoup(resp.text, "html.parser")
                # Wellfound uses startup-styled job cards
                for card in soup.select("a[href*='/jobs/'], div[class*='job'], div[class*='listing'], article"):
                    job = self._parse_card(card, keywords)
                    if job and job.uid not in seen:
                        seen.add(job.uid)
                        jobs.append(job)
            except Exception as e:
                print(f"  [{self.name}] Parse error: {e}")
            time.sleep(0.5)
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_card(self, card, keywords: list) -> Optional[JobListing]:
        text = card.get_text(separator=" ", strip=True)
        if len(text) < 15:
            return None

        url = ""
        if card.name == "a" and card.get("href"):
            url = card["href"]
            if not url.startswith("http"):
                url = self.BASE_URL + url
        else:
            link = card.find("a", href=True)
            if link:
                url = link["href"]
                if not url.startswith("http"):
                    url = self.BASE_URL + url

        title = ""
        company = ""
        heading = card.find(["h2", "h3", "h4", "strong"])
        if heading:
            title = heading.get_text(strip=True)
        if not title:
            title = text[:80]

        # Try to find salary info
        salary_text = ""
        salary_match = re.search(r'\$[\d,]+\s*[-–]\s*\$[\d,]+', text)
        if salary_match:
            salary_text = salary_match.group()
        smin, smax, currency = self._parse_salary_text(salary_text)

        combined = f"{title} {text}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None

        return JobListing(
            title=title[:120],
            company=company[:60] if company else "",
            location="Remote",
            salary=salary_text,
            job_type="full time",
            description=text[:2000],
            url=url,
            source="Wellfound",
            tags=[],
            region="Global",
            salary_min=smin,
            salary_max=smax,
        )


# ─── Major Platforms ───────────────────────────────────────────────


class LinkedInScraper(AutoHealingScraper):
    """Scrapes LinkedIn public guest API — no login required.
    Uses linkedin.com/jobs-guest/ endpoints served to search crawlers."""

    BASE_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

    def __init__(self):
        super().__init__("LinkedIn")
        # LinkedIn guest API needs a browser-like accept header
        self.session.headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        })

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        # Build search queries from keywords
        search_terms = [
            "remote engineer",
            "remote developer",
            "remote architect",
            "remote AI ML",
            "remote mobile developer",
        ]
        # Also use actual keywords
        top_kw = [k for k in keywords if len(k) > 3][:3]
        for kw in top_kw:
            search_terms.append(f"remote {kw}")

        for term in search_terms[:6]:  # Cap at 6 queries
            for start in [0, 25]:  # 2 pages of 25
                url = f"{self.BASE_URL}?keywords={quote_plus(term)}&location=Worldwide&f_WT=2&start={start}"
                resp = self.fetch(url, retries=2, delay=3.0)
                if not resp:
                    continue
                try:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for card in soup.select("li, div.base-card, div.job-search-card"):
                        job = self._parse_card(card, keywords)
                        if job and job.uid not in seen:
                            seen.add(job.uid)
                            jobs.append(job)
                except Exception as e:
                    print(f"  [{self.name}] Parse error: {e}")
                time.sleep(1.5)  # Respectful rate limiting
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_card(self, card, keywords: list) -> Optional[JobListing]:
        # LinkedIn guest cards have specific class names
        title_el = card.find(class_=re.compile(r"base-search-card__title|job-search-card__title"))
        company_el = card.find(class_=re.compile(r"base-search-card__subtitle|job-search-card__company"))
        location_el = card.find(class_=re.compile(r"job-search-card__location"))
        link_el = card.find("a", href=True)
        date_el = card.find("time")

        title = title_el.get_text(strip=True) if title_el else ""
        company = company_el.get_text(strip=True) if company_el else ""
        location = location_el.get_text(strip=True) if location_el else "Remote"
        url = link_el["href"].split("?")[0] if link_el else ""
        posted = date_el.get("datetime", "") if date_el else ""

        if not title:
            # Fallback: try any heading
            heading = card.find(["h3", "h4", "strong"])
            if heading:
                title = heading.get_text(strip=True)
        if not title or len(title) < 5:
            return None

        combined = f"{title} {company} {location}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None

        return JobListing(
            title=title[:120],
            company=company[:80],
            location=location,
            salary="",
            job_type="full time",
            description=f"{title} at {company}. Location: {location}",
            url=url,
            source="LinkedIn",
            tags=[],
            posted_date=posted,
            region="Global",
            source_quality=SOURCE_QUALITY.get("LinkedIn", 92),
        )


class IndeedScraper(AutoHealingScraper):
    """Scrapes Indeed public search pages — server-side rendered."""

    BASE_URL = "https://www.indeed.com"

    def __init__(self):
        super().__init__("Indeed")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        search_terms = ["remote engineer", "remote developer", "remote architect"]
        top_kw = [k for k in keywords if len(k) > 3][:2]
        for kw in top_kw:
            search_terms.append(f"remote {kw}")

        for term in search_terms[:4]:
            # remotejob filter for Indeed
            url = f"{self.BASE_URL}/jobs?q={quote_plus(term)}&remotejob=032b3046-06a3-4876-8dfd-474eb5e7ed11&sort=date"
            resp = self.fetch(url, retries=2, delay=3.0)
            if not resp:
                continue
            try:
                soup = BeautifulSoup(resp.text, "html.parser")
                # Indeed uses various card selectors
                for card in soup.select("div.job_seen_beacon, div.jobsearch-ResultsList div[data-jk], td.resultContent, div[class*='cardOutline']"):
                    job = self._parse_card(card, keywords)
                    if job and job.uid not in seen:
                        seen.add(job.uid)
                        jobs.append(job)
            except Exception as e:
                print(f"  [{self.name}] Parse error: {e}")
            time.sleep(2.0)  # Indeed is aggressive on rate limits
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_card(self, card, keywords: list) -> Optional[JobListing]:
        title_el = card.find(class_=re.compile(r"jobTitle|job-title")) or card.find("h2")
        company_el = card.find(class_=re.compile(r"company|companyName")) or card.find("span", {"data-testid": "company-name"})
        location_el = card.find(class_=re.compile(r"companyLocation|job-location"))
        salary_el = card.find(class_=re.compile(r"salary|attribute_snippet"))
        link_el = card.find("a", href=True)

        title = title_el.get_text(strip=True) if title_el else ""
        company = company_el.get_text(strip=True) if company_el else ""
        location = location_el.get_text(strip=True) if location_el else "Remote"
        salary_text = salary_el.get_text(strip=True) if salary_el else ""

        url = ""
        if link_el:
            href = link_el.get("href", "")
            if href.startswith("/"):
                url = self.BASE_URL + href
            elif href.startswith("http"):
                url = href

        if not title or len(title) < 5:
            return None

        smin, smax, cur = self._parse_salary_text(salary_text)
        combined = f"{title} {company} {location}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None

        return JobListing(
            title=title[:120],
            company=company[:80],
            location=location,
            salary=salary_text,
            job_type="full time",
            description=f"{title} at {company}. {location}. {salary_text}",
            url=url,
            source="Indeed",
            tags=[],
            region="Global",
            salary_min=smin,
            salary_max=smax,
            source_quality=SOURCE_QUALITY.get("Indeed", 74),
        )


class NaukriScraper(AutoHealingScraper):
    """Scrapes Naukri.com — India's largest job portal, WFH filter."""

    BASE_URL = "https://www.naukri.com"

    def __init__(self):
        super().__init__("Naukri")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        search_terms = ["remote-software-engineer", "work-from-home-developer", "remote-architect"]
        top_kw = [k for k in keywords if len(k) > 3][:3]
        for kw in top_kw:
            search_terms.append(f"remote-{kw.replace(' ', '-')}")

        for term in search_terms[:5]:
            url = f"{self.BASE_URL}/{term}-jobs?wfhType=2"  # wfhType=2 = remote
            resp = self.fetch(url, retries=2, delay=2.0)
            if not resp:
                continue
            try:
                soup = BeautifulSoup(resp.text, "html.parser")
                for card in soup.select("article.jobTuple, div.srp-jobtuple, div[class*='jobTuple'], div[class*='cust-job-tuple']"):
                    job = self._parse_card(card, keywords)
                    if job and job.uid not in seen:
                        seen.add(job.uid)
                        jobs.append(job)
            except Exception as e:
                print(f"  [{self.name}] Parse error: {e}")
            time.sleep(1.5)
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_card(self, card, keywords: list) -> Optional[JobListing]:
        title_el = card.find(class_=re.compile(r"title|designation|jobTitle")) or card.find("a", class_=re.compile(r"title"))
        company_el = card.find(class_=re.compile(r"comp-name|companyInfo|subTitle"))
        location_el = card.find(class_=re.compile(r"loc|location|locWd498"))
        salary_el = card.find(class_=re.compile(r"sal|salary|salWd498"))
        exp_el = card.find(class_=re.compile(r"exp|experience|expwdth"))

        title = title_el.get_text(strip=True) if title_el else ""
        company = company_el.get_text(strip=True) if company_el else ""
        location = location_el.get_text(strip=True) if location_el else "India — Remote"
        salary_text = salary_el.get_text(strip=True) if salary_el else ""

        url = ""
        link = title_el if title_el and title_el.name == "a" else card.find("a", href=True)
        if link and link.get("href"):
            url = link["href"]
            if not url.startswith("http"):
                url = self.BASE_URL + url

        if not title or len(title) < 5:
            return None

        combined = f"{title} {company}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None

        return JobListing(
            title=title[:120],
            company=company[:80],
            location=location,
            salary=salary_text,
            job_type="full time",
            description=f"{title} at {company}. {location}. {salary_text}",
            url=url,
            source="Naukri",
            tags=["india"],
            region="India",
            source_quality=SOURCE_QUALITY.get("Naukri", 73),
        )


class MonsterScraper(AutoHealingScraper):
    """Scrapes Monster.com — legacy major board."""

    BASE_URL = "https://www.monster.com"

    def __init__(self):
        super().__init__("Monster")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        search_terms = ["remote-software-engineer", "remote-developer"]
        top_kw = [k for k in keywords if len(k) > 3][:2]
        for kw in top_kw:
            search_terms.append(f"remote-{kw.replace(' ', '-')}")

        for term in search_terms[:4]:
            url = f"{self.BASE_URL}/jobs/search?q={quote_plus(term)}&where=remote"
            resp = self.fetch(url, retries=2, delay=2.0)
            if not resp:
                continue
            try:
                soup = BeautifulSoup(resp.text, "html.parser")
                for card in soup.select("div[class*='job-cardstyle'], div[class*='card-content'], article[class*='job'], div[data-testid*='job']"):
                    job = self._parse_card(card, keywords)
                    if job and job.uid not in seen:
                        seen.add(job.uid)
                        jobs.append(job)
            except Exception as e:
                print(f"  [{self.name}] Parse error: {e}")
            time.sleep(1.5)
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_card(self, card, keywords: list) -> Optional[JobListing]:
        title_el = card.find(class_=re.compile(r"title|job-title")) or card.find(["h2", "h3"])
        company_el = card.find(class_=re.compile(r"company|employer"))
        location_el = card.find(class_=re.compile(r"location"))
        link_el = card.find("a", href=True)

        title = title_el.get_text(strip=True) if title_el else ""
        company = company_el.get_text(strip=True) if company_el else ""
        location = location_el.get_text(strip=True) if location_el else "Remote"

        url = ""
        if link_el:
            href = link_el.get("href", "")
            url = href if href.startswith("http") else self.BASE_URL + href

        if not title or len(title) < 5:
            return None

        combined = f"{title} {company}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None

        return JobListing(
            title=title[:120],
            company=company[:80],
            location=location,
            salary="",
            job_type="full time",
            description=f"{title} at {company}. {location}.",
            url=url,
            source="Monster",
            tags=[],
            region="Global",
            source_quality=SOURCE_QUALITY.get("Monster", 62),
        )


class GlassdoorScraper(AutoHealingScraper):
    """Scrapes Glassdoor public job listings."""

    BASE_URL = "https://www.glassdoor.com"

    def __init__(self):
        super().__init__("Glassdoor")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        search_terms = ["remote software engineer", "remote developer"]
        top_kw = [k for k in keywords if len(k) > 3][:2]
        for kw in top_kw:
            search_terms.append(f"remote {kw}")

        for term in search_terms[:3]:
            url = f"{self.BASE_URL}/Job/remote-{quote_plus(term.replace(' ', '-'))}-jobs-SRCH_IL.0,6_IS11047_KO7,30.htm"
            resp = self.fetch(url, retries=2, delay=3.0)
            if not resp:
                # Try alternate URL format
                url = f"{self.BASE_URL}/Job/jobs.htm?sc.keyword={quote_plus(term)}&remoteWorkType=1"
                resp = self.fetch(url, retries=1, delay=2.0)
                if not resp:
                    continue
            try:
                soup = BeautifulSoup(resp.text, "html.parser")
                for card in soup.select("li.react-job-listing, div[data-test='jobListing'], div[class*='JobCard'], article"):
                    job = self._parse_card(card, keywords)
                    if job and job.uid not in seen:
                        seen.add(job.uid)
                        jobs.append(job)
            except Exception as e:
                print(f"  [{self.name}] Parse error: {e}")
            time.sleep(2.0)
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_card(self, card, keywords: list) -> Optional[JobListing]:
        title_el = card.find(class_=re.compile(r"jobTitle|job-title")) or card.find(["h2", "h3", "a"])
        company_el = card.find(class_=re.compile(r"employer|company"))
        location_el = card.find(class_=re.compile(r"location|loc"))
        salary_el = card.find(class_=re.compile(r"salary"))
        link_el = card.find("a", href=True)

        title = title_el.get_text(strip=True) if title_el else ""
        company = company_el.get_text(strip=True) if company_el else ""
        location = location_el.get_text(strip=True) if location_el else "Remote"
        salary_text = salary_el.get_text(strip=True) if salary_el else ""

        url = ""
        if link_el:
            href = link_el.get("href", "")
            url = href if href.startswith("http") else self.BASE_URL + href

        if not title or len(title) < 5:
            return None

        smin, smax, cur = self._parse_salary_text(salary_text)
        combined = f"{title} {company}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None

        return JobListing(
            title=title[:120],
            company=company[:80],
            location=location,
            salary=salary_text,
            job_type="full time",
            description=f"{title} at {company}. {location}. {salary_text}",
            url=url,
            source="Glassdoor",
            tags=[],
            region="Global",
            salary_min=smin,
            salary_max=smax,
            source_quality=SOURCE_QUALITY.get("Glassdoor", 65),
        )


class RemoteCoScraper(AutoHealingScraper):
    """Scrapes Remote.co — curated, hand-picked remote roles."""

    BASE_URL = "https://remote.co"

    def __init__(self):
        super().__init__("Remote.co")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        pages = [
            f"{self.BASE_URL}/remote-jobs/developer/",
            f"{self.BASE_URL}/remote-jobs/design/",
            f"{self.BASE_URL}/remote-jobs/devops-sysadmin/",
            f"{self.BASE_URL}/remote-jobs/it/",
        ]
        for page_url in pages:
            resp = self.fetch(page_url)
            if not resp:
                continue
            try:
                soup = BeautifulSoup(resp.text, "html.parser")
                for card in soup.select("a.card, .job_listing, a[href*='/job/'], div[class*='job']"):
                    job = self._parse_card(card, keywords)
                    if job and job.uid not in seen:
                        seen.add(job.uid)
                        jobs.append(job)
            except Exception as e:
                print(f"  [{self.name}] Parse error: {e}")
            time.sleep(0.5)
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_card(self, card, keywords: list) -> Optional[JobListing]:
        text = card.get_text(separator=" ", strip=True)
        if len(text) < 10:
            return None
        url = ""
        if card.name == "a" and card.get("href"):
            url = card["href"]
            if not url.startswith("http"):
                url = self.BASE_URL + url
        else:
            link = card.find("a", href=True)
            if link:
                url = link["href"]
                if not url.startswith("http"):
                    url = self.BASE_URL + url
        title = ""
        company = ""
        heading = card.find(["h2", "h3", "h4", "strong"])
        if heading:
            title = heading.get_text(strip=True)
        if not title:
            title = text[:80]
        combined = f"{title} {text}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None
        return JobListing(
            title=title[:120], company=company[:60], location="Remote",
            salary="", job_type="full time", description=text[:2000],
            url=url, source="Remote.co", tags=[], region="Global",
        )


class JobgetherScraper(AutoHealingScraper):
    """Scrapes Jobgether — AI-matched remote jobs, has India page."""

    BASE_URL = "https://jobgether.com"

    def __init__(self):
        super().__init__("Jobgether")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        pages = [
            f"{self.BASE_URL}/remote-jobs/india",
            f"{self.BASE_URL}/remote-jobs/engineering",
            f"{self.BASE_URL}/remote-jobs/data-science",
        ]
        for page_url in pages:
            resp = self.fetch(page_url)
            if not resp:
                continue
            try:
                soup = BeautifulSoup(resp.text, "html.parser")
                for card in soup.select("a[href*='/offer/'], a[href*='/job/'], div[class*='job'], article"):
                    job = self._parse_card(card, keywords)
                    if job and job.uid not in seen:
                        seen.add(job.uid)
                        jobs.append(job)
            except Exception as e:
                print(f"  [{self.name}] Parse error: {e}")
            time.sleep(0.5)
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_card(self, card, keywords: list) -> Optional[JobListing]:
        text = card.get_text(separator=" ", strip=True)
        if len(text) < 15:
            return None
        url = ""
        if card.name == "a" and card.get("href"):
            url = card["href"]
            if not url.startswith("http"):
                url = self.BASE_URL + url
        else:
            link = card.find("a", href=True)
            if link:
                url = link["href"]
                if not url.startswith("http"):
                    url = self.BASE_URL + url
        title = ""
        heading = card.find(["h2", "h3", "h4", "strong"])
        if heading:
            title = heading.get_text(strip=True)
        if not title:
            title = text[:80]
        combined = f"{title} {text}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None
        # Try salary
        salary_text = ""
        salary_match = re.search(r'[\$€£][\d,]+\s*[-–]\s*[\$€£]?[\d,]+', text)
        if salary_match:
            salary_text = salary_match.group()
        smin, smax, cur = self._parse_salary_text(salary_text)
        return JobListing(
            title=title[:120], company="", location="Remote",
            salary=salary_text, job_type="full time", description=text[:2000],
            url=url, source="Jobgether", tags=[], region="Global",
            salary_min=smin, salary_max=smax, currency=cur,
        )


class ToptalScraper(AutoHealingScraper):
    """Scrapes Toptal — top 3% freelancers, high-paying contracts."""

    BASE_URL = "https://www.toptal.com"

    def __init__(self):
        super().__init__("Toptal")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        pages = [
            f"{self.BASE_URL}/freelance-jobs",
            f"{self.BASE_URL}/freelance-jobs/developers",
            f"{self.BASE_URL}/freelance-jobs/mobile",
        ]
        for page_url in pages:
            resp = self.fetch(page_url)
            if not resp:
                continue
            try:
                soup = BeautifulSoup(resp.text, "html.parser")
                for card in soup.select("a[href*='/freelance-jobs/'], div[class*='job'], li[class*='job'], article"):
                    job = self._parse_card(card, keywords)
                    if job and job.uid not in seen:
                        seen.add(job.uid)
                        jobs.append(job)
            except Exception as e:
                print(f"  [{self.name}] Parse error: {e}")
            time.sleep(0.5)
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_card(self, card, keywords: list) -> Optional[JobListing]:
        text = card.get_text(separator=" ", strip=True)
        if len(text) < 15:
            return None
        url = ""
        if card.name == "a" and card.get("href"):
            url = card["href"]
            if not url.startswith("http"):
                url = self.BASE_URL + url
        else:
            link = card.find("a", href=True)
            if link:
                url = link["href"]
                if not url.startswith("http"):
                    url = self.BASE_URL + url
        title = ""
        heading = card.find(["h2", "h3", "h4", "strong"])
        if heading:
            title = heading.get_text(strip=True)
        if not title:
            title = text[:80]
        combined = f"{title} {text}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None
        return JobListing(
            title=title[:120], company="Toptal Network", location="Remote",
            salary="", job_type="contract", description=text[:2000],
            url=url, source="Toptal", tags=["freelance", "contract"], region="Global",
        )


class ContraScraper(AutoHealingScraper):
    """Scrapes Contra — 0% commission freelance platform."""

    BASE_URL = "https://contra.com"

    def __init__(self):
        super().__init__("Contra")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        pages = [
            f"{self.BASE_URL}/opportunity",
            f"{self.BASE_URL}/search/opportunities?query=engineer",
            f"{self.BASE_URL}/search/opportunities?query=developer",
        ]
        for page_url in pages:
            resp = self.fetch(page_url)
            if not resp:
                continue
            try:
                soup = BeautifulSoup(resp.text, "html.parser")
                for card in soup.select("a[href*='/opportunity/'], a[href*='/project/'], div[class*='opportunity'], article"):
                    job = self._parse_card(card, keywords)
                    if job and job.uid not in seen:
                        seen.add(job.uid)
                        jobs.append(job)
            except Exception as e:
                print(f"  [{self.name}] Parse error: {e}")
            time.sleep(0.5)
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_card(self, card, keywords: list) -> Optional[JobListing]:
        text = card.get_text(separator=" ", strip=True)
        if len(text) < 15:
            return None
        url = ""
        if card.name == "a" and card.get("href"):
            url = card["href"]
            if not url.startswith("http"):
                url = self.BASE_URL + url
        else:
            link = card.find("a", href=True)
            if link:
                url = link["href"]
                if not url.startswith("http"):
                    url = self.BASE_URL + url
        title = ""
        heading = card.find(["h2", "h3", "h4", "strong"])
        if heading:
            title = heading.get_text(strip=True)
        if not title:
            title = text[:80]
        combined = f"{title} {text}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None
        return JobListing(
            title=title[:120], company="", location="Remote",
            salary="", job_type="freelance", description=text[:2000],
            url=url, source="Contra", tags=["freelance", "0% commission"], region="Global",
        )


class GunIOScraper(AutoHealingScraper):
    """Scrapes Gun.io — vetted freelance dev contracts."""

    BASE_URL = "https://gun.io"

    def __init__(self):
        super().__init__("Gun.io")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        resp = self.fetch(f"{self.BASE_URL}/freelance/find-work")
        if not resp:
            return jobs
        try:
            soup = BeautifulSoup(resp.text, "html.parser")
            for card in soup.select("a[href*='/projects/'], div[class*='project'], div[class*='job'], article"):
                job = self._parse_card(card, keywords)
                if job and job.uid not in seen:
                    seen.add(job.uid)
                    jobs.append(job)
        except Exception as e:
            print(f"  [{self.name}] Parse error: {e}")
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_card(self, card, keywords: list) -> Optional[JobListing]:
        text = card.get_text(separator=" ", strip=True)
        if len(text) < 15:
            return None
        url = ""
        if card.name == "a" and card.get("href"):
            url = card["href"]
            if not url.startswith("http"):
                url = self.BASE_URL + url
        else:
            link = card.find("a", href=True)
            if link:
                url = link["href"]
                if not url.startswith("http"):
                    url = self.BASE_URL + url
        title = ""
        heading = card.find(["h2", "h3", "h4", "strong"])
        if heading:
            title = heading.get_text(strip=True)
        if not title:
            title = text[:80]
        combined = f"{title} {text}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None
        return JobListing(
            title=title[:120], company="", location="Remote",
            salary="", job_type="contract", description=text[:2000],
            url=url, source="Gun.io", tags=["freelance", "vetted"], region="Global",
        )


class TuringScraper(AutoHealingScraper):
    """Scrapes Turing.com — US companies hiring Indian devs specifically."""

    BASE_URL = "https://www.turing.com"

    def __init__(self):
        super().__init__("Turing")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        pages = [
            f"{self.BASE_URL}/remote-developer-jobs",
            f"{self.BASE_URL}/remote-developer-jobs/a/android",
            f"{self.BASE_URL}/remote-developer-jobs/a/machine-learning",
            f"{self.BASE_URL}/remote-developer-jobs/a/full-stack",
        ]
        for page_url in pages:
            resp = self.fetch(page_url)
            if not resp:
                continue
            try:
                soup = BeautifulSoup(resp.text, "html.parser")
                for card in soup.select("a[href*='/remote-developer-jobs/'], div[class*='job'], li[class*='job'], article"):
                    job = self._parse_card(card, keywords)
                    if job and job.uid not in seen:
                        seen.add(job.uid)
                        jobs.append(job)
            except Exception as e:
                print(f"  [{self.name}] Parse error: {e}")
            time.sleep(0.5)
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_card(self, card, keywords: list) -> Optional[JobListing]:
        text = card.get_text(separator=" ", strip=True)
        if len(text) < 15:
            return None
        url = ""
        if card.name == "a" and card.get("href"):
            url = card["href"]
            if not url.startswith("http"):
                url = self.BASE_URL + url
        else:
            link = card.find("a", href=True)
            if link:
                url = link["href"]
                if not url.startswith("http"):
                    url = self.BASE_URL + url
        title = ""
        heading = card.find(["h2", "h3", "h4", "strong"])
        if heading:
            title = heading.get_text(strip=True)
        if not title:
            title = text[:80]
        # Try salary
        salary_text = ""
        salary_match = re.search(r'\$[\d,]+\s*[-–/]\s*\$?[\d,]+', text)
        if salary_match:
            salary_text = salary_match.group()
        smin, smax, cur = self._parse_salary_text(salary_text)
        combined = f"{title} {text}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None
        return JobListing(
            title=title[:120], company="Turing Network", location="Remote — India",
            salary=salary_text, job_type="contract", description=text[:2000],
            url=url, source="Turing", tags=["india-friendly"], region="Global",
            salary_min=smin, salary_max=smax,
        )


class FlexJobsScraper(AutoHealingScraper):
    """Scrapes FlexJobs public listing pages — hand-screened remote jobs."""

    BASE_URL = "https://www.flexjobs.com"

    def __init__(self):
        super().__init__("FlexJobs")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        pages = [
            f"{self.BASE_URL}/remote-jobs/world/india",
            f"{self.BASE_URL}/remote-jobs/computer-it",
            f"{self.BASE_URL}/remote-jobs/software-development",
        ]
        for page_url in pages:
            resp = self.fetch(page_url)
            if not resp:
                continue
            try:
                soup = BeautifulSoup(resp.text, "html.parser")
                for card in soup.select("a[href*='/job/'], li[class*='job'], div[class*='job'], article"):
                    job = self._parse_card(card, keywords)
                    if job and job.uid not in seen:
                        seen.add(job.uid)
                        jobs.append(job)
            except Exception as e:
                print(f"  [{self.name}] Parse error: {e}")
            time.sleep(0.5)
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_card(self, card, keywords: list) -> Optional[JobListing]:
        text = card.get_text(separator=" ", strip=True)
        if len(text) < 15:
            return None
        url = ""
        if card.name == "a" and card.get("href"):
            url = card["href"]
            if not url.startswith("http"):
                url = self.BASE_URL + url
        else:
            link = card.find("a", href=True)
            if link:
                url = link["href"]
                if not url.startswith("http"):
                    url = self.BASE_URL + url
        title = ""
        heading = card.find(["h2", "h3", "h4", "strong"])
        if heading:
            title = heading.get_text(strip=True)
        if not title:
            title = text[:80]
        combined = f"{title} {text}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None
        return JobListing(
            title=title[:120], company="", location="Remote",
            salary="", job_type="full time", description=text[:2000],
            url=url, source="FlexJobs", tags=["screened"], region="Global",
        )


# ─── India-Specific Portals ───────────────────────────────────────


class InstahyreScraper(AutoHealingScraper):
    """Scrapes Instahyre — AI-matched, India + remote global."""

    BASE_URL = "https://www.instahyre.com"

    def __init__(self):
        super().__init__("Instahyre")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        pages = [
            f"{self.BASE_URL}/search-jobs?work_from_home=true",
            f"{self.BASE_URL}/search-jobs?work_from_home=true&designation=software-engineer",
        ]
        for page_url in pages:
            resp = self.fetch(page_url)
            if not resp:
                continue
            try:
                soup = BeautifulSoup(resp.text, "html.parser")
                for card in soup.select("a[href*='/job/'], div[class*='job'], div[class*='listing'], article"):
                    job = self._parse_card(card, keywords)
                    if job and job.uid not in seen:
                        seen.add(job.uid)
                        jobs.append(job)
            except Exception as e:
                print(f"  [{self.name}] Parse error: {e}")
            time.sleep(0.5)
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_card(self, card, keywords: list) -> Optional[JobListing]:
        text = card.get_text(separator=" ", strip=True)
        if len(text) < 15:
            return None
        url = ""
        if card.name == "a" and card.get("href"):
            url = card["href"]
            if not url.startswith("http"):
                url = self.BASE_URL + url
        else:
            link = card.find("a", href=True)
            if link:
                url = link["href"]
                if not url.startswith("http"):
                    url = self.BASE_URL + url
        title = ""
        heading = card.find(["h2", "h3", "h4", "strong"])
        if heading:
            title = heading.get_text(strip=True)
        if not title:
            title = text[:80]
        combined = f"{title} {text}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None
        return JobListing(
            title=title[:120], company="", location="Remote — India",
            salary="", job_type="full time", description=text[:2000],
            url=url, source="Instahyre", tags=["india"], region="India",
        )


class CutshortScraper(AutoHealingScraper):
    """Scrapes Cutshort — India tech jobs, many remote-global."""

    BASE_URL = "https://cutshort.io"

    def __init__(self):
        super().__init__("Cutshort")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        pages = [
            f"{self.BASE_URL}/jobs?work_type=remote",
            f"{self.BASE_URL}/jobs?work_type=remote&skills=android",
            f"{self.BASE_URL}/jobs?work_type=remote&skills=machine-learning",
        ]
        for page_url in pages:
            resp = self.fetch(page_url)
            if not resp:
                continue
            try:
                soup = BeautifulSoup(resp.text, "html.parser")
                for card in soup.select("a[href*='/job/'], div[class*='job'], div[class*='listing'], article"):
                    job = self._parse_card(card, keywords)
                    if job and job.uid not in seen:
                        seen.add(job.uid)
                        jobs.append(job)
            except Exception as e:
                print(f"  [{self.name}] Parse error: {e}")
            time.sleep(0.5)
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_card(self, card, keywords: list) -> Optional[JobListing]:
        text = card.get_text(separator=" ", strip=True)
        if len(text) < 15:
            return None
        url = ""
        if card.name == "a" and card.get("href"):
            url = card["href"]
            if not url.startswith("http"):
                url = self.BASE_URL + url
        else:
            link = card.find("a", href=True)
            if link:
                url = link["href"]
                if not url.startswith("http"):
                    url = self.BASE_URL + url
        title = ""
        heading = card.find(["h2", "h3", "h4", "strong"])
        if heading:
            title = heading.get_text(strip=True)
        if not title:
            title = text[:80]
        # Try salary (Indian format: ₹XX LPA or $XX)
        salary_text = ""
        salary_match = re.search(r'[\$₹][\d,.]+\s*[-–]\s*[\$₹]?[\d,.]+\s*(?:LPA|lpa|per year|/yr)?', text)
        if salary_match:
            salary_text = salary_match.group()
        combined = f"{title} {text}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None
        return JobListing(
            title=title[:120], company="", location="Remote — India",
            salary=salary_text, job_type="full time", description=text[:2000],
            url=url, source="Cutshort", tags=["india"], region="India",
        )


class TruelancerScraper(AutoHealingScraper):
    """Scrapes Truelancer — popular in India for freelance gigs."""

    BASE_URL = "https://www.truelancer.com"

    def __init__(self):
        super().__init__("Truelancer")

    def scrape(self, keywords: list) -> list[JobListing]:
        jobs = []
        seen = set()
        pages = [
            f"{self.BASE_URL}/freelance-jobs/software-development",
            f"{self.BASE_URL}/freelance-jobs/mobile-development",
            f"{self.BASE_URL}/freelance-jobs/web-development",
        ]
        for page_url in pages:
            resp = self.fetch(page_url)
            if not resp:
                continue
            try:
                soup = BeautifulSoup(resp.text, "html.parser")
                for card in soup.select("a[href*='/freelance-job/'], div[class*='job'], div[class*='project'], article"):
                    job = self._parse_card(card, keywords)
                    if job and job.uid not in seen:
                        seen.add(job.uid)
                        jobs.append(job)
            except Exception as e:
                print(f"  [{self.name}] Parse error: {e}")
            time.sleep(0.5)
        print(f"  [{self.name}] Found {len(jobs)} relevant jobs")
        return jobs

    def _parse_card(self, card, keywords: list) -> Optional[JobListing]:
        text = card.get_text(separator=" ", strip=True)
        if len(text) < 15:
            return None
        url = ""
        if card.name == "a" and card.get("href"):
            url = card["href"]
            if not url.startswith("http"):
                url = self.BASE_URL + url
        else:
            link = card.find("a", href=True)
            if link:
                url = link["href"]
                if not url.startswith("http"):
                    url = self.BASE_URL + url
        title = ""
        heading = card.find(["h2", "h3", "h4", "strong"])
        if heading:
            title = heading.get_text(strip=True)
        if not title:
            title = text[:80]
        combined = f"{title} {text}".lower()
        if not any(kw.lower() in combined for kw in keywords):
            return None
        return JobListing(
            title=title[:120], company="", location="Remote",
            salary="", job_type="freelance", description=text[:2000],
            url=url, source="Truelancer", tags=["freelance"], region="Global",
        )


# ─── Orchestrator ──────────────────────────────────────────────────


class JobSearchAgent:
    """Orchestrates multiple scrapers, deduplicates, and merges results."""

    # Per-scraper category tags. "general" = works for any profession;
    # "tech" = the source itself or its hardcoded URLs only carry software
    # roles. Pipeline uses these to skip tech-only sources for non-tech
    # candidates (a Chartered Accountant should not be searched on Arc.dev
    # or GunIO — those return only engineering jobs).
    SCRAPER_CATEGORIES = {
        # Major general-purpose platforms
        "LinkedIn": {"general"},
        "Indeed": {"general"},
        "Naukri": {"general"},
        "Monster": {"general"},
        "Glassdoor": {"general"},
        "FlexJobs": {"general"},
        "Instahyre": {"general"},
        "Cutshort": {"general"},
        # General remote boards
        "Remotive": {"general", "tech"},
        "Arbeitnow": {"general"},
        "Working Nomads": {"general"},
        "RemoteCo": {"general"},
        "Jobgether": {"general"},
        "Himalayas": {"general"},
        "JobIcy": {"general"},
        "Just Remote": {"general"},
        "Wellfound": {"general", "tech"},
        # Tech-specific (skip for non-tech profiles)
        "RemoteOK": {"tech"},
        "We Work Remotely": {"tech"},
        "Remote Rocketship": {"tech"},
        "Arc.dev": {"tech"},
        "Toptal": {"tech"},
        "Contra": {"tech", "design"},
        "GunIO": {"tech"},
        "Turing": {"tech"},
        "Truelancer": {"general", "tech"},
    }

    def __init__(self):
        self.scrapers = [
            # ── Major Platforms (highest conversion) ──
            LinkedInScraper(),
            IndeedScraper(),
            NaukriScraper(),
            MonsterScraper(),
            GlassdoorScraper(),
            # ── API-based remote boards ──
            RemotiveScraper(),
            RemoteOKScraper(),
            ArbeitnowScraper(),
            HimalayasScraper(),
            JobIcyScraper(),
            WorkingNomadsScraper(),
            # ── RSS-based ──
            WeWorkRemotelyScraper(),
            # ── Remote job boards (web) ──
            RemoteRocketshipScraper(),
            JustRemoteScraper(),
            ArcDevScraper(),
            WellfoundScraper(),
            RemoteCoScraper(),
            JobgetherScraper(),
            FlexJobsScraper(),
            # ── Freelance / Contract platforms ──
            ToptalScraper(),
            ContraScraper(),
            GunIOScraper(),
            TuringScraper(),
            TruelancerScraper(),
            # ── India-specific portals ──
            InstahyreScraper(),
            CutshortScraper(),
        ]

    @classmethod
    def filter_scrapers_by_profile(cls, scrapers: list, profile_domain: str) -> list:
        """Skip tech-only scrapers when the profile is non-tech.

        Returns the same scraper list when profile_domain is empty or matches a
        tech category (no harm in querying everything). For non-tech profiles
        (finance, legal, medical, marketing, etc.) we drop scrapers whose
        category set does not include "general"."""
        if not profile_domain:
            return scrapers
        d = profile_domain.lower()
        tech_terms = {"software", "engineering", "mobile", "backend", "frontend",
                      "full-stack", "fullstack", "devops", "data", "ml", "ai",
                      "ml-ai", "tech", "developer"}
        if any(t in d for t in tech_terms):
            return scrapers
        # Non-tech profile — keep only scrapers tagged "general".
        kept = []
        for s in scrapers:
            cats = cls.SCRAPER_CATEGORIES.get(s.name, {"general"})
            if "general" in cats:
                kept.append(s)
        return kept
        self.all_jobs: list[JobListing] = []
        self.stats = {
            "sources_queried": 0,
            "total_raw": 0,
            "total_unique": 0,
            "by_source": {},
            "scraper_stats": {},
        }

    def search(self, search_keywords: list, work_mode: str = "remote", applicant_location: str = "india") -> list[JobListing]:
        """
        Search all sources, deduplicate, then hard-filter for:
         - Work mode (remote/hybrid/onsite/any)
         - Applicant location eligibility (e.g. India → global)
        """
        # Build filter keywords from search queries
        filter_keywords = set()
        for q in search_keywords:
            for word in q.lower().split():
                if len(word) > 2 and word not in ("the", "and", "for", "with", "from", "remote", "hybrid", "onsite"):
                    filter_keywords.add(word)
        filter_keywords = list(filter_keywords)[:30] or ["engineer", "developer", "software"]

        all_raw = []
        for scraper in self.scrapers:
            print(f"\n🔍 Searching {scraper.name}...", flush=True)
            try:
                jobs = scraper.scrape(filter_keywords)
                all_raw.extend(jobs)
                self.stats["by_source"][scraper.name] = len(jobs)
                self.stats["scraper_stats"][scraper.name] = scraper.stats.copy()
            except Exception as e:
                print(f"  ❌ {scraper.name} failed: {e}")
                traceback.print_exc()
                self.stats["by_source"][scraper.name] = 0
                self.stats["scraper_stats"][scraper.name] = scraper.stats.copy()

        self.stats["sources_queried"] = len(self.scrapers)
        self.stats["total_raw"] = len(all_raw)

        unique = self._deduplicate(all_raw)

        # Hard-filter by work mode + location eligibility
        filtered = self._filter_eligibility(unique, work_mode, applicant_location)

        self.stats["total_unique"] = len(filtered)
        self.stats["total_before_filter"] = len(unique)
        self.all_jobs = filtered
        print(f"\n📊 Total: {len(all_raw)} raw → {len(unique)} unique → {len(filtered)} eligible ({work_mode}, from {applicant_location})", flush=True)
        return filtered

    def _filter_eligibility(self, jobs: list, work_mode: str, applicant_location: str) -> list:
        """
        Hard-filter: only keep jobs the applicant can actually work.
        For remote-from-India: job must allow remote AND be open to India/worldwide/APAC.
        Excludes jobs restricted to US-only, EU-only (with visa/residency requirements).
        """
        # ── Truly global signals: job explicitly open to worldwide applicants ──
        INDIA_ELIGIBLE_SIGNALS = [
            "worldwide", "global", "anywhere", "work from anywhere",
            "india", "apac", "asia", "asia-pacific",
            "international", "all countries", "all locations",
            "global remote", "globally distributed", "location flexible",
            "any timezone", "any time zone", "distributed team",
            # Explicit multi-region openness
            "worldwide remote", "remote worldwide", "remote global",
            "remote - anywhere", "remote anywhere",
            "open to all locations", "open to all countries",
        ]
        # ── Signals that EXCLUDE India-based applicants ──
        # These catch "remote from US" false positives
        EXCLUSION_SIGNALS = [
            # US-restricted
            "us only", "usa only", "u.s. only",
            "must be based in us", "must be based in the us",
            "must be based in usa", "must be based in the usa",
            "us citizens only", "us residents only",
            "must reside in the united states",
            "us work authorization required",
            "must be authorized to work in the united states",
            "united states only", "based in the united states",
            # "Remote" but geo-locked to a single country
            "remote (us)", "remote (usa)", "remote - us", "remote - usa",
            "remote (united states)", "remote us only",
            "us-based remote", "usa-based remote",
            "remote within the us", "remote within usa",
            "remote within the united states",
            "remote from us", "remote from usa",
            "remote from the us", "remote from the united states",
            # EU-restricted
            "eu only", "european union only", "must be based in eu",
            "remote (eu)", "remote - eu", "eu-based remote",
            "remote within eu", "remote from eu",
            # UK-restricted
            "uk only", "must be based in uk",
            "remote (uk)", "remote - uk", "uk-based remote",
            "remote within uk", "remote from uk",
            # Generic geo-lock patterns
            "must be located in", "must reside in",
            "no sponsorship", "no visa sponsorship",
            "work authorization required",
            "candidates must be located",
            "only open to residents",
            "applicants must reside",
            # Country-specific remote (not global)
            "remote in ", "remote from ",
        ]
        REMOTE_SIGNALS = [
            "remote", "work from home", "wfh", "distributed", "anywhere",
            "work from anywhere", "remote-first", "fully remote", "100% remote",
            "telecommute", "virtual",
        ]
        HYBRID_SIGNALS = ["hybrid", "flexible", "2 days", "3 days", "partial remote"]
        ONSITE_SIGNALS = ["onsite", "on-site", "in-office", "office-based", "in office"]

        filtered = []
        for job in jobs:
            loc = str(job.location).lower() if isinstance(job.location, str) else " ".join(str(x) for x in job.location).lower()
            title = str(job.title).lower()
            desc = str(job.description).lower()[:1000]
            jtype = str(job.job_type).lower() if isinstance(job.job_type, str) else " ".join(str(x) for x in job.job_type).lower()
            combined = f"{loc} {title} {desc} {jtype}"

            # ── Work mode gate ──
            if work_mode == "remote":
                if not any(sig in combined for sig in REMOTE_SIGNALS):
                    continue
            elif work_mode == "hybrid":
                if not any(sig in combined for sig in HYBRID_SIGNALS):
                    continue
            elif work_mode == "onsite":
                if not any(sig in combined for sig in ONSITE_SIGNALS):
                    continue

            # ── Location eligibility gate (for remote from India) ──
            if work_mode == "remote" and applicant_location == "india":
                # 1. Hard exclusion phrases
                if any(exc in combined for exc in EXCLUSION_SIGNALS):
                    continue

                # 2. Regex: catch "Remote (US)", "Remote — USA", "Remote, United States" etc.
                #    These are geo-locked remote jobs, NOT globally open
                geo_locked = False
                GEO_LOCK_PATTERNS = [
                    r'\bremote\s*[\(\-–—,/]\s*(?:us|usa|united states|america)\b',
                    r'\bremote\s*[\(\-–—,/]\s*(?:uk|united kingdom|england|britain)\b',
                    r'\bremote\s*[\(\-–—,/]\s*(?:eu|europe|european union)\b',
                    r'\bremote\s*[\(\-–—,/]\s*(?:canada|germany|france|spain|italy|netherlands|brazil|brasil|australia|japan|korea|mexico|argentina)\b',
                    r'\bus[\s-]*based\s+remote\b',
                    r'\bremote\b.*\bus\s+only\b',
                    r'\bremote\b.*\busa\s+only\b',
                ]
                for pat in GEO_LOCK_PATTERNS:
                    if re.search(pat, combined, re.IGNORECASE):
                        geo_locked = True
                        break

                # Also check if location field is JUST a country (no "worldwide"/"remote"/"anywhere")
                if not geo_locked:
                    COUNTRY_ONLY_LOCATIONS = [
                        "united states", "usa", "us", "america",
                        "united kingdom", "uk", "england",
                        "canada", "germany", "france", "spain", "italy",
                        "netherlands", "switzerland", "sweden",
                        "brazil", "brasil", "australia", "japan", "south korea",
                        "mexico", "argentina", "colombia", "chile", "peru",
                        "portugal", "ireland", "poland", "czech republic",
                        "austria", "belgium", "denmark", "norway", "finland",
                        "singapore", "china", "taiwan", "philippines",
                        "new zealand", "south africa", "israel", "turkey",
                        "thailand", "vietnam", "indonesia", "malaysia",
                    ]
                    loc_stripped = loc.strip().rstrip(".")
                    if loc_stripped in COUNTRY_ONLY_LOCATIONS:
                        geo_locked = True
                    elif re.match(r'^remote\s*[\-–—,/|]\s*(' + '|'.join(re.escape(c) for c in COUNTRY_ONLY_LOCATIONS) + r')$', loc_stripped, re.IGNORECASE):
                        geo_locked = True

                if geo_locked:
                    continue

                # 3. Check if job title contains a specific non-India city/country
                #    e.g. "Staff Engineer - Porto Alegre" means location-locked
                NON_INDIA_CITIES = [
                    # Americas
                    "porto alegre", "sao paulo", "são paulo", "rio de janeiro",
                    "buenos aires", "bogota", "bogotá", "mexico city", "santiago",
                    "new york", "san francisco", "los angeles", "chicago", "seattle",
                    "austin", "boston", "denver", "miami", "dallas", "atlanta",
                    "toronto", "vancouver", "montreal",
                    # Europe
                    "london", "berlin", "paris", "amsterdam", "barcelona",
                    "madrid", "lisbon", "dublin", "munich", "zurich", "stockholm",
                    "copenhagen", "oslo", "helsinki", "prague", "warsaw", "vienna",
                    "brussels", "milan", "rome",
                    # Asia-Pacific (non-India)
                    "tokyo", "sydney", "melbourne", "singapore", "hong kong",
                    "seoul", "taipei", "shanghai", "beijing",
                    # Middle East / Africa
                    "tel aviv", "dubai", "cape town", "nairobi",
                ]
                title_lower = title.strip()
                title_has_city = False
                for city in NON_INDIA_CITIES:
                    if city in title_lower or city in loc:
                        title_has_city = True
                        break
                if title_has_city:
                    # City in title means it's location-specific.
                    # Only override if the job is EXPLICITLY open to India
                    # (not just generic "worldwide" in location field — that could be
                    # a company HQ description, not actual eligibility)
                    STRONG_GLOBAL_OVERRIDES = [
                        "india", "work from anywhere", "open to all countries",
                        "open to all locations",
                    ]
                    if not any(sig in desc for sig in STRONG_GLOBAL_OVERRIDES):
                        continue

                # 4. Must have at least one truly global/India-eligible signal
                if not any(sig in combined for sig in INDIA_ELIGIBLE_SIGNALS):
                    continue

            filtered.append(job)

        return filtered

    def _deduplicate(self, jobs: list[JobListing]) -> list[JobListing]:
        seen = {}
        for job in jobs:
            # Ensure source_quality is set
            if not job.source_quality:
                job.source_quality = SOURCE_QUALITY.get(job.source, 50)
            title_norm = re.sub(r"[^a-z0-9]", "", job.title.lower())
            company_norm = re.sub(r"[^a-z0-9]", "", job.company.lower())
            key = f"{title_norm}_{company_norm}"
            if key not in seen:
                seen[key] = job
            else:
                existing = seen[key]
                # Keep the one from the higher-quality source
                if job.source_quality > existing.source_quality:
                    job.tags = list(set(job.tags + existing.tags))
                    seen[key] = job
                elif len(job.description) > len(existing.description):
                    job.tags = list(set(job.tags + existing.tags))
                    job.source_quality = max(job.source_quality, existing.source_quality)
                    seen[key] = job
                else:
                    existing.tags = list(set(existing.tags + job.tags))
                    existing.source_quality = max(existing.source_quality, job.source_quality)
        return list(seen.values())

    def to_dicts(self) -> list[dict]:
        return [asdict(j) for j in self.all_jobs]
