"""LinkedIn Voyager-backed provider.

Reverse-engineers the unofficial LinkedIn Voyager API — the same JSON API the
LinkedIn web client uses — to fetch profile data directly, with no browser and
no third-party service.

How to get credentials
-----------------------
1. Log in to LinkedIn in your browser.
2. Open DevTools → Application → Cookies → linkedin.com.
3. Copy the value of the ``li_at`` cookie → LI_AT env var.
4. Copy the value of the ``JSESSIONID`` cookie (including the surrounding
   quotes, e.g. ``"ajax:1234567890"``) → LI_JSESSIONID env var.

Endpoints used
--------------
* ``GET /voyager/api/identity/profiles/{vanityName}/profileView``
  Returns the full profile in LinkedIn's normalized-JSON format (data +
  included entities).  Covers name, headline, summary, location, experience,
  education, certifications, languages, and profile picture.

* ``GET /voyager/api/identity/profiles/{vanityName}/networkInfo``
  Returns follower and connection counts.  Fetched in parallel with profileView.

* ``GET /voyager/api/identity/profiles/{vanityName}/skills?count=100``
  Returns up to 100 skills.  Fetched in parallel; merged with any skills
  already present in profileView.

Limitations
-----------
* LinkedIn may block datacenter IP ranges and trigger checkpoint challenges.
  Residential proxies significantly improve reliability.
* Session cookies expire and can be invalidated by LinkedIn.
* Direct automated access to LinkedIn's internal endpoints likely violates
  LinkedIn's User Agreement (section 8.2).  See the README for context.
* Profiles with "Private mode" or restricted visibility return limited data.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Optional

import httpx

from app.providers.base import (
    ProfileNotFound,
    ProfileProvider,
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

_BASE = "https://www.linkedin.com/voyager/api"

# LinkedIn's normalized-JSON content type
_ACCEPT = "application/vnd.linkedin.normalized+json+2.1"

# Type discriminators inside the `included` array
_T_PROFILE = "com.linkedin.voyager.identity.profile.Profile"
_T_POSITION = "com.linkedin.voyager.identity.profile.Position"
_T_EDUCATION = "com.linkedin.voyager.identity.profile.Education"
_T_SKILL = "com.linkedin.voyager.identity.profile.Skill"
_T_CERTIFICATION = "com.linkedin.voyager.identity.profile.Certification"
_T_LANGUAGE = "com.linkedin.voyager.identity.profile.Language"


def _extract_public_id(profile_url: str) -> str:
    """Pull the vanity name out of a normalized linkedin.com/in/<id>/ URL."""
    m = re.search(r"linkedin\.com/in/([^/?#]+)", profile_url)
    if not m:
        raise ValueError(f"Cannot extract public ID from URL: {profile_url}")
    return m.group(1).rstrip("/")


def _parse_date(raw: Optional[dict]) -> Optional[DateInfo]:
    if not raw:
        return None
    return DateInfo(year=raw.get("year"), month=raw.get("month"), day=raw.get("day"))


def _pic_url(picture: dict) -> Optional[str]:
    """Reconstruct a profile-picture URL from LinkedIn's VectorImage blob."""
    root = picture.get("rootUrl", "")
    artifacts = picture.get("artifacts", [])
    if not root or not artifacts:
        return None
    # Pick the largest artifact (sorted by width; fall back to last entry).
    best = max(artifacts, key=lambda a: a.get("width", 0), default=artifacts[-1])
    segment = best.get("fileIdentifyingUrlPathSegment", "")
    return (root + segment) if segment else None


def _group_by_type(included: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for item in included:
        t = item.get("$type")
        if t:
            out[t].append(item)
    return out


class LinkedInProvider(ProfileProvider):
    """Fetches LinkedIn profiles directly via the Voyager internal API."""

    def __init__(
        self,
        li_at: str,
        jsessionid: str,
        timeout: float = 30.0,
        bcookie: str = "",
        bscookie: str = "",
    ) -> None:
        if not li_at:
            raise ValueError("LI_AT cookie is required for the LinkedIn provider.")
        if not jsessionid:
            raise ValueError("LI_JSESSIONID cookie is required for the LinkedIn provider.")
        self._li_at = li_at
        # Normalize: store without surrounding quotes; add them when building cookies.
        self._jsessionid = jsessionid.strip('"')
        self._bcookie = bcookie.strip('"') if bcookie else ""
        self._bscookie = bscookie.strip('"') if bscookie else ""
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def fetch(self, profile_url: str) -> Profile:
        public_id = _extract_public_id(profile_url)

        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=False,
        ) as client:
            headers = self._headers(public_id)
            cookies = self._cookies()
            # Fire profileView, networkInfo, and skills in parallel.
            pv_req = client.get(
                f"{_BASE}/identity/profiles/{public_id}/profileView",
                headers=headers,
                cookies=cookies,
            )
            ni_req = client.get(
                f"{_BASE}/identity/profiles/{public_id}/networkInfo",
                headers=headers,
                cookies=cookies,
            )
            sk_req = client.get(
                f"{_BASE}/identity/profiles/{public_id}/skills",
                params={"count": "100"},
                headers=headers,
                cookies=cookies,
            )

            import asyncio
            pv_resp, ni_resp, sk_resp = await asyncio.gather(
                pv_req, ni_req, sk_req, return_exceptions=True
            )

        pv_resp = self._check_response(pv_resp, profile_url, critical=True)
        ni_json = self._safe_json(ni_resp)
        sk_json = self._safe_json(sk_resp)

        return self._build_profile(pv_resp.json(), ni_json, sk_json, profile_url, public_id)

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _headers(self, public_id: str = "") -> dict[str, str]:
        csrf = self._jsessionid if self._jsessionid.startswith("ajax:") else f"ajax:{self._jsessionid}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": _ACCEPT,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "X-Li-Lang": "en_US",
            "X-RestLi-Protocol-Version": "2.0.0",
            # csrf-token must equal the JSESSIONID value (without quotes).
            "csrf-token": csrf,
            "X-Li-Track": (
                '{"clientVersion":"1.13.9217","osName":"web",'
                '"timezoneOffset":0,"timezone":"America/New_York",'
                '"deviceFormFactor":"DESKTOP","mpName":"voyager-web",'
                '"displayDensity":2,"displayWidth":1920,"displayHeight":1080}'
            ),
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }
        if public_id:
            headers["Referer"] = f"https://www.linkedin.com/in/{public_id}/"
        return headers

    def _cookies(self) -> dict[str, str]:
        jsessionid_cookie = (
            f"ajax:{self._jsessionid}"
            if not self._jsessionid.startswith("ajax:")
            else self._jsessionid
        )
        cookies: dict[str, str] = {
            "li_at": self._li_at,
            # LinkedIn expects the JSESSIONID value wrapped in double-quotes.
            "JSESSIONID": f'"{jsessionid_cookie}"',
            "lang": "v=2&lang=en-us",
        }
        # bcookie / bscookie are browser-fingerprint cookies LinkedIn sets on first
        # visit. Without them some data-centre IPs get 410 responses. Copy them
        # from DevTools → Application → Cookies → linkedin.com.
        if self._bcookie:
            cookies["bcookie"] = f'"{self._bcookie}"'
        if self._bscookie:
            cookies["bscookie"] = f'"{self._bscookie}"'
        return cookies

    def _check_response(
        self,
        resp: Any,
        profile_url: str,
        *,
        critical: bool = True,
    ) -> httpx.Response:
        if isinstance(resp, Exception):
            raise UpstreamError(f"Network error: {resp}") from resp

        ct = resp.headers.get("content-type", "")
        if resp.status_code in (200, 201) and "json" not in ct and critical:
            # LinkedIn returned HTML — almost always a login/checkpoint redirect.
            raise UpstreamError(
                "LinkedIn returned non-JSON (got HTML). Your session cookies may "
                "have expired or LinkedIn is presenting a CAPTCHA challenge.",
                status=resp.status_code,
            )

        if resp.status_code == 404:
            raise ProfileNotFound(profile_url)
        if resp.status_code in (401, 403):
            raise UpstreamError(
                "LinkedIn rejected the session credentials. "
                "Refresh your li_at / JSESSIONID cookies.",
                status=resp.status_code,
            )
        if resp.status_code == 429 or resp.status_code == 999:
            # 999 is LinkedIn's anti-bot challenge status code.
            raise RateLimited()
        if resp.status_code == 410:
            raise UpstreamError(
                "LinkedIn returned 410. Possible causes: (1) session cookies "
                "expired — refresh li_at / JSESSIONID from your browser; "
                "(2) missing bcookie/bscookie — copy them from DevTools → "
                "Application → Cookies → linkedin.com and add to .env; "
                "(3) datacenter IP block — LinkedIn blocks cloud-provider IPs.",
                status=410,
            )
        if resp.status_code >= 400:
            raise UpstreamError(
                f"LinkedIn returned HTTP {resp.status_code}: {resp.text[:200]}",
                status=resp.status_code,
            )
        return resp

    @staticmethod
    def _safe_json(resp: Any) -> dict:
        """Return parsed JSON or {} if the request failed / non-JSON."""
        if isinstance(resp, Exception):
            return {}
        if resp.status_code != 200:
            return {}
        try:
            return resp.json()
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _build_profile(
        self,
        pv: dict,
        ni: dict,
        sk: dict,
        profile_url: str,
        public_id: str,
    ) -> Profile:
        included = pv.get("included", [])
        by_type = _group_by_type(included)

        profile_ent = self._find_profile_entity(by_type, public_id)
        mini = profile_ent.get("miniProfile", {}) or {}

        # Skills: merge profileView skills with the dedicated skills endpoint.
        pv_skills = [
            s.get("name")
            for s in by_type.get(_T_SKILL, [])
            if s.get("name")
        ]
        sk_skills = [
            s.get("name")
            for s in sk.get("included", [])
            if s.get("$type") == _T_SKILL and s.get("name")
        ]
        # Deduplicate while preserving order.
        seen: set[str] = set()
        skills: list[str] = []
        for name in pv_skills + sk_skills:
            if name not in seen:
                seen.add(name)
                skills.append(name)

        # Follower / connection counts from networkInfo endpoint.
        ni_data = ni.get("data", ni)  # some versions nest under "data"
        follower_count: Optional[int] = ni_data.get("followersCount") or ni_data.get("followingCount")
        connection_count: Optional[int] = ni_data.get("connectionsCount")
        # networkInfo may also be in "included"
        for item in ni.get("included", []):
            if follower_count is None:
                follower_count = item.get("followersCount")
            if connection_count is None:
                connection_count = item.get("connectionsCount")

        # Location: prefer the finer-grained geo name.
        location = (
            profile_ent.get("geoLocationName")
            or profile_ent.get("locationName")
        )

        # Profile picture
        pic: Optional[str] = None
        for candidate in (
            mini.get("picture"),
            profile_ent.get("profilePicture", {}).get("displayImageReference", {}).get("vectorImage"),
        ):
            if candidate:
                pic = _pic_url(candidate)
                if pic:
                    break

        return Profile(
            public_identifier=public_id,
            profile_url=profile_url,
            full_name=self._full_name(profile_ent),
            first_name=profile_ent.get("firstName"),
            last_name=profile_ent.get("lastName"),
            headline=profile_ent.get("headline") or mini.get("occupation"),
            summary=profile_ent.get("summary"),
            location=location,
            country=profile_ent.get("geoCountryName"),
            profile_picture_url=pic,
            background_image_url=self._background_url(profile_ent),
            follower_count=follower_count,
            connection_count=connection_count,
            experiences=[
                self._parse_position(p)
                for p in by_type.get(_T_POSITION, [])
            ],
            education=[
                self._parse_education(e)
                for e in by_type.get(_T_EDUCATION, [])
            ],
            skills=skills,
            certifications=[
                self._parse_certification(c)
                for c in by_type.get(_T_CERTIFICATION, [])
            ],
            languages=[
                self._parse_language(l)
                for l in by_type.get(_T_LANGUAGE, [])
                if l.get("name")
            ],
        )

    # ------------------------------------------------------------------
    # Entity helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_profile_entity(by_type: dict, public_id: str) -> dict:
        candidates = by_type.get(_T_PROFILE, [])
        # Prefer the one whose publicIdentifier matches.
        for p in candidates:
            if p.get("publicIdentifier") == public_id:
                return p
        return candidates[0] if candidates else {}

    @staticmethod
    def _full_name(ent: dict) -> Optional[str]:
        first = ent.get("firstName", "") or ""
        last = ent.get("lastName", "") or ""
        name = f"{first} {last}".strip()
        return name or None

    @staticmethod
    def _background_url(ent: dict) -> Optional[str]:
        # Background cover image lives under backgroundCoverImage / displayImage.
        bg = ent.get("backgroundCoverImage") or ent.get("backgroundImage", {})
        if not bg:
            return None
        vi = bg.get("vectorImage") or bg.get("displayImageReference", {}).get("vectorImage") or bg
        return _pic_url(vi) if isinstance(vi, dict) else None

    @staticmethod
    def _parse_position(pos: dict) -> Experience:
        period = pos.get("timePeriod", {}) or {}
        start = period.get("startDate")
        end = period.get("endDate")

        company = pos.get("company", {}) or {}
        mini_co = company.get("miniCompany", {}) or {}
        company_name = pos.get("companyName") or mini_co.get("name")
        universal = mini_co.get("universalName")
        company_url = (
            f"https://www.linkedin.com/company/{universal}/" if universal else None
        )

        return Experience(
            title=pos.get("title"),
            company=company_name,
            company_linkedin_url=company_url,
            location=pos.get("locationName"),
            description=pos.get("description"),
            starts_at=_parse_date(start),
            ends_at=_parse_date(end),
            is_current=end is None and start is not None,
        )

    @staticmethod
    def _parse_education(edu: dict) -> Education:
        period = edu.get("timePeriod", {}) or {}
        return Education(
            school=edu.get("schoolName"),
            degree=edu.get("degreeName"),
            field_of_study=edu.get("fieldOfStudy"),
            description=edu.get("description"),
            starts_at=_parse_date(period.get("startDate")),
            ends_at=_parse_date(period.get("endDate")),
        )

    @staticmethod
    def _parse_certification(cert: dict) -> Certification:
        period = cert.get("timePeriod", {}) or {}
        return Certification(
            name=cert.get("name"),
            authority=cert.get("authority"),
            url=cert.get("url"),
            starts_at=_parse_date(period.get("startDate")),
            ends_at=_parse_date(period.get("endDate")),
        )

    @staticmethod
    def _parse_language(lang: dict) -> Language:
        return Language(name=lang["name"], proficiency=lang.get("proficiency"))
