"""LinkedIn profile URL validation and normalization."""
from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

# Matches https://(www.)linkedin.com/in/<slug>[/...]
_LINKEDIN_HOST_RE = re.compile(r"^([a-z]{2,3}\.)?linkedin\.com$", re.IGNORECASE)
_SLUG_RE = re.compile(r"^/in/[^/]+/?$")


class InvalidProfileURL(ValueError):
    pass


def normalize_profile_url(raw: str) -> str:
    """Validate that `raw` is a LinkedIn /in/ profile URL and canonicalize it.

    Returns a clean https URL with query/fragment stripped and a trailing slash.
    Raises InvalidProfileURL otherwise.
    """
    if not raw or not isinstance(raw, str):
        raise InvalidProfileURL("A profile URL string is required.")

    candidate = raw.strip()
    if not candidate.startswith(("http://", "https://")):
        candidate = "https://" + candidate

    parsed = urlparse(candidate)

    if not _LINKEDIN_HOST_RE.match(parsed.netloc):
        raise InvalidProfileURL("URL host must be linkedin.com.")

    path = parsed.path.rstrip("/") + "/"
    if not _SLUG_RE.match(path):
        raise InvalidProfileURL(
            "URL must point to a profile, e.g. https://www.linkedin.com/in/<username>/"
        )

    clean = urlunparse(("https", "www.linkedin.com", path, "", "", ""))
    return clean
