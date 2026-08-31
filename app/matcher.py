"""Claude-powered candidate-to-job fit scoring.

Accepts a fetched LinkedIn profile and a job description (plain text or URL),
sends both to Claude Opus 4.6 with adaptive thinking at high effort, and returns
a structured JobMatch with a 0-100 fit score, verdict, strengths, skill gaps,
interview angles, and a tailored outreach pitch.

What makes this different from a basic summariser:
- Claude reasons like a senior technical recruiter, not a keyword matcher
- References specific companies, roles, and achievements from the profile
- Produces interview angles grounded in the actual gap analysis
- The tailored pitch is role-specific, not a template
"""
from __future__ import annotations

import json
import re

import anthropic
import httpx

from app.providers.base import UpstreamError
from app.schema import JobMatch, Profile

_SYSTEM_PROMPT = """\
You are a senior technical recruiter with 15 years of experience at top-tier companies.
You are given a LinkedIn profile (JSON) and a job description.
Your task: evaluate how well this specific candidate fits this specific role.

Be concrete — reference actual companies, job titles, skills, and achievements from the
profile. Do not be generic. A vague answer ("good communication skills") is wrong.

Return ONLY a valid JSON object with these exact keys:

- fit_score: integer 0-100
    0 = completely wrong fit, 100 = exceptionally strong match.
    Score relative to a competitive hiring bar, not charitably.

- verdict: string — exactly one of: "Strong Match", "Moderate Match", "Weak Match"

- recommendation: string — exactly one of: "Hire", "Consider", "Pass"

- strengths: array of strings (3-5 items)
    Specific ways the candidate's background aligns. Name real skills, companies, or
    achievements. Example: "Led ML infra at Stripe which maps directly to the MLOps
    requirements" — not "has relevant experience".

- skill_gaps: array of strings (0-4 items, empty array if no meaningful gaps)
    Specific missing skills or experience. Be honest. Reference the JD explicitly.

- experience_alignment: string (1-2 sentences)
    How the candidate's seniority, years of experience, and career trajectory fit
    the role's level requirements. Is this a step up, lateral, or overqualified?

- standout_factor: string (1 sentence)
    The single most interesting thing about this candidate for this specific role —
    or the biggest red flag. Be direct.

- interview_angles: array of exactly 3 strings
    Specific interview topics to probe, grounded in the gap analysis and the
    candidate's background. Not generic ("tell me about a challenge") — specific
    ("How did they scale the real-time pipeline at Razorpay beyond 10k RPS?").

- tailored_pitch: string (2-3 sentences)
    A personalized outreach message a recruiter would actually send. Must reference
    something specific from the profile AND something specific from the job.
    Do not use "I came across your profile" or any other generic opener.

Return only the JSON object. No markdown fences, no extra text.\
"""

_JOB_FETCH_TIMEOUT = 15.0
_JOB_TEXT_LIMIT = 10_000  # chars — enough context without blowing the token budget


async def _load_job_description(source: str) -> str:
    """Return job description text.

    If ``source`` looks like a URL, fetch the page and strip HTML.
    Otherwise treat it as raw text.
    """
    if not source.startswith(("http://", "https://")):
        return source[:_JOB_TEXT_LIMIT]

    try:
        async with httpx.AsyncClient(
            timeout=_JOB_FETCH_TIMEOUT, follow_redirects=True
        ) as client:
            resp = await client.get(
                source,
                headers={"User-Agent": "Mozilla/5.0 (compatible; profilebot/1.0)"},
            )
        html = resp.text
    except httpx.RequestError as exc:
        raise UpstreamError(f"Could not fetch job URL: {exc}") from exc

    # Strip HTML tags → collapse whitespace → truncate
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:_JOB_TEXT_LIMIT]


async def match_profile_to_job(
    profile: Profile,
    job_source: str,
    api_key: str,
) -> JobMatch:
    """Score a LinkedIn profile against a job description using Claude Opus 4.6.

    ``job_source`` can be:
    - Plain text of the job description
    - A URL (fetched and HTML-stripped automatically)
    """
    if not api_key:
        raise UpstreamError(
            "ANTHROPIC_API_KEY is not configured. "
            "Set it in your environment to enable job matching."
        )

    job_text = await _load_job_description(job_source)
    if not job_text.strip():
        raise UpstreamError("Job description is empty after fetching/parsing.")

    profile_json = profile.model_dump_json(indent=2)

    client = anthropic.AsyncAnthropic(api_key=api_key)

    user_content = (
        f"## LinkedIn Profile\n\n{profile_json}\n\n"
        f"## Job Description\n\n{job_text}"
    )

    try:
        async with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=4096,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},   # higher than /analyze — nuanced reasoning
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": user_content,
                }
            ],
        ) as stream:
            response = await stream.get_final_message()
    except anthropic.AuthenticationError:
        raise UpstreamError("Anthropic API key is invalid.")
    except anthropic.RateLimitError:
        raise UpstreamError("Anthropic rate limit reached. Try again shortly.")
    except anthropic.APIConnectionError as exc:
        raise UpstreamError(f"Network error reaching Anthropic: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise UpstreamError(
            f"Anthropic returned HTTP {exc.status_code}: {exc.message}"
        ) from exc

    text_block = next((b for b in response.content if b.type == "text"), None)
    if not text_block:
        raise UpstreamError("Claude returned no text in its response.")

    try:
        data = json.loads(text_block.text)
        return JobMatch(**data)
    except (json.JSONDecodeError, Exception) as exc:
        raise UpstreamError(
            f"Could not parse Claude's response as JobMatch: {exc}"
        ) from exc
