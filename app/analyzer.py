"""Claude-powered LinkedIn profile analysis.

Uses claude-opus-4-6 with adaptive thinking at medium effort to generate
structured career insights from a fetched LinkedIn profile.

The system prompt is prompt-cached so repeated calls only pay for the
profile content as input, not the static instructions.
"""
from __future__ import annotations

import json

import anthropic

from app.providers.base import UpstreamError
from app.schema import Profile, ProfileAnalysis

_SYSTEM_PROMPT = """\
You are a professional career intelligence analyst.
Given a LinkedIn profile in JSON format, produce a structured career analysis.

Return ONLY a valid JSON object with these exact keys:
- summary: string — 2-3 sentence professional overview
- career_trajectory: string — career progression narrative (1-2 sentences)
- top_skills: array of strings — top 5 skills inferred from experience and listed skills
- industry: string — primary industry
- seniority_level: string — one of: Junior, Mid-level, Senior, Lead, Principal, Director, VP, C-Level
- years_of_experience: integer or null — estimated total years of professional experience
- notable_achievements: array of strings — up to 3 key career highlights
- outreach_hook: string — a personalized, non-generic opening line for professional outreach

Return only the JSON object. No markdown fences, no extra text.\
"""


async def analyze_profile(profile: Profile, api_key: str) -> ProfileAnalysis:
    """Send a fetched LinkedIn profile to Claude Opus 4.6 and return structured insights.

    Uses:
    - claude-opus-4-6 (latest, most capable model)
    - adaptive thinking  — Claude decides when and how deeply to reason
    - medium effort      — balanced cost/quality tradeoff
    - streaming          — avoids HTTP timeouts on longer responses
    - prompt caching     — the static system prompt is cached across calls
    """
    if not api_key:
        raise UpstreamError(
            "ANTHROPIC_API_KEY is not configured. "
            "Set it in your environment to enable profile analysis."
        )

    client = anthropic.AsyncAnthropic(api_key=api_key)

    profile_json = profile.model_dump_json(indent=2)

    try:
        async with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=4096,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    # Cache the static system prompt — subsequent calls read from cache
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Analyze this LinkedIn profile and return the JSON object:\n\n"
                        f"{profile_json}"
                    ),
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

    # Extract the text block — skip any thinking blocks
    text_block = next(
        (b for b in response.content if b.type == "text"), None
    )
    if not text_block:
        raise UpstreamError("Claude returned no text in its response.")

    try:
        data = json.loads(text_block.text)
        return ProfileAnalysis(**data)
    except (json.JSONDecodeError, Exception) as exc:
        raise UpstreamError(
            f"Could not parse Claude's response as ProfileAnalysis: {exc}"
        ) from exc
