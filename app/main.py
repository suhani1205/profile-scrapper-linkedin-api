"""LinkedIn Profile API — FastAPI application.

Accepts a LinkedIn profile URL and returns structured profile JSON, sourced
from a licensed data provider (default: Proxycurl). The data source sits behind
a provider interface, so the public contract is independent of the upstream.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from app.analyzer import analyze_profile
from app.config import get_provider, get_settings
from app.providers.base import ProfileNotFound, RateLimited, UpstreamError
from app.schema import ErrorResponse, Profile, ProfileAnalysis
from app.validation import InvalidProfileURL, normalize_profile_url

app = FastAPI(
    title="Profile Scrapper LinkedIn API",
    description=(
        "Accepts a LinkedIn profile URL and returns structured profile data as JSON, "
        "sourced by reverse-engineering LinkedIn's Voyager API. "
        "Also exposes /analyze, which runs the profile through Claude Opus 4.6 "
        "to generate structured career insights."
    ),
    version="2.0.0",
)

# --- naive in-memory rate limiter (per client, best-effort) --------------
_WINDOW_SECONDS = 60
_MAX_REQUESTS = 20
_hits: dict[str, deque] = defaultdict(deque)


def _rate_limit(client_id: str) -> None:
    now = time.time()
    q = _hits[client_id]
    while q and q[0] < now - _WINDOW_SECONDS:
        q.popleft()
    if len(q) >= _MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Try again shortly.",
        )
    q.append(now)


def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """Optional auth: if API_KEY is set in the environment, require it."""
    expected = get_settings().api_key
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


@app.get("/health")
async def health() -> dict:
    s = get_settings()
    return {"status": "ok", "provider": s.provider_name}


@app.get(
    "/profile",
    response_model=Profile,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
async def get_profile(
    url: str = Query(..., description="LinkedIn profile URL, e.g. https://www.linkedin.com/in/username/"),
    _auth: None = Depends(require_api_key),
    x_forwarded_for: Optional[str] = Header(default=None),
) -> Profile:
    _rate_limit(x_forwarded_for or "local")

    try:
        clean_url = normalize_profile_url(url)
    except InvalidProfileURL as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    provider = get_provider()
    try:
        return await provider.fetch(clean_url)
    except ProfileNotFound:
        raise HTTPException(status_code=404, detail="Profile not found.")
    except RateLimited:
        raise HTTPException(
            status_code=429, detail="Upstream provider rate limit reached."
        )
    except UpstreamError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream error: {exc}")


@app.get(
    "/analyze",
    response_model=ProfileAnalysis,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
    summary="Analyze a LinkedIn profile with Claude Opus 4.6",
    description=(
        "Fetches a LinkedIn profile and passes it to Claude Opus 4.6 "
        "(adaptive thinking, medium effort) to produce structured career insights: "
        "summary, trajectory, top skills, industry, seniority, achievements, and "
        "a personalized outreach hook. Requires ANTHROPIC_API_KEY to be configured."
    ),
)
async def get_profile_analysis(
    url: str = Query(..., description="LinkedIn profile URL, e.g. https://www.linkedin.com/in/username/"),
    _auth: None = Depends(require_api_key),
    x_forwarded_for: Optional[str] = Header(default=None),
) -> ProfileAnalysis:
    _rate_limit(x_forwarded_for or "local")

    try:
        clean_url = normalize_profile_url(url)
    except InvalidProfileURL as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    provider = get_provider()
    try:
        profile = await provider.fetch(clean_url)
    except ProfileNotFound:
        raise HTTPException(status_code=404, detail="Profile not found.")
    except RateLimited:
        raise HTTPException(status_code=429, detail="Upstream provider rate limit reached.")
    except UpstreamError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream error: {exc}")

    try:
        return await analyze_profile(profile, get_settings().anthropic_api_key)
    except UpstreamError as exc:
        raise HTTPException(status_code=502, detail=f"Analysis error: {exc}")


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    code_map = {400: "bad_request", 401: "unauthorized", 404: "not_found",
                429: "rate_limited", 502: "upstream_error"}
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": code_map.get(exc.status_code, "error")},
    )
