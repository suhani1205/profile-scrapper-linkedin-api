"""Fixture provider.

Returns a realistic sample profile without any external call. Used for tests,
local development, and as a graceful demo fallback when live provider credits
are exhausted. Never claims to be live data — the public_identifier is marked.
"""
from __future__ import annotations

from app.providers.base import ProfileProvider
from app.schema import (
    Certification,
    DateInfo,
    Education,
    Experience,
    Language,
    Profile,
)


class FixtureProvider(ProfileProvider):
    async def fetch(self, profile_url: str) -> Profile:
        return Profile(
            public_identifier="sample-profile",
            profile_url=profile_url,
            full_name="Ada Lovelace",
            first_name="Ada",
            last_name="Lovelace",
            headline="Backend Engineer | Distributed Systems | JVM",
            summary="Sample profile served by the fixture provider. Set "
            "PROVIDER=linkedin with valid LI_AT and LI_JSESSIONID cookies for live data.",
            location="London, England, United Kingdom",
            country="United Kingdom",
            profile_picture_url="https://example.com/pic.jpg",
            follower_count=1200,
            connection_count=500,
            experiences=[
                Experience(
                    title="Senior Backend Engineer",
                    company="Analytical Engines Ltd",
                    location="London, UK",
                    description="Designed high-throughput event pipelines.",
                    starts_at=DateInfo(year=2021, month=3),
                    is_current=True,
                ),
                Experience(
                    title="Backend Engineer",
                    company="Difference Co",
                    starts_at=DateInfo(year=2018, month=6),
                    ends_at=DateInfo(year=2021, month=2),
                ),
            ],
            education=[
                Education(
                    school="University of London",
                    degree="B.Sc.",
                    field_of_study="Mathematics",
                    starts_at=DateInfo(year=2014),
                    ends_at=DateInfo(year=2018),
                )
            ],
            skills=["Java", "Spring Boot", "Kafka", "PostgreSQL", "AWS"],
            certifications=[
                Certification(
                    name="AWS Certified Solutions Architect",
                    authority="Amazon Web Services",
                    starts_at=DateInfo(year=2022, month=1),
                )
            ],
            languages=[
                Language(name="English", proficiency="Native or bilingual"),
                Language(name="French", proficiency="Professional working"),
            ],
        )
