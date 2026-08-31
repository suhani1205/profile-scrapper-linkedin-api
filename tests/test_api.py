import os
os.environ["PROVIDER"] = "fixture"

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.validation import normalize_profile_url, InvalidProfileURL

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_profile_happy_path():
    r = client.get("/profile", params={"url": "https://www.linkedin.com/in/johndoe/"})
    assert r.status_code == 200
    body = r.json()
    assert body["full_name"] == "Ada Lovelace"
    assert "Java" in body["skills"]
    assert len(body["experiences"]) >= 1


def test_profile_rejects_non_linkedin():
    r = client.get("/profile", params={"url": "https://example.com/in/foo/"})
    assert r.status_code == 400


def test_profile_rejects_company_url():
    r = client.get("/profile", params={"url": "https://www.linkedin.com/company/acme/"})
    assert r.status_code == 400


@pytest.mark.parametrize("raw,expected", [
    ("linkedin.com/in/johndoe", "https://www.linkedin.com/in/johndoe/"),
    ("https://in.linkedin.com/in/jane/", "https://www.linkedin.com/in/jane/"),
    ("https://www.linkedin.com/in/bob/?trk=xyz", "https://www.linkedin.com/in/bob/"),
])
def test_normalize(raw, expected):
    assert normalize_profile_url(raw) == expected


def test_normalize_rejects_bad():
    with pytest.raises(InvalidProfileURL):
        normalize_profile_url("https://twitter.com/in/x/")
