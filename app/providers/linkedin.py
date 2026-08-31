"""LinkedIn Voyager-backed provider.

Reverse-engineers the unofficial LinkedIn Voyager API — the same JSON API the
LinkedIn web client uses — to fetch profile data directly, with no browser and
no third-party service.

How to get credentials
-----------------------
1. Log in to LinkedIn in your browser.
2. Open DevTools → Application → Cookies → linkedin.com.
3. Copy the value of the ``li_at`` cookie → LI_AT env var.
4. Copy the value of the ``JSESSIONID`` cookie → LI_JSESSIONID env var.
5. Optionally copy ``bcookie`` and ``bscookie`` → LI_BCOOKIE / LI_BSCOOKIE.

Fetch strategy (tried in order)
---------------------------------
1. **Voyager profileView** — one call, richest data, legacy endpoint.
2. **HTML fallback** — fetches the public profile page and extracts the
   voyager JSON payloads LinkedIn embeds in ``<code>`` tags, then falls back
   to the JSON-LD ``ProfilePage`` block for minimal public data.

Limitations
-----------
* LinkedIn may block datacenter IP ranges — residential proxies help.
* Session cookies expire; refresh them from a logged-in browser as needed.
* Profiles with "Private mode" return limited data.
"""
from __future__ import annotations

import asyncio
import json
import logging
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

logger = logging.getLogger(__name__)

_BASE = "https://www.linkedin.com/voyager/api"
_LI_BASE = "https://www.linkedin.com"

# LinkedIn's normalized-JSON content type
_ACCEPT = "application/vnd.linkedin.normalized+json+2.1"

# Type discriminators inside the `included` array
_T_PROFILE = "com.linkedin.voyager.identity.profile.Profile"
_T_POSITION = "com.linkedin.voyager.identity.profile.Position"
_T_EDUCATION = "com.linkedin.voyager.identity.profile.Education"
_T_SKILL = "com.linkedin.voyager.identity.profile.Skill"
_T_CERTIFICATION = "com.linkedin.voyager.identity.profile.Certification"
_T_LANGUAGE = "com.linkedin.voyager.identity.profile.Language"

# Dash (newer) type discriminators
_T_DASH_PROFILE = "com.linkedin.voyager.dash.identity.profile.Profile"
_T_DASH_POSITION = "com.linkedin.voyager.dash.identity.profile.Position"
_T_DASH_EDUCATION = "com.linkedin.voyager.dash.identity.profile.Education"
_T_DASH_SKILL = "com.linkedin.voyager.dash.identity.profile.Skill"


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


def _text_of(value: Any) -> Optional[str]:
    """Dash wraps display strings as {'text': '...'}; REST uses bare strings."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        return value.get("text") or None
    return None


class LinkedInProvider(ProfileProvider):
    """Fetches LinkedIn profiles via Voyager API with HTML fallback."""

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
        self._jsessionid = jsessionid.strip('"')
        self._bcookie = bcookie.strip('"') if bcookie else ""
        self._bscookie = bscookie.strip('"') if bscookie else ""
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def fetch(self, profile_url: str) -> Profile:
        public_id = _extract_public_id(profile_url)

        # Tier 1: Voyager profileView JSON API
        try:
            return await self._fetch_voyager(public_id, profile_url)
        except (UpstreamError, RateLimited) as exc:
            logger.warning("Voyager API failed for %s (%s), trying HTML fallback", public_id, exc)

        # Tier 2: HTML fallback — parse embedded JSON + JSON-LD from the page
        return await self._fetch_html(public_id, profile_url)

    # ------------------------------------------------------------------
    # Tier 1: Voyager JSON API
    # ------------------------------------------------------------------

    async def _fetch_voyager(self, public_id: str, profile_url: str) -> Profile:
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=False,
        ) as client:
            headers = self._headers(public_id)
            cookies = self._cookies()

            pv_resp, ni_resp, sk_resp = await asyncio.gather(
                client.get(
                    f"{_BASE}/identity/profiles/{public_id}/profileView",
                    headers=headers,
                    cookies=cookies,
                ),
                client.get(
                    f"{_BASE}/identity/profiles/{public_id}/networkInfo",
                    headers=headers,
                    cookies=cookies,
                ),
                client.get(
                    f"{_BASE}/identity/profiles/{public_id}/skills",
                    params={"count": "100"},
                    headers=headers,
                    cookies=cookies,
                ),
                return_exceptions=True,
            )

        pv_resp = self._check_response(pv_resp, profile_url, critical=True)
        ni_json = self._safe_json(ni_resp)
        sk_json = self._safe_json(sk_resp)

        return self._build_profile(pv_resp.json(), ni_json, sk_json, profile_url, public_id)

    # ------------------------------------------------------------------
    # Tier 2: HTML fallback
    # ------------------------------------------------------------------

    async def _fetch_html(self, public_id: str, profile_url: str) -> Profile:
        """Fetch the public profile HTML page and extract embedded JSON payloads."""
        url = f"{_LI_BASE}/in/{public_id}/"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
            ) as client:
                resp = await client.get(
                    url,
                    headers=self._html_headers(public_id),
                    cookies=self._cookies(),
                )
        except Exception as exc:
            raise UpstreamError(f"Network error fetching profile page: {exc}") from exc

        if resp.status_code == 404:
            raise ProfileNotFound(profile_url)
        if resp.status_code == 999 or resp.status_code == 429:
            raise RateLimited()
        if resp.status_code >= 400:
            raise UpstreamError(
                f"LinkedIn profile page returned HTTP {resp.status_code}. "
                "Session cookies may be expired or the IP is blocked.",
                status=resp.status_code,
            )

        html = resp.text
        profile = self._parse_html(html, public_id, profile_url)
        if profile is None:
            raise UpstreamError(
                "LinkedIn returned a profile page but no parseable data was found. "
                "The session may be hitting an auth wall."
            )
        return profile

    def _parse_html(self, html: str, public_id: str, profile_url: str) -> Optional[Profile]:
        """Try embedded voyager JSON first, then JSON-LD."""
        profile = self._from_embedded_json(html, public_id, profile_url)
        if profile is not None:
            logger.info("HTML fallback: extracted embedded JSON for %s", public_id)
            return profile

        profile = self._from_json_ld(html, public_id, profile_url)
        if profile is not None:
            logger.info("HTML fallback: extracted JSON-LD for %s", public_id)
            return profile

        return None

    def _from_embedded_json(self, html: str, public_id: str, profile_url: str) -> Optional[Profile]:
        """
        LinkedIn embeds voyager payloads in <code> elements on the page.
        Merge all their `included` arrays and parse as a normal profileView response.
        """
        try:
            from selectolax.parser import HTMLParser
        except ImportError:
            # Fallback regex approach if selectolax not installed
            return self._from_embedded_json_regex(html, public_id, profile_url)

        tree = HTMLParser(html)
        merged_included: list[dict] = []

        for node in tree.css("code"):
            text = node.text(strip=True)
            if not text or '"included"' not in text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            included = payload.get("included")
            if isinstance(included, list):
                merged_included.extend(e for e in included if isinstance(e, dict))

        if not merged_included:
            return None

        # Build a synthetic profileView-style response and parse it
        synthetic = {"data": {}, "included": merged_included}
        return self._build_profile_from_included(merged_included, profile_url, public_id)

    def _from_embedded_json_regex(self, html: str, public_id: str, profile_url: str) -> Optional[Profile]:
        """Regex fallback for extracting <code> tag JSON without selectolax."""
        merged_included: list[dict] = []
        for match in re.finditer(r"<code[^>]*>(.*?)</code>", html, re.DOTALL):
            text = match.group(1).strip()
            if '"included"' not in text:
                continue
            try:
                payload = json.loads(text)
                included = payload.get("included")
                if isinstance(included, list):
                    merged_included.extend(e for e in included if isinstance(e, dict))
            except json.JSONDecodeError:
                continue
        if not merged_included:
            return None
        return self._build_profile_from_included(merged_included, profile_url, public_id)

    def _from_json_ld(self, html: str, public_id: str, profile_url: str) -> Optional[Profile]:
        """
        Extract basic profile from JSON-LD (available on public/logged-out pages).
        Returns a minimal Profile — no experience/education detail, but better than nothing.
        """
        try:
            from selectolax.parser import HTMLParser
            tree = HTMLParser(html)
            scripts = tree.css('script[type="application/ld+json"]')
            blobs = [s.text(strip=True) for s in scripts]
        except ImportError:
            blobs = re.findall(
                r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                html, re.DOTALL
            )

        for blob_text in blobs:
            try:
                blob = json.loads(blob_text)
            except json.JSONDecodeError:
                continue

            person = self._find_person_in_jsonld(blob)
            if person is None:
                continue

            name = (person.get("name") or "").strip()
            given = (person.get("givenName") or "").strip()
            family = (person.get("familyName") or "").strip()
            if not given and name:
                parts = name.split(" ", 1)
                given = parts[0]
                family = parts[1] if len(parts) > 1 else ""

            address = person.get("address") or {}
            image = person.get("image") or {}
            pic_url = image.get("contentUrl") if isinstance(image, dict) else None

            # Extract experience from worksFor
            experiences = []
            for org in (person.get("worksFor") or []):
                if isinstance(org, dict):
                    experiences.append(Experience(
                        title=org.get("jobTitle"),
                        company=org.get("name"),
                        is_current=True,
                    ))

            # Extract alma mater from alumniOf
            education = []
            for school in (person.get("alumniOf") or []):
                if isinstance(school, dict):
                    education.append(Education(school=school.get("name")))

            return Profile(
                public_identifier=public_id,
                profile_url=profile_url,
                full_name=name or None,
                first_name=given or None,
                last_name=family or None,
                headline=person.get("jobTitle"),
                summary=person.get("description"),
                location=address.get("addressLocality"),
                country=address.get("addressCountry"),
                profile_picture_url=pic_url,
                experiences=experiences,
                education=education,
                skills=[],
                certifications=[],
                languages=[],
            )

        return None

    @staticmethod
    def _find_person_in_jsonld(blob: Any) -> Optional[dict]:
        """Find a Person node in a JSON-LD graph, which may be nested."""
        if isinstance(blob, dict):
            if blob.get("@type") == "Person":
                return blob
            # BreadcrumbList or ProfilePage may wrap a Person
            graph = blob.get("@graph") or []
            for item in graph:
                if isinstance(item, dict) and item.get("@type") == "Person":
                    return item
            # ProfilePage may have a mainEntity Person
            main = blob.get("mainEntity")
            if isinstance(main, dict) and main.get("@type") == "Person":
                return main
        if isinstance(blob, list):
            for item in blob:
                result = LinkedInProvider._find_person_in_jsonld(item)
                if result:
                    return result
        return None

    def _build_profile_from_included(
        self,
        included: list[dict],
        profile_url: str,
        public_id: str,
    ) -> Optional[Profile]:
        """Parse a merged included[] array — works for both profileView and HTML-embedded payloads."""
        by_type = _group_by_type(included)

        # Support both legacy and dash type discriminators
        profile_ent = self._find_profile_entity(by_type, public_id)
        if not profile_ent:
            return None

        mini = profile_ent.get("miniProfile", {}) or {}

        # Skills
        pv_skills = [s.get("name") for s in by_type.get(_T_SKILL, []) if s.get("name")]
        dash_skills = [
            _text_of(s.get("name")) for s in by_type.get(_T_DASH_SKILL, [])
            if _text_of(s.get("name"))
        ]
        seen: set[str] = set()
        skills: list[str] = []
        for name in pv_skills + dash_skills:
            if name and name not in seen:
                seen.add(name)
                skills.append(name)

        location = (
            profile_ent.get("geoLocationName")
            or profile_ent.get("locationName")
            or _text_of(profile_ent.get("geoLocation"))
        )

        pic: Optional[str] = None
        for candidate in (
            mini.get("picture"),
            profile_ent.get("profilePicture", {}).get("displayImageReference", {}).get("vectorImage"),
            profile_ent.get("profilePhoto", {}).get("displayImageReference", {}).get("vectorImage"),
        ):
            if candidate:
                pic = _pic_url(candidate)
                if pic:
                    break

        # Experience — try both legacy and dash types
        experiences = [
            self._parse_position(p)
            for p in by_type.get(_T_POSITION, []) + by_type.get(_T_DASH_POSITION, [])
        ]
        education = [
            self._parse_education(e)
            for e in by_type.get(_T_EDUCATION, []) + by_type.get(_T_DASH_EDUCATION, [])
        ]

        return Profile(
            public_identifier=public_id,
            profile_url=profile_url,
            full_name=self._full_name(profile_ent),
            first_name=profile_ent.get("firstName"),
            last_name=profile_ent.get("lastName"),
            headline=profile_ent.get("headline") or _text_of(profile_ent.get("headline")) or mini.get("occupation"),
            summary=profile_ent.get("summary"),
            location=location,
            country=profile_ent.get("geoCountryName"),
            profile_picture_url=pic,
            background_image_url=self._background_url(profile_ent),
            experiences=experiences,
            education=education,
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

    def _html_headers(self, public_id: str = "") -> dict[str, str]:
        """Headers for fetching the HTML profile page (not the JSON API)."""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "none",
            "Upgrade-Insecure-Requests": "1",
        }
        if public_id:
            headers["Referer"] = "https://www.linkedin.com/"
        return headers

    def _cookies(self) -> dict[str, str]:
        jsessionid_cookie = (
            f"ajax:{self._jsessionid}"
            if not self._jsessionid.startswith("ajax:")
            else self._jsessionid
        )
        cookies: dict[str, str] = {
            "li_at": self._li_at,
            "JSESSIONID": f'"{jsessionid_cookie}"',
            "lang": "v=2&lang=en-us",
        }
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

        # Detect forced logout: LinkedIn sets li_at=delete me when it kills the session
        set_cookie = resp.headers.get("set-cookie", "")
        if "li_at=delete me" in set_cookie:
            raise UpstreamError(
                "LinkedIn expired your session cookie. Copy fresh li_at and "
                "JSESSIONID values from a logged-in browser.",
                status=resp.status_code,
            )

        ct = resp.headers.get("content-type", "")
        if resp.status_code in (200, 201) and "json" not in ct and critical:
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
            raise RateLimited()
        if resp.status_code == 410:
            raise UpstreamError(
                "LinkedIn returned 410 — session may be expired or IP is blocked.",
                status=410,
            )
        # Redirect loop to same URL = session refused
        if resp.status_code in (301, 302, 307, 308):
            location = resp.headers.get("location", "")
            raise UpstreamError(
                f"LinkedIn redirected the API request (→ {location or 'unknown'}). "
                "Session is being rejected — try fresh cookies.",
                status=resp.status_code,
            )
        if resp.status_code >= 400:
            raise UpstreamError(
                f"LinkedIn returned HTTP {resp.status_code}: {resp.text[:200]}",
                status=resp.status_code,
            )
        return resp

    @staticmethod
    def _safe_json(resp: Any) -> dict:
        if isinstance(resp, Exception):
            return {}
        if resp.status_code != 200:
            return {}
        try:
            return resp.json()
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # profileView parsing (Tier 1)
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

        pv_skills = [s.get("name") for s in by_type.get(_T_SKILL, []) if s.get("name")]
        sk_skills = [
            s.get("name")
            for s in sk.get("included", [])
            if s.get("$type") == _T_SKILL and s.get("name")
        ]
        seen: set[str] = set()
        skills: list[str] = []
        for name in pv_skills + sk_skills:
            if name not in seen:
                seen.add(name)
                skills.append(name)

        ni_data = ni.get("data", ni)
        follower_count: Optional[int] = ni_data.get("followersCount") or ni_data.get("followingCount")
        connection_count: Optional[int] = ni_data.get("connectionsCount")
        for item in ni.get("included", []):
            if follower_count is None:
                follower_count = item.get("followersCount")
            if connection_count is None:
                connection_count = item.get("connectionsCount")

        location = (
            profile_ent.get("geoLocationName")
            or profile_ent.get("locationName")
        )

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
            experiences=[self._parse_position(p) for p in by_type.get(_T_POSITION, [])],
            education=[self._parse_education(e) for e in by_type.get(_T_EDUCATION, [])],
            skills=skills,
            certifications=[self._parse_certification(c) for c in by_type.get(_T_CERTIFICATION, [])],
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
        for type_key in (_T_PROFILE, _T_DASH_PROFILE):
            candidates = by_type.get(type_key, [])
            for p in candidates:
                if p.get("publicIdentifier") == public_id:
                    return p
            if candidates:
                return candidates[0]
        return {}

    @staticmethod
    def _full_name(ent: dict) -> Optional[str]:
        first = ent.get("firstName", "") or ""
        last = ent.get("lastName", "") or ""
        name = f"{first} {last}".strip()
        return name or None

    @staticmethod
    def _background_url(ent: dict) -> Optional[str]:
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
        company_name = pos.get("companyName") or mini_co.get("name") or _text_of(pos.get("companyName"))
        universal = mini_co.get("universalName")
        company_url = f"https://www.linkedin.com/company/{universal}/" if universal else None
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
