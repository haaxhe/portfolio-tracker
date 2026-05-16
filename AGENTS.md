# Portfolio Tracker Agent Workflow

Use this workflow for future code changes in this repo.

## Environment Modes

Treat the app as three distinct modes:

- `local`: fast personal development.
  - `ENVIRONMENT=local`
  - `AUTH_MODE=local`
  - SQLite by default.
  - Use this for dashboard UI, portfolio logic, CSV import, tax views, and testing through `one-person-bank`.

- `staging`: local live-like validation before promoting.
  - `ENVIRONMENT=staging`
  - `AUTH_MODE=supabase`
  - Requires a Postgres `DATABASE_URL`.
  - Allows localhost URLs.
  - Must use a separate staging Supabase project/database, never production.

- `production`: live `getwealthbrief.com`.
  - `ENVIRONMENT=production`
  - `AUTH_MODE=supabase`
  - Requires Postgres, Supabase auth, HTTPS app URL, and HTTPS CORS origins.

## Standard Commands

Run local portfolio-tracker for `one-person-bank` embedding:

```bash
npm run app:local
```

Then open `http://localhost:8787`; the Portfolio tab embeds portfolio-tracker from `http://localhost:8000`.

Run local live-like staging:

```bash
cp .env.staging.example .env.staging
npm run app:staging-local
```

Run release checks before merge/deploy:

```bash
npm run check:release
```

## Change Rules

- Prefer testing in `local` first, then `staging` for auth/database/user-isolation behavior.
- Do not silently rely on `frontend/dashboard.html`; build `frontend/dist` with `npm run build`.
- Keep `ALLOW_LEGACY_DASHBOARD=false` unless intentionally opening the old fallback.
- Do not commit `.env`, `.env.staging`, database files, build output, logs, or secrets.
- Keep production Render/Supabase values separate from local and staging values.
- If a change touches auth, tenancy, database persistence, analytics, account deletion, export-all, or deployment config, run `npm run check:release`.

## Commit And Push Discipline

- Check `git status --short` before editing and before committing.
- Do not revert unrelated user changes.
- Commit only after tests/build pass or after clearly documenting why a check could not run.
- Push only when the user explicitly asks for it.
