"""Job enricher — follow the listing URL and extract the canonical apply URL
and the full job description.

Why it exists
-------------
Scraper output is good enough to surface a job, but two pieces of data are
often missing or wrong:

  1. **Apply URL.** LinkedIn / Indeed often point at their own landing page
     instead of the employer's ATS (Greenhouse, Lever, Workday, Ashby,
     Greenhouse-on-Greenhouse boards, etc.). Users can't apply from there
     without an account.
  2. **Full description.** Many scrapers return a 1-line description
     (or none at all). The JD-matcher and the dashboard preview both
     improve substantially when given the full description.

The enricher fetches the listing page (with a short timeout, polite
per-domain rate limit, and an HTTP session that reuses connections),
detects the ATS template, and pulls the structured fields out of it.

What it returns
---------------
A dict with two keys:

    {"apply_url": "https://boards.greenhouse.io/.../jobs/12345", "description": "...full text..."}

Either key may be empty — the caller decides whether to overwrite the
scraped values or keep them. The function never raises; failures return
empty strings.

Concurrency model
-----------------
`enrich_jobs(jobs, max_workers, per_domain_rps)` runs N enrichers in
parallel via ThreadPoolExecutor, but throttles per-domain to avoid hitting
the same ATS host too fast (some return 429 after ~5 rps). Cancellation
is propagated via the optional `cancel_event`.
"""

from __future__ import annotations

import collections
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlparse

import requests


# ─── Knobs ─────────────────────────────────────────────────────────

DEFAULT_TIMEOUT_S = 6.0          # per-request HTTP timeout
DEFAULT_MAX_WORKERS = 8          # parallel HTTP fetchers
DEFAULT_PER_DOMAIN_RPS = 3.0     # max requests/sec per host
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
MAX_DESC_CHARS = 8000            # cap description length so DB rows stay sane


# Hosts whose pages are bot-walled or whose URL we should NOT follow —
# any direct URL we land at is no better than what we started with.
SKIP_HOSTS = {
    "linkedin.com", "www.linkedin.com",
    "indeed.com", "www.indeed.com",
    "glassdoor.com", "www.glassdoor.com",
    "ziprecruiter.com", "www.ziprecruiter.com",
    # Google search pages aren't job pages — leave them alone
    "google.com", "www.google.com",
}


# ─── Per-domain rate limiter ───────────────────────────────────────


class _DomainRateLimiter:
    """Token-bucket per host. Thread-safe."""

    def __init__(self, rps: float):
        self._rps = max(rps, 0.1)
        self._last_call: dict[str, float] = collections.defaultdict(float)
        self._lock = threading.Lock()

    def acquire(self, host: str):
        """Block until safe to call ``host`` again."""
        if not host:
            return
        min_gap = 1.0 / self._rps
        with self._lock:
            now = time.monotonic()
            last = self._last_call[host]
            wait = (last + min_gap) - now
            if wait > 0:
                # Release lock while sleeping so other hosts don't queue
                # behind this one.
                self._last_call[host] = now + wait
            else:
                self._last_call[host] = now
                wait = 0.0
        if wait > 0:
            time.sleep(wait)


# ─── ATS detection / extraction ────────────────────────────────────


@dataclass
class _AtsHit:
    """A successful match against an ATS template."""
    ats: str                # short id: "greenhouse" / "lever" / ...
    apply_url: str          # canonical apply URL (may equal page url)
    description: str        # extracted plain-text description


_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(html: str) -> str:
    """Quick-and-dirty HTML→text. Avoids BeautifulSoup so this module has
    no extra dependency footprint beyond `requests`."""
    if not html:
        return ""
    # Drop <script>/<style> blocks completely
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = _TAG_STRIP_RE.sub(" ", html)
    # Convert common HTML entities
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&#39;", "'"))
    text = _WS_RE.sub(" ", text).strip()
    return text


def _detect_ats(url: str, html: str) -> Optional[_AtsHit]:
    """Match the page against known ATS templates. Returns the first hit
    or None.  Order matters — more specific patterns first."""
    host = urlparse(url).netloc.lower()
    lower_html = html.lower() if html else ""

    # ── Greenhouse ──
    if "greenhouse.io" in host or "boards.greenhouse.io" in lower_html:
        # The apply URL is typically the page itself when host is greenhouse
        m = re.search(r'href="(https?://boards\.greenhouse\.io/[^"]+)"', html)
        apply = m.group(1) if m else (url if "greenhouse.io" in host else "")
        # Greenhouse wraps the JD in <div id="content"> or class="content"
        body = re.search(r'<div[^>]+id="content"[^>]*>(.*?)</div>\s*<(?:footer|/section)',
                         html, re.DOTALL | re.IGNORECASE)
        desc = _strip_html(body.group(1) if body else html)
        return _AtsHit("greenhouse", apply, desc[:MAX_DESC_CHARS])

    # ── Lever ──
    if "jobs.lever.co" in host or "jobs.lever.co" in lower_html:
        m = re.search(r'href="(https?://jobs\.lever\.co/[^"]+)"', html)
        apply = m.group(1) if m else (url if "lever.co" in host else "")
        body = re.search(r'<div[^>]+class="[^"]*posting-page[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>',
                         html, re.DOTALL | re.IGNORECASE)
        desc = _strip_html(body.group(1) if body else html)
        return _AtsHit("lever", apply, desc[:MAX_DESC_CHARS])

    # ── Ashby ──
    if "jobs.ashbyhq.com" in host or "ashbyhq.com" in lower_html:
        m = re.search(r'href="(https?://jobs\.ashbyhq\.com/[^"]+)"', html)
        apply = m.group(1) if m else (url if "ashbyhq.com" in host else "")
        body = re.search(r'<div[^>]+class="[^"]*_descriptionText[^"]*"[^>]*>(.*?)</div>',
                         html, re.DOTALL | re.IGNORECASE)
        desc = _strip_html(body.group(1) if body else html)
        return _AtsHit("ashby", apply, desc[:MAX_DESC_CHARS])

    # ── Workday ──
    if "myworkdayjobs.com" in host or "myworkdayjobs.com" in lower_html:
        m = re.search(r'href="(https?://[^"]*myworkdayjobs\.com/[^"]+)"', html)
        apply = m.group(1) if m else (url if "myworkdayjobs.com" in host else "")
        body = re.search(r'<div[^>]+data-automation-id="jobPostingDescription"[^>]*>(.*?)</div>',
                         html, re.DOTALL | re.IGNORECASE)
        desc = _strip_html(body.group(1) if body else html)
        return _AtsHit("workday", apply, desc[:MAX_DESC_CHARS])

    # ── SmartRecruiters ──
    if "smartrecruiters.com" in host or "smartrecruiters.com" in lower_html:
        m = re.search(r'href="(https?://[^"]*smartrecruiters\.com/[^"]+)"', html)
        apply = m.group(1) if m else (url if "smartrecruiters.com" in host else "")
        body = re.search(r'<section[^>]+id="job-description"[^>]*>(.*?)</section>',
                         html, re.DOTALL | re.IGNORECASE)
        desc = _strip_html(body.group(1) if body else html)
        return _AtsHit("smartrecruiters", apply, desc[:MAX_DESC_CHARS])

    return None


# ─── Public API ────────────────────────────────────────────────────


@dataclass
class EnrichResult:
    """Outcome for a single job."""
    job_url: str            # original URL we were asked to enrich
    apply_url: str = ""     # canonical apply URL (may be the same as job_url)
    description: str = ""   # extracted full description
    ats: str = ""           # ATS template id ("greenhouse", ...) or ""
    ok: bool = False        # True iff we extracted something
    error: str = ""         # short error string on failure


def enrich_one(url: str,
               session: Optional[requests.Session] = None,
               timeout: float = DEFAULT_TIMEOUT_S,
               rate_limiter: Optional[_DomainRateLimiter] = None) -> EnrichResult:
    """Fetch a single URL and try to extract apply_url + description.

    Never raises — returns ``EnrichResult(ok=False, error=...)`` on failure.

    Pre-decides to skip noisy hosts (LinkedIn / Indeed / Glassdoor) so we
    don't waste a roundtrip on a page we know we can't parse.
    """
    if not url:
        return EnrichResult(job_url=url, error="empty url")

    host = urlparse(url).netloc.lower()
    if host in SKIP_HOSTS:
        return EnrichResult(job_url=url, error=f"skipped host: {host}")

    sess = session or requests.Session()
    if rate_limiter is not None:
        rate_limiter.acquire(host)

    try:
        resp = sess.get(url, timeout=timeout, headers={"User-Agent": DEFAULT_USER_AGENT,
                                                       "Accept-Language": "en-US,en;q=0.9"})
        if resp.status_code != 200:
            return EnrichResult(job_url=url, error=f"http {resp.status_code}")
        html = resp.text or ""
    except requests.exceptions.Timeout:
        return EnrichResult(job_url=url, error="timeout")
    except Exception as e:
        return EnrichResult(job_url=url, error=f"fetch_error: {type(e).__name__}")

    hit = _detect_ats(url, html)
    if hit is None:
        # Fallback: strip the whole page. Better than nothing for dashboard
        # preview, even if JD matcher can't fully use it.
        desc = _strip_html(html)[:MAX_DESC_CHARS]
        return EnrichResult(job_url=url, apply_url=url, description=desc,
                            ats="", ok=bool(desc))

    return EnrichResult(job_url=url, apply_url=hit.apply_url or url,
                        description=hit.description, ats=hit.ats,
                        ok=bool(hit.description))


def enrich_jobs(jobs: list[dict],
                max_workers: int = DEFAULT_MAX_WORKERS,
                per_domain_rps: float = DEFAULT_PER_DOMAIN_RPS,
                timeout: float = DEFAULT_TIMEOUT_S,
                cancel_event: Optional[threading.Event] = None,
                on_progress: Optional[Callable[[int, int], None]] = None) -> list[dict]:
    """Enrich a list of job dicts in place. Returns the same list (mutated).

    Each job is expected to have a ``url`` / ``source_url`` field. The
    enriched results are merged like this:

      - If we got a ``description``, it REPLACES the scraped description
        (full text is always better than a one-liner).
      - If we got an ``apply_url`` AND it's on a different host than the
        current url, we set ``apply_url`` AND store the original URL in
        ``listing_url`` so the UI can offer both ("Apply directly" vs
        "View on LinkedIn"). Same-host hits are written to ``apply_url``
        without touching ``url``.
      - Failures leave the job unchanged.
    """
    if not jobs:
        return jobs

    rate_limiter = _DomainRateLimiter(per_domain_rps)
    session = requests.Session()
    total = len(jobs)
    done = 0
    lock = threading.Lock()

    def _work(idx: int, job: dict) -> tuple[int, EnrichResult]:
        if cancel_event and cancel_event.is_set():
            return idx, EnrichResult(job_url="", error="cancelled")
        url = job.get("url") or job.get("source_url") or ""
        res = enrich_one(url, session=session, timeout=timeout,
                         rate_limiter=rate_limiter)
        return idx, res

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_work, i, j): i for i, j in enumerate(jobs)}
        for fut in as_completed(futures):
            if cancel_event and cancel_event.is_set():
                break
            try:
                idx, res = fut.result()
            except Exception:
                continue
            if res.ok:
                job = jobs[idx]
                if res.description:
                    job["description"] = res.description
                if res.apply_url:
                    orig_url = job.get("url") or job.get("source_url") or ""
                    orig_host = urlparse(orig_url).netloc.lower()
                    new_host = urlparse(res.apply_url).netloc.lower()
                    if new_host and new_host != orig_host:
                        job["apply_url"] = res.apply_url
                        job["listing_url"] = orig_url
                    else:
                        job["apply_url"] = res.apply_url
                if res.ats:
                    job["ats"] = res.ats
                job["enriched"] = True
            with lock:
                done += 1
                if on_progress:
                    try:
                        on_progress(done, total)
                    except Exception:
                        pass

    return jobs


# ─── JD match hardening ─────────────────────────────────────────────


def keyword_overlap_score(profile: dict, description: str) -> float:
    """Cheap, deterministic profile↔description overlap score in [0, 100].

    Used to harden the LLM's match_score: if the LLM hallucinates a high
    score but the description shares almost no vocabulary with the profile,
    we want to discount the LLM. Symmetrically, an LLM that misses obvious
    matches gets a boost when overlap is high.

    The signal is keyword-recall against the profile's primary_skills +
    domain_keywords + desired_roles (case-insensitive, word-boundary
    matching for short tokens to avoid 'ca' matching 'capacity').
    """
    if not description:
        return 0.0

    desc = description.lower()

    def _toks(field) -> set[str]:
        if not field:
            return set()
        if isinstance(field, str):
            field = [field]
        out: set[str] = set()
        for x in field:
            if not isinstance(x, str):
                continue
            x = x.strip().lower()
            if x and len(x) >= 3:
                out.add(x)
        return out

    needles = (_toks(profile.get("primary_skills"))
               | _toks(profile.get("domain_keywords"))
               | _toks(profile.get("desired_roles"))
               | _toks(profile.get("title")))

    if not needles:
        return 0.0

    hits = 0
    for n in needles:
        # Short tokens — require word boundary to avoid substring noise
        if len(n) <= 4:
            if re.search(r"\b" + re.escape(n) + r"\b", desc):
                hits += 1
        else:
            if n in desc:
                hits += 1

    return round(min(100.0, (hits / len(needles)) * 100.0), 1)


def harden_match_score(llm_score: float, overlap: float,
                       weight_overlap: float = 0.35) -> float:
    """Blend the LLM match score with the deterministic keyword overlap.

    Default weight gives the LLM 65% and the overlap signal 35% — enough
    to pull down hallucinated high scores while still letting the LLM
    drive the ranking when the profile and JD genuinely align.
    """
    llm = max(0.0, min(100.0, float(llm_score or 0)))
    ovr = max(0.0, min(100.0, float(overlap or 0)))
    w = max(0.0, min(1.0, weight_overlap))
    return round(llm * (1 - w) + ovr * w, 1)
