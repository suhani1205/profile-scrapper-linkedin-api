# LinkedIn Profile API

A hosted HTTPS API that accepts a LinkedIn profile URL and returns the profile's
public information as structured JSON — name, headline, location, about,
experience, education, skills, certifications, languages, and profile images.

The default provider (`PROVIDER=linkedin`) **directly reverse-engineers
LinkedIn's internal Voyager API** using your own LinkedIn session credentials,
with no browser and no third-party service.

---

## Quick start

```bash
git clone <your-repo-url>
cd linkedin-profile-api
cp .env.example .env          # fill in LI_AT and LI_JSESSIONID (see below)
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Try it without credentials using the built-in fixture provider:

```bash
PROVIDER=fixture uvicorn app.main:app --reload
curl "http://127.0.0.1:8000/profile?url=https://www.linkedin.com/in/johndoe/"
```

---

## Getting your LinkedIn session cookies

The LinkedIn provider authenticates using two cookies from your own logged-in
browser session:

1. Open **Chrome / Firefox / Safari** and log in to [linkedin.com](https://www.linkedin.com).
2. Open **DevTools** → **Application** (Chrome) or **Storage** (Firefox) →
   **Cookies** → `https://www.linkedin.com`.
3. Copy the value of the `li_at` cookie → paste into `LI_AT` in your `.env`.
4. Copy the value of the `JSESSIONID` cookie (e.g. `ajax:1234567890`) →
   paste into `LI_JSESSIONID` in your `.env`.  
   Do **not** include the surrounding quotes that DevTools sometimes shows.

The cookies are valid until you log out or LinkedIn rotates them (typically
weeks to months).

---

## Configuration

All configuration is via environment variables. **No secrets live in the repo.**

| Variable          | Required for        | Default    | Description                                                  |
|-------------------|---------------------|------------|--------------------------------------------------------------|
| `PROVIDER`        | —                   | `linkedin` | Data source: `linkedin`, `proxycurl`, or `fixture`.          |
| `LI_AT`           | `linkedin`          | —          | `li_at` session cookie from a logged-in LinkedIn browser.    |
| `LI_JSESSIONID`   | `linkedin`          | —          | `JSESSIONID` cookie value (e.g. `ajax:1234567890`).          |
| `PROXYCURL_API_KEY` | `proxycurl`       | —          | Licensed provider key from nubela.co/proxycurl.              |
| `API_KEY`         | —                   | —          | If set, callers must send `X-API-Key: <value>`.              |
| `REQUEST_TIMEOUT` | —                   | `30`       | Upstream request timeout, seconds.                           |

---

## API documentation

### `GET /health`

Liveness check.

```json
{ "status": "ok", "provider": "linkedin" }
```

### `GET /profile`

Returns structured profile data for a LinkedIn profile URL.

**Query parameters**

| Name  | Type   | Required | Description                                             |
|-------|--------|----------|---------------------------------------------------------|
| `url` | string | yes      | A `linkedin.com/in/<username>` profile URL.             |

**Headers**

| Header      | Required                         | Description                       |
|-------------|----------------------------------|-----------------------------------|
| `X-API-Key` | only if `API_KEY` is configured  | Your API key.                     |

**Example**

```bash
curl "https://<your-host>/profile?url=https://www.linkedin.com/in/johndoe/"
```

**200 response** — fields are `null` / `[]` when unavailable:

```json
{
  "public_identifier": "johndoe",
  "profile_url": "https://www.linkedin.com/in/johndoe/",
  "full_name": "John Doe",
  "first_name": "John",
  "last_name": "Doe",
  "headline": "Senior Backend Engineer",
  "summary": "Building reliable distributed systems.",
  "location": "Bengaluru, Karnataka, India",
  "country": "India",
  "profile_picture_url": "https://media.licdn.com/dms/image/...",
  "background_image_url": null,
  "follower_count": 3400,
  "connection_count": 500,
  "experiences": [
    {
      "title": "Senior Backend Engineer",
      "company": "Acme Corp",
      "company_linkedin_url": "https://www.linkedin.com/company/acme/",
      "location": "Bengaluru",
      "description": "…",
      "starts_at": { "year": 2021, "month": 3, "day": null },
      "ends_at": null,
      "is_current": true
    }
  ],
  "education": [
    {
      "school": "IIT Bombay",
      "degree": "B.Tech",
      "field_of_study": "Computer Science",
      "description": null,
      "starts_at": { "year": 2013, "month": null, "day": null },
      "ends_at":   { "year": 2017, "month": null, "day": null }
    }
  ],
  "skills": ["Java", "Spring Boot", "Kafka"],
  "certifications": [
    {
      "name": "AWS Solutions Architect",
      "authority": "Amazon Web Services",
      "url": null,
      "starts_at": { "year": 2022, "month": 1, "day": null },
      "ends_at": null
    }
  ],
  "languages": [
    { "name": "English", "proficiency": "Native or bilingual" }
  ]
}
```

**Error responses**

| Status | `code`           | Meaning                                           |
|--------|------------------|---------------------------------------------------|
| 400    | `bad_request`    | URL missing or not a LinkedIn profile URL.        |
| 401    | `unauthorized`   | `API_KEY` configured and key missing/wrong.       |
| 404    | `not_found`      | Profile does not exist or is not accessible.      |
| 429    | `rate_limited`   | Local or upstream rate limit hit.                 |
| 502    | `upstream_error` | Provider / network failure (includes expired cookies). |

Interactive docs (Swagger UI) are at `/docs`.

---

## Approach

### Reverse-engineering LinkedIn's Voyager API

LinkedIn's web client communicates with an internal JSON API called **Voyager**
(`linkedin.com/voyager/api/…`).  Every page load you see in the browser is
backed by these same endpoints.  The default provider calls three of them
directly, in parallel:

| Endpoint | What it returns |
|---|---|
| `GET /voyager/api/identity/profiles/{vanityName}/profileView` | Full profile: name, headline, summary, location, experience, education, certifications, languages, profile picture |
| `GET /voyager/api/identity/profiles/{vanityName}/networkInfo` | Follower count and connection count |
| `GET /voyager/api/identity/profiles/{vanityName}/skills?count=100` | Complete skills list (up to 100 entries) |

#### Authentication

LinkedIn's Voyager endpoints require two cookies from an active browser session:

* **`li_at`** — the primary session token.
* **`JSESSIONID`** — used as a CSRF token; the value (e.g. `ajax:1234567890`)
  is also sent in the `csrf-token` request header.

No username/password login flow is performed at request time.

#### Response format

Voyager returns data in a *normalized JSON* format
(`application/vnd.linkedin.normalized+json+2.1`).  The response has two keys:

```json
{
  "data": { … },
  "included": [ { "$type": "…", … }, … ]
}
```

`included` is a flat list of all entities referenced by `data`, each tagged
with a `$type` discriminator (`com.linkedin.voyager.identity.profile.Profile`,
`…Position`, `…Education`, etc.).  The provider groups these by type and maps
them into the API's own schema (`app/schema.py`), which is entirely independent
of the upstream format.

#### Provider abstraction

The data-source decision lives behind a single interface
(`app/providers/base.py`).  Swapping to a different source — the Proxycurl
licensed provider, or a future implementation — is one class and one env var;
the public API contract never changes.

```
        ┌──────────────┐   validate URL   ┌────────────────────┐
 client │  FastAPI     │ ───────────────▶ │  ProfileProvider   │  (interface)
 ─────▶ │  /profile    │                  └─────────┬──────────┘
        │  auth, RL    │                             │
        └──────────────┘          ┌──────────────────┼──────────────────┐
                                  ▼                  ▼                  ▼
                         LinkedInProvider   ProxycurlProvider   FixtureProvider
                         (Voyager API)      (licensed data)     (sample data)
                                  │
                         3 parallel requests
                         profileView / networkInfo / skills
                                  │
                         group included[] by $type
                         ──▶ Profile schema
```

---

## Running tests

```bash
PROVIDER=fixture pytest -v
```

---

## Deployment

The app is a standard ASGI service. Any of these work over HTTPS out of the box:

**Render / Railway / Fly.io** — connect the repo, set `PROVIDER=linkedin`,
`LI_AT`, and `LI_JSESSIONID` as environment variables; the platform terminates
TLS automatically. A `Procfile` and `Dockerfile` are included.

**Docker**

```bash
docker build -t linkedin-profile-api .
docker run -p 8000:8000 \
  -e PROVIDER=linkedin \
  -e LI_AT=your_li_at \
  -e LI_JSESSIONID=ajax:your_jsessionid \
  linkedin-profile-api
```

Set secrets in the platform's environment / secret manager — never commit them.

---

## Known limitations

* **Datacenter IP blocking.** LinkedIn aggressively challenges requests
  originating from cloud hosting providers.  If you see 999 (challenge) or
  non-JSON (HTML) responses, consider fronting the service through a residential
  proxy.

* **Session expiry.** `li_at` / `JSESSIONID` cookies expire or get invalidated
  by LinkedIn.  When this happens the API returns 502 with a message about
  credentials.  Refresh the cookies from your browser.

* **Terms of Service.** Automated, authenticated access to LinkedIn's internal
  endpoints likely conflicts with LinkedIn's User Agreement (section 8.2).  Run
  this service for personal or educational use; do not use it to build a
  commercial product at scale.

* **Private / restricted profiles.** Profiles set to "Private mode" or those
  outside your network may return partial data or 404.

* **Skills cap.** The skills endpoint is called with `count=100`.  LinkedIn
  users can have more skills than this, though 100 covers the vast majority.

* **Rate limiting is in-memory.** The included limiter is per-process and
  resets on restart.  A production deployment should use a shared store (Redis).

---

## Project layout

```
app/
  main.py              FastAPI app: routing, auth, rate limiting, error mapping
  schema.py            Pydantic response models (the public contract)
  validation.py        LinkedIn URL validation + normalization
  config.py            Env-based settings and provider factory
  providers/
    base.py            Provider interface + typed errors
    linkedin.py        Voyager reverse-engineering provider  ← default
    proxycurl.py       Licensed live provider + upstream→schema mapping
    fixture.py         Sample-data provider for tests / demos
tests/
  test_api.py          Endpoint, validation, and normalization tests
```

## License

MIT
