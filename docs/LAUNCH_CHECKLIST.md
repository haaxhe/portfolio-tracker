# WealthBrief Launch Checklist

Domain: `getwealthbrief.com`

## 1. Supabase

Create a Supabase project and capture:

- Project URL: `https://<project-ref>.supabase.co`
- Publishable key
- Postgres connection string

Enable Google login:

- Supabase Dashboard -> Authentication -> Providers -> Google
- Add the Google client id and secret
- In Google Cloud OAuth, add the Supabase callback URL shown by Supabase
- Add authorized JavaScript origins:
  - `https://getwealthbrief.com`
  - `https://www.getwealthbrief.com`

## 2. Render

Create a Render Blueprint from `render.yaml`, or create a Web Service manually.

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```

Production environment:

```bash
ENVIRONMENT=production
APP_BASE_URL=https://getwealthbrief.com
AUTH_MODE=supabase
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_PUBLISHABLE_KEY=<publishable-key>
DATABASE_URL=<supabase-postgres-connection-string>
CORS_ORIGINS=https://getwealthbrief.com,https://www.getwealthbrief.com
ENABLE_BROKER_CONNECTORS=false
REFRESH_INTERVAL_MINUTES=0
```

Optional market data environment:

```bash
ALPACA_API_KEY=<key>
ALPACA_SECRET_KEY=<secret>
```

## 3. Cloudflare DNS

After Render gives you a target host, add:

- `CNAME getwealthbrief.com -> <render-target>`
- `CNAME www -> <render-target>`

Keep Cloudflare SSL/TLS in Full mode.

## 4. Smoke Tests

After deploy:

- Visit `https://getwealthbrief.com/api/public-config`
- Visit `https://getwealthbrief.com`
- Sign in with Google
- Add one manual position
- Refresh `/api/portfolio` while signed in
- Sign out and confirm protected API requests return `401`

## 5. Before Public Beta

- Privacy Policy
- Terms of Service
- Not financial advice disclaimer
- Support/contact email
- Account deletion path
- Data export path
- Backup/restore procedure for Supabase
