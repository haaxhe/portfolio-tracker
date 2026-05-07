# Web MVP Architecture

This repo is being moved from a local personal tracker toward a launchable web
MVP. The current goal is not full SaaS maturity yet; it is to create the right
boundaries so auth, billing, hosted Postgres, and broker aggregation can be added
without rewriting the product.

## Product Scope

The MVP should lead with manual and CSV-based taxable portfolio tracking:

- Holdings and cash balances
- CSV import
- Tax lots
- Closed positions and realized P&L
- Portfolio snapshots and manually entered history
- CSV export
- Mobile-friendly web dashboard

Broker sync and investment signals should stay secondary until the core tracker
is multi-user, secure, and trusted.

## Current Runtime

- FastAPI serves both the API and the single-file dashboard.
- SQLite remains the local development database.
- Set `DATABASE_URL` to use hosted Postgres in production.
- Every user-owned table now has `user_id`.
- API handlers resolve a request owner through `backend.auth.get_current_user`.
- Broker connectors are disabled by default with `ENABLE_BROKER_CONNECTORS=false`.

## Auth Modes

`AUTH_MODE=local`

- Default development mode.
- All requests are scoped to `DEFAULT_USER_ID`.
- Keeps the local dashboard working without login.

`AUTH_MODE=token`

- First hosted-MVP mode.
- Requires `Authorization: Bearer <API_TOKEN>`.
- Uses `X-User-Id` when supplied, otherwise falls back to `DEFAULT_USER_ID`.
- This is suitable behind a trusted auth gateway or temporary private beta, not
  a final consumer auth system.

`AUTH_MODE=supabase`

- Production Google-login mode for `getwealthbrief.com`.
- Requires `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`.
- Verifies the bearer token with Supabase Auth.
- Uses the verified Supabase user id as `user_id`.

## Production Direction

Before public launch:

- Use Supabase auth mode for public users.
- Use Supabase Postgres through `DATABASE_URL`.
- Add formal migrations once the schema stabilizes.
- Put all user-owned queries behind owner-scoped repositories or ORM models.
- Store CSV import files in object storage.
- Add Stripe billing and subscription gates.
- Add account deletion and data export flows.
- Add structured audit logs for imports, deletes, and broker sync changes.
- Keep broker passwords out of the product; use an aggregation provider.

## Data Boundaries

These tables are now owner-scoped:

- `positions`
- `tax_lots`
- `closed_positions`
- `snapshots`
- `portfolio_history`
- `signals`

Tests in `tests/test_db_tenancy.py` cover the current isolation behavior.

## Known Limitations

- SQLite is still the local development store.
- The frontend is still a single HTML/React file.
- Token auth is a bridge, not a production identity system.
- Supabase auth currently verifies via `/auth/v1/user` on each protected request;
  caching or local JWT verification can be added later if needed.
- Market data licensing is not solved.
- Signals need product/legal review before public positioning.
