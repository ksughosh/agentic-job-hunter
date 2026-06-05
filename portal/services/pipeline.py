from __future__ import annotations

"""Pipeline orchestration — status tracking, cancellation, and 3-agent execution.

Flow:
  1. Scrape all 26 sources → ephemeral job list
  2. 3-tier dedup against Supabase (URL → title+company → content hash)
  3. Insert truly new jobs into DB
  4. Company review (only new companies, rest from cache)
  5. JD match & rank (only unanalyzed jobs for this user)
  6. Save analyses to DB + local results.json for dashboard
"""

import threading
from dataclasses import asdict
from datetime import datetime

from portal.models import user as user_model
from portal.services import data as data_svc
from portal.services import db

# ─── Pipeline status (in-memory, per-user) ────────────────────────

_statuses: dict[str, dict] = {}
_cancel_flags: dict[str, bool] = {}
_lock = threading.Lock()

_EMPTY = {"running": False, "message": "", "progress": 0, "current_source": "", "scraped_sources": []}


def get_status(user_id: str | None = None) -> dict:
    uid = user_id or user_model.current_id()
    with _lock:
        return _statuses.get(uid, _EMPTY).copy()


def set_status(user_id: str, status: dict):
    with _lock:
        _statuses[user_id] = status


def _set(uid, msg, pct, running=True):
    set_status(uid, {"running": running, "message": msg, "progress": pct})


def request_cancel(user_id: str):
    """Signal the running pipeline to stop at the next checkpoint."""
    with _lock:
        _cancel_flags[user_id] = True
        if uid_status := _statuses.get(user_id):
            uid_status["message"] = "Cancelling..."


def _is_cancelled(user_id: str) -> bool:
    with _lock:
        return _cancel_flags.get(user_id, False)


def _clear_cancel(user_id: str):
    with _lock:
        _cancel_flags.pop(user_id, None)


def _check_cancel(user_id: str):
    """Raise if cancellation was requested — call at every checkpoint."""
    if _is_cancelled(user_id):
        raise PipelineCancelled()


class PipelineCancelled(Exception):
    pass


# ─── Profile injection ───────────────────────────────────────────


def inject_profile(user_id: str | None = None):
    profile = data_svc.load_profile(user_id)
    if profile:
        import config
        for k, v in profile.items():
            config.CANDIDATE_PROFILE[k] = v


def _scan_to_profile(scan: dict) -> dict:
    """Convert a fast_scan_resume result into the profile format used by the pipeline."""
    return {
        "name": scan.get("name", ""),
        "title": scan.get("title", ""),
        "years_experience": scan.get("experience_years", 0),
        "min_experience_level": scan.get("experience_level", "senior"),
        "location": scan.get("location", ""),
        "email": scan.get("email", ""),
        "phone": scan.get("phone", ""),
        "primary_skills": scan.get("primary_skills", []),
        "domain_keywords": scan.get("domain_keywords", []),
        # Carry the candidate's domain through so JDReviewerAgent and
        # JobSearchAgent.filter_scrapers_by_profile() see it. Was previously
        # dropped here, which is why CA profiles still got tech-only scrapers.
        "domain": scan.get("domain", ""),
        "companies_worked": scan.get("companies_worked", []),
        "experience_entries": scan.get("experience_entries", []),
        "education": scan.get("education", []),
        "experience_highlights": [],
        "preferred_role_types": ["full time", "contract"],
        "preferred_regions": ["Remote", "Global"],
        "languages": ["English"],
        "summary": scan.get("summary", ""),
    }


# ─── Full pipeline (onboarding: resume → search → agents) ────────


def run_full(user_id: str, resume_path: str, desired_roles: list, work_mode: str = "remote", location: str = "Anywhere"):
    print(f"[Pipeline] run_full started for {user_id}, roles={desired_roles}, mode={work_mode}, location={location}", flush=True)
    _clear_cancel(user_id)
    try:
        roles_str = ", ".join(desired_roles)

        # ── Try cached scan result first (from upload-time fast_scan_resume) ──
        scan = data_svc.load_scan_result(user_id)
        has_scan = bool(scan and scan.get("name"))

        if has_scan:
            # ── FAST PATH: reuse scan result (no LLM calls needed) ──
            _set(user_id, "Using pre-scanned profile...", 10)
            _check_cancel(user_id)

            profile = _scan_to_profile(scan)
            data_svc.save_profile(profile, user_id)

            db.upsert_user(user_id, name=profile.get("name", "New User"), desired_roles=desired_roles)
            db.upsert_profile(user_id, profile)

            pname = profile["name"]
            if pname == pname.upper():
                pname = pname.title()
            user_model.update(user_id, {"name": pname})

            _set(user_id, f"Profile ready: {profile['name']} — {profile.get('title', '')}", 15)
            _check_cancel(user_id)

            # Search queries from scan + user-selected roles
            years_exp = scan.get("experience_years", 0)
            exp_level = scan.get("experience_level", "senior")

            # Merge scan queries with role-based queries
            scan_queries = scan.get("search_queries", [])
            role_queries = []
            for role in desired_roles:
                role_queries.append(f"remote {role}")
                role_queries.append(f"remote {role} contract")
            all_queries = scan_queries + role_queries
            unique_queries = _build_search_queries(all_queries, work_mode, location)

            print(f"  ⚡ Fast path: reused scan result, skipped LLM parse+refine", flush=True)

        else:
            # ── SLOW PATH: full LLM parse + refine (fallback) ──
            _set(user_id, "Parsing your resume...", 5)
            _check_cancel(user_id)
            from agents.profile_parser import parse_resume_to_profile, refine_search_context

            profile = parse_resume_to_profile(resume_path)
            if not profile.get("name"):
                _set(user_id, "Could not parse resume. Please try a different PDF.", 0, running=False)
                return

            data_svc.save_profile(profile, user_id)

            db.upsert_user(user_id, name=profile.get("name", "New User"), desired_roles=desired_roles)
            db.upsert_profile(user_id, profile)

            pname = profile["name"]
            if pname == pname.upper():
                pname = pname.title()
            user_model.update(user_id, {"name": pname})

            _set(user_id, f"Profile parsed: {profile['name']} — {profile.get('title', '')}", 15)
            _check_cancel(user_id)

            _set(user_id, "Refining search parameters with AI...", 20)

            years_exp = profile.get("years_experience", 0)
            exp_level = profile.get("min_experience_level", "senior")
            if years_exp >= 15: exp_level = "principal"
            elif years_exp >= 10: exp_level = "staff"
            elif years_exp >= 6: exp_level = "senior"
            elif years_exp >= 3: exp_level = "mid"

            all_queries = []
            for role in desired_roles:
                ctx = refine_search_context(profile, role)
                all_queries.extend(ctx.get("search_queries", [f"remote {role}"]))
            unique_queries = _build_search_queries(all_queries, work_mode, location)

        search_ctx = {
            "desired_roles": desired_roles,
            "desired_role": roles_str,
            "work_mode": work_mode,
            "location": location,
            "search_queries": unique_queries[:20],
            "search_summary": f"Searching for {work_mode} {roles_str} roles ({location})",
            "experience_level": exp_level,
            "years_experience": years_exp,
        }
        data_svc.save_search_context(search_ctx, user_id)
        db.upsert_search_context(user_id, search_ctx)

        _set(user_id, f"Searching: {work_mode} roles for {roles_str}", 25)
        _check_cancel(user_id)

        # Steps 3–5: Agent pipeline
        run_agents(user_id, profile, unique_queries[:20], roles_str, work_mode)

    except PipelineCancelled:
        _set(user_id, "Pipeline cancelled.", 0, running=False)
    except Exception as e:
        print(f"[Pipeline] run_full CRASHED: {e}", flush=True)
        _set(user_id, f"Error: {e}", 0, running=False)
        import traceback; traceback.print_exc()
    finally:
        _clear_cancel(user_id)


def _build_search_queries(raw_queries: list, work_mode: str, location: str = "Anywhere") -> list[str]:
    """Enrich queries with work mode(s) and location.

    work_mode can be comma-separated ("remote,hybrid") or single ("remote") or "any".
    """
    modes = [m.strip().lower() for m in work_mode.split(",") if m.strip()] if work_mode else ["any"]
    loc = location.strip() if location and location.lower() not in ("anywhere", "global", "") else ""

    enriched = []
    for q in raw_queries:
        ql = q.lower()
        added = False
        for mode in modes:
            if mode == "any":
                enriched.append(q)
                added = True
            elif mode not in ql:
                enriched.append(f"{mode} {q}")
                added = True
            else:
                enriched.append(q)
                added = True
        if not added:
            enriched.append(q)

    # Add location-enriched variants for first few queries.
    if loc:
        for q in raw_queries[:4]:
            enriched.append(f"{q} {loc}")

    seen, unique = set(), []
    for q in enriched:
        key = q.lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(q)
    return unique


# ─── Agent pipeline (scrape → dedup → review → match) ────────────


def run_agents(user_id: str, profile: dict, search_queries: list,
               desired_role: str, work_mode: str = "remote"):
    _clear_cancel(user_id)
    run_id = None
    try:
        inject_profile(user_id)

        # Ensure user exists in DB
        db.upsert_user(user_id, name=profile.get("name", "New User"))

        run_id = db.create_pipeline_run(user_id, work_mode, search_queries, desired_role)

        # ── Agent 1: Scrape ──
        raw_jobs, scraper = _run_scraper(user_id, profile, search_queries, work_mode)
        _check_cancel(user_id)

        if not raw_jobs:
            _set(user_id, f"No eligible {work_mode} jobs found. Try 'Any' mode or different roles.", 0, running=False)
            db.finish_pipeline_run(run_id, "completed", total_scraped=0, total_new=0)
            return

        # ── 3-tier dedup against DB ──
        new_jobs, skipped = _dedup_against_db(user_id, raw_jobs)
        _check_cancel(user_id)

        total_scraped = len(raw_jobs)
        total_new = len(new_jobs)
        total_skipped = skipped

        _set(user_id, f"Dedup: {total_new} new jobs, {total_skipped} already in DB. Reviewing companies...", 55)

        # ── Insert new jobs into DB ──
        inserted_rows = []
        if new_jobs:
            job_dicts_for_db = _jobs_to_dicts(new_jobs)
            inserted_rows = db.insert_jobs(job_dicts_for_db)

        # Collect all job IDs we need to analyze (new + existing unanalyzed)
        all_eligible_job_ids = [r["id"] for r in inserted_rows]

        # Also check: are there previously-scraped jobs this user hasn't analyzed yet?
        if total_skipped > 0:
            # Get IDs of the skipped jobs (they exist in DB) to check for unanalyzed ones
            skipped_urls = [_job_url(j) for j in raw_jobs if j not in new_jobs]
            if skipped_urls:
                existing_ids = _get_job_ids_by_urls(skipped_urls)
                unanalyzed = db.get_unanalyzed_jobs_for_user(user_id, existing_ids)
                all_eligible_job_ids.extend(unanalyzed)
                if unanalyzed:
                    print(f"  📋 {len(unanalyzed)} previously-scraped jobs also need analysis for this user", flush=True)

        if not all_eligible_job_ids:
            _set(user_id, f"All {total_scraped} jobs already analyzed. Nothing new to process.", 100, running=False)
            db.finish_pipeline_run(run_id, "completed",
                                   total_scraped=total_scraped, total_new=0,
                                   total_skipped=total_skipped, total_analyzed=0,
                                   sources_queried=scraper.stats.get("sources_queried", 0))
            _save_local_results(user_id, profile, scraper)
            return

        # Fetch full job data for jobs that need analysis
        jobs_to_analyze = db.get_jobs_by_ids(all_eligible_job_ids)
        _check_cancel(user_id)

        _set(user_id, f"{len(jobs_to_analyze)} jobs to analyze. Agent 2: Reviewing companies...", 55)

        # ── Agent 2: Company Review (with cache) ──
        company_reviews = _run_company_review_cached(jobs_to_analyze)
        _check_cancel(user_id)
        _set(user_id, f"Agent 2: {len(company_reviews)} companies reviewed. Agent 3: Matching JDs...", 70)

        # ── Agent 3: JD Match & Rank ──
        search_ctx = data_svc.load_search_context(user_id)
        reviewed_jobs, jd_agent = _run_jd_match(user_id, jobs_to_analyze, company_reviews, profile, search_context=search_ctx)
        _check_cancel(user_id)
        _set(user_id, "Saving results to database...", 90)

        # ── Save analyses to DB ──
        _save_analyses_to_db(user_id, reviewed_jobs, jd_agent, jobs_to_analyze)

        # ── Save local results.json for dashboard ──
        _save_local_results(user_id, profile, scraper, jd_agent, reviewed_jobs, company_reviews)

        db.finish_pipeline_run(run_id, "completed",
                               total_scraped=total_scraped, total_new=total_new,
                               total_skipped=total_skipped,
                               total_analyzed=len(reviewed_jobs),
                               sources_queried=scraper.stats.get("sources_queried", 0))

        _set(user_id,
             f"Done! {len(reviewed_jobs)} jobs analyzed ({total_new} new, {total_skipped} cached) from {scraper.stats.get('sources_queried', 0)} sources.",
             100, running=False)

    except PipelineCancelled:
        _set(user_id, "Pipeline cancelled.", 0, running=False)
        if run_id:
            db.finish_pipeline_run(run_id, "cancelled")
    except Exception as e:
        _set(user_id, f"Error: {e}", 0, running=False)
        if run_id:
            db.finish_pipeline_run(run_id, "failed", error_message=str(e))
        import traceback; traceback.print_exc()
    finally:
        _clear_cancel(user_id)


# ─── Scraper ──────────────────────────────────────────────────────


def _run_scraper(user_id, profile, search_queries, work_mode):
    _set(user_id, "Agent 1: Scraping 26 job boards (parallel)...", 25)
    from agents.job_scraper import JobSearchAgent
    from portal.services.parallel import scrape_parallel

    applicant_location = profile.get("location", "").lower()
    if any(city in applicant_location for city in ("india", "bangalore", "bengaluru")):
        applicant_location = "india"

    scraper = JobSearchAgent()
    # Configure the JobSpy aggregator with the candidate's actual location
    # (and a country hint for Indeed) so its results aren't biased toward US.
    from agents.job_scraper import JobSpyScraper
    for s in scraper.scrapers:
        if isinstance(s, JobSpyScraper):
            s.applicant_location = profile.get("location", "") or ""
            if applicant_location == "india":
                s.sites = list(JobSpyScraper.INDIA_SITES)
                s.country_indeed = "India"
            break

    # Skip tech-only scrapers (Arc.dev, GunIO, RemoteOK, etc.) when the
    # candidate's domain is non-tech. Those scrapers hardcode "/software-engineer"
    # URL paths and would otherwise return only engineering jobs for a CA,
    # marketer, lawyer, etc.
    profile_domain = (profile.get("domain") or "").strip()
    scraper.scrapers = JobSearchAgent.filter_scrapers_by_profile(scraper.scrapers, profile_domain)
    total_sources = len(scraper.scrapers)
    if profile_domain:
        print(f"  🎯 Scrapers filtered for domain '{profile_domain}': {total_sources} active", flush=True)

    stopwords = {"the", "and", "for", "with", "from", "remote", "hybrid", "onsite"}
    filter_keywords = list({
        w for q in search_queries for w in q.lower().split()
        if len(w) > 2 and w not in stopwords
    })[:30]
    # Fallback to profile-derived keywords (NOT a hardcoded engineering list).
    # An empty list lets the scraper return everything; downstream LLM matching
    # will rank correctly against the candidate's actual profile.
    if not filter_keywords:
        filter_keywords = [
            w.lower() for w in (profile.get("primary_skills") or [])
            + (profile.get("domain_keywords") or [])
            if isinstance(w, str) and len(w) > 2
        ][:30]

    # Build cancel event from pipeline cancel flag
    cancel_event = threading.Event()
    scraped_sources = []

    def _on_source_done(source_name, count, ok, completed, total):
        scraped_sources.append({"name": source_name, "count": count, "ok": ok})
        if _is_cancelled(user_id):
            cancel_event.set()
        pct = 25 + int((completed / total) * 20)  # 25% → 45%
        current = source_name if completed < total else ""
        set_status(user_id, {
            "running": True,
            "message": f"Scraping... ({completed}/{total} sources done)",
            "progress": pct,
            "current_source": current,
            "scraped_sources": list(scraped_sources),
        })

    results, errors = scrape_parallel(
        scraper.scrapers,
        keywords=filter_keywords,
        max_workers=8,
        on_source_done=_on_source_done,
        cancel_event=cancel_event,
    )
    _check_cancel(user_id)

    # Collect all jobs and update stats
    all_raw = []
    for r in results:
        all_raw.extend(r.jobs)
        scraper.stats["by_source"][r.source_name] = len(r.jobs)
    for e in errors:
        print(f"  ❌ {e}", flush=True)

    scraper.stats["sources_queried"] = total_sources
    scraper.stats["total_raw"] = len(all_raw)

    unique = scraper._deduplicate(all_raw)
    filtered = scraper._filter_eligibility(unique, work_mode, applicant_location)

    # Seniority pre-filter: drop obviously junior roles for experienced candidates
    years_exp = profile.get("years_experience", 0)
    exp_level = profile.get("min_experience_level", "")
    if years_exp >= 10 or exp_level in ("staff", "principal"):
        before_seniority = len(filtered)
        filtered = _filter_seniority(filtered, years_exp)
        dropped = before_seniority - len(filtered)
        if dropped:
            print(f"  🎯 Seniority filter: dropped {dropped} junior/mid roles ({years_exp}+ yrs experience)", flush=True)

    scraper.stats["total_unique"] = len(filtered)
    scraper.stats["total_before_filter"] = len(unique)
    scraper.all_jobs = filtered

    print(f"\n📊 Scraped: {len(all_raw)} raw → {len(unique)} unique → {len(filtered)} eligible ({work_mode}, from {applicant_location})", flush=True)
    return filtered, scraper


def _filter_seniority(jobs: list, years_exp: int) -> list:
    """Drop jobs that are clearly below the candidate's seniority level.
    Only removes obvious mismatches — keeps ambiguous titles.
    """
    import re

    JUNIOR_PATTERNS = [
        r"\bjunior\b", r"\bjr\.?\b", r"\bentry[\s-]?level\b",
        r"\bintern\b", r"\bassociate\b",
        r"\bgraduate\b", r"\bfresher\b", r"\btrainee\b",
    ]
    # For 10+ year candidates, also drop "mid-level" explicit tags
    MID_PATTERNS = [
        r"\bmid[\s-]?level\b", r"\bintermediate\b",
    ]
    # Experience-year mismatch: job asks for 1-3 years but candidate has 10+
    LOW_EXP_PATTERNS = [
        r"\b[12]\+?\s*(?:years|yrs)\s+(?:of\s+)?experience\b",
        r"\b[12]-[34]\s*(?:years|yrs)\b",
    ]

    kept = []
    for job in jobs:
        title = (job.get("title", "") if isinstance(job, dict) else getattr(job, "title", "")).lower()
        desc_start = (job.get("description", "") if isinstance(job, dict) else getattr(job, "description", ""))[:500].lower()

        # Always drop junior titles
        if any(re.search(p, title) for p in JUNIOR_PATTERNS):
            continue

        # For 10+ years, also drop explicit mid-level
        if years_exp >= 10 and any(re.search(p, title) for p in MID_PATTERNS):
            continue

        # Drop if JD explicitly asks for very low experience (1-3 yrs) in first 500 chars
        if years_exp >= 8 and any(re.search(p, desc_start) for p in LOW_EXP_PATTERNS):
            continue

        kept.append(job)

    return kept


# ─── 3-Tier Dedup ────────────────────────────────────────────────


def _dedup_against_db(user_id, jobs):
    """Compare scraped jobs against DB using 3-tier dedup. Returns (new_jobs, skipped_count)."""
    _set(user_id, "Checking database for existing jobs...", 47)

    # Jobs without a URL can't be deduplicated by URL — pass them through to tier 2/3
    jobs_with_url    = [j for j in jobs if _job_url(j).strip()]
    jobs_without_url = [j for j in jobs if not _job_url(j).strip()]
    if jobs_without_url:
        print(f"  ⚠️  {len(jobs_without_url)} jobs have no URL — skipping URL dedup for them", flush=True)

    # Tier 1: URL check (only for jobs that have a URL)
    urls = [_job_url(j) for j in jobs_with_url]
    known_urls = db.find_existing_urls(urls)

    # Bump last_seen for re-scraped jobs
    reseen = [u for u in urls if u in known_urls]
    if reseen:
        try:
            db.bump_last_seen(reseen)
        except Exception:
            pass  # non-critical

    # After tier 1: unknown-url jobs + no-url jobs all proceed to tier 2
    after_t1 = [j for j in jobs_with_url if _job_url(j) not in known_urls] + jobs_without_url
    skipped_t1 = len(jobs_with_url) - (len(after_t1) - len(jobs_without_url))
    print(f"  🔗 Tier 1 (URL): {skipped_t1} known, {len(after_t1)} remaining", flush=True)

    if not after_t1:
        return [], len(jobs)

    # Tier 2: title+company hash
    _set(user_id, f"Dedup tier 2: checking {len(after_t1)} by title+company...", 49)
    tc_hashes = {id(j): db.make_title_company_hash(_job_title(j), _job_company(j)) for j in after_t1}
    known_tc = db.find_existing_title_company(list(set(tc_hashes.values())))
    after_t2 = [j for j in after_t1 if tc_hashes[id(j)] not in known_tc]
    skipped_t2 = len(after_t1) - len(after_t2)
    print(f"  📝 Tier 2 (title+company): {skipped_t2} dupes, {len(after_t2)} remaining", flush=True)

    if not after_t2:
        return [], skipped_t1 + skipped_t2

    # Tier 3: content hash
    _set(user_id, f"Dedup tier 3: checking {len(after_t2)} by content...", 51)
    c_hashes = {id(j): db.make_content_hash(_job_company(j), _job_desc(j)) for j in after_t2}
    known_c = db.find_existing_content(list(set(c_hashes.values())))
    after_t3 = [j for j in after_t2 if c_hashes[id(j)] not in known_c]
    skipped_t3 = len(after_t2) - len(after_t3)
    print(f"  📄 Tier 3 (content): {skipped_t3} dupes, {len(after_t3)} truly new", flush=True)

    total_skipped = skipped_t1 + skipped_t2 + skipped_t3
    return after_t3, total_skipped


# ─── Company Review (DB-cached) ──────────────────────────────────


def _run_company_review_cached(jobs: list[dict]):
    """Run company reviews, using DB cache for already-reviewed companies."""
    company_names = list({j.get("company", "") for j in jobs if j.get("company")})
    cached = db.get_cached_company_reviews(company_names)

    uncached_companies = [n for n in company_names if n.strip().lower() not in cached]
    print(f"  🏢 Companies: {len(cached)} cached, {len(uncached_companies)} new to review", flush=True)

    from agents.company_reviewer import CompanyReviewerAgent
    agent = CompanyReviewerAgent()

    if uncached_companies:
        # Build minimal job dicts for new companies only
        new_company_jobs = []
        seen = set()
        for j in jobs:
            c = j.get("company", "")
            if c.strip().lower() in seen or c.strip().lower() not in {n.strip().lower() for n in uncached_companies}:
                continue
            seen.add(c.strip().lower())
            new_company_jobs.append(j)

        new_reviews = agent.batch_review(new_company_jobs)

        # Persist new reviews to DB
        review_dicts = []
        if hasattr(agent, "to_dicts"):
            review_dicts_raw = agent.to_dicts(new_reviews)
        else:
            review_dicts_raw = new_reviews if isinstance(new_reviews, dict) else {}

        for company_name, review_data in (review_dicts_raw.items() if isinstance(review_dicts_raw, dict) else []):
            review_dicts.append({
                "company_name": company_name,
                "rating": review_data.get("rating", 0),
                "summary": review_data.get("summary", ""),
                "pros": review_data.get("pros", []),
                "cons": review_data.get("cons", []),
                "culture": review_data.get("culture", ""),
            })
        if review_dicts:
            try:
                db.upsert_company_reviews(review_dicts)
            except Exception as e:
                print(f"  ⚠️ Failed to cache company reviews: {e}", flush=True)
    else:
        new_reviews = {}

    # Merge cached + new for downstream use
    # Return in the format the JD reviewer expects
    all_reviews = new_reviews if isinstance(new_reviews, dict) else {}
    return all_reviews


def _get_local_parallel() -> int:
    """Number of concurrent LLM requests for local providers (LM Studio / Ollama).

    Both LM Studio's MLX engine and Ollama batch concurrent requests. Measured
    on M5 Pro: aggregate throughput knees at ~4-5 concurrent (≈2.8x a single
    request); beyond that it saturates (8 ≈ 4). Default 6 captures the knee with
    margin. Override with LLM_NUM_PARALLEL (falls back to OLLAMA_NUM_PARALLEL).
    """
    import os
    for var in ("LLM_NUM_PARALLEL", "OLLAMA_NUM_PARALLEL"):
        env_val = os.environ.get(var, "")
        if env_val.isdigit() and int(env_val) > 0:
            return int(env_val)
    return 6


# ─── JD Match ─────────────────────────────────────────────────────


def _run_jd_match(user_id, jobs, company_reviews, profile=None, search_context=None):
    from agents.jd_reviewer import JDReviewerAgent
    from agents.llm_client import get_provider
    from portal.services.parallel import heuristic_top_n, jd_match_parallel

    # Determine if local model — use parallel + heuristic pre-filter
    is_local = get_provider() in ("gemma", "ollama", "local", "mlx")

    # jobs from DB are dicts already
    job_dicts = jobs if all(isinstance(j, dict) for j in jobs) else [asdict(j) if not isinstance(j, dict) else j for j in jobs]

    # ── Two-pass: heuristic pre-filter for local models ──
    if is_local and len(job_dicts) > 40:
        # Extract profile keywords for heuristic scoring
        profile_kw = []
        if profile:
            profile_kw = [s.lower() for s in profile.get("primary_skills", [])]
            profile_kw += [s.lower() for s in profile.get("domain_keywords", [])]
        exp_level = (profile or {}).get("min_experience_level", "senior")

        _set(user_id, f"Pre-scoring {len(job_dicts)} jobs (heuristic)...", 68)
        top_jobs = heuristic_top_n(job_dicts, profile_keywords=profile_kw, n=40, experience_level=exp_level)
        remaining = [j for j in job_dicts if j not in top_jobs]
        print(f"  🎯 Heuristic pre-filter: {len(top_jobs)} top jobs for LLM, {len(remaining)} heuristic-only", flush=True)

        llm_jobs = top_jobs
    else:
        llm_jobs = job_dicts
        remaining = []

    # ── LLM JD matching (parallel for local, sequential for cloud rate limits) ──
    # Pass profile so the reviewer's TF-IDF corpus and skill buckets are built
    # from THIS candidate's resume, not a baked-in template.
    agent = JDReviewerAgent(use_llm=True, search_context=search_context, profile=profile)
    cancel_event = threading.Event()

    def on_progress(done, total):
        if _is_cancelled(user_id):
            cancel_event.set()
        pct = 70 + int((done / total) * 20)
        _set(user_id, f"Agent 3: Matching JDs... {done}/{total} jobs scored", pct)

    if is_local:
        # Concurrent request capacity for the local LLM server
        local_workers = _get_local_parallel()
        print(f"  ⚡ Local LLM: {local_workers} parallel workers", flush=True)
        reviewed = jd_match_parallel(
            agent, llm_jobs, company_reviews,
            max_workers=local_workers, on_progress=on_progress, cancel_event=cancel_event,
        )
    else:
        # Sequential for cloud (Gemini rate limits: 15 req/min)
        reviewed = agent.batch_review(llm_jobs, company_reviews, progress_callback=on_progress)

    # For remaining jobs (heuristic-only), create lightweight results without LLM
    if remaining:
        no_llm_agent = JDReviewerAgent(use_llm=False, search_context=search_context, profile=profile)
        _set(user_id, f"Scoring {len(remaining)} remaining jobs (heuristic)...", 88)
        for j in remaining:
            company = j.get("company", "")
            cr = company_reviews.get(company)
            result = no_llm_agent.review_job(j, cr)
            reviewed.append(result)

    # Sort by composite score, assign ranks
    reviewed.sort(key=lambda r: r.composite_score, reverse=True)
    for i, r in enumerate(reviewed):
        r.rank = i + 1

    return reviewed, agent


# ─── Save to DB ───────────────────────────────────────────────────


def _save_analyses_to_db(user_id, reviewed_jobs, jd_agent, db_jobs):
    """Persist JD analysis results back to the DB."""
    if not reviewed_jobs:
        return

    # Build a map from source_url to DB job ID
    url_to_id = {j["source_url"]: j["id"] for j in db_jobs}

    analyses = []
    reviewed_dicts = jd_agent.to_dicts(reviewed_jobs) if hasattr(jd_agent, "to_dicts") else reviewed_jobs
    if isinstance(reviewed_dicts, list):
        for rd in reviewed_dicts:
            url = rd.get("url", rd.get("source_url", ""))
            job_id = url_to_id.get(url)
            if not job_id:
                continue
            analyses.append({
                "job_id": job_id,
                "match_score": rd.get("match_score", 0),
                "composite_score": rd.get("composite_score", 0),
                "seniority_score": rd.get("seniority_score", 0),
                "salary_score": rd.get("salary_score", 0),
                "listing_quality_score": rd.get("listing_quality_score", 0),
                "seniority_tag": rd.get("seniority_tag", ""),
                "skill_matches": rd.get("skill_matches", []),
                "strengths": rd.get("strengths", []),
                "weaknesses": rd.get("weaknesses", []),
                "recommendation": rd.get("recommendation", ""),
                "fit_summary": rd.get("fit_summary", ""),
                "contract_bonus": rd.get("contract_bonus", False),
                "genai_mobile_bonus": rd.get("genai_mobile_bonus", False),
            })

    if analyses:
        db.upsert_analyses(user_id, analyses)
        print(f"  💾 Saved {len(analyses)} analyses to DB", flush=True)


# ─── Save local results.json (for dashboard backward compat) ─────


def _save_local_results(user_id, profile, scraper, jd_agent=None, reviewed_jobs=None, company_reviews=None):
    """Write results.json from DB data for the dashboard to read."""
    from agents.company_reviewer import CompanyReviewerAgent

    search_ctx = data_svc.load_search_context(user_id)

    # If we have fresh results, use them
    if jd_agent and reviewed_jobs:
        job_dicts = jd_agent.to_dicts(reviewed_jobs) if hasattr(jd_agent, "to_dicts") else reviewed_jobs

        cr_dicts = {}
        if company_reviews:
            cr_agent = CompanyReviewerAgent.__new__(CompanyReviewerAgent)
            cr_dicts = (cr_agent.to_dicts(company_reviews)
                        if hasattr(cr_agent, "to_dicts") else company_reviews)
            if not isinstance(cr_dicts, dict):
                cr_dicts = {}
    else:
        # Load existing results to preserve them
        existing = data_svc.load_results(user_id)
        job_dicts = existing.get("jobs", [])
        cr_dicts = existing.get("company_reviews", {})

    # Also merge in any previously-analyzed jobs from DB
    try:
        db_analyses = db.get_jobs_for_user(user_id, limit=200)
        if db_analyses:
            existing_urls = {j.get("url", j.get("source_url", "")) for j in job_dicts}
            for a in db_analyses:
                job_data = a.get("jobs", {})
                if not job_data:
                    continue
                url = job_data.get("source_url", "")
                if url in existing_urls:
                    continue
                # Merge job + analysis into dashboard format
                merged = {
                    **job_data,
                    "url": url,
                    "match_score": a.get("match_score", 0),
                    "composite_score": a.get("composite_score", 0),
                    "seniority_tag": a.get("seniority_tag", ""),
                    "skill_matches": a.get("skill_matches", []),
                    "strengths": a.get("strengths", []),
                    "weaknesses": a.get("weaknesses", []),
                    "recommendation": a.get("recommendation", ""),
                    "fit_summary": a.get("fit_summary", ""),
                    "contract_bonus": a.get("contract_bonus", False),
                    "genai_mobile_bonus": a.get("genai_mobile_bonus", False),
                }
                job_dicts.append(merged)
    except Exception as e:
        print(f"  ⚠️ Could not merge DB analyses: {e}", flush=True)

    results = {
        "jobs": job_dicts if isinstance(job_dicts, list) else [],
        "company_reviews": cr_dicts,
        "stats": {
            **scraper.stats,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "candidate": profile.get("name", "Unknown"),
        },
        "profile": profile,
        "search_context": search_ctx,
    }
    data_svc.save_results(results, user_id)


# ─── Job accessor helpers (works with both dataclass and dict) ────


def _job_url(j) -> str:
    if isinstance(j, dict):
        return j.get("source_url", j.get("url", ""))
    return getattr(j, "url", "") or getattr(j, "source_url", "")


def _job_title(j) -> str:
    if isinstance(j, dict):
        return j.get("title", "")
    return getattr(j, "title", "")


def _job_company(j) -> str:
    if isinstance(j, dict):
        return j.get("company", "")
    return getattr(j, "company", "")


def _job_desc(j) -> str:
    if isinstance(j, dict):
        return j.get("description", "")
    return getattr(j, "description", "")


def _jobs_to_dicts(jobs) -> list[dict]:
    """Convert scraped jobs (dataclass or dict) to DB-ready dicts."""
    result = []
    for j in jobs:
        if isinstance(j, dict):
            d = j.copy()
            d["source_url"] = d.pop("url", d.get("source_url", ""))
            result.append(d)
        else:
            d = asdict(j) if hasattr(j, "__dataclass_fields__") else vars(j)
            d["source_url"] = d.pop("url", d.get("source_url", ""))
            result.append(d)
    return result


def _get_job_ids_by_urls(urls):
    """Look up DB job IDs by source URLs.

    Uses RPC (find_job_ids_by_urls) instead of PostgREST in_() filter because
    URLs with ?&=, chars break query-string encoding for batch lookups.
    """
    if not urls:
        return []
    from portal.services.db import _sb
    ids = []
    for i in range(0, len(urls), 500):
        chunk = urls[i:i+500]
        r = _sb().rpc("find_job_ids_by_urls", {"input_urls": chunk}).execute()
        ids.extend(row["id"] for row in (r.data or []))
    return ids
