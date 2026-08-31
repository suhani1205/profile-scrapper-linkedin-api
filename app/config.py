"""Configuration and provider selection.

All secrets come from environment variables — nothing is hard-coded, so the
repository stays credential-free. See .env.example.
"""
from __future__ import annotations

import os
from functools import lru_cache

from app.providers.base import ProfileProvider
from app.providers.fixture import FixtureProvider
from app.providers.linkedin import LinkedInProvider


class Settings:
    def __init__(self) -> None:
        self.provider_name = os.getenv("PROVIDER", "linkedin").lower()
        self.li_at = os.getenv("LI_AT", "")
        self.li_jsessionid = os.getenv("LI_JSESSIONID", "")
        self.li_bcookie = os.getenv("LI_BCOOKIE", "")
        self.li_bscookie = os.getenv("LI_BSCOOKIE", "")
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.api_key = os.getenv("API_KEY", "")  # optional auth on our own API
        self.request_timeout = float(os.getenv("REQUEST_TIMEOUT", "30"))


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_provider() -> ProfileProvider:
    s = get_settings()
    if s.provider_name == "fixture":
        return FixtureProvider()
    if s.provider_name == "linkedin":
        return LinkedInProvider(
            s.li_at,
            s.li_jsessionid,
            timeout=s.request_timeout,
            bcookie=s.li_bcookie,
            bscookie=s.li_bscookie,
        )
    raise ValueError(f"Unknown PROVIDER: {s.provider_name}  (valid: linkedin, fixture)")
