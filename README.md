# Profile Scrapper — LinkedIn API

A hosted HTTPS API that accepts a LinkedIn profile URL and returns structured JSON.
Layered with two Claude Opus 4.6 endpoints for career analysis and candidate-to-job
fit scoring.

**Live demo:** https://profile-scrapper-linkedin-api.onrender.com

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness check |
| `GET` | `/profile` | Structured profile JSON |
| `GET` | `/analyze` | Career insights via Claude Opus 4.6 |
| `GET` | `/match` | Job-fit scoring via Claude Opus 4.6 |

Interactive docs (Swagger UI): `/docs`

---

## Quick start

```bash
git clone https://github.com/suhani1205/profile-scrapper-linkedin-api
cd profile-scrapper-linkedin-api
cp .env.example .env      # fill in LI_AT, LI_JSESSIONID, ANTHROPIC_API_KEY
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Test without credentials using the built-in fixture provider:

```bash
PROVIDER=fixture uvicorn app.main:app --reload
curl "http://127.0.0.1:8000/profile?url=https://www.linkedin.com/in/satyanadella/"
```

---

## Getting LinkedIn session cookies

1. Log in to LinkedIn in Chrome/Firefox.
2. Open **DevTools → Application → Cookies → `https://www.linkedin.com`**.
3. Copy:

| Cookie | Env var |
|--------|---------|
| `li_at` | `LI_AT` |
| `JSESSIONID` | `LI_JSESSIONID` (e.g. `ajax:1234567890`) |
| `bcookie` *(optional)* | `LI_BCOOKIE` |
| `bscookie` *(optional)* | `LI_BSCOOKIE` |

`bcookie` and `bscookie` are browser fingerprint cookies that reduce 410 errors,
especially when calling from cloud IPs.

---

## Configuration

| Variable | Required for | Default | Description |
|---|---|---|---|
| `PROVIDER` | — | `linkedin` | `linkedin` or `fixture` |
| `LI_AT` | `linkedin` | — | Session cookie from a logged-in browser |
| `LI_JSESSIONID` | `linkedin` | — | JSESSIONID cookie (e.g. `ajax:1234567890`) |
| `LI_BCOOKIE` | — | — | Browser fingerprint cookie (optional) |
| `LI_BSCOOKIE` | — | — | Browser security cookie (optional) |
| `ANTHROPIC_API_KEY` | `/analyze`, `/match` | — | Key from console.anthropic.com |
| `API_KEY` | — | — | If set, callers must send `X-API-Key: <value>` |
| `REQUEST_TIMEOUT` | — | `30` | Upstream timeout in seconds |

---

## API reference

### `GET /health`

```json
{ "status": "ok", "provider": "linkedin" }
```

---

### `GET /profile?url=<linkedin-url>`

Returns structured profile data.

```bash
curl "https://profile-scrapper-linkedin-api.onrender.com/profile?url=https://www.linkedin.com/in/satyanadella/"
```

**200 response:**

```json
{
  "public_identifier": "satyanadella",
  "profile_url": "https://www.linkedin.com/in/satyanadella/",
  "full_name": "Satya Nadella",
  "first_name": "Satya",
  "last_name": "Nadella",
  "headline": "Chairman and CEO at Microsoft",
  "summary": "...",
  "location": "Redmond, Washington, United States",
  "country": "United States",
  "profile_picture_url": "https://media.licdn.com/dms/image/...",
  "background_image_url": null,
  "follower_count": 10000000,
  "connection_count": 500,
  "experiences": [
    {
      "title": "Chairman and CEO",
      "company": "Microsoft",
      "company_linkedin_url": "https://www.linkedin.com/company/microsoft/",
      "location": "Redmond, WA",
      "description": "...",
      "starts_at": { "year": 2014, "month": 2, "day": null },
      "ends_at": null,
      "is_current": true
    }
  ],
  "education": [
    {
      "school": "University of Chicago",
      "degree": "MBA",
      "field_of_study": "Business Administration",
      "starts_at": { "year": 1994, "month": null, "day": null },
      "ends_at": { "year": 1997, "month": null, "day": null }
    }
  ],
  "skills": ["Cloud Computing", "Enterprise Software", "Leadership"],
  "certifications": [],
  "languages": []
}
```

---

### `GET /analyze?url=<linkedin-url>`

Passes the profile to **Claude Opus 4.6** (adaptive thinking, medium effort) and
returns structured career insights.

```bash
curl "https://profile-scrapper-linkedin-api.onrender.com/analyze?url=https://www.linkedin.com/in/satyanadella/"
```

**200 response:**

```json
{
  "summary": "Satya Nadella is a transformational technology executive...",
  "career_trajectory": "Engineering → executive leadership at Microsoft over 25+ years...",
  "top_skills": ["Cloud strategy", "Enterprise sales", "Product vision"],
  "industry": "Enterprise Technology",
  "seniority_level": "C-Suite",
  "years_of_experience": 30,
  "notable_achievements": [
    "Led Microsoft's pivot to cloud, growing Azure into a $100B+ business",
    "Tripled Microsoft's market cap to $3T during tenure as CEO"
  ],
  "outreach_hook": "Your work redefining Microsoft's culture around growth mindset..."
}
```

---

### `GET /match?profile_url=<linkedin-url>&job=<jd-text-or-url>`

Scores a candidate against a job description using **Claude Opus 4.6** (adaptive
thinking, high effort). `job` can be raw JD text or a URL to the job posting.

```bash
curl "https://profile-scrapper-linkedin-api.onrender.com/match?\
profile_url=https://www.linkedin.com/in/satyanadella/\
&job=We+are+looking+for+a+VP+of+Engineering..."
```

**200 response:**

```json
{
  "fit_score": 91,
  "verdict": "Strong Match",
  "recommendation": "Hire",
  "strengths": [
    "25+ years scaling engineering orgs at Microsoft",
    "Deep cloud infrastructure expertise matching the JD's core requirement"
  ],
  "skill_gaps": ["No public Rust experience mentioned"],
  "experience_alignment": "Direct experience leading 60,000+ person engineering org far exceeds the JD's expectation of 500-person team leadership.",
  "standout_factor": "Only candidate in consideration who has shipped a $100B+ cloud product.",
  "interview_angles": [
    "How would you apply Microsoft's growth mindset culture here?",
    "Walk us through a platform migration decision you made under pressure."
  ],
  "tailored_pitch": "Satya, your work rebuilding Microsoft's engineering culture around empathy and growth mindset aligns directly with what we're trying to do at scale..."
}
```

**Error responses:**

| Status | `code` | Meaning |
|--------|--------|---------|
| 400 | `bad_request` | Missing or invalid URL |
| 401 | `unauthorized` | `API_KEY` set and key missing/wrong |
| 404 | `not_found` | Profile not found |
| 429 | `rate_limited` | Rate limit hit (20 req/min per client) |
| 502 | `upstream_error` | LinkedIn or Anthropic error |

---

## How it works

### Reverse-engineering LinkedIn's Voyager API

LinkedIn's web client talks to an internal JSON API at `linkedin.com/voyager/api/…`.
Every profile page you see in the browser is backed by these same endpoints. This
project calls them directly — no browser, no headless Chrome, no third-party service.

Three things make an unauthenticated HTTP client look like the real web app:

| Requirement | How it's satisfied |
|---|---|
| Session | `li_at` cookie from a logged-in browser |
| CSRF | `csrf-token` header = `JSESSIONID` cookie value |
| Protocol | `X-RestLi-Protocol-Version: 2.0.0` + `X-Li-Track` client metadata |

`Accept: application/vnd.linkedin.normalized+json+2.1` makes Voyager return a flat
`included[]` array of entities keyed by `$type` discriminator — far easier to parse
than a deeply nested graph.

### Two-tier fetch strategy

Rather than fail when LinkedIn's JSON API is blocked, the provider degrades gracefully:

```
Tier 1 — Voyager JSON API (richest data)
  GET /voyager/api/identity/profiles/{id}/profileView   ┐
  GET /voyager/api/identity/profiles/{id}/networkInfo   ├ parallel
  GET /voyager/api/identity/profiles/{id}/skills        ┘
         │
         │ blocked? (302 redirect / 410 / 999)
         ▼
Tier 2 — HTML fallback
  GET linkedin.com/in/{id}/
         │
         ├─▶ extract voyager payloads from <code> tags  (selectolax)
         └─▶ extract JSON-LD ProfilePage block          (fallback)
```

LinkedIn embeds the same voyager payloads it fetches via XHR directly into the
server-rendered HTML inside `<code>` elements. The HTML fallback extracts and merges
their `included[]` arrays, then runs the same parser as Tier 1 — so the output
schema is identical regardless of which tier succeeded.

### Claude AI layer

`/analyze` and `/match` pass the structured `Profile` object to Claude Opus 4.6
with adaptive thinking enabled. Prompt caching (via `cache_control: ephemeral` on
the system prompt) keeps latency low on repeated calls to the same endpoint.

```
/analyze  →  Claude Opus 4.6, thinking=adaptive, effort=medium
/match    →  Claude Opus 4.6, thinking=adaptive, effort=high
              (job description fetched + HTML-stripped if a URL is passed)
```

### Provider abstraction

The data source lives behind a single `ProfileProvider` interface. Swapping
providers is one env var; the public API contract never changes.

```
        ┌─────────────────┐
 client │  FastAPI        │
 ─────▶ │  /profile       │──▶ normalize URL
        │  /analyze       │──▶ rate limit (20 req/min, in-memory)
        │  /match         │──▶ optional API key auth
        └────────┬────────┘
                 │
        ┌────────▼────────┐
        │ ProfileProvider │  (interface)
        └────────┬────────┘
                 │
        ┌────────┴──────────────┐
        ▼                       ▼
 LinkedInProvider         FixtureProvider
 ┌─ Tier 1: Voyager API   (sample data, no
 └─ Tier 2: HTML fallback  credentials needed)
```

---

## Known limitations

**Datacenter IP blocking.** LinkedIn returns HTTP 999 for requests from cloud
provider IP ranges. The HTML fallback mitigates this for residential IPs; for
datacenter deployments a residential proxy is the reliable fix.

**Session expiry.** `li_at` / `JSESSIONID` cookies expire or get invalidated by
LinkedIn. Refresh them from a logged-in browser when the API returns 502.

**Terms of Service.** Automated authenticated access to LinkedIn's Voyager API
likely conflicts with LinkedIn's User Agreement (section 8.2). Run this for
personal or educational use only.

---

## Project layout

```
app/
  main.py           FastAPI app — routing, auth, rate limiting, error mapping
  schema.py         Pydantic response models (the public contract)
  validation.py     LinkedIn URL validation and normalization
  config.py         Env-based settings and provider factory
  analyzer.py       Claude Opus 4.6 career analysis
  matcher.py        Claude Opus 4.6 job-fit scoring
  providers/
    base.py         Provider interface + typed errors
    linkedin.py     Two-tier Voyager + HTML fallback provider  ← default
    fixture.py      Sample-data provider for tests and demos
```

## License

MIT
