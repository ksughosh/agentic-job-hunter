"""Tests for parallel pipeline operations.

Tests are written BEFORE implementation (TDD). Each test describes the expected
behavior of the parallel system, covering:

1. Parallel scraping — thread safety, status updates, error isolation
2. Heuristic pre-filter — correct top-N selection, seniority awareness
3. Parallel JD matching — thread safety, progress tracking, cancellation
4. Parallel company review — thread safety, cache integration
5. End-to-end orchestration — all phases together, error recovery
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import FakeScraper, make_fake_job


# ═══════════════════════════════════════════════════════════════════
# 1. PARALLEL SCRAPING
# ═══════════════════════════════════════════════════════════════════


class TestParallelScraping:
    """Tests for scraping multiple sources concurrently."""

    def test_all_scrapers_called_exactly_once(self, fake_scrapers):
        """Every scraper must be called exactly once, regardless of parallelism."""
        from portal.services.parallel import scrape_parallel

        results, errors = scrape_parallel(fake_scrapers, keywords=["engineer"], max_workers=4)
        for s in fake_scrapers:
            assert s.scrape_count == 1, f"{s.name} called {s.scrape_count} times, expected 1"

    def test_parallel_faster_than_sequential(self, fake_scrapers):
        """Parallel scraping must be measurably faster than sequential."""
        from portal.services.parallel import scrape_parallel

        start = time.monotonic()
        scrape_parallel(fake_scrapers, keywords=["engineer"], max_workers=4)
        parallel_time = time.monotonic() - start

        # Sequential would take ~sum of all delays = ~0.5s+
        # Parallel with 4 workers should be ~3-4 rounds
        # We just check it's under the sequential total
        sequential_total = sum(s._delay for s in fake_scrapers)
        assert parallel_time < sequential_total, (
            f"Parallel ({parallel_time:.2f}s) should be faster than sequential ({sequential_total:.2f}s)"
        )

    def test_failed_scraper_doesnt_crash_others(self, fake_scrapers):
        """One failing scraper must not kill the entire scrape run."""
        from portal.services.parallel import scrape_parallel

        results, errors = scrape_parallel(fake_scrapers, keywords=["engineer"], max_workers=4)

        # Scraper 8 fails — should appear in errors
        assert any("FailSource" in str(e) for e in errors), "Failed scraper should be in errors list"

        # Other scrapers should still return results
        total_jobs = sum(len(r.jobs) for r in results)
        assert total_jobs > 0, "Other scrapers should still produce results"

    def test_results_contain_source_metadata(self, fake_scrapers):
        """Each result batch must carry the source name + job count for status updates."""
        from portal.services.parallel import scrape_parallel

        results, _ = scrape_parallel(fake_scrapers, keywords=["engineer"], max_workers=4)
        for r in results:
            assert hasattr(r, "source_name"), "Result must have source_name"
            assert hasattr(r, "jobs"), "Result must have jobs list"
            assert hasattr(r, "ok"), "Result must have ok flag"
            assert isinstance(r.jobs, list)

    def test_status_callback_called_per_source(self, fake_scrapers):
        """Progress callback fires for each completed source (success or fail)."""
        from portal.services.parallel import scrape_parallel

        callback_calls = []

        def on_source_done(source_name, count, ok, completed, total):
            callback_calls.append({
                "name": source_name, "count": count, "ok": ok,
                "completed": completed, "total": total,
            })

        scrape_parallel(fake_scrapers, keywords=["engineer"], max_workers=4,
                        on_source_done=on_source_done)

        assert len(callback_calls) == len(fake_scrapers), (
            f"Callback called {len(callback_calls)} times, expected {len(fake_scrapers)}"
        )
        # Completed counter should monotonically increase
        completed_seq = [c["completed"] for c in callback_calls]
        assert completed_seq == sorted(completed_seq), "Completed counter must increase monotonically"
        assert completed_seq[-1] == len(fake_scrapers)

    def test_no_shared_state_mutation_between_scrapers(self):
        """Scrapers must not corrupt each other's results via shared state."""
        from portal.services.parallel import scrape_parallel

        # Create scrapers that return different jobs
        scrapers = []
        for i in range(8):
            jobs = [make_fake_job(title=f"UniqueJob-{i}-{j}", source=f"S{i}") for j in range(5)]
            scrapers.append(FakeScraper(f"Source{i}", jobs=jobs, delay=0.05))

        results, _ = scrape_parallel(scrapers, keywords=["engineer"], max_workers=8)

        # Flatten all jobs
        all_jobs = [j for r in results for j in r.jobs]
        all_titles = [j["title"] for j in all_jobs]

        # Each title should appear exactly once (no cross-contamination)
        assert len(all_titles) == len(set(all_titles)), "Job titles must be unique — no cross-scraper contamination"
        assert len(all_jobs) == 40, "8 scrapers × 5 jobs = 40 total"

    def test_cancellation_stops_pending_scrapers(self):
        """Cancel flag should prevent queued (not yet started) scrapers from running."""
        from portal.services.parallel import scrape_parallel

        # 20 slow scrapers, cancel after ~2 complete
        scrapers = [FakeScraper(f"S{i}", [make_fake_job()], delay=0.15) for i in range(20)]
        cancel_event = threading.Event()

        def on_done(name, count, ok, completed, total):
            if completed >= 2:
                cancel_event.set()

        results, _ = scrape_parallel(scrapers, keywords=["test"], max_workers=2,
                                     on_source_done=on_done, cancel_event=cancel_event)

        # Not all 20 should have run
        ran = sum(1 for s in scrapers if s.scrape_count > 0)
        assert ran < 20, f"Cancellation should prevent some scrapers from running. {ran}/20 ran."

    def test_empty_scraper_list(self):
        """Edge case: no scrapers at all."""
        from portal.services.parallel import scrape_parallel

        results, errors = scrape_parallel([], keywords=["test"], max_workers=4)
        assert results == []
        assert errors == []


# ═══════════════════════════════════════════════════════════════════
# 2. HEURISTIC PRE-FILTER (TOP-N FOR LLM)
# ═══════════════════════════════════════════════════════════════════


class TestHeuristicPreFilter:
    """Tests for the fast heuristic scoring that selects top-N jobs for LLM review."""

    def test_returns_top_n_jobs(self, sample_jobs):
        """Must return exactly N jobs when there are more than N available."""
        from portal.services.parallel import heuristic_top_n

        top = heuristic_top_n(sample_jobs, profile_keywords=["android", "mobile", "kotlin"], n=20)
        assert len(top) == 20

    def test_returns_all_if_fewer_than_n(self):
        """If only 5 jobs available and n=20, return all 5."""
        from portal.services.parallel import heuristic_top_n

        jobs = [make_fake_job(title=f"Job {i}") for i in range(5)]
        top = heuristic_top_n(jobs, profile_keywords=["engineer"], n=20)
        assert len(top) == 5

    def test_relevant_jobs_ranked_higher(self):
        """Jobs matching profile keywords should score higher than generic ones."""
        from portal.services.parallel import heuristic_top_n

        jobs = [
            make_fake_job(title="Staff Android Engineer", description="Kotlin, Jetpack Compose, mobile SDK"),
            make_fake_job(title="Receptionist", description="Answer phones, schedule meetings"),
            make_fake_job(title="Senior Mobile Developer", description="Android, iOS, React Native"),
        ]
        top = heuristic_top_n(jobs, profile_keywords=["android", "mobile", "kotlin"], n=2)

        titles = [j["title"] for j in top]
        assert "Receptionist" not in titles, "Irrelevant job should not be in top-N"
        assert "Staff Android Engineer" in titles

    def test_seniority_boost(self):
        """Jobs with matching seniority should rank higher than mismatched ones."""
        from portal.services.parallel import heuristic_top_n

        jobs = [
            make_fake_job(title="Junior Python Developer", description="Python, Flask, 1-2 years"),
            make_fake_job(title="Staff Python Engineer", description="Python, Flask, distributed systems"),
            make_fake_job(title="Mid-Level Python Developer", description="Python, Flask, 3-5 years"),
        ]
        top = heuristic_top_n(
            jobs,
            profile_keywords=["python", "flask"],
            n=2,
            experience_level="staff",
        )
        titles = [j["title"] for j in top]
        assert titles[0] == "Staff Python Engineer", "Staff role should rank first for staff-level candidate"

    def test_returns_dict_with_heuristic_score(self, sample_jobs):
        """Each returned job should carry a _heuristic_score field."""
        from portal.services.parallel import heuristic_top_n

        top = heuristic_top_n(sample_jobs, profile_keywords=["engineer"], n=10)
        for j in top:
            assert "_heuristic_score" in j, "Job must have _heuristic_score attached"
            assert isinstance(j["_heuristic_score"], (int, float))

    def test_empty_jobs_list(self):
        """Edge case: no jobs to filter."""
        from portal.services.parallel import heuristic_top_n

        top = heuristic_top_n([], profile_keywords=["test"], n=20)
        assert top == []

    def test_empty_keywords(self, sample_jobs):
        """With no keywords, should still return top N (fallback to other signals)."""
        from portal.services.parallel import heuristic_top_n

        top = heuristic_top_n(sample_jobs, profile_keywords=[], n=10)
        assert len(top) == 10


# ═══════════════════════════════════════════════════════════════════
# 3. PARALLEL JD MATCHING
# ═══════════════════════════════════════════════════════════════════


class TestParallelJDMatch:
    """Tests for running JD review_job() calls in parallel."""

    def _make_mock_reviewer(self, delay=0.02):
        """Create a mock JD reviewer with thread-safe call tracking."""
        reviewer = MagicMock()
        call_log = []
        lock = threading.Lock()

        def fake_review(job, company_review=None):
            with lock:
                call_log.append(job.get("title", ""))
            time.sleep(delay)
            result = MagicMock()
            result.composite_score = 75.0
            result.match_score = 70.0
            result.job_title = job.get("title", "")
            result.company = job.get("company", "")
            return result

        reviewer.review_job = fake_review
        reviewer._call_log = call_log
        return reviewer

    def test_all_jobs_reviewed_exactly_once(self, sample_jobs):
        """Every job must be reviewed exactly once in parallel mode."""
        from portal.services.parallel import jd_match_parallel

        reviewer = self._make_mock_reviewer()
        results = jd_match_parallel(reviewer, sample_jobs[:20], company_reviews={}, max_workers=4)

        assert len(results) == 20
        assert len(reviewer._call_log) == 20
        # Each title should appear exactly once
        assert len(set(reviewer._call_log)) == 20

    def test_parallel_faster_than_sequential(self, sample_jobs):
        """Parallel JD matching must be measurably faster."""
        from portal.services.parallel import jd_match_parallel

        reviewer = self._make_mock_reviewer(delay=0.05)
        jobs = sample_jobs[:20]

        start = time.monotonic()
        jd_match_parallel(reviewer, jobs, company_reviews={}, max_workers=4)
        parallel_time = time.monotonic() - start

        sequential_total = 0.05 * 20  # 1.0s
        assert parallel_time < sequential_total * 0.7, (
            f"Parallel ({parallel_time:.2f}s) should be <70% of sequential ({sequential_total:.2f}s)"
        )

    def test_progress_callback_monotonic(self, sample_jobs):
        """Progress callback must report monotonically increasing done count."""
        from portal.services.parallel import jd_match_parallel

        reviewer = self._make_mock_reviewer()
        progress = []

        def on_progress(done, total):
            progress.append((done, total))

        jd_match_parallel(reviewer, sample_jobs[:15], company_reviews={},
                          max_workers=4, on_progress=on_progress)

        done_values = [p[0] for p in progress]
        assert done_values == sorted(done_values), "Done count must increase monotonically"
        assert progress[-1] == (15, 15)

    def test_one_review_failure_doesnt_crash_batch(self, sample_jobs):
        """If one review_job() throws, other jobs should still be reviewed."""
        from portal.services.parallel import jd_match_parallel

        call_count = {"n": 0}
        lock = threading.Lock()

        def flaky_review(job, company_review=None):
            with lock:
                call_count["n"] += 1
                if call_count["n"] == 5:
                    raise ValueError("LLM returned garbage")
            result = MagicMock()
            result.composite_score = 50.0
            result.job_title = job.get("title", "")
            return result

        reviewer = MagicMock()
        reviewer.review_job = flaky_review

        results = jd_match_parallel(reviewer, sample_jobs[:20], company_reviews={}, max_workers=4)
        # 19 succeed, 1 fails gracefully
        assert len(results) >= 19, f"Expected ~19 results, got {len(results)}"

    def test_cancellation_stops_pending_reviews(self, sample_jobs):
        """Cancel event should stop queued review tasks."""
        from portal.services.parallel import jd_match_parallel

        reviewer = self._make_mock_reviewer(delay=0.1)
        cancel_event = threading.Event()

        def on_progress(done, total):
            if done >= 5:
                cancel_event.set()

        results = jd_match_parallel(
            reviewer, sample_jobs[:50], company_reviews={},
            max_workers=2, on_progress=on_progress, cancel_event=cancel_event,
        )
        # Should have significantly fewer than 50 results
        assert len(results) < 50, f"Cancellation should prevent all 50 reviews, got {len(results)}"

    def test_company_review_passed_correctly(self):
        """Each job's company review should be passed to review_job()."""
        from portal.services.parallel import jd_match_parallel

        jobs = [
            make_fake_job(title="Eng at Google", company="Google"),
            make_fake_job(title="Eng at Meta", company="Meta"),
        ]
        company_reviews = {"Google": {"rating": 4.5}, "Meta": {"rating": 4.2}}

        passed_reviews = []
        lock = threading.Lock()

        def tracking_review(job, company_review=None):
            with lock:
                passed_reviews.append((job["company"], company_review))
            result = MagicMock()
            result.composite_score = 50.0
            return result

        reviewer = MagicMock()
        reviewer.review_job = tracking_review

        jd_match_parallel(reviewer, jobs, company_reviews, max_workers=2)

        reviews_by_company = {c: r for c, r in passed_reviews}
        assert reviews_by_company["Google"] == {"rating": 4.5}
        assert reviews_by_company["Meta"] == {"rating": 4.2}

    def test_thread_safety_of_results_collection(self):
        """Results list must not have race conditions under heavy parallelism."""
        from portal.services.parallel import jd_match_parallel

        jobs = [make_fake_job(title=f"Job-{i}") for i in range(100)]

        def fast_review(job, company_review=None):
            result = MagicMock()
            result.composite_score = float(hash(job["title"]) % 100)
            result.job_title = job["title"]
            return result

        reviewer = MagicMock()
        reviewer.review_job = fast_review

        results = jd_match_parallel(reviewer, jobs, company_reviews={}, max_workers=8)
        assert len(results) == 100, f"Expected 100 results, got {len(results)} — likely race condition"

        # No duplicate titles
        titles = [r.job_title for r in results]
        assert len(set(titles)) == 100, "Duplicate results detected — race condition"


# ═══════════════════════════════════════════════════════════════════
# 4. PARALLEL COMPANY REVIEW
# ═══════════════════════════════════════════════════════════════════


class TestParallelCompanyReview:
    """Tests for running company reviews in parallel."""

    def test_each_company_reviewed_once(self):
        """Even if 10 jobs are from Google, Google should be reviewed only once."""
        from portal.services.parallel import company_review_parallel

        jobs = [make_fake_job(company="Google") for _ in range(10)]
        jobs += [make_fake_job(company="Meta") for _ in range(5)]

        call_log = []
        lock = threading.Lock()

        def fake_review(company_name, desc="", job_type="", location=""):
            with lock:
                call_log.append(company_name)
            time.sleep(0.02)
            return MagicMock(company_name=company_name, rating=4.0)

        reviewer = MagicMock()
        reviewer.review = fake_review

        results = company_review_parallel(reviewer, jobs, max_workers=4)
        assert call_log.count("Google") == 1, "Google should be reviewed only once"
        assert call_log.count("Meta") == 1
        assert len(results) == 2  # 2 unique companies

    def test_review_failure_isolated(self):
        """One company review failing shouldn't block others."""
        from portal.services.parallel import company_review_parallel

        jobs = [
            make_fake_job(company="GoodCorp"),
            make_fake_job(company="BadCorp"),
            make_fake_job(company="OkCorp"),
        ]

        def flaky_review(company_name, desc="", job_type="", location=""):
            if company_name == "BadCorp":
                raise RuntimeError("Review failed")
            return MagicMock(company_name=company_name, rating=3.5)

        reviewer = MagicMock()
        reviewer.review = flaky_review

        results = company_review_parallel(reviewer, jobs, max_workers=4)
        assert "GoodCorp" in results
        assert "OkCorp" in results
        # BadCorp may or may not be in results, but shouldn't crash


# ═══════════════════════════════════════════════════════════════════
# 5. INTEGRATION: STATUS UPDATES THREAD SAFETY
# ═══════════════════════════════════════════════════════════════════


class TestStatusThreadSafety:
    """Tests for pipeline status updates under concurrent access."""

    def test_concurrent_status_writes_dont_corrupt(self):
        """Multiple threads writing status for same user must not corrupt dict."""
        from portal.services.pipeline import set_status, get_status

        user_id = "test-concurrent-user"
        errors = []

        def writer(thread_id):
            for i in range(100):
                try:
                    set_status(user_id, {
                        "running": True,
                        "message": f"Thread {thread_id} iteration {i}",
                        "progress": i,
                    })
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Concurrent writes caused errors: {errors}"

        # Final status should be valid dict
        status = get_status(user_id)
        assert isinstance(status, dict)
        assert "running" in status
        assert "message" in status

    def test_cancel_flag_visible_across_threads(self):
        """Cancel set in one thread must be visible in another."""
        from portal.services.pipeline import (
            request_cancel, _is_cancelled, _clear_cancel, set_status
        )

        user_id = "test-cancel-visibility"
        set_status(user_id, {"running": True, "message": "test", "progress": 50})

        seen_cancel = {"value": False}

        def worker():
            for _ in range(100):
                if _is_cancelled(user_id):
                    seen_cancel["value"] = True
                    return
                time.sleep(0.01)

        t = threading.Thread(target=worker)
        t.start()
        time.sleep(0.05)
        request_cancel(user_id)
        t.join(timeout=2)

        assert seen_cancel["value"], "Cancel flag must be visible across threads"
        _clear_cancel(user_id)


# ═══════════════════════════════════════════════════════════════════
# 6. INTEGRATION: TWO-PASS ARCHITECTURE
# ═══════════════════════════════════════════════════════════════════


class TestTwoPassArchitecture:
    """Tests for the full two-pass flow: heuristic → parallel LLM."""

    def test_two_pass_reviews_only_top_n(self, sample_jobs):
        """LLM reviewer should only be called for top-N jobs, not all."""
        from portal.services.parallel import heuristic_top_n, jd_match_parallel

        # 50 jobs in, top 20 go to LLM
        top = heuristic_top_n(sample_jobs, profile_keywords=["engineer"], n=20)

        call_count = {"n": 0}
        lock = threading.Lock()

        def counting_review(job, company_review=None):
            with lock:
                call_count["n"] += 1
            result = MagicMock()
            result.composite_score = 50.0
            return result

        reviewer = MagicMock()
        reviewer.review_job = counting_review

        jd_match_parallel(reviewer, top, company_reviews={}, max_workers=4)
        assert call_count["n"] == 20, f"LLM called {call_count['n']} times, expected 20"

    def test_non_llm_jobs_get_heuristic_scores(self, sample_jobs):
        """Jobs below top-N cutoff should still have scores (heuristic-only)."""
        from portal.services.parallel import heuristic_top_n

        top = heuristic_top_n(sample_jobs, profile_keywords=["engineer"], n=20)

        # The remaining 30 should not be discarded — they still have heuristic scores
        # This test validates the caller can merge them
        all_scored = heuristic_top_n(sample_jobs, profile_keywords=["engineer"], n=50)
        assert len(all_scored) == 50
        for j in all_scored:
            assert "_heuristic_score" in j
