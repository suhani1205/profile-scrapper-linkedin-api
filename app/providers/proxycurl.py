"""Proxycurl-backed provider.

Proxycurl is a licensed LinkedIn data provider. It handles the compliance and
infrastructure of sourcing profile data; we consume its API over HTTPS and map
the response into our own schema so our public contract is provider-independent.

Docs: https://nubela.co/proxycurl/docs
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from app.providers.base import (
    ProfileProvider,
    ProfileNotFound,
    RateLimited,
    UpstreamError,
)
from app.schema import (
    Certification,
    DateInfo,
    Education,
    Experience,
    Language,
    Profile,
)

PROXYCURL_ENDPOINT = "https://nubela.co/proxycurl/api/v2/linkedin"


def _date(raw: Optional[dict]) -> Optional[DateInfo]:
    if not raw:
        return None
    return DateInfo(
        year=raw.get("year"),
        month=raw.get("month"),
        day=raw.get("day"),
    )


class ProxycurlProvider(ProfileProvider):
    def __init__(self, api_key: str, timeout: float = 30.0):
        if not api_key:
            raise ValueError("Proxycurl API key is required")
        self._api_key = api_key
        self._timeout = timeout

    async def fetch(self, profile_url: str) -> Profile:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        params = {
            "url": profile_url,
            "skills": "include",
            "use_cache": "if-present",
            "fallback_to_cache": "on-error",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    PROXYCURL_ENDPOINT, headers=headers, params=params
                )
        except httpx.RequestError as exc:
            raise UpstreamError(f"Network error contacting provider: {exc}") from exc

        if resp.status_code == 404:
            raise ProfileNotFound(profile_url)
        if resp.status_code == 429:
            raise RateLimited()
        if resp.status_code == 401:
            raise UpstreamError("Provider rejected credentials", status=401)
        if resp.status_code >= 400:
            raise UpstreamError(
                f"Provider returned {resp.status_code}: {resp.text[:200]}",
                status=resp.status_code,
            )

        return self._map(resp.json(), profile_url)

    # --- mapping upstream JSON -> our schema -----------------------------
    def _map(self, data: dict[str, Any], profile_url: str) -> Profile:
        return Profile(
            public_identifier=data.get("public_identifier"),
            profile_url=profile_url,
            full_name=data.get("full_name"),
            first_name=data.get("first_name"),
            last_name=data.get("last_name"),
            headline=data.get("headline"),
            summary=data.get("summary"),
            location=self._location(data),
            country=data.get("country_full_name"),
            profile_picture_url=data.get("profile_pic_url"),
            background_image_url=data.get("background_cover_image_url"),
            follower_count=data.get("follower_count"),
            connection_count=data.get("connections"),
            experiences=[self._experience(e) for e in data.get("experiences", [])],
            education=[self._education(e) for e in data.get("education", [])],
            skills=data.get("skills", []) or [],
            certifications=[
                self._certification(c) for c in data.get("certifications", [])
            ],
            languages=self._languages(data),
        )

    @staticmethod
    def _location(data: dict) -> Optional[str]:
        parts = [data.get("city"), data.get("state"), data.get("country_full_name")]
        parts = [p for p in parts if p]
        return ", ".join(parts) if parts else None

    @staticmethod
    def _experience(e: dict) -> Experience:
        return Experience(
            title=e.get("title"),
            company=e.get("company"),
            company_linkedin_url=e.get("company_linkedin_profile_url"),
            location=e.get("location"),
            description=e.get("description"),
            starts_at=_date(e.get("starts_at")),
            ends_at=_date(e.get("ends_at")),
            is_current=e.get("ends_at") is None and e.get("starts_at") is not None,
        )

    @staticmethod
    def _education(e: dict) -> Education:
        return Education(
            school=e.get("school"),
            degree=e.get("degree_name"),
            field_of_study=e.get("field_of_study"),
            description=e.get("description"),
            starts_at=_date(e.get("starts_at")),
            ends_at=_date(e.get("ends_at")),
        )

    @staticmethod
    def _certification(c: dict) -> Certification:
        return Certification(
            name=c.get("name"),
            authority=c.get("authority"),
            url=c.get("url"),
            starts_at=_date(c.get("starts_at")),
            ends_at=_date(c.get("ends_at")),
        )

    @staticmethod
    def _languages(data: dict) -> list[Language]:
        out: list[Language] = []
        for item in data.get("languages_and_proficiencies", []) or []:
            if isinstance(item, dict) and item.get("name"):
                out.append(
                    Language(name=item["name"], proficiency=item.get("proficiency"))
                )
        # Fallback: plain string list
        for name in data.get("languages", []) or []:
            if isinstance(name, str) and not any(l.name == name for l in out):
                out.append(Language(name=name))
        return out
