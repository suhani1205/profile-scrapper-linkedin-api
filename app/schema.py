"""Response schema for the LinkedIn Profile API.

The schema is intentionally source-agnostic: nothing here is tied to a
particular data provider. The provider client (see providers/) maps raw
upstream data into these models, so swapping providers never changes the
public contract.
"""
from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field, HttpUrl


class DateInfo(BaseModel):
    year: Optional[int] = None
    month: Optional[int] = None
    day: Optional[int] = None


class Experience(BaseModel):
    title: Optional[str] = None
    company: Optional[str] = None
    company_linkedin_url: Optional[HttpUrl] = None
    location: Optional[str] = None
    description: Optional[str] = None
    starts_at: Optional[DateInfo] = None
    ends_at: Optional[DateInfo] = None
    is_current: bool = False


class Education(BaseModel):
    school: Optional[str] = None
    degree: Optional[str] = None
    field_of_study: Optional[str] = None
    description: Optional[str] = None
    starts_at: Optional[DateInfo] = None
    ends_at: Optional[DateInfo] = None


class Certification(BaseModel):
    name: Optional[str] = None
    authority: Optional[str] = None
    url: Optional[HttpUrl] = None
    starts_at: Optional[DateInfo] = None
    ends_at: Optional[DateInfo] = None


class Language(BaseModel):
    name: str
    proficiency: Optional[str] = None


class Profile(BaseModel):
    """The public contract returned by GET /profile."""
    public_identifier: Optional[str] = None
    profile_url: HttpUrl
    full_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    headline: Optional[str] = None
    summary: Optional[str] = Field(None, description="The 'About' section text.")
    location: Optional[str] = None
    country: Optional[str] = None
    profile_picture_url: Optional[HttpUrl] = None
    background_image_url: Optional[HttpUrl] = None
    follower_count: Optional[int] = None
    connection_count: Optional[int] = None

    experiences: List[Experience] = []
    education: List[Education] = []
    skills: List[str] = []
    certifications: List[Certification] = []
    languages: List[Language] = []

    class Config:
        json_schema_extra = {
            "example": {
                "public_identifier": "johndoe",
                "profile_url": "https://www.linkedin.com/in/johndoe/",
                "full_name": "John Doe",
                "headline": "Senior Backend Engineer",
                "summary": "Building reliable distributed systems.",
                "location": "Bengaluru, Karnataka, India",
                "skills": ["Java", "Spring Boot", "Kafka"],
            }
        }


class ErrorResponse(BaseModel):
    detail: str
    code: str
