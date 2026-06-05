from __future__ import annotations

"""Database service — Supabase-backed persistence with 3-tier job dedup.

Dedup tiers (applied during scraping, before expensive LLM calls):
  1. source_url  — exact URL match (fastest, catches same-source re-scrapes)
  2. title_company_hash — md5(lower(title)::lower(company)) catches cross-source dupes
  3. content_hash — md5(lower(company)::first-500-chars-of-desc) catches reposts/renames
"""

import hashlib
import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"))


@lru_cache(maxsize=1)
def _get_client():
    """Singleton Supabase client.
    Uses service_role key (bypasses RLS) for server-side access.
    Falls back to anon key if service_role not configured yet.
    """
    from supabase import create_client
    url = os.environ.get("SUPABASE_URL", "")
    # service_role bypasses RLS — required now that RLS is enabled
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not key:
        key = os.environ.get("SUPABASE_ANON_KEY", "")
        if key:
            import warnings
            warnings.warn(
                "Using anon key — RLS is enabled, DB writes will fail. "
                "Add SUPABASE_SERVICE_ROLE_KEY to .env (Supabase Dashboard → Settings → API → service_role).",
                stacklevel=2,
            )
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
    return create_client(url, key)


def _sb():
    return _get_client()


# ─── Hash helpers ─────────────────────────────────────────────────


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def make_title_company_hash(title: str, company: str) -> str:
    return _md5(f"{title.strip().lower()}::{company.strip().lower()}")


def make_content_hash(company: str, description: str) -> str:
    desc_prefix = description.strip().lower()[:500]
    return _md5(f"{company.strip().lower()}::{desc_prefix}")


# ─── Users ────────────────────────────────────────────────────────


def upsert_user(user_id: str, name: str = "New User", desired_roles: list = None):
    _sb().table("users").upsert({
        "id": user_id,
        "name": name,
        "desired_roles": desired_roles or [],
        "updated_at": _now(),
    }, on_conflict="id").execute()


def get_user(user_id: str) -> dict | None:
    r = _sb().table("users").select("*").eq("id", user_id).maybe_single().execute()
    return r.data


# ─── Jobs — 3-tier dedup ─────────────────────────────────────────


def find_existing_urls(urls: list[str]) -> set[str]:
    """Tier 1: batch-check which source URLs already exist. Returns set of known URLs.
    Uses RPC to avoid PostgREST query-string issues with special chars in URLs.
    """
    if not urls:
        return set()
    known = set()
    for i in range(0, len(urls), 500):
        chunk = urls[i:i+500]
        r = _sb().rpc("find_existing_urls", {"input_urls": chunk}).execute()
        known.update(row["source_url"] for row in (r.data or []))
    return known


def find_existing_title_company(hashes: list[str]) -> set[str]:
    """Tier 2: batch-check which title+company hashes exist. Returns set of known hashes."""
    if not hashes:
        return set()
    known = set()
    for i in range(0, len(hashes), 500):
        chunk = hashes[i:i+500]
        r = _sb().table("jobs").select("title_company_hash").in_("title_company_hash", chunk).execute()
        known.update(row["title_company_hash"] for row in (r.data or []))
    return known


def find_existing_content(hashes: list[str]) -> set[str]:
    """Tier 3: batch-check which content hashes exist. Returns set of known hashes."""
    if not hashes:
        return set()
    known = set()
    for i in range(0, len(hashes), 500):
        chunk = hashes[i:i+500]
        r = _sb().table("jobs").select("content_hash").in_("content_hash", chunk).execute()
        known.update(row["content_hash"] for row in (r.data or []))
    return known


def bump_last_seen(urls: list[str]):
    """Mark re-seen jobs: update last_seen_at and increment times_seen."""
    if not urls:
        return
    for i in range(0, len(urls), 500):
        chunk = urls[i:i+500]
        _sb().rpc("bump_jobs_last_seen", {"urls": chunk}).execute()


def insert_jobs(jobs: list[dict]) -> list[dict]:
    """Upsert new jobs into the DB. Returns the inserted/updated rows with IDs.
    Each dict must have: source_url, title, company, description, source, + optional fields.
    Hashes are computed here.
    Rows with empty source_url are skipped — they can't be deduplicated.
    """
    if not jobs:
        return []
    now = _now()
    rows = []
    skipped = 0
    for j in jobs:
        url = j.get("source_url", "").strip()
        if not url:
            skipped += 1
            continue  # can't store or dedup without a URL
        rows.append({
            "source_url": url,
            "title": j.get("title", ""),
            "company": j.get("company", ""),
            "location": j.get("location", ""),
            "salary": j.get("salary", ""),
            "job_type": j.get("job_type", "Full-Time"),
            "description": j.get("description", "")[:10000],
            "source": j.get("source", ""),
            "source_quality": j.get("source_quality", 50),
            "tags": j.get("tags", []),
            "posted_date": j.get("posted_date", ""),
            "remote": j.get("remote", True),
            "region": j.get("region", ""),
            "currency": j.get("currency", "USD"),
            "salary_min": j.get("salary_min", 0),
            "salary_max": j.get("salary_max", 0),
            "title_company_hash": make_title_company_hash(j.get("title", ""), j.get("company", "")),
            "content_hash": make_content_hash(j.get("company", ""), j.get("description", "")),
            "first_seen_at": now,
            "last_seen_at": now,
            "times_seen": 1,
        })

    if skipped:
        print(f"  ⚠️ Skipped {skipped} jobs with empty source_url", flush=True)

    # Deduplicate within batch — keep last occurrence per URL
    seen_urls: dict[str, int] = {}
    for idx, row in enumerate(rows):
        seen_urls[row["source_url"]] = idx
    if len(seen_urls) < len(rows):
        print(f"  ⚠️ Deduped {len(rows) - len(seen_urls)} intra-batch URL duplicates", flush=True)
        rows = [rows[i] for i in sorted(seen_urls.values())]

    if not rows:
        return []

    # Upsert in chunks — on URL conflict just update last_seen (safe for reruns)
    inserted = []
    for i in range(0, len(rows), 100):
        chunk = rows[i:i+100]
        r = (_sb().table("jobs")
             .upsert(chunk, on_conflict="source_url",
                     ignore_duplicates=False)
             .execute())
        inserted.extend(r.data or [])
    return inserted


def get_jobs_for_user(user_id: str, limit: int = 200) -> list[dict]:
    """Get all analyzed jobs for a user (joined with job data), sorted by composite_score."""
    r = (_sb().table("job_analyses")
         .select("*, jobs(*)")
         .eq("user_id", user_id)
         .order("composite_score", desc=True)
         .limit(limit)
         .execute())
    return r.data or []


def get_unanalyzed_jobs_for_user(user_id: str, job_ids: list[int]) -> list[int]:
    """Given a list of job IDs, return those that DON'T have an analysis for this user yet."""
    if not job_ids:
        return []
    existing = set()
    for i in range(0, len(job_ids), 500):
        chunk = job_ids[i:i+500]
        r = (_sb().table("job_analyses")
             .select("job_id")
             .eq("user_id", user_id)
             .in_("job_id", chunk)
             .execute())
        existing.update(row["job_id"] for row in (r.data or []))
    return [jid for jid in job_ids if jid not in existing]


def get_jobs_by_ids(job_ids: list[int]) -> list[dict]:
    """Fetch full job rows by IDs."""
    if not job_ids:
        return []
    all_jobs = []
    for i in range(0, len(job_ids), 500):
        chunk = job_ids[i:i+500]
        r = _sb().table("jobs").select("*").in_("id", chunk).execute()
        all_jobs.extend(r.data or [])
    return all_jobs


# ─── Job Analyses ─────────────────────────────────────────────────


def upsert_analyses(user_id: str, analyses: list[dict]):
    """Batch upsert job analyses for a user."""
    if not analyses:
        return
    now = _now()
    rows = []
    for a in analyses:
        rows.append({
            "user_id": user_id,
            "job_id": a["job_id"],
            "match_score": a.get("match_score", 0),
            "composite_score": a.get("composite_score", 0),
            "seniority_score": a.get("seniority_score", 0),
            "salary_score": a.get("salary_score", 0),
            "listing_quality_score": a.get("listing_quality_score", 0),
            "seniority_tag": a.get("seniority_tag", ""),
            "skill_matches": a.get("skill_matches", []),
            "strengths": a.get("strengths", []),
            "weaknesses": a.get("weaknesses", []),
            "recommendation": a.get("recommendation", ""),
            "fit_summary": a.get("fit_summary", ""),
            "contract_bonus": a.get("contract_bonus", False),
            "genai_mobile_bonus": a.get("genai_mobile_bonus", False),
            "analyzed_at": now,
        })
    for i in range(0, len(rows), 100):
        chunk = rows[i:i+100]
        _sb().table("job_analyses").upsert(chunk, on_conflict="user_id,job_id").execute()


# ─── Company Reviews ─────────────────────────────────────────────


def get_cached_company_reviews(company_names: list[str]) -> dict[str, dict]:
    """Return cached reviews keyed by company_name_lower."""
    if not company_names:
        return {}
    lower_names = [n.strip().lower() for n in company_names]
    cached = {}
    for i in range(0, len(lower_names), 500):
        chunk = lower_names[i:i+500]
        r = _sb().table("company_reviews").select("*").in_("company_name_lower", chunk).execute()
        for row in (r.data or []):
            cached[row["company_name_lower"]] = row
    return cached


def upsert_company_reviews(reviews: list[dict]):
    """Batch upsert company reviews."""
    if not reviews:
        return
    now = _now()
    rows = []
    for r in reviews:
        rows.append({
            "company_name": r.get("company_name", r.get("company", "")),
            "company_name_lower": r.get("company_name", r.get("company", "")).strip().lower(),
            "rating": r.get("rating", 0),
            "summary": r.get("summary", ""),
            "pros": r.get("pros", []),
            "cons": r.get("cons", []),
            "culture": r.get("culture", ""),
            "reviewed_at": now,
        })
    for i in range(0, len(rows), 100):
        chunk = rows[i:i+100]
        _sb().table("company_reviews").upsert(chunk, on_conflict="company_name_lower").execute()


# ─── Pipeline Runs ────────────────────────────────────────────────


def create_pipeline_run(user_id: str, work_mode: str, search_queries: list,
                        desired_role: str) -> int:
    """Create a pipeline run record. Returns the run ID."""
    r = _sb().table("pipeline_runs").insert({
        "user_id": user_id,
        "status": "running",
        "work_mode": work_mode,
        "search_queries": search_queries,
        "desired_role": desired_role,
    }).execute()
    return r.data[0]["id"]


def update_pipeline_run(run_id: int, updates: dict):
    _sb().table("pipeline_runs").update(updates).eq("id", run_id).execute()


def finish_pipeline_run(run_id: int, status: str = "completed", **stats):
    updates = {"status": status, "finished_at": _now()}
    updates.update(stats)
    update_pipeline_run(run_id, updates)


# ─── Profiles & Search Contexts ──────────────────────────────────


def upsert_profile(user_id: str, profile: dict):
    _sb().table("user_profiles").upsert({
        "user_id": user_id,
        "name": profile.get("name", ""),
        "title": profile.get("title", ""),
        "location": profile.get("location", ""),
        "email": profile.get("email", ""),
        "phone": profile.get("phone", ""),
        "summary": profile.get("summary", ""),
        "skills": profile.get("skills", []),
        "experience": profile.get("experience", []),
        "education": profile.get("education", []),
        "certifications": profile.get("certifications", []),
        "profile_data": profile,
        "updated_at": _now(),
    }, on_conflict="user_id").execute()


def upsert_search_context(user_id: str, ctx: dict):
    _sb().table("search_contexts").upsert({
        "user_id": user_id,
        "desired_roles": ctx.get("desired_roles", []),
        "desired_role": ctx.get("desired_role", ""),
        "work_mode": ctx.get("work_mode", "remote"),
        "search_queries": ctx.get("search_queries", []),
        "search_summary": ctx.get("search_summary", ""),
        "updated_at": _now(),
    }, on_conflict="user_id").execute()


# ─── Helpers ──────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
