"""
Unified LLM Client — supports Gemini (cloud), Groq (cloud), Gemma (Ollama), MLX (Apple Silicon), and LM Studio.
Toggle between providers at runtime.

Usage:
    from agents.llm_client import call_llm, set_provider, get_provider
    set_provider("mlx")    # or "gemma" / "gemini" / "groq" / "lmstudio"
    result = call_llm("prompt here")
"""

import os
import json
import re
import time
import requests
from pathlib import Path
from urllib.parse import quote_plus

# ─── Load .env ────────────────────────────────────────────────────────

ENV_PATH = Path(__file__).parent.parent / ".env"
if ENV_PATH.exists():
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())

# ─── Provider config ──────────────────────────────────────────────────

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODELS = [
    os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
    "gemini-2.0-flash",
]
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

OLLAMA_BASE = os.environ.get("OLLAMA_BASE", "http://localhost:11434")
GEMMA_MODEL = os.environ.get("GEMMA_MODEL", "gemma4:latest")

# MLX / LM Studio config
# Supports: local path for mlx-lm, or OpenAI-compatible server (LM Studio, vLLM, etc.)
MLX_MODEL = os.environ.get("MLX_MODEL", "gemma-4-E4B-it-MLX-4bit")
MLX_BASE_URL = os.environ.get("MLX_BASE_URL", "http://localhost:1234/v1")  # LM Studio default

# LM Studio standalone (separate from MLX so users can pick either)
LMSTUDIO_MODEL = os.environ.get("LMSTUDIO_MODEL", MLX_MODEL)
LMSTUDIO_BASE_URL = os.environ.get("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")

# Groq cloud
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_BASE_URL = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

# ─── Provider Registry ───────────────────────────────────────────────
#
# Single source of truth for every LLM provider. Each entry declares:
#   kind      – "cloud" or "local"
#   parallel  – max concurrent LLM requests (cloud uses 1 to respect rate limits)
#   dispatch  – callable (prompt, max_tokens, temperature) → str  (bound lazily below)
#   probe     – callable () → bool  (cheap reachability check, bound lazily below)
#
# To add a new provider: add ONE entry here. Everything else (is_cloud,
# available_local_provider, _dispatch, parallelism, validation) derives
# from this registry automatically — no hardcoded lists elsewhere.

_PROVIDERS: dict[str, dict] = {
    "mlx":      {"kind": "local",  "parallel": 1},   # native on-device, no server batching
    "lmstudio": {"kind": "local",  "parallel": 6},   # OpenAI-compat server, batches well
    "gemma":    {"kind": "local",  "parallel": 6},   # Ollama server, batches well
    "groq":     {"kind": "cloud",  "parallel": 1},   # rate-limited cloud
    "gemini":   {"kind": "cloud",  "parallel": 1},   # rate-limited cloud (15 req/min)
}

_active_provider = os.environ.get("LLM_PROVIDER", "mlx")

# MLX model cache (loaded once, reused across calls)
_mlx_model = None
_mlx_tokenizer = None

# Groq rate-limit circuit breaker — set to a unix timestamp; all _call_groq
# requests short-circuit until time.time() >= this value.
_GROQ_BLOCK_UNTIL = 0.0


def set_provider(provider: str):
    global _active_provider
    if provider not in _PROVIDERS:
        raise ValueError(f"Provider must be one of {tuple(_PROVIDERS)}")
    _active_provider = provider
    print(f"[LLM] Switched to provider: {provider}")


def get_provider() -> str:
    return _active_provider


# ─── Registry-driven helpers ─────────────────────────────────────────


def is_cloud(provider: str = "") -> bool:
    p = provider or _active_provider
    entry = _PROVIDERS.get(p)
    return entry["kind"] == "cloud" if entry else False


def get_parallel(provider: str = "") -> int:
    """Max concurrent LLM requests for a provider.

    Checks env overrides first (LLM_NUM_PARALLEL, OLLAMA_NUM_PARALLEL),
    then falls back to the registry default.
    """
    for var in ("LLM_NUM_PARALLEL", "OLLAMA_NUM_PARALLEL"):
        env_val = os.environ.get(var, "")
        if env_val.isdigit() and int(env_val) > 0:
            return int(env_val)
    p = provider or _active_provider
    entry = _PROVIDERS.get(p)
    return entry["parallel"] if entry else 1


# Dispatch table — maps provider id → call function.
# Populated after the _call_* functions are defined (bottom of file).
_DISPATCH_TABLE: dict = {}


def _dispatch(provider: str, prompt: str, max_tokens: int, temperature: float) -> str:
    fn = _DISPATCH_TABLE.get(provider)
    if fn:
        return fn(prompt, max_tokens, temperature)
    # Fallback for unknown provider — try gemini
    return _DISPATCH_TABLE.get("gemini", lambda *a: "")(prompt, max_tokens, temperature)


def available_local_provider() -> str:
    """Return the first reachable local provider id, or '' if none.

    Iterates registry locals and runs their probe. No hardcoded list —
    adding a new local provider to _PROVIDERS is sufficient.
    """
    for pid, entry in _PROVIDERS.items():
        if entry["kind"] != "local":
            continue
        probe = _PROBE_TABLE.get(pid)
        if probe and probe():
            return pid
    return ""


# Probe table — maps provider id → reachability check.
# Populated after probe functions are defined (bottom of file).
_PROBE_TABLE: dict = {}


def call_provider(provider: str, prompt: str, max_tokens: int = 4096, temperature: float = 0.7) -> str:
    """Direct call to a named provider, bypassing the active-provider router.
    Lets call sites that need an explicit cascade (e.g. the scan agent) pick
    the order themselves instead of relying on call_llm's automatic fallback.
    """
    return _dispatch(provider, prompt, max_tokens, temperature)


def call_llm(prompt: str, max_tokens: int = 4096, temperature: float = 0.7) -> str:
    """Unified entry point used by every LLM call site (jd_match, resume_writer,
    cover_letter, talent_review, etc.). Follows the same cascade as the resume
    scanner so the rule is identical wherever an LM is invoked:

      1. Try the active provider (cloud OR local — whichever the user picked).
      2. If empty AND the active is cloud, probe for a reachable local
         provider (mlx → lmstudio → gemma). Retry against that local provider.
      3. Return "" if everything failed.

    We deliberately do NOT cascade cloud→cloud (avoid silently spending quota
    on a different account) and do NOT cascade local→cloud (a local failure
    is usually a config issue the user should see, not paper over with a
    cloud spend).
    """
    primary = _dispatch(_active_provider, prompt, max_tokens, temperature)
    if primary:
        return primary

    if is_cloud(_active_provider):
        local = available_local_provider()
        if local and local != _active_provider:
            result = _dispatch(local, prompt, max_tokens, temperature)
            if result:
                print(f"[LLM] {_active_provider} unavailable; fell back to local {local}", flush=True)
                return result
            print(f"[LLM] {_active_provider} unavailable; local {local} also failed", flush=True)
        else:
            print(f"[LLM] {_active_provider} unavailable; no local LM reachable", flush=True)
    return ""


def _call_groq(prompt: str, max_tokens: int = 4096, temperature: float = 0.7) -> str:
    """Call Groq cloud (OpenAI-compatible API)."""
    if not GROQ_API_KEY:
        print("[Groq] GROQ_API_KEY not set", flush=True)
        return ""

    # Free-tier circuit breaker — once we hit 429, hold off subsequent calls
    # until the server-supplied Retry-After window elapses. Prevents the
    # pipeline from spamming hundreds of 429s while parallel workers race.
    global _GROQ_BLOCK_UNTIL
    now = time.time()
    if now < _GROQ_BLOCK_UNTIL:
        return ""

    for attempt in range(3):
        try:
            resp = requests.post(
                f"{GROQ_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": GROQ_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=60,
            )
            if resp.status_code == 429:
                # Honor Retry-After if present, else exponential backoff.
                ra = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
                wait = float(ra) if ra and ra.replace(".", "", 1).isdigit() else (10 * (attempt + 1))
                wait = min(wait, 60.0)
                _GROQ_BLOCK_UNTIL = time.time() + wait
                print(f"[Groq] 429 rate-limited; blocking all calls for {wait:.0f}s", flush=True)
                return ""
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except requests.exceptions.RequestException as e:
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            print(f"[Groq] Error after {attempt+1} retries: {e}", flush=True)
            return ""
    return ""


def _call_lmstudio(prompt: str, max_tokens: int = 4096, temperature: float = 0.7) -> str:
    """Call LM Studio's OpenAI-compatible server (separate from MLX path)."""
    try:
        resp = requests.post(
            f"{LMSTUDIO_BASE_URL}/chat/completions",
            json={
                "model": LMSTUDIO_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
                "reasoning_effort": "none",
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except requests.exceptions.ConnectionError:
        print("[LMStudio] Server not running on " + LMSTUDIO_BASE_URL, flush=True)
        return ""
    except Exception as e:
        print(f"[LMStudio] Error: {e}", flush=True)
        return ""


# ─── Gemini implementation ────────────────────────────────────────────


def _call_gemini(prompt: str, max_tokens: int = 4096, temperature: float = 0.7) -> str:
    """Call Gemini API with model fallback and retry."""
    if not GEMINI_API_KEY or GEMINI_API_KEY == "your-gemini-api-key-here":
        return ""

    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }

    for model in GEMINI_MODELS:
        url = f"{GEMINI_API_BASE}/{model}:generateContent?key={GEMINI_API_KEY}"
        for attempt in range(3):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=45)
                if resp.status_code == 429:
                    wait = (attempt + 1) * 15
                    print(f"[Gemini/{model}] Rate limited, waiting {wait}s ({attempt+1}/3)...")
                    time.sleep(wait)
                    continue
                if resp.status_code == 503:
                    print(f"[Gemini/{model}] Unavailable, trying next model...")
                    break
                resp.raise_for_status()
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    for part in parts:
                        if "text" in part and "thought" not in part:
                            return part["text"]
                    for part in parts:
                        if "text" in part:
                            return part["text"]
            except requests.exceptions.Timeout:
                print(f"[Gemini/{model}] Timeout ({attempt+1}/3)")
                time.sleep(5)
            except requests.exceptions.RequestException as e:
                print(f"[Gemini/{model}] Error: {e}")
                break
            except (KeyError, IndexError, json.JSONDecodeError) as e:
                print(f"[Gemini/{model}] Parse error: {e}")
                break
    return ""


# ─── Gemma4 (Ollama) implementation ──────────────────────────────────


def _call_gemma(prompt: str, max_tokens: int = 4096, temperature: float = 0.7) -> str:
    """Call local Gemma4 via Ollama REST API."""
    try:
        resp = requests.post(
            f"{OLLAMA_BASE}/api/generate",
            json={
                "model": GEMMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                    "num_ctx": 4096,
                },
            },
            timeout=120,  # local models can be slower
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", "")
    except requests.exceptions.ConnectionError:
        print("[Gemma4] Ollama not running. Start with: ollama serve")
        return ""
    except requests.exceptions.Timeout:
        print("[Gemma4] Timeout — model may be loading")
        return ""
    except Exception as e:
        print(f"[Gemma4] Error: {e}")
        return ""


# ─── MLX / LM Studio implementation ─────────────────────────────────


def _call_mlx(prompt: str, max_tokens: int = 4096, temperature: float = 0.7) -> str:
    """Call local model via LM Studio / OpenAI-compatible API, or native mlx-lm.

    Priority:
      1. LM Studio server (OpenAI-compatible API at MLX_BASE_URL)
      2. Native mlx-lm (loads model into memory)
      3. Fallback to Ollama
    """
    # Try OpenAI-compatible API first (LM Studio, mlx_lm.server, etc.)
    result = _call_openai_compat(prompt, max_tokens, temperature)
    if result:
        return result

    # Try native mlx-lm
    result = _call_mlx_native(prompt, max_tokens, temperature)
    if result:
        return result

    # Fallback to Ollama
    print("[MLX] No MLX backend available, falling back to Ollama", flush=True)
    return _call_gemma(prompt, max_tokens, temperature)


def _call_openai_compat(prompt: str, max_tokens: int, temperature: float) -> str:
    """Call OpenAI-compatible local API (LM Studio, vLLM, mlx_lm.server)."""
    try:
        resp = requests.post(
            f"{MLX_BASE_URL}/chat/completions",
            json={
                "model": MLX_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
                # Disable model "thinking" — Gemma in LM Studio otherwise burns
                # hundreds of reasoning tokens before the answer (~5x slower).
                # Unknown to servers that don't support it; they ignore it.
                "reasoning_effort": "none",
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except requests.exceptions.ConnectionError:
        return ""  # server not running, try next backend
    except Exception as e:
        print(f"[MLX/API] Error: {e}", flush=True)
        return ""


def _load_mlx_model():
    """Load MLX model once, cache in memory."""
    global _mlx_model, _mlx_tokenizer
    if _mlx_model is not None:
        return _mlx_model, _mlx_tokenizer

    try:
        from mlx_lm import load
        model_path = MLX_MODEL
        # If it's a local path, use directly; otherwise HuggingFace ID
        print(f"[MLX] Loading model: {model_path} (first call)...", flush=True)
        _mlx_model, _mlx_tokenizer = load(model_path)
        print(f"[MLX] Model loaded successfully.", flush=True)
        return _mlx_model, _mlx_tokenizer
    except ImportError:
        print("[MLX] mlx-lm not installed. Run: pip install mlx-lm")
        return None, None
    except Exception as e:
        print(f"[MLX] Failed to load model: {e}")
        return None, None


def _call_mlx_native(prompt: str, max_tokens: int = 4096, temperature: float = 0.7) -> str:
    """Call model via native mlx-lm (loads model into process memory)."""
    model, tokenizer = _load_mlx_model()
    if model is None:
        return ""

    try:
        from mlx_lm import generate

        # Apply chat template if tokenizer supports it
        if hasattr(tokenizer, "apply_chat_template"):
            messages = [{"role": "user", "content": prompt}]
            formatted = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
        else:
            formatted = prompt

        response = generate(
            model, tokenizer,
            prompt=formatted,
            max_tokens=max_tokens,
            temp=temperature,
            verbose=False,
        )
        return response.strip() if response else ""
    except Exception as e:
        print(f"[MLX] Generation error: {e}", flush=True)
        return ""


# ─── Web tools for Gemma4 ────────────────────────────────────────────
# Gemma4 runs locally and can't browse — these tools give it web access.


def web_search(query: str, num_results: int = 5) -> list[dict]:
    """
    Search the web using DuckDuckGo HTML (no API key needed).
    Returns list of {title, url, snippet}.
    """
    results = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for result in soup.select(".result")[:num_results]:
            title_el = result.select_one(".result__title a")
            snippet_el = result.select_one(".result__snippet")
            if title_el:
                href = title_el.get("href", "")
                # DuckDuckGo wraps URLs
                if "uddg=" in href:
                    from urllib.parse import unquote, parse_qs, urlparse
                    parsed = parse_qs(urlparse(href).query)
                    href = unquote(parsed.get("uddg", [href])[0])
                results.append({
                    "title": title_el.get_text(strip=True),
                    "url": href,
                    "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
                })
    except Exception as e:
        print(f"[WebSearch] Error: {e}")
    return results


def web_fetch(url: str, max_chars: int = 5000) -> str:
    """Fetch and extract main text content from a URL."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove script/style
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        # Try article or main content
        main = soup.find("article") or soup.find("main") or soup.find("body")
        if main:
            text = main.get_text(separator="\n", strip=True)
        else:
            text = soup.get_text(separator="\n", strip=True)

        # Clean up
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        return "\n".join(lines)[:max_chars]
    except Exception as e:
        print(f"[WebFetch] Error fetching {url}: {e}")
        return ""


def web_search_and_summarize(query: str) -> str:
    """Search web + fetch top results + summarize with Gemma4. Tool-augmented LLM."""
    results = web_search(query, num_results=3)
    if not results:
        return f"No web results found for: {query}"

    # Fetch content from top results
    context_parts = []
    for r in results[:3]:
        content = web_fetch(r["url"], max_chars=2000)
        if content:
            context_parts.append(f"Source: {r['title']} ({r['url']})\n{content[:1500]}")

    if not context_parts:
        # Use snippets as fallback
        context_parts = [f"{r['title']}: {r['snippet']}" for r in results]

    context = "\n\n---\n\n".join(context_parts)

    # Ask Gemma to summarize
    prompt = f"""Based on the following web search results for "{query}", provide a concise factual summary.

WEB RESULTS:
{context}

Provide a concise factual summary. Respond in plain text only."""

    return _call_gemma(prompt, max_tokens=1000, temperature=0.3)


def gemma_with_web_search(prompt: str, search_query: str = "") -> str:
    """
    Enhanced Gemma call that can optionally enrich context with web search.
    If search_query is provided, web results are prepended to the prompt.
    """
    if search_query:
        web_context = ""
        results = web_search(search_query, num_results=3)
        for r in results[:3]:
            content = web_fetch(r["url"], max_chars=1500)
            if content:
                web_context += f"\n[Source: {r['title']}]\n{content[:1000]}\n"
            else:
                web_context += f"\n[Source: {r['title']}] {r['snippet']}\n"

        if web_context:
            prompt = f"""I've searched the web for "{search_query}" and found the following context:

{web_context}

Now, using this context along with your own knowledge:

{prompt}"""

    return _call_gemma(prompt, max_tokens=4096, temperature=0.7)


# ─── High-level agent functions (provider-agnostic) ──────────────────
# These are the same interfaces as gemini_client.py, but route through call_llm.


def llm_jd_match(profile: dict, job_posting: dict) -> dict:
    """Expert headhunter job match scoring."""
    profile_text = _format_profile(profile)
    job_text = _format_job(job_posting)

    prompt = f"""You are an expert head hunter finding the right jobs given a profile.

CANDIDATE PROFILE:
{profile_text}

JOB POSTING:
{job_text}

Compare this job posting against the candidate profile and provide:
1. A match score from 0-100 based on how well the profile fits this job
2. Top 3 strengths (why this candidate is a good fit)
3. Top 3 gaps (what's missing or weak)
4. One-line reasoning

Respond ONLY in this exact JSON format:
{{"match_score": <number>, "reasoning": "<one line>", "strengths": ["<s1>", "<s2>", "<s3>"], "gaps": ["<g1>", "<g2>", "<g3>"]}}"""

    response = call_llm(prompt, max_tokens=2000, temperature=0.3)
    return _parse_json_response(response, default={"match_score": 0, "reasoning": "", "strengths": [], "gaps": []})


def llm_resume_writer(profile: dict, job_description: str, job_title: str, company: str, current_resume: str = "", reviewer_feedback: str = "") -> str:
    """Write/refine a tailored single-page resume."""
    profile_text = _format_profile(profile)

    feedback_section = ""
    if reviewer_feedback:
        feedback_section = f"""
PREVIOUS REVIEWER FEEDBACK (address these issues):
{reviewer_feedback}

CURRENT RESUME DRAFT (refine this):
{current_resume}
"""
    else:
        feedback_section = "\nWrite a fresh tailored resume based on the profile and job description.\n"

    prompt = f"""You are a professional resume writer. Tailor this candidate's resume for the role below.

CANDIDATE PROFILE:
{profile_text}

JOB DESCRIPTION:
Title: {job_title} at {company}
{job_description}
{feedback_section}
CRITICAL FORMATTING RULES — the output is parsed by code, follow EXACTLY:

Line 1: FULL NAME IN UPPERCASE
Line 2: Job title / tagline
Line 3: email | phone | location (pipe-separated)

SUMMARY
2-3 sentences. Max 50 words.

SKILLS
One line: skill1, skill2, skill3 (max 10 skills, comma-separated, most relevant first)

EXPERIENCE
Role Title
Company Name | Jan 2020 – Present
• Achievement bullet with numbers (max 15 words)
• Achievement bullet with numbers
(Max 4 roles, max 3 bullets each. Most recent first.)

EDUCATION
Degree — University, Year

RULES:
- Total under 500 words. MUST fit single A4 page.
- Section headers must be EXACTLY: SUMMARY, SKILLS, EXPERIENCE, EDUCATION (all caps, alone on line)
- Bullets start with •
- Experience detail line uses | to separate company and dates
- No markdown, no JSON, no extra formatting. Plain text only.
- Quantify achievements (%, $, numbers) wherever possible"""

    return call_llm(prompt, max_tokens=3000, temperature=0.6)


def llm_cover_letter_writer(profile: dict, job_description: str, job_title: str, company: str, tailored_resume: str = "", reviewer_feedback: str = "") -> str:
    """Write/refine a concise cover letter."""
    profile_text = _format_profile(profile)

    feedback_section = ""
    if reviewer_feedback:
        feedback_section = f"""
PREVIOUS REVIEWER FEEDBACK (address these issues):
{reviewer_feedback}

CURRENT COVER LETTER DRAFT (refine this):
{tailored_resume}
"""

    # If using Gemma and we want company context, do a web search
    company_context = ""
    if _active_provider == "gemma" and company:
        results = web_search(f"{company} company about mission", num_results=2)
        for r in results[:2]:
            company_context += f"  - {r['title']}: {r['snippet']}\n"
        if company_context:
            company_context = f"\nCOMPANY RESEARCH:\n{company_context}"

    prompt = f"""You are a professional applying for the following role. Write a concise but effective cover letter.

CANDIDATE PROFILE:
{profile_text}

JOB DESCRIPTION:
Title: {job_title} at {company}
{job_description}

TAILORED RESUME CONTEXT:
{tailored_resume[:1500] if tailored_resume else 'Not provided'}
{company_context}{feedback_section}
Write a concise cover letter (under 350 words) that:
- Opens with a compelling hook for this role and company
- Maps 2-3 key experiences to requirements with metrics
- Shows genuine interest in the company
- Closes with a clear call to action

Write ONLY the cover letter text. No JSON, no markdown."""

    return call_llm(prompt, max_tokens=1500, temperature=0.7)


def llm_talent_review(profile: dict, job_description: str, job_title: str, company: str, resume_draft: str) -> dict:
    """Expert TA specialist review — what's missing from the profile."""
    profile_text = _format_profile(profile)

    prompt = f"""You are an expert talent acquisition specialist reviewing profiles for the following role:

JOB: {job_title} at {company}
JOB DESCRIPTION:
{job_description}

CANDIDATE RESUME/PROFILE:
{resume_draft}

FULL CANDIDATE BACKGROUND:
{profile_text}

Tell me what is missing from this profile that would persuade you to NOT go with this candidate. Be specific and actionable.

Respond ONLY in this exact JSON format:
{{"score": <0-100>, "verdict": "<hire/maybe/pass>", "missing": ["<specific gap 1>", "<specific gap 2>", "<specific gap 3>"], "red_flags": ["<concern 1>", "<concern 2>"], "suggestions_to_fix": ["<actionable fix 1>", "<actionable fix 2>", "<actionable fix 3>"], "overall_feedback": "<2-3 sentence summary>"}}"""

    response = call_llm(prompt, max_tokens=2000, temperature=0.4)
    return _parse_json_response(response, default={
        "score": 50, "verdict": "maybe", "missing": [], "red_flags": [],
        "suggestions_to_fix": [], "overall_feedback": ""
    })


# ─── Helpers ──────────────────────────────────────────────────────────


def _format_profile(profile: dict) -> str:
    lines = [
        f"Name: {profile.get('name', '')}",
        f"Title: {profile.get('title', '')}",
        f"Experience: {profile.get('years_experience', '')} years",
        f"Location: {profile.get('location', '')}",
        f"Primary Skills: {', '.join(profile.get('primary_skills', []))}",
        f"Preferred Roles: {', '.join(profile.get('preferred_role_types', []))}",
        f"Experience Highlights:",
    ]
    for h in profile.get("experience_highlights", []):
        lines.append(f"  - {h}")
    lines.append("Education:")
    for edu in profile.get("education", []):
        lines.append(f"  - {edu.get('degree', '')} from {edu.get('university', '')} ({edu.get('year', '')})")
    lines.append(f"Companies: {', '.join(profile.get('companies_worked', []))}")
    return "\n".join(lines)


def _format_job(job: dict) -> str:
    title = job.get("title", job.get("job_title", ""))
    company = job.get("company", "")
    desc = job.get("description", "")[:2000]
    job_type = job.get("job_type", "")
    location = job.get("location", "")
    salary_min = job.get("salary_min", 0)
    salary_max = job.get("salary_max", 0)

    if isinstance(job_type, list):
        job_type = " ".join(str(x) for x in job_type)
    if isinstance(location, list):
        location = " ".join(str(x) for x in location)

    lines = [f"Title: {title}", f"Company: {company}", f"Type: {job_type}", f"Location: {location}"]
    if salary_max > 0:
        lines.append(f"Salary: ${salary_min:,.0f} - ${salary_max:,.0f}")
    lines.append(f"Description: {desc}")
    return "\n".join(lines)


def _parse_json_response(text: str, default: dict) -> dict:
    if not text:
        return default
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
    return default


# ─── Backward compatibility aliases ──────────────────────────────────
# ─── Populate registry tables (after all _call_* functions are defined) ───

_DISPATCH_TABLE.update({
    "mlx":      _call_mlx,
    "lmstudio": _call_lmstudio,
    "gemma":    _call_gemma,
    "groq":     _call_groq,
    "gemini":   _call_gemini,
})


def _probe_mlx() -> bool:
    try:
        import mlx_lm  # noqa: F401
        return True
    except Exception:
        return False


def _probe_lmstudio() -> bool:
    try:
        return requests.get(f"{LMSTUDIO_BASE_URL}/models", timeout=2).status_code == 200
    except Exception:
        return False


def _probe_ollama() -> bool:
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=2)
        return r.status_code == 200 and bool(r.json().get("models"))
    except Exception:
        return False


_PROBE_TABLE.update({
    "mlx":      _probe_mlx,
    "lmstudio": _probe_lmstudio,
    "gemma":    _probe_ollama,
})


# Other modules import from gemini_client — keep those names working.

call_gemini = call_llm
gemini_jd_match = llm_jd_match
gemini_resume_writer = llm_resume_writer
gemini_cover_letter_writer = llm_cover_letter_writer
gemini_talent_review = llm_talent_review
