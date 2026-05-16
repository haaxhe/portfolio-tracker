# WealthBrief

Tax-aware portfolio tracker moving from a local prototype toward a web MVP.

The current launch path is manual/CSV-first: holdings, cash, tax lots, closed
positions, realized/unrealized P&L, snapshots, and export. Broker connectors are
kept available for local experimentation, but are disabled by default for the web
MVP because public broker sync should be handled through approved aggregation
providers.

## Architecture

```
portfolio-tracker/
├── backend/
│   ├── main.py              # FastAPI app entry point
│   ├── config.py            # Settings & env vars
│   ├── auth.py              # MVP request-owner boundary
│   ├── brokers/
│   │   ├── base.py          # Abstract broker interface
│   │   ├── robinhood.py     # Robinhood via robin_stocks
│   │   ├── etrade.py        # E*Trade official API
│   │   └── csv_import.py    # CSV manual import fallback
│   ├── models.py            # Unified data models
│   ├── portfolio.py         # Portfolio aggregation logic
│   ├── scheduler.py         # Periodic refresh (APScheduler)
│   └── db.py                # SQLite persistence
├── frontend/
│   ├── index.html            # Vite app shell
│   ├── src/                  # React dashboard source
│   └── dashboard.html        # Local legacy fallback
├── package.json
├── requirements.txt
├── docs/
│   └── MVP_ARCHITECTURE.md
├── .env.example
└── README.md
```

## Quick Start

```bash
# 1. Create venv
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows

# 2. Install deps
pip install -r requirements.txt
npm install

# 3. Copy env config
cp .env.example .env
# For local MVP mode, broker credentials are optional.

# 4. Build frontend assets
npm run build

# 5. Run (starts backend + serves dashboard)
npm run app:local

# 6. Open http://localhost:8000
```

When using the `one-person-bank` wrapper, keep portfolio-tracker running on
`http://localhost:8000`; the wrapper's Portfolio tab embeds that local app.

## MVP Auth Modes

Local development defaults to:

```bash
AUTH_MODE=local
DEFAULT_USER_ID=local-user
ENABLE_BROKER_CONNECTORS=false
```

For local or private single-user testing, token mode can protect the default
user with one bearer token:

```bash
AUTH_MODE=token
API_TOKEN=replace-with-a-long-random-secret
CORS_ORIGINS=https://your-app.example
```

Requests in token mode must send:

```http
Authorization: Bearer <API_TOKEN>
```

Browser-supplied `X-User-Id` is ignored. If token mode is behind a trusted auth
proxy that strips client identity headers and injects its own identity, set
`TRUST_PROXY_USER_HEADER=true` and send:

```http
X-Authenticated-User-Id: <stable-user-id>
```

Token mode is blocked by production startup validation.

Production Google login uses Supabase:

```bash
AUTH_MODE=supabase
APP_BASE_URL=https://getwealthbrief.com
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_PUBLISHABLE_KEY=<publishable-key>
DATABASE_URL=<supabase-postgres-connection-string>
CORS_ORIGINS=https://getwealthbrief.com,https://www.getwealthbrief.com
```

The bundled dashboard attaches Supabase bearer tokens only to same-origin API
requests. For private token-mode testing, a local browser token can still be set
manually:

```js
localStorage.setItem('portfolio_tracker_api_token', '<API_TOKEN>');
```

## Local, Staging, Production Workflow

Use local personal mode for fast dashboard work:

```bash
npm run app:local
```

This sets `ENVIRONMENT=local`, `AUTH_MODE=local`, uses SQLite by default, and
shows a `LOCAL` badge in the dashboard header. It is the right mode for quick UI,
portfolio logic, CSV import, tax view, and one-person-bank embedding work.

Use local live-like mode before promoting changes:

```bash
cp .env.staging.example .env.staging
# Fill .env.staging with a staging Supabase URL, publishable key, and Postgres URL.
npm run app:staging-local
```

This sets `ENVIRONMENT=staging` and `AUTH_MODE=supabase`, requires a Postgres
`DATABASE_URL`, and still allows localhost URLs. Use a separate staging Supabase
project/database from production.

Before merging or deploying:

```bash
npm run check:release
```

The backend no longer silently serves the legacy `frontend/dashboard.html`
fallback. If `frontend/dist` is missing, run `npm run build`. Set
`ALLOW_LEGACY_DASHBOARD=true` only when intentionally opening the old fallback.

## Broker Setup

Broker connectors are disabled unless `ENABLE_BROKER_CONNECTORS=true`.
For public launch, prefer a broker aggregation provider over direct broker
password collection.

### CSV Import (Day 1)
Export CSV from Robinhood / E*Trade and POST to:
```
POST http://localhost:8000/api/import/csv
```

### Robinhood (robin_stocks)
- Unofficial API; not recommended for a public product.
- Set RH_USERNAME & RH_PASSWORD in .env only for local experimentation.
- Requires 2FA setup (TOTP) — see robin_stocks docs

### E*Trade (Official API)
- Apply for API key at https://developer.etrade.com
- Set ETRADE_CONSUMER_KEY & ETRADE_CONSUMER_SECRET in .env
- OAuth 1.0a — browser-based auth flow on first run

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/portfolio` | Unified portfolio view |
| GET | `/api/portfolio/history` | Historical snapshots |
| GET | `/api/positions/{broker}` | Positions by broker |
| POST | `/api/import/csv` | Import CSV file |
| POST | `/api/refresh` | Force refresh all brokers |
| GET | `/api/export/csv` | Export unified CSV |
| GET | `/` | Dashboard UI |

## YouTube Market Monitor

The low-cost YouTube monitor is deterministic by default: it checks configured
channels, extracts available captions, scores market-related snippets with
keywords, and stores compact results in SQLite. It does not call an LLM.
If YouTube exposes videos but not captions to the backend request, the monitor
falls back to title-only matching and marks the row's `transcript_status`.

Optional OpenAI summarization runs only after a video passes that cheap filter.
Set `OPENAI_API_KEY` and optionally `OPENAI_MODEL` to enable it for manual scans
or scheduled runs.

Configure channels in:

```text
config/youtube_sources.json
```

Run a manual scan:

```bash
python -m backend.youtube_monitor --config config/youtube_sources.json
```

Run a manual scan with LLM summaries:

```bash
OPENAI_API_KEY=sk-... python -m backend.youtube_monitor --config config/youtube_sources.json --summarize
```

Or trigger it from the API:

```http
POST /api/youtube-monitor/scan
POST /api/youtube-monitor/scan?summarize=true
GET  /api/youtube-monitor/mentions
```

Daily scheduling is disabled unless explicitly enabled:

```bash
YOUTUBE_MONITOR_ENABLED=true
YOUTUBE_MONITOR_INTERVAL_HOURS=24
YOUTUBE_MONITOR_CONFIG_PATH=config/youtube_sources.json
YOUTUBE_MONITOR_LLM_ENABLED=false
YOUTUBE_MONITOR_SUMMARIZE_LIMIT=3
OPENAI_MODEL=gpt-5.2
```

## Web MVP Plan

See [docs/MVP_ARCHITECTURE.md](docs/MVP_ARCHITECTURE.md) for the current
architecture boundary, known limitations, and production direction.

See [docs/LAUNCH_CHECKLIST.md](docs/LAUNCH_CHECKLIST.md) for the
`getwealthbrief.com` Supabase/Render/Cloudflare setup steps.

## Tests

```bash
python -m unittest discover -s tests
```
