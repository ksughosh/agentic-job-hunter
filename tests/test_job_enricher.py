"""Tests for agents.job_enricher.

The enricher does network I/O, so we stub `requests.Session.get` instead
of hitting real ATS hosts.  The strip / detect / overlap helpers are
pure functions and tested directly.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from agents.job_enricher import (
    DEFAULT_PER_DOMAIN_RPS,
    EnrichResult,
    SKIP_HOSTS,
    _DomainRateLimiter,
    _detect_ats,
    _strip_html,
    enrich_jobs,
    enrich_one,
    harden_match_score,
    keyword_overlap_score,
)


# ─── _strip_html ──────────────────────────────────────────────────


class TestStripHtml:
    def test_drops_script_and_style(self):
        html = "<html><script>alert(1)</script><style>a{}</style><p>Keep me</p></html>"
        out = _strip_html(html)
        assert "alert" not in out
        assert "a{}" not in out
        assert "Keep me" in out

    def test_decodes_common_entities(self):
        html = "<p>Tom &amp; Jerry&nbsp;&lt;3&gt;</p>"
        assert _strip_html(html) == "Tom & Jerry <3>"

    def test_collapses_whitespace(self):
        html = "<p>hello\n\n\n  world\t\t!</p>"
        assert _strip_html(html) == "hello world !"

    def test_empty_input(self):
        assert _strip_html("") == ""
        assert _strip_html(None) == ""


# ─── _detect_ats ──────────────────────────────────────────────────


class TestDetectAts:
    def test_greenhouse_by_host(self):
        html = '<html><body><div id="content">Senior Auditor at Acme. Must love numbers.</div><footer/></body></html>'
        hit = _detect_ats("https://boards.greenhouse.io/acme/jobs/9", html)
        assert hit is not None
        assert hit.ats == "greenhouse"
        assert "Senior Auditor" in hit.description

    def test_lever_by_host(self):
        html = '<html><body><div class="posting-page">Lead Engineer JD body</div></div></div></body></html>'
        hit = _detect_ats("https://jobs.lever.co/foo/abc", html)
        assert hit is not None
        assert hit.ats == "lever"

    def test_ashby_by_host(self):
        html = '<html><body><div class="_descriptionText_x">Hello Ashby</div></body></html>'
        hit = _detect_ats("https://jobs.ashbyhq.com/foo/abc", html)
        assert hit is not None
        assert hit.ats == "ashby"

    def test_workday_by_host(self):
        html = '<html><body><div data-automation-id="jobPostingDescription">Workday body</div></body></html>'
        hit = _detect_ats("https://acme.wd1.myworkdayjobs.com/external/job/123", html)
        assert hit is not None
        assert hit.ats == "workday"

    def test_unknown_host_returns_none(self):
        assert _detect_ats("https://example.com/jobs/1", "<html><body>x</body></html>") is None


# ─── keyword_overlap_score / harden_match_score ───────────────────


class TestKeywordOverlap:
    def test_empty_description_zero(self):
        p = {"primary_skills": ["python", "django"]}
        assert keyword_overlap_score(p, "") == 0.0

    def test_full_match_hundred(self):
        p = {"primary_skills": ["python", "django"]}
        desc = "We need a senior Python and Django engineer."
        assert keyword_overlap_score(p, desc) == 100.0

    def test_partial_match(self):
        p = {"primary_skills": ["python", "django", "rust", "kafka"]}
        desc = "Python and Django role."
        # 2 of 4 needles hit → 50
        assert keyword_overlap_score(p, desc) == 50.0

    def test_short_needle_uses_word_boundary(self):
        # Short acronyms must use word-boundary matching to avoid substring
        # noise (e.g. "cpa" must NOT match "capacity").  Tokens shorter than
        # 3 chars are dropped entirely as too noisy.
        p = {"primary_skills": ["cpa"]}
        assert keyword_overlap_score(p, "we have capacity planning") == 0.0
        assert keyword_overlap_score(p, "Hiring a CPA today.") == 100.0

    def test_too_short_tokens_dropped(self):
        # Two-char tokens are too noisy — dropped entirely
        p = {"primary_skills": ["ca"]}
        assert keyword_overlap_score(p, "Hiring a CA today.") == 0.0

    def test_no_needles_returns_zero(self):
        assert keyword_overlap_score({}, "anything") == 0.0


class TestHardenMatchScore:
    def test_blends_llm_and_overlap(self):
        # 65% LLM + 35% overlap
        assert harden_match_score(100, 0) == 65.0
        assert harden_match_score(0, 100) == 35.0
        assert harden_match_score(80, 60) == round(80 * 0.65 + 60 * 0.35, 1)

    def test_clamps_inputs(self):
        assert harden_match_score(200, 200) == 100.0
        assert harden_match_score(-50, -50) == 0.0

    def test_custom_weight(self):
        # All weight on overlap
        assert harden_match_score(80, 20, weight_overlap=1.0) == 20.0
        # All weight on LLM
        assert harden_match_score(80, 20, weight_overlap=0.0) == 80.0


# ─── _DomainRateLimiter ───────────────────────────────────────────


class TestDomainRateLimiter:
    def test_blocks_within_window(self):
        rl = _DomainRateLimiter(rps=10.0)  # 100ms gap
        import time as _t
        t0 = _t.monotonic()
        rl.acquire("a.com")
        rl.acquire("a.com")
        elapsed = _t.monotonic() - t0
        assert elapsed >= 0.09, f"expected >=90ms, got {elapsed*1000:.0f}ms"

    def test_different_hosts_independent(self):
        rl = _DomainRateLimiter(rps=5.0)
        import time as _t
        t0 = _t.monotonic()
        rl.acquire("a.com")
        rl.acquire("b.com")  # different host — should not wait
        elapsed = _t.monotonic() - t0
        assert elapsed < 0.05

    def test_empty_host_noop(self):
        rl = _DomainRateLimiter(rps=1.0)
        rl.acquire("")  # must not raise or sleep


# ─── enrich_one (network mocked) ──────────────────────────────────


def _mock_response(status: int = 200, body: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.text = body
    return r


class TestEnrichOne:
    def test_skips_known_bot_walled_hosts(self):
        for host in ("linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com"):
            r = enrich_one(f"https://{host}/jobs/1")
            assert not r.ok
            assert "skipped" in r.error

    def test_empty_url(self):
        r = enrich_one("")
        assert not r.ok
        assert "empty" in r.error

    def test_http_error(self):
        sess = MagicMock()
        sess.get.return_value = _mock_response(status=503)
        r = enrich_one("https://acme.com/jobs/1", session=sess)
        assert not r.ok
        assert "503" in r.error

    def test_timeout_returns_clean_error(self):
        import requests as _req
        sess = MagicMock()
        sess.get.side_effect = _req.exceptions.Timeout()
        r = enrich_one("https://acme.com/jobs/1", session=sess)
        assert not r.ok
        assert r.error == "timeout"

    def test_generic_exception_returns_clean_error(self):
        sess = MagicMock()
        sess.get.side_effect = RuntimeError("boom")
        r = enrich_one("https://acme.com/jobs/1", session=sess)
        assert not r.ok
        assert r.error.startswith("fetch_error")

    def test_greenhouse_extraction(self):
        body = ('<html><body><div id="content">'
                'Looking for a Senior CA with 10y exp.'
                '</div><footer></footer></body></html>')
        sess = MagicMock()
        sess.get.return_value = _mock_response(200, body)
        r = enrich_one("https://boards.greenhouse.io/acme/jobs/9", session=sess)
        assert r.ok
        assert r.ats == "greenhouse"
        assert "Senior CA" in r.description

    def test_unknown_host_falls_back_to_full_text(self):
        body = "<html><body><h1>Hello world</h1><p>Some long description here.</p></body></html>"
        sess = MagicMock()
        sess.get.return_value = _mock_response(200, body)
        r = enrich_one("https://example.com/jobs/1", session=sess)
        assert r.ok
        assert "Hello world" in r.description
        assert "Some long description here." in r.description
        assert r.apply_url == "https://example.com/jobs/1"

    def test_rate_limiter_invoked(self):
        rl = MagicMock()
        sess = MagicMock()
        sess.get.return_value = _mock_response(200, "<html></html>")
        enrich_one("https://acme.com/jobs/1", session=sess, rate_limiter=rl)
        rl.acquire.assert_called_once_with("acme.com")


# ─── enrich_jobs (integration over enrich_one) ────────────────────


class TestEnrichJobs:
    def test_empty_input_passes_through(self):
        assert enrich_jobs([]) == []

    def test_mutates_job_with_description(self):
        jobs = [{"url": "https://acme.com/jobs/1", "description": "one liner"}]
        body = "<html><body><p>Full description with lots of words.</p></body></html>"
        with patch("agents.job_enricher.requests.Session") as MockSess:
            inst = MagicMock()
            inst.get.return_value = _mock_response(200, body)
            MockSess.return_value = inst
            enrich_jobs(jobs, max_workers=1, per_domain_rps=100.0)
        assert "Full description" in jobs[0]["description"]
        assert jobs[0].get("enriched") is True

    def test_different_host_apply_url_promotes(self):
        # Original URL is LinkedIn; enrichment finds greenhouse → must store
        # apply_url + listing_url (so the UI can offer both)
        jobs = [{"url": "https://www.linkedin.com/jobs/view/123"}]
        # LinkedIn is in SKIP_HOSTS — should NOT be fetched.  Job unchanged.
        with patch("agents.job_enricher.requests.Session") as MockSess:
            inst = MagicMock()
            MockSess.return_value = inst
            enrich_jobs(jobs, max_workers=1)
            assert inst.get.call_count == 0
        assert "apply_url" not in jobs[0]

    def test_cancel_event_short_circuits(self):
        jobs = [{"url": f"https://acme{i}.com/jobs/{i}"} for i in range(5)]
        ev = threading.Event()
        ev.set()  # pre-cancelled
        with patch("agents.job_enricher.requests.Session") as MockSess:
            inst = MagicMock()
            MockSess.return_value = inst
            enrich_jobs(jobs, max_workers=2, cancel_event=ev)
        # Cancelled before any work — none enriched
        for j in jobs:
            assert "enriched" not in j

    def test_on_progress_callback_invoked(self):
        jobs = [{"url": "https://acme.com/jobs/1"}]
        calls = []
        body = "<html><body>text</body></html>"
        with patch("agents.job_enricher.requests.Session") as MockSess:
            inst = MagicMock()
            inst.get.return_value = _mock_response(200, body)
            MockSess.return_value = inst
            enrich_jobs(jobs, max_workers=1, per_domain_rps=100.0,
                        on_progress=lambda d, t: calls.append((d, t)))
        assert calls and calls[-1] == (1, 1)
