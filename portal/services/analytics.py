from __future__ import annotations

"""Dashboard analytics — compute stats from job results."""

JOB_TYPE_NORMALIZE = {
    "full time": "Full-Time", "full-time": "Full-Time", "fulltime": "Full-Time",
    "part time": "Part-Time", "part-time": "Part-Time", "parttime": "Part-Time",
    "contract": "Contract", "freelance": "Contract", "contractor": "Contract",
    "internship": "Internship", "intern": "Internship",
}


def compute_dashboard_stats(jobs: list[dict]) -> dict:
    """Return all pre-computed stats needed by the dashboard template."""
    n = max(len(jobs), 1)

    return {
        "score_distribution": _score_distribution(jobs),
        "source_counts": _count_by(jobs, "source", "Unknown"),
        "type_counts": _type_counts(jobs),
        "top_skills": _top_skills(jobs, limit=15),
        "salary_ranges": _salary_ranges(jobs),
        "avg_match": round(sum(j.get("match_score", 0) for j in jobs) / n, 1),
        "avg_composite": round(sum(j.get("composite_score", 0) for j in jobs) / n, 1),
        "contract_count": sum(
            1 for j in jobs
            if any(k in j.get("job_type", "").lower() for k in ("contract", "freelance"))
        ),
        "high_match": sum(1 for j in jobs if j.get("match_score", 0) >= 60),
        "source_quality_data": _source_quality(jobs),
    }


# ─── Private helpers ──────────────────────────────────────────────


def _score_distribution(jobs):
    buckets = {"90-100": 0, "70-89": 0, "50-69": 0, "30-49": 0, "0-29": 0}
    for j in jobs:
        s = j.get("composite_score", 0)
        if s >= 90:    buckets["90-100"] += 1
        elif s >= 70:  buckets["70-89"] += 1
        elif s >= 50:  buckets["50-69"] += 1
        elif s >= 30:  buckets["30-49"] += 1
        else:          buckets["0-29"] += 1
    return buckets


def _count_by(jobs, key, default="Unknown"):
    counts = {}
    for j in jobs:
        val = j.get(key, default)
        counts[val] = counts.get(val, 0) + 1
    return counts


def _normalize_job_type(raw) -> str:
    """Normalize any job_type value (str, list, etc.) to a clean category."""
    if isinstance(raw, list):
        raw = raw[0] if raw else "Unknown"
    if not isinstance(raw, str) or not raw.strip():
        return "Unknown"
    jt = raw.strip()
    # Check exact match first
    normalized = JOB_TYPE_NORMALIZE.get(jt.lower())
    if normalized:
        return normalized
    # Fuzzy: any combo of "full" + "time" → Full-Time
    jt_lower = jt.lower().replace("-", " ").replace("_", " ")
    if "full" in jt_lower and "time" in jt_lower:
        return "Full-Time"
    if "part" in jt_lower and "time" in jt_lower:
        return "Part-Time"
    if "contract" in jt_lower or "freelance" in jt_lower:
        return "Contract"
    if "intern" in jt_lower:
        return "Internship"
    return jt.title()


def _type_counts(jobs):
    counts = {}
    for j in jobs:
        normalized = _normalize_job_type(j.get("job_type", "Unknown"))
        counts[normalized] = counts.get(normalized, 0) + 1
    return counts


def _top_skills(jobs, limit=15):
    freq = {}
    for j in jobs:
        for s in j.get("skill_matches", []):
            freq[s] = freq.get(s, 0) + 1
    return sorted(freq.items(), key=lambda x: x[1], reverse=True)[:limit]


def _salary_ranges(jobs):
    ranges = {"$200k+": 0, "$150-200k": 0, "$100-150k": 0, "$50-100k": 0, "Undisclosed": 0}
    for j in jobs:
        smax = j.get("salary_max", 0)
        if smax >= 200000:    ranges["$200k+"] += 1
        elif smax >= 150000:  ranges["$150-200k"] += 1
        elif smax >= 100000:  ranges["$100-150k"] += 1
        elif smax > 0:        ranges["$50-100k"] += 1
        else:                 ranges["Undisclosed"] += 1
    return ranges


def _source_quality(jobs):
    from agents.job_scraper import SOURCE_QUALITY

    data = {}
    for j in jobs:
        src = j.get("source", "Unknown")
        sq = j.get("source_quality", SOURCE_QUALITY.get(src, 50))
        if src not in data:
            data[src] = {"quality": sq, "count": 0, "avg_composite": 0, "total_composite": 0}
        data[src]["count"] += 1
        data[src]["total_composite"] += j.get("composite_score", 0)

    for d in data.values():
        d["avg_composite"] = round(d["total_composite"] / max(d["count"], 1), 1)

    return dict(sorted(data.items(), key=lambda x: x[1]["quality"], reverse=True))
