# Portfolio Tracker

Unified portfolio tracker for Robinhood & E*Trade accounts.

## Architecture

```
portfolio-tracker/
├── backend/
│   ├── main.py              # FastAPI app entry point
│   ├── config.py            # Settings & env vars
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
│   └── dashboard.html        # Single-file React dashboard
├── requirements.txt
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

# 3. Copy env config
cp .env.example .env
# Edit .env with your credentials

# 4. Run (starts backend + serves dashboard)
python -m backend.main

# 5. Open http://localhost:8000
```

## Broker Setup

### CSV Import (Day 1)
Export CSV from Robinhood / E*Trade and POST to:
```
POST http://localhost:8000/api/import/csv
```

### Robinhood (robin_stocks)
- Unofficial API — set RH_USERNAME & RH_PASSWORD in .env
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
