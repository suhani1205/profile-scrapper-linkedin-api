"""Provider interface.

Any data source (Proxycurl, ScrapingDog, PDL, a fixture file, ...) implements
this one method. The rest of the app depends only on this interface, so the
choice of upstream is a single-line swap in config.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.schema import Profile


class ProfileNotFound(Exception):
    pass


class UpstreamError(Exception):
    """Raised for provider/network failures we can't recover from."""
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class RateLimited(Exception):
    pass


class ProfileProvider(ABC):
    @abstractmethod
    async def fetch(self, profile_url: str) -> Profile:
        """Fetch a profile and return it mapped into our schema."""
        raise NotImplementedError
