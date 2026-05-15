import React, { useState, useEffect, useCallback, useRef } from 'react';
import { createRoot } from 'react-dom/client';
import { createClient } from '@supabase/supabase-js';
import './styles.css';

const API = '';
const API_AUTH_TOKEN_KEY = 'portfolio_tracker_api_token';
const ANALYTICS_SESSION_KEY = 'wealthbrief_analytics_session_id';
const AUTO_REFRESH_INTERVAL_MS = 5 * 60 * 1000;
const SUPPORT_EMAIL = 'support@getwealthbrief.com';
const originalFetch = window.fetch.bind(window);
let apiAuthToken = null;

function sameOriginApiRequest(input) {
  const url = typeof input === 'string' ? input : input?.url;
  if (typeof url !== 'string') return false;
  const parsed = new URL(url, window.location.origin);
  return parsed.origin === window.location.origin && parsed.pathname.startsWith('/api/');
}

window.fetch = (input, init = {}) => {
  const token = apiAuthToken || localStorage.getItem(API_AUTH_TOKEN_KEY);
  if (!token || !sameOriginApiRequest(input)) return originalFetch(input, init);

  const headers = new Headers(init.headers || {});
  headers.set('Authorization', `Bearer ${token}`);
  return originalFetch(input, { ...init, headers });
};

function makeAnalyticsSessionId() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  return `wb_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 12)}`;
}

function getAnalyticsSessionId() {
  try {
    let sessionId = localStorage.getItem(ANALYTICS_SESSION_KEY);
    if (!sessionId) {
      sessionId = makeAnalyticsSessionId();
      localStorage.setItem(ANALYTICS_SESSION_KEY, sessionId);
    }
    return sessionId;
  } catch {
    if (!window.__wealthbriefAnalyticsSessionId) {
      window.__wealthbriefAnalyticsSessionId = makeAnalyticsSessionId();
    }
    return window.__wealthbriefAnalyticsSessionId;
  }
}

function trackAnalyticsEvent(eventName, metadata = {}) {
  try {
    const payload = {
      event_name: eventName,
      session_id: getAnalyticsSessionId(),
      path: `${window.location.pathname}${window.location.search}`,
      referrer: document.referrer || '',
      metadata,
    };
    fetch(`${API}/api/analytics/events`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      keepalive: true,
    }).catch(() => {});
  } catch {
    // Analytics must never block the product experience.
  }
}

function AuthGate() {
  const [publicView, setPublicView] = React.useState('landing');
  const trackedSessionRef = React.useRef(false);
  const [state, setState] = React.useState({
    loading: true,
    config: null,
    client: null,
    session: null,
    error: null,
  });

  React.useEffect(() => {
    let unsubscribe = null;

    async function boot() {
      try {
        const res = await originalFetch('/api/public-config');
        const config = await res.json();
        if (config.auth_mode !== 'supabase') {
          setState({ loading: false, config, client: null, session: null, error: null });
          return;
        }
        if (!config.supabase_url || !config.supabase_publishable_key) {
          setState({ loading: false, config, client: null, session: null, error: 'Login is not configured.' });
          return;
        }

        const client = createClient(
          config.supabase_url,
          config.supabase_publishable_key
        );
        const { data } = await client.auth.getSession();
        syncSession(data.session);
        const sub = client.auth.onAuthStateChange((_event, session) => {
          syncSession(session);
          setState(prev => ({ ...prev, session }));
        });
        unsubscribe = sub.data.subscription.unsubscribe;
        setState({ loading: false, config, client, session: data.session, error: null });
      } catch (e) {
        setState({ loading: false, config: null, client: null, session: null, error: 'Unable to load app config.' });
      }
    }

    boot();
    return () => { if (unsubscribe) unsubscribe(); };
  }, []);

  const syncSession = (session) => {
    if (session?.access_token) {
      apiAuthToken = session.access_token;
      if (!trackedSessionRef.current) {
        trackedSessionRef.current = true;
        trackAnalyticsEvent('auth_session_active', {
          provider: session.user?.app_metadata?.provider || 'unknown',
        });
      }
    } else {
      apiAuthToken = null;
      localStorage.removeItem(API_AUTH_TOKEN_KEY);
      trackedSessionRef.current = false;
    }
  };

  const signIn = async () => {
    if (!state.client) return;
    trackAnalyticsEvent('signup_google_click');
    await state.client.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: window.location.origin },
    });
  };

  const sendMagicLink = async (email) => {
    if (!state.client || !email) return { error: 'Email sign-in is not configured.' };
    const { error } = await state.client.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: window.location.origin },
    });
    return { error: error?.message || null };
  };

  const openSignIn = (source) => {
    trackAnalyticsEvent('signup_entry_click', { source });
    setPublicView('signin');
  };

  const openDemo = (source) => {
    trackAnalyticsEvent('demo_launch_click', { source });
    setPublicView('demo');
  };

  const signOut = async () => {
    if (state.client) await state.client.auth.signOut();
    trackAnalyticsEvent('signout_click');
    syncSession(null);
    setState(prev => ({ ...prev, session: null }));
  };

  if (state.loading) {
    return <div className="app-container"><div className="empty-state"><h3>Loading WealthBrief</h3></div></div>;
  }
  if (state.error) {
    return <div className="app-container"><div className="empty-state"><h3>{state.error}</h3></div></div>;
  }
  if (publicView === 'demo') {
    const canOpenPortfolio = state.session || state.config?.auth_mode !== 'supabase';
    return (
      <App
        authUser={state.session?.user || (state.config?.auth_mode !== 'supabase' ? { id: 'local' } : null)}
        demoMode
        demoData={DEMO_DATA}
        onExitDemo={() => setPublicView('landing')}
        onSignIn={canOpenPortfolio ? () => setPublicView('landing') : () => openSignIn('demo_use_my_portfolio')}
      />
    );
  }
  if (['privacy', 'terms', 'security', 'support'].includes(publicView)) {
    return (
      <TrustPage
        page={publicView}
        onBack={() => setPublicView(state.config?.auth_mode === 'supabase' && !state.session ? 'landing' : 'app')}
        onSelectPage={setPublicView}
        onSignIn={() => openSignIn(`trust_${publicView}`)}
        isAuthed={!!state.session || state.config?.auth_mode !== 'supabase'}
      />
    );
  }
  if (publicView === 'signin') {
    return (
      <SignInPage
        onBack={() => setPublicView('landing')}
        onContinueGoogle={signIn}
        onSendMagicLink={sendMagicLink}
        onViewDemo={() => openDemo('signup_page')}
        onSelectTrustPage={setPublicView}
      />
    );
  }
  if (state.config?.auth_mode === 'supabase' && !state.session) {
    return (
      <LandingPage
        onSignIn={openSignIn}
        onViewDemo={openDemo}
        onSelectTrustPage={setPublicView}
      />
    );
  }
  return (
    <App
      authUser={state.session?.user || null}
      onSignOut={state.client ? signOut : null}
      onViewDemo={() => openDemo('dashboard_empty_state')}
      onSelectTrustPage={setPublicView}
    />
  );
}

function formatMoney(n) {
  if (n == null || isNaN(n)) return '$0.00';
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD' });
}
function formatPct(n) {
  if (n == null || isNaN(n)) return '0.00%';
  return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
}
function formatPriceTimestamp(isoValue) {
  if (!isoValue) return null;
  const d = new Date(isoValue);
  if (isNaN(d.getTime())) return null;
  const now = new Date();
  const diffMs = now - d;
  const diffMins = Math.round(diffMs / 60000);
  const diffHrs = Math.round(diffMs / 3600000);
  const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const dateStr = d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  const isToday = d.toDateString() === now.toDateString();
  const age = diffMins < 2 ? 'just now'
    : diffMins < 60 ? `${diffMins}m ago`
    : diffHrs < 24 ? `${diffHrs}h ago`
    : `${Math.round(diffMs / 86400000)}d ago`;
  return `${isToday ? timeStr : dateStr + ' ' + timeStr} (${age})`;
}

const BROKER_OPTIONS = [
  { value: 'csv', label: 'Generic CSV' },
  { value: 'robinhood', label: 'Robinhood' },
  { value: 'etrade', label: 'E*Trade' },
  { value: 'schwab', label: 'Schwab' },
  { value: 'fidelity', label: 'Fidelity' },
  { value: 'interactive_brokers', label: 'Interactive Brokers' },
];

const MANUAL_BROKER_OPTIONS = [
  { value: 'csv', label: 'Manual / Other' },
  ...BROKER_OPTIONS.filter(b => b.value !== 'csv'),
];

const CSV_TEMPLATES = {
  csv: {
    filename: 'wealthbrief-generic-template.csv',
    rows: [
      ['Symbol', 'Name', 'Quantity', 'Average Cost', 'Current Price', 'Type'],
      ['AAPL', 'Apple Inc.', '10', '150.00', '178.35', 'stock'],
    ],
  },
  robinhood: {
    filename: 'wealthbrief-robinhood-template.csv',
    rows: [
      ['Instrument', 'Description', 'Quantity', 'Average Price', 'Market Price', 'Type'],
      ['NVDA', 'NVIDIA Corp.', '5', '512.20', '925.35', 'stock'],
    ],
  },
  etrade: {
    filename: 'wealthbrief-etrade-template.csv',
    rows: [
      ['Symbol', 'Description', 'Quantity', 'Avg Cost', 'Last Price', 'Security Type'],
      ['MSFT', 'Microsoft Corp.', '8', '319.80', '431.40', 'stock'],
    ],
  },
  schwab: {
    filename: 'wealthbrief-schwab-template.csv',
    rows: [
      ['Symbol', 'Description', 'Quantity', 'Cost Basis Per Share', 'Market Price', 'Security Type'],
      ['VTI', 'Vanguard Total Stock Market ETF', '12', '211.60', '276.10', 'etf'],
    ],
  },
  fidelity: {
    filename: 'wealthbrief-fidelity-template.csv',
    rows: [
      ['Symbol', 'Description', 'Quantity', 'Average Cost', 'Last Price', 'Type'],
      ['AAPL', 'Apple Inc.', '10', '205.10', '178.35', 'stock'],
    ],
  },
  interactive_brokers: {
    filename: 'wealthbrief-interactive-brokers-template.csv',
    rows: [
      ['Symbol', 'Description', 'Quantity', 'Average Price', 'Market Price', 'Asset Type'],
      ['TSLA', 'Tesla Inc.', '6', '192.40', '248.75', 'stock'],
    ],
  },
};

function brokerLabel(value) {
  return BROKER_OPTIONS.find(b => b.value === value)?.label || value;
}

function csvEscape(value) {
  const text = String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function downloadCsvTemplate(broker) {
  const template = CSV_TEMPLATES[broker] || CSV_TEMPLATES.csv;
  const csv = template.rows.map(row => row.map(csvEscape).join(',')).join('\n') + '\n';
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = template.filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

const TRUST_CONTENT = {
  privacy: {
    title: 'Privacy Policy',
    kicker: 'Trust basics',
    updated: 'May 12, 2026',
    sections: [
      {
        heading: 'What WealthBrief stores',
        body: 'WealthBrief stores the portfolio records you add or import, including positions, tax lots, closed trades, portfolio history entries, and app-generated analytics. Authentication is handled by the configured login provider.',
      },
      {
        heading: 'How data is used',
        body: 'Your data is used to operate the dashboard, calculate portfolio and tax-lot views, generate exports, and maintain account security. WealthBrief is for tracking and organization only, not financial, tax, or investment advice.',
      },
      {
        heading: 'Exports and deletion',
        body: 'Signed-in users can export their WealthBrief app data from the dashboard. They can also delete stored WealthBrief app data from the Account & Data panel.',
      },
      {
        heading: 'Support',
        body: `Questions or deletion requests can be sent to ${SUPPORT_EMAIL}.`,
      },
    ],
  },
  terms: {
    title: 'Terms of Use',
    kicker: 'Use terms',
    updated: 'May 12, 2026',
    sections: [
      {
        heading: 'Use of WealthBrief',
        body: 'WealthBrief is provided as a portfolio organization tool. You are responsible for checking imported data, broker exports, tax lots, prices, and any decisions made from the dashboard.',
      },
      {
        heading: 'No advice',
        body: 'WealthBrief does not provide financial, tax, legal, or investment advice. Always consult qualified professionals for tax and investment decisions.',
      },
      {
        heading: 'Availability and data sources',
        body: 'Market data and integrations may be delayed, incomplete, unavailable, or changed by upstream providers. WealthBrief may change or discontinue features as the product evolves.',
      },
      {
        heading: 'Account data',
        body: 'You can export or delete WealthBrief app data from the dashboard. Deleting app data does not necessarily remove an identity held by an external authentication provider.',
      },
    ],
  },
  security: {
    title: 'Security',
    kicker: 'Security posture',
    updated: 'May 12, 2026',
    sections: [
      {
        heading: 'Authentication',
        body: 'Production login is designed around Supabase authentication. API requests are scoped to the authenticated user and protected by bearer-token checks.',
      },
      {
        heading: 'Transport and browser protections',
        body: 'Production should be served over HTTPS. The backend applies security headers, including content security policy, frame restrictions, and related browser protections.',
      },
      {
        heading: 'Data isolation',
        body: 'Portfolio records, tax lots, closed trades, history, and signals are stored with a user identifier and loaded through user-scoped queries.',
      },
      {
        heading: 'Responsible reporting',
        body: `Please report suspected security issues to ${SUPPORT_EMAIL}. Include steps to reproduce and avoid sharing sensitive account data in email.`,
      },
    ],
  },
  support: {
    title: 'Support',
    kicker: 'Contact',
    updated: 'May 12, 2026',
    sections: [
      {
        heading: 'Support email',
        body: `Contact ${SUPPORT_EMAIL} for product support, privacy questions, data export issues, or account deletion requests.`,
      },
      {
        heading: 'Account deletion',
        body: 'Signed-in users can delete WealthBrief app data from the dashboard Account & Data panel. If you need help with authentication-provider identity deletion, email support.',
      },
    ],
  },
};

function TrustLinks({ onSelectPage }) {
  return (
    <span className="trust-links">
      <button type="button" onClick={() => onSelectPage('privacy')}>Privacy</button>
      <button type="button" onClick={() => onSelectPage('terms')}>Terms</button>
      <button type="button" onClick={() => onSelectPage('security')}>Security</button>
      <button type="button" onClick={() => onSelectPage('support')}>Support</button>
    </span>
  );
}

function TrustPage({ page, onBack, onSelectPage, onSignIn, isAuthed }) {
  const content = TRUST_CONTENT[page] || TRUST_CONTENT.privacy;
  return (
    <main className="trust-page">
      <nav className="landing-nav">
        <button type="button" className="landing-brand" onClick={onBack}><span>▸</span> WealthBrief</button>
        <div className="landing-nav-actions">
          <button className="btn" onClick={onBack}>{isAuthed ? 'Back to dashboard' : 'Back to site'}</button>
          {!isAuthed && <button className="btn btn-primary" onClick={onSignIn}>Sign in</button>}
        </div>
      </nav>
      <section className="trust-document">
        <div className="landing-kicker">{content.kicker}</div>
        <h1>{content.title}</h1>
        <p className="trust-updated">Last updated {content.updated}</p>
        <div className="trust-section-list">
          {content.sections.map(section => (
            <section className="trust-section" key={section.heading}>
              <h2>{section.heading}</h2>
              <p>{section.body}</p>
            </section>
          ))}
        </div>
        <div className="trust-document-footer">
          <TrustLinks onSelectPage={onSelectPage} />
          <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>
        </div>
      </section>
    </main>
  );
}

function SignInPage({ onBack, onContinueGoogle, onSendMagicLink, onViewDemo, onSelectTrustPage }) {
  const [email, setEmail] = React.useState('');
  const [status, setStatus] = React.useState(null);
  const [sending, setSending] = React.useState(false);

  React.useEffect(() => {
    trackAnalyticsEvent('signup_page_view');
  }, []);

  const submitMagicLink = async (e) => {
    e.preventDefault();
    const trimmedEmail = email.trim();
    if (!trimmedEmail) return;

    setSending(true);
    setStatus(null);
    trackAnalyticsEvent('signup_magic_link_submit');
    try {
      const result = await onSendMagicLink(trimmedEmail);
      if (result?.error) {
        trackAnalyticsEvent('signup_magic_link_failure', { reason: 'provider_error' });
        setStatus({ type: 'error', message: result.error });
      } else {
        trackAnalyticsEvent('signup_magic_link_success');
        setStatus({ type: 'success', message: 'Check your email for a secure sign-in link.' });
      }
    } catch {
      trackAnalyticsEvent('signup_magic_link_failure', { reason: 'network_error' });
      setStatus({ type: 'error', message: 'Unable to send a sign-in link right now.' });
    } finally {
      setSending(false);
    }
  };

  return (
    <main className="signin-page">
      <nav className="landing-nav">
        <button type="button" className="landing-brand" onClick={onBack}><span>▸</span> WealthBrief</button>
        <div className="landing-nav-actions">
          <button className="btn" onClick={onViewDemo}>Try demo</button>
          <button className="btn" onClick={onBack}>Back to site</button>
        </div>
      </nav>

      <section className="signin-shell">
        <div className="signin-copy">
          <div className="landing-kicker">Start free</div>
          <h1>Build your tax-aware portfolio view in minutes.</h1>
          <p>
            Explore the sample portfolio first, then import CSV records or add positions manually when you are ready to track your real account.
          </p>
          <div className="signin-proof-list">
            <span>No card required</span>
            <span>Demo before signup</span>
            <span>Export or delete your data</span>
          </div>
        </div>

        <div className="signin-card" aria-label="Create your WealthBrief account">
          <h2>Create your WealthBrief account</h2>
          <p>Use Google or get a secure email sign-in link. You can open the demo without creating an account.</p>

          <button className="btn btn-primary signin-google" type="button" onClick={onContinueGoogle}>
            Continue with Google
          </button>

          <div className="signin-divider"><span>or</span></div>

          <form className="signin-email-form" onSubmit={submitMagicLink}>
            <label className="form-label" htmlFor="signin-email">Email</label>
            <input
              id="signin-email"
              className="form-input"
              type="email"
              placeholder="you@example.com"
              value={email}
              onChange={e => setEmail(e.target.value)}
              required
            />
            <button className="btn" type="submit" disabled={sending}>
              {sending ? 'Sending...' : 'Email me a magic link'}
            </button>
          </form>

          {status && <div className={`signin-status ${status.type}`}>{status.message}</div>}

          <button className="signin-demo-link" type="button" onClick={onViewDemo}>
            Explore the sample portfolio without signing up
          </button>

          <p className="signin-legal">
            By continuing, you agree to{' '}
            <button type="button" onClick={() => onSelectTrustPage('terms')}>Terms</button>
            {' '}and{' '}
            <button type="button" onClick={() => onSelectTrustPage('privacy')}>Privacy Policy</button>.
          </p>
        </div>
      </section>
    </main>
  );
}

function isoDaysAgo(days) {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

function makeDemoPosition(input) {
  const market_value = input.quantity * input.current_price;
  const costBasis = input.quantity * input.average_cost;
  const unrealized_gain = market_value - costBasis;
  return {
    id: input.id,
    symbol: input.symbol,
    name: input.name,
    broker: input.broker,
    asset_type: input.asset_type || 'stock',
    quantity: input.quantity,
    average_cost: input.average_cost,
    current_price: input.current_price,
    market_value,
    unrealized_gain,
    unrealized_gain_pct: costBasis > 0 ? (unrealized_gain / costBasis) * 100 : 0,
    updated_at: new Date().toISOString(),
  };
}

function makeDemoClosedPosition(input) {
  const realized_gain = (input.close_price - input.average_cost) * input.quantity;
  const costBasis = input.average_cost * input.quantity;
  return {
    ...input,
    realized_gain,
    realized_gain_pct: costBasis > 0 ? (realized_gain / costBasis) * 100 : 0,
  };
}

function makeDemoHistory(currentPrice, trendPct, wavePct = 0.018) {
  const dates = [];
  const closes = [];
  const points = 42;
  const startingPrice = currentPrice / (1 + trendPct);
  for (let i = points - 1; i >= 0; i -= 1) {
    const progress = (points - 1 - i) / (points - 1);
    const wave = Math.sin(progress * Math.PI * 4.5) * currentPrice * wavePct;
    const drift = startingPrice + (currentPrice - startingPrice) * progress;
    dates.push(isoDaysAgo(i));
    closes.push(Number((drift + wave).toFixed(2)));
  }
  closes[closes.length - 1] = currentPrice;
  return { dates, closes };
}

function buildDemoPortfolio(positions) {
  const total_value = positions.reduce((s, p) => s + p.market_value, 0);
  const total_cost = positions.reduce((s, p) => s + p.quantity * p.average_cost, 0);
  const total_gain = total_value - total_cost;
  const broker_breakdown = positions.reduce((acc, p) => {
    acc[p.broker] = (acc[p.broker] || 0) + p.market_value;
    return acc;
  }, {});
  return {
    total_value,
    total_cost,
    total_gain,
    total_gain_pct: total_cost > 0 ? (total_gain / total_cost) * 100 : 0,
    positions,
    broker_breakdown,
    last_refresh: new Date().toISOString(),
  };
}

function groupTaxLots(lots) {
  return lots.reduce((acc, lot) => {
    const key = `${lot.symbol}-${lot.broker}`;
    if (!acc[key]) acc[key] = [];
    acc[key].push(lot);
    return acc;
  }, {});
}

const DEMO_DATA = (() => {
  const positions = [
    makeDemoPosition({ id: 1, symbol: 'NVDA', name: 'NVIDIA Corp.', broker: 'robinhood', quantity: 42, average_cost: 512.2, current_price: 925.35 }),
    makeDemoPosition({ id: 2, symbol: 'MSFT', name: 'Microsoft Corp.', broker: 'etrade', quantity: 58, average_cost: 319.8, current_price: 431.4 }),
    makeDemoPosition({ id: 3, symbol: 'VTI', name: 'Vanguard Total Stock Market ETF', broker: 'etrade', quantity: 120, average_cost: 211.6, current_price: 276.1, asset_type: 'etf' }),
    makeDemoPosition({ id: 4, symbol: 'AAPL', name: 'Apple Inc.', broker: 'csv', quantity: 70, average_cost: 205.1, current_price: 178.35 }),
    makeDemoPosition({ id: 5, symbol: 'TSLA', name: 'Tesla Inc.', broker: 'robinhood', quantity: 32, average_cost: 192.4, current_price: 248.75 }),
    makeDemoPosition({ id: 6, symbol: 'CASH', name: 'Cash Balance', broker: 'etrade', quantity: 1, average_cost: 18500, current_price: 18500, asset_type: 'cash' }),
  ];

  const taxLots = groupTaxLots([
    { id: 101, symbol: 'NVDA', broker: 'robinhood', quantity: 24, cost_basis: 420.1, acquired_at: '2024-06-14', holding_period: 'long' },
    { id: 102, symbol: 'NVDA', broker: 'robinhood', quantity: 18, cost_basis: 635.0, acquired_at: '2025-08-22', holding_period: 'short' },
    { id: 201, symbol: 'MSFT', broker: 'etrade', quantity: 58, cost_basis: 319.8, acquired_at: '2023-11-17', holding_period: 'long' },
    { id: 301, symbol: 'VTI', broker: 'etrade', quantity: 120, cost_basis: 211.6, acquired_at: '2022-03-09', holding_period: 'long' },
    { id: 401, symbol: 'AAPL', broker: 'csv', quantity: 70, cost_basis: 205.1, acquired_at: '2025-12-04', holding_period: 'short' },
    { id: 501, symbol: 'TSLA', broker: 'robinhood', quantity: 32, cost_basis: 192.4, acquired_at: '2025-07-15', holding_period: 'short' },
  ]);

  const closedPositions = [
    makeDemoClosedPosition({ id: 1, symbol: 'META', name: 'Meta Platforms Inc.', broker: 'etrade', quantity: 18, average_cost: 312.4, close_price: 487.2, acquired_at: '2024-01-12', closed_at: `${new Date().getFullYear()}-02-21` }),
    makeDemoClosedPosition({ id: 2, symbol: 'AMD', name: 'Advanced Micro Devices', broker: 'robinhood', quantity: 40, average_cost: 167.2, close_price: 141.8, acquired_at: '2025-10-08', closed_at: `${new Date().getFullYear()}-03-18` }),
    makeDemoClosedPosition({ id: 3, symbol: 'CRM', name: 'Salesforce Inc.', broker: 'csv', quantity: 22, average_cost: 221.5, close_price: 274.9, acquired_at: '2024-04-26', closed_at: `${new Date().getFullYear()}-04-11` }),
  ];

  const priceHistory = {
    NVDA: makeDemoHistory(925.35, 0.18),
    MSFT: makeDemoHistory(431.4, 0.07),
    VTI: makeDemoHistory(276.1, 0.045),
    AAPL: makeDemoHistory(178.35, -0.08),
    TSLA: makeDemoHistory(248.75, 0.12, 0.04),
  };

  const snapshots = [
    { date: isoDaysAgo(120), total_value: 129800 },
    { date: isoDaysAgo(90), total_value: 137450 },
    { date: isoDaysAgo(60), total_value: 144900 },
    { date: isoDaysAgo(30), total_value: 151200 },
  ];
  const historyEntries = [
    { id: 1, date: isoDaysAgo(210), total_value: 118000, label: 'Started tracking', is_estimate: false },
    { id: 2, date: isoDaysAgo(150), total_value: 126500, label: 'CSV import', is_estimate: false },
  ];

  return {
    portfolio: buildDemoPortfolio(positions),
    taxLots,
    closedPositions,
    priceHistory,
    snapshots,
    historyEntries,
  };
})();

// Slice dates+closes arrays to only include points within the chosen range
function filterByRange(dates, closes, rangeKey) {
  if (!dates || !closes || dates.length === 0) return { dates, closes };
  const today = new Date();
  let cutStr = null;
  if (rangeKey === '1W') cutStr = new Date(today - 7 * 86400000).toISOString().slice(0, 10);
  else if (rangeKey === '1M') cutStr = new Date(today - 30 * 86400000).toISOString().slice(0, 10);
  else if (rangeKey === '3M') cutStr = new Date(today - 90 * 86400000).toISOString().slice(0, 10);
  else if (rangeKey === 'YTD') cutStr = `${today.getFullYear()}-01-01`;
  // 'All' → no cutoff
  if (!cutStr) return { dates, closes };
  const startIdx = dates.findIndex(d => d >= cutStr);
  if (startIdx === -1) return { dates: [], closes: [] };
  return { dates: dates.slice(startIdx), closes: closes.slice(startIdx) };
}

function LandingDashboardPreview() {
  const positions = DEMO_DATA.portfolio.positions.filter(p => p.asset_type !== 'cash').slice(0, 4);
  const gain = DEMO_DATA.portfolio.total_gain;
  const ytd = DEMO_DATA.closedPositions.reduce((sum, p) => sum + p.realized_gain, 0);
  const tslaLot = DEMO_DATA.taxLots['TSLA-robinhood']?.[0];
  return (
    <div className="landing-dashboard-preview" aria-hidden="true">
      <div className="landing-preview-bar">
        <span>WealthBrief</span>
        <span>Sample Tax Center</span>
      </div>
      <div className="landing-preview-metrics">
        <div>
          <span>Total Value</span>
          <strong>{formatMoney(DEMO_DATA.portfolio.total_value)}</strong>
        </div>
        <div>
          <span>Unrealized</span>
          <strong className={gain >= 0 ? 'positive' : 'negative'}>{formatMoney(gain)}</strong>
        </div>
        <div>
          <span>YTD Realized</span>
          <strong className={ytd >= 0 ? 'positive' : 'negative'}>{formatMoney(ytd)}</strong>
        </div>
      </div>
      <div className="landing-preview-grid">
        <div className="landing-preview-table">
          <div className="landing-preview-heading">Holdings</div>
          {positions.map(position => {
            const lots = DEMO_DATA.taxLots[`${position.symbol}-${position.broker}`] || [];
            const longLots = lots.filter(l => l.holding_period === 'long').length;
            const shortLots = lots.length - longLots;
            return (
              <div className="landing-preview-row" key={`${position.symbol}-${position.broker}`}>
                <span>{position.symbol}</span>
                <span>{formatMoney(position.market_value)}</span>
                <span className={position.unrealized_gain >= 0 ? 'positive' : 'negative'}>
                  {formatPct(position.unrealized_gain_pct)}
                </span>
                <span>{longLots} long-term / {shortLots} short-term</span>
              </div>
            );
          })}
        </div>
        <div className="landing-preview-tax">
          <div className="landing-preview-heading">Tax Watch</div>
          <div>
            <span>Loss candidate</span>
            <strong>AAPL {formatMoney(-1872.5)}</strong>
          </div>
          <div>
            <span>Long-term soon</span>
            <strong>TSLA lot in {tslaLot ? daysUntilLongTerm(tslaLot) : 0} days</strong>
          </div>
          <div>
            <span>Next export</span>
            <strong>Advisor-ready CSV</strong>
          </div>
        </div>
      </div>
    </div>
  );
}

function LandingPage({ onSignIn, onViewDemo, onSelectTrustPage }) {
  useEffect(() => {
    trackAnalyticsEvent('landing_view');
  }, []);

  return (
    <main className="landing-page">
      <nav className="landing-nav">
        <button className="landing-brand" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}>
          <span>▸</span> WealthBrief
        </button>
        <div className="landing-nav-actions">
          <button className="btn" onClick={() => onViewDemo('landing_nav')}>View demo</button>
          <button className="btn btn-primary" onClick={() => onSignIn('landing_nav')}>Start free</button>
        </div>
      </nav>

      <section className="landing-hero">
        <div className="landing-hero-visual">
          <LandingDashboardPreview />
        </div>
        <div className="landing-hero-copy">
          <div className="landing-kicker">Tax-aware portfolio tracking</div>
          <h1>WealthBrief</h1>
          <p>
            Track holdings, tax lots, realized gains and losses, and portfolio history in one place before you make taxable trades.
          </p>
          <div className="landing-actions">
            <button className="btn btn-primary" onClick={() => onViewDemo('landing_hero')}>Explore demo portfolio</button>
            <button className="btn" onClick={() => onSignIn('landing_hero')}>Create free account</button>
          </div>
          <div className="landing-proof-row">
            <span>CSV-first onboarding</span>
            <span>Lot-level gains</span>
            <span>Exportable records</span>
          </div>
        </div>
      </section>

      <section className="landing-section">
        <div className="landing-section-heading">
          <span className="landing-kicker">Why it exists</span>
          <h2>Built for investors who outgrew spreadsheets.</h2>
        </div>
        <div className="landing-feature-grid">
          <div className="landing-feature-card">
            <span>01</span>
            <h3>See tax lots before you sell</h3>
            <p>Separate long-term and short-term lots, inspect cost basis, and spot taxable gains while positions are still open.</p>
          </div>
          <div className="landing-feature-card">
            <span>02</span>
            <h3>Track realized gains clearly</h3>
            <p>Keep closed positions, YTD gains, and monthly gains and losses in a dashboard that is easier to review than a broker export.</p>
          </div>
          <div className="landing-feature-card">
            <span>03</span>
            <h3>Start manually, upgrade later</h3>
            <p>Add positions by hand or CSV while WealthBrief keeps broker integrations out of the trust-critical first step.</p>
          </div>
        </div>
      </section>

      <section className="landing-demo-band">
        <div>
          <span className="landing-kicker">No login required</span>
          <h2>Open a realistic sample portfolio.</h2>
          <p>Click holdings, inspect tax lots, review closed trades, and see the kind of records WealthBrief is designed to organize.</p>
        </div>
        <button className="btn btn-primary" onClick={() => onViewDemo('landing_demo_band')}>Launch demo</button>
      </section>

      <footer className="landing-footer">
        <span>WealthBrief is for tracking and organization only. Not financial, tax, or investment advice.</span>
        <TrustLinks onSelectPage={onSelectTrustPage} />
        <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>
      </footer>
    </main>
  );
}

function DateRangeFilter({ value, onChange }) {
  const opts = ['1W', '1M', '3M', 'YTD', 'All'];
  return (
    <div style={{ display: 'flex', gap: '3px' }}>
      {opts.map(o => (
        <button key={o} className={`tab-btn${value === o ? ' active' : ''}`}
          onClick={() => onChange(o)}
          style={{ fontSize: '10px', padding: '4px 9px' }}>{o}</button>
      ))}
    </div>
  );
}

// Pure SVG line — driven by an external hover index
function SparklineInner({ values, width, height, color, hover, showZeroLine = false }) {
  const pad = 2;
  const min = showZeroLine ? Math.min(0, ...values) : Math.min(...values);
  const max = showZeroLine ? Math.max(0, ...values) : Math.max(...values);
  const range = max - min || 1;
  const toY = v => (height - pad) - ((v - min) / range) * (height - pad * 2) + pad;
  const toX = i => (i / (values.length - 1)) * width;
  const pts = values.map((v, i) => `${toX(i).toFixed(1)},${toY(v).toFixed(1)}`).join(' ');
  const hx = hover != null ? toX(hover) : null;
  const hy = hover != null ? toY(values[hover]) : null;
  return (
    <svg width={width} height={height} style={{display:'block', overflow:'visible'}}>
      {showZeroLine && min < 0 && max > 0 && (
        <line x1="0" x2={width} y1={toY(0).toFixed(1)} y2={toY(0).toFixed(1)}
          stroke="rgba(80,100,92,0.18)" strokeWidth="1" strokeDasharray="2,2" />
      )}
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5"
        strokeLinejoin="round" strokeLinecap="round" />
      {hx != null && (
        <>
          <line x1={hx.toFixed(1)} x2={hx.toFixed(1)} y1="0" y2={height}
            stroke="rgba(47,111,159,0.26)" strokeWidth="1" />
          <circle cx={hx.toFixed(1)} cy={hy.toFixed(1)} r="2.5"
            fill={color} stroke="var(--bg-card)" strokeWidth="1.5" />
        </>
      )}
    </svg>
  );
}

// Interactive container: handles mouse, owns hover state, renders two stacked lines + tooltip
function PositionSparklines({ closes, dates, avgCost, quantity, width = 88 }) {
  const [hover, setHover] = React.useState(null); // { idx, cx, cy }
  const ref = React.useRef(null);
  const H = 22;

  const pnlValues = avgCost > 0 ? closes.map(c => (c - avgCost) * quantity) : null;
  const priceUp = closes[closes.length - 1] >= closes[0];
  const pnlFinal = pnlValues ? pnlValues[pnlValues.length - 1] : 0;
  const priceColor = priceUp ? 'var(--accent-green)' : 'var(--accent-red)';
  const pnlColor = pnlFinal >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';

  const onMove = e => {
    if (!ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    const idx = Math.max(0, Math.min(closes.length - 1,
      Math.round(((e.clientX - rect.left) / rect.width) * (closes.length - 1))
    ));
    setHover({ idx, cx: e.clientX, cy: e.clientY });
  };

  return (
    <div ref={ref} style={{display:'inline-flex', flexDirection:'column', gap:'5px', cursor:'crosshair', position:'relative'}}
      onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      <div style={{display:'flex', alignItems:'center', gap:'5px'}}>
        <span style={{fontSize:'8px', fontFamily:'var(--font-mono)', color:'var(--text-muted)', width:'34px', textAlign:'right', lineHeight:1}}>price</span>
        <SparklineInner values={closes} width={width} height={H} color={priceColor} hover={hover?.idx} />
      </div>
      <div style={{display:'flex', alignItems:'center', gap:'5px'}}>
        <span style={{fontSize:'8px', fontFamily:'var(--font-mono)', color:'var(--text-muted)', width:'34px', textAlign:'right', lineHeight:1}}>gain</span>
        <SparklineInner values={pnlValues || closes.map(() => 0)} width={width} height={H}
          color={pnlColor} hover={hover?.idx} showZeroLine />
      </div>
      {hover && (
        <div style={{
          position:'fixed', left: hover.cx + 14, top: hover.cy - 54,
          background:'var(--bg-card)', border:'1px solid var(--border-accent)',
          borderRadius:'6px', padding:'7px 11px', fontFamily:'var(--font-mono)',
          fontSize:'11px', zIndex:9999, pointerEvents:'none', whiteSpace:'nowrap',
          boxShadow:'0 4px 16px rgba(0,0,0,0.5)',
        }}>
          {dates?.[hover.idx] && <div style={{color:'var(--text-muted)', fontSize:'9px', marginBottom:'5px', letterSpacing:'0.3px'}}>{dates[hover.idx]}</div>}
          <div style={{display:'flex', flexDirection:'column', gap:'3px'}}>
            <div style={{display:'flex', justifyContent:'space-between', gap:'18px'}}>
              <span style={{color:'var(--text-muted)'}}>Price</span>
              <span style={{color:priceColor, fontWeight:600}}>{formatMoney(closes[hover.idx])}</span>
            </div>
            {pnlValues && (
              <div style={{display:'flex', justifyContent:'space-between', gap:'18px'}}>
                <span style={{color:'var(--text-muted)'}}>Gain/Loss</span>
                <span style={{color:pnlColor, fontWeight:600}}>{formatMoney(pnlValues[hover.idx])}</span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// Full-width interactive portfolio gain/loss chart with date range filter
function OverallTrend30D({ positions, priceHistory, dateRange, onRangeChange }) {
  const [hover, setHover] = React.useState(null);
  const svgRef = React.useRef(null);

  const { dates, gains, totalValues } = React.useMemo(() => {
    const stockPos = positions.filter(p => p.asset_type !== 'cash' && p.average_cost > 0);
    if (!stockPos.length || !Object.keys(priceHistory).length) return { dates: [], gains: [], totalValues: [] };

    // Compute cutoff date string based on selected range
    const today = new Date();
    let cutStr = null;
    if (dateRange === '1W') cutStr = new Date(today - 7 * 86400000).toISOString().slice(0, 10);
    else if (dateRange === '1M') cutStr = new Date(today - 30 * 86400000).toISOString().slice(0, 10);
    else if (dateRange === '3M') cutStr = new Date(today - 90 * 86400000).toISOString().slice(0, 10);
    else if (dateRange === 'YTD') cutStr = `${today.getFullYear()}-01-01`;

    // Build { symbol → { date → close } } lookup, filtered to range
    const byDate = {};
    Object.entries(priceHistory).forEach(([sym, { dates: ds, closes: cs }]) => {
      byDate[sym] = {};
      ds.forEach((d, i) => {
        if (!cutStr || d >= cutStr) byDate[sym][d] = cs[i];
      });
    });

    // All unique trading dates in range, sorted ascending
    const allDates = [...new Set(Object.values(byDate).flatMap(m => Object.keys(m)))].sort();
    const totalCost = stockPos.reduce((s, p) => s + p.quantity * p.average_cost, 0);

    const dates = [], gains = [], totalValues = [];
    allDates.forEach(date => {
      let val = 0, count = 0;
      stockPos.forEach(p => {
        const c = byDate[p.symbol]?.[date];
        if (c != null) { val += c * p.quantity; count++; }
      });
      // Only include day if at least half the positions have data
      if (count >= Math.max(1, stockPos.length * 0.5)) {
        dates.push(date);
        gains.push(val - totalCost);
        totalValues.push(val);
      }
    });
    return { dates, gains, totalValues };
  }, [positions, priceHistory, dateRange]);

  if (dates.length < 3) return null;

  const rangeLabel = { '1W': '7D', '1M': '30D', '3M': '3M', 'YTD': 'YTD', 'All': 'All' }[dateRange] || dateRange;
  const VW = 1000, H = 88, pad = { t: 8, r: 12, b: 8, l: 12 };
  const min = Math.min(0, ...gains);
  const max = Math.max(0, ...gains);
  const range = max - min || 1;
  const toX = i => pad.l + (i / (gains.length - 1)) * (VW - pad.l - pad.r);
  const toY = v => (H - pad.b) - ((v - min) / range) * (H - pad.t - pad.b) + pad.t;
  const zeroY = toY(0);
  const isUp = gains[gains.length - 1] >= gains[0];
  const lineColor = isUp ? 'var(--accent-green)' : 'var(--accent-red)';
  const fillColor = isUp ? 'rgba(52,211,153,0.07)' : 'rgba(248,113,113,0.07)';

  const linePts = gains.map((v, i) => `${toX(i).toFixed(1)},${toY(v).toFixed(1)}`).join(' ');
  const areaD = `M${toX(0).toFixed(1)},${zeroY.toFixed(1)} `
    + gains.map((v, i) => `L${toX(i).toFixed(1)},${toY(v).toFixed(1)}`).join(' ')
    + ` L${toX(gains.length - 1).toFixed(1)},${zeroY.toFixed(1)} Z`;

  const onMove = e => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return;
    const rel = (e.clientX - rect.left) / rect.width;
    const idx = Math.max(0, Math.min(gains.length - 1, Math.round(rel * (gains.length - 1))));
    setHover({ idx, cx: e.clientX, cy: e.clientY });
  };

  const hx = hover ? toX(hover.idx) : null;
  const hy = hover ? toY(gains[hover.idx]) : null;
  const hGain = hover ? gains[hover.idx] : null;
  const hVal  = hover ? totalValues[hover.idx] : null;

  return (
    <div className="panel" style={{ marginBottom: '20px' }}>
      <div className="panel-header" style={{ justifyContent: 'space-between', flexWrap: 'wrap', gap: '8px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <span className="panel-title">Portfolio {rangeLabel} Gain/Loss</span>
          <DateRangeFilter value={dateRange} onChange={onRangeChange} />
        </div>
        {hover ? (
          <div style={{ fontFamily:'var(--font-mono)', fontSize:'12px', display:'flex', gap:'16px', alignItems:'center' }}>
            <span style={{ color:'var(--text-muted)', fontSize:'10px' }}>{dates[hover.idx]}</span>
            <span style={{ color:'var(--text-muted)', fontSize:'10px' }}>Value <span style={{color:'var(--text-primary)', fontWeight:600}}>{formatMoney(hVal)}</span></span>
            <span style={{ color: hGain >= 0 ? 'var(--accent-green)' : 'var(--accent-red)', fontWeight: 700 }}>{hGain >= 0 ? '+' : ''}{formatMoney(hGain)}</span>
          </div>
        ) : (
          <span style={{ fontFamily:'var(--font-mono)', fontSize:'10px', color:'var(--text-muted)' }}>
            {dates[0]} – {dates[dates.length - 1]}
          </span>
        )}
      </div>
      <svg ref={svgRef} viewBox={`0 0 ${VW} ${H}`} preserveAspectRatio="none"
        style={{ width:'100%', height:`${H}px`, display:'block', cursor:'crosshair' }}
        onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {/* zero line */}
        <line x1={pad.l} x2={VW - pad.r} y1={zeroY.toFixed(1)} y2={zeroY.toFixed(1)}
          stroke="rgba(80,100,92,0.14)" strokeWidth="1" />
        {/* area fill */}
        <path d={areaD} fill={fillColor} />
        {/* line */}
        <polyline points={linePts} fill="none" stroke={lineColor} strokeWidth="2"
          strokeLinejoin="round" strokeLinecap="round" />
        {/* hover */}
        {hover && (
          <>
            <line x1={hx.toFixed(1)} x2={hx.toFixed(1)} y1={pad.t} y2={H - pad.b}
              stroke="rgba(80,100,92,0.2)" strokeWidth="1" strokeDasharray="3,3" />
            <circle cx={hx.toFixed(1)} cy={hy.toFixed(1)} r="4"
              fill={lineColor} stroke="var(--bg-card)" strokeWidth="2" />
          </>
        )}
      </svg>
      {/* X-axis date labels */}
      <div style={{ display:'flex', justifyContent:'space-between', padding:'4px 14px 10px',
        fontFamily:'var(--font-mono)', fontSize:'9px', color:'var(--text-muted)' }}>
        <span>{dates[0]}</span>
        <span>{dates[Math.floor(dates.length / 2)]}</span>
        <span>{dates[dates.length - 1]}</span>
      </div>
    </div>
  );
}

function Toast({ message, type, onClose }) {
  useEffect(() => {
    const t = setTimeout(onClose, 3000);
    return () => clearTimeout(t);
  }, []);
  return <div className={`toast toast-${type}`}>{message}</div>;
}

function StatCard({ label, value, sub, subClass }) {
  return (
    <div className="stat-card">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub && <div className={`stat-sub ${subClass || ''}`}>{sub}</div>}
    </div>
  );
}

function SortTh({ label, col, sortCol, sortDir, onSort, style }) {
  const active = sortCol === col;
  return (
    <th onClick={() => onSort(col)} style={{ cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap', ...style }}>
      {label}
      <span style={{ marginLeft: '4px', opacity: active ? 1 : 0.25, fontSize: '9px' }}>
        {active ? (sortDir === 'asc' ? '▲' : '▼') : '▲'}
      </span>
    </th>
  );
}

function PositionsTable({ positions, taxLots, selectedKey, onSelectPosition, priceHistory, dateRange }) {
  const [sortCol, setSortCol] = React.useState('market_value');
  const [sortDir, setSortDir] = React.useState('desc');

  const onSort = col => {
    if (sortCol === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortCol(col); setSortDir('desc'); }
  };

  const colVal = (p, col) => {
    switch (col) {
      case 'symbol': return p.symbol;
      case 'broker': return p.broker;
      case 'quantity': return p.asset_type === 'cash' ? Infinity : p.quantity;
      case 'average_cost': return p.average_cost;
      case 'current_price': return p.current_price;
      case 'market_value': return p.market_value;
      case 'unrealized_gain': return p.unrealized_gain;
      case 'unrealized_gain_pct': return p.unrealized_gain_pct;
      default: return p.market_value;
    }
  };

  const sorted = [...positions].sort((a, b) => {
    // Cash always last regardless of sort
    if (a.asset_type === 'cash' && b.asset_type !== 'cash') return 1;
    if (b.asset_type === 'cash' && a.asset_type !== 'cash') return -1;
    const av = colVal(a, sortCol), bv = colVal(b, sortCol);
    const cmp = typeof av === 'string' ? av.localeCompare(bv) : av - bv;
    return sortDir === 'asc' ? cmp : -cmp;
  });

  const sp = { sortCol, sortDir, onSort };

  return (
    <table>
      <thead>
        <tr>
          <SortTh label="Symbol" col="symbol" {...sp} />
          <SortTh label="Broker" col="broker" {...sp} />
          <SortTh label="Qty" col="quantity" {...sp} style={{textAlign:'right'}} />
          <SortTh label="Avg Cost" col="average_cost" {...sp} style={{textAlign:'right'}} />
          <SortTh label="Price" col="current_price" {...sp} style={{textAlign:'right'}} />
          <SortTh label="Value" col="market_value" {...sp} style={{textAlign:'right'}} />
          <SortTh label="Gain/Loss" col="unrealized_gain" {...sp} style={{textAlign:'right'}} />
          <th style={{ minWidth: '94px' }}>Tax Lots</th>
          <th style={{textAlign:'center'}}>30d Trends</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((p, i) => {
          const isCash = p.asset_type === 'cash';
          const key = `${p.symbol}-${p.broker}`;
          const isSelected = selectedKey === key;
          const lots = isCash ? [] : (taxLots[key] || []);
          const ltCount = lots.filter(l => l.holding_period === 'long').length;
          const stCount = lots.filter(l => l.holding_period === 'short').length;
          const hist = !isCash ? (priceHistory[p.symbol] || null) : null;
          const rawCloses = hist?.closes || null;
          const rawDates = hist?.dates || null;
          const { dates: histDates, closes } = rawCloses
            ? filterByRange(rawDates, rawCloses, dateRange)
            : { dates: null, closes: null };
          return (
            <tr
              key={`${key}-${i}`}
              className={isSelected ? 'selected-row' : ''}
              style={isCash ? { opacity: 0.75 } : { cursor: 'pointer' }}
              onClick={() => !isCash && onSelectPosition(p)}
            >
              <td>
                <div className="symbol-cell">
                  <span className="symbol-ticker" style={isCash ? { color: 'var(--accent-amber)' } : {}}>{p.symbol}</span>
                  <span className="symbol-name">{isCash ? 'Cash Balance' : p.name}</span>
                </div>
              </td>
              <td><span className={`broker-tag broker-${p.broker}`}>{p.broker}</span></td>
              <td style={{ color: 'var(--text-muted)' }}>{isCash ? '—' : p.quantity}</td>
              <td style={{ color: 'var(--text-muted)' }}>{isCash ? '—' : formatMoney(p.average_cost)}</td>
              <td style={{ color: 'var(--text-muted)' }}>{isCash ? '—' : formatMoney(p.current_price)}</td>
              <td>{formatMoney(p.market_value)}</td>
              <td className={isCash ? '' : (p.unrealized_gain >= 0 ? 'positive' : 'negative')}
                  style={isCash ? { color: 'var(--text-muted)' } : {}}>
                {isCash ? '—' : (<>{formatMoney(p.unrealized_gain)}<br/><span style={{fontSize:'10px'}}>{formatPct(p.unrealized_gain_pct)}</span></>)}
              </td>
              <td>
                {isCash ? <span style={{color:'var(--text-muted)'}}>—</span> : (
                  lots.length === 0
                    ? <button className="lot-add" onClick={e => { e.stopPropagation(); onSelectPosition(p); }}>+ add lot</button>
                    : <>
                        {ltCount > 0 && <span className="lot-badge lot-long">{ltCount} long-term</span>}
                        {stCount > 0 && <span className="lot-badge lot-short">{stCount} short-term</span>}
                      </>
                )}
              </td>
              <td style={{textAlign:'center', verticalAlign:'middle'}}>
                {isCash ? (
                  <span style={{color:'var(--text-muted)'}}>—</span>
                ) : closes ? (
                  <PositionSparklines
                    closes={closes}
                    dates={histDates}
                    avgCost={p.average_cost}
                    quantity={p.quantity}
                  />
                ) : (
                  <span style={{color:'var(--text-muted)', fontSize:'9px', fontFamily:'var(--font-mono)'}}>…</span>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function BrokerBreakdown({ breakdown, total }) {
  const colors = {
    robinhood: '#0f8a5f',
    etrade: '#2f6f9f',
    schwab: '#6f7f2f',
    fidelity: '#8a5f0f',
    interactive_brokers: '#5f7092',
    csv: '#b98217',
  };
  return (
    <div>
      {Object.entries(breakdown).map(([broker, value]) => (
        <div className="breakdown-item" key={broker}>
          <span className="breakdown-label">
            <span className="status-dot" style={{background: colors[broker] || '#888'}}></span>
            {broker}
          </span>
          <div className="breakdown-bar-wrap">
            <div
              className="breakdown-bar"
              style={{
                width: total > 0 ? `${(value/total)*100}%` : '0%',
                background: colors[broker] || '#888',
              }}
            />
          </div>
          <span className="breakdown-value">{formatMoney(value)}</span>
        </div>
      ))}
    </div>
  );
}

function UploadPanel({ onUpload }) {
  const fileRef = useRef();
  const [broker, setBroker] = useState('csv');

  const uploadFile = (file) => {
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    onUpload(form, broker);
    if (fileRef.current) fileRef.current.value = '';
  };

  const handleFile = async (e) => {
    uploadFile(e.target.files[0]);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    uploadFile(e.dataTransfer.files[0]);
  };

  return (
    <div className="upload-panel">
      <div className="upload-controls">
        <select
          className="form-input"
          value={broker}
          onChange={e => setBroker(e.target.value)}
        >
          {BROKER_OPTIONS.map(option => (
            <option value={option.value} key={option.value}>{option.label}</option>
          ))}
        </select>
        <button type="button" className="btn" onClick={() => downloadCsvTemplate(broker)}>
          Download {brokerLabel(broker)} template
        </button>
      </div>
      <div
        className="upload-zone"
        onClick={() => fileRef.current.click()}
        onDragOver={e => e.preventDefault()}
        onDrop={handleDrop}
      >
        <div className="upload-icon">↑</div>
        <p>Drop CSV or click to upload</p>
        <span>Use the selected broker label so imported positions stay organized.</span>
        <input ref={fileRef} type="file" accept=".csv" hidden onChange={handleFile} />
      </div>
    </div>
  );
}

function OnboardingPanel({ onUpload, onAdd, onViewDemo }) {
  const [choice, setChoice] = useState('import');
  const selectChoice = (nextChoice) => {
    setChoice(nextChoice);
    trackAnalyticsEvent('onboarding_choice_selected', { choice: nextChoice });
  };

  return (
    <div className="onboarding-panel">
      <div className="onboarding-intro">
        <span>Get started</span>
        <h3>Choose how to build your first portfolio</h3>
        <p>Import a broker export, enter one position manually, or explore the sample portfolio before adding real data.</p>
      </div>
      <div className="onboarding-choice-grid">
        <button
          type="button"
          className={`onboarding-choice${choice === 'import' ? ' active' : ''}`}
          onClick={() => selectChoice('import')}
        >
          <strong>Import CSV</strong>
          <span>Upload holdings from Robinhood, E*Trade, Schwab, Fidelity, Interactive Brokers, or a generic export.</span>
        </button>
        <button
          type="button"
          className={`onboarding-choice${choice === 'manual' ? ' active' : ''}`}
          onClick={() => selectChoice('manual')}
        >
          <strong>Add manually</strong>
          <span>Start with a single holding and add tax lots after the position is saved.</span>
        </button>
        <button
          type="button"
          className="onboarding-choice"
          onClick={() => {
            trackAnalyticsEvent('onboarding_choice_selected', { choice: 'sample_portfolio' });
            onViewDemo();
          }}
        >
          <strong>Use sample portfolio</strong>
          <span>Open a realistic dashboard with sample holdings, tax lots, and closed trades.</span>
        </button>
      </div>
      <div className="onboarding-workspace">
        {choice === 'import' ? (
          <UploadPanel onUpload={onUpload} />
        ) : (
          <AddActivePositionForm onAdd={onAdd} compact />
        )}
      </div>
    </div>
  );
}

function CashPanel({ onSave }) {
  const brokers = BROKER_OPTIONS.filter(b => b.value !== 'csv');
  const [amounts, setAmounts] = React.useState(Object.fromEntries(brokers.map(b => [b.value, ''])));
  const [saved, setSaved] = React.useState({});

  useEffect(() => {
    fetch('/api/cash').then(r => r.json()).then(data => {
      const m = {};
      data.forEach(({ broker, amount }) => { m[broker] = amount; });
      setAmounts(prev => ({ ...prev, ...Object.fromEntries(Object.entries(m).map(([k,v]) => [k, v])) }));
    });
  }, []);

  const handleSave = async (broker) => {
    const amount = parseFloat(amounts[broker]) || 0;
    await fetch('/api/cash', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ broker, amount }),
    });
    setSaved(s => ({ ...s, [broker]: true }));
    setTimeout(() => setSaved(s => ({ ...s, [broker]: false })), 1500);
    onSave();
  };

  return (
    <div style={{ padding: '4px 0' }}>
      {brokers.map(({ value: broker, label }) => (
        <div key={broker} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '10px 16px', borderBottom: '1px solid rgba(153,174,164,0.32)' }}>
          <span className={`broker-tag broker-${broker}`} style={{ minWidth: '72px' }}>{label}</span>
          <input
            type="number"
            min="0"
            step="0.01"
            value={amounts[broker]}
            onChange={e => setAmounts(a => ({ ...a, [broker]: e.target.value }))}
            placeholder="0.00"
            style={{
              flex: 1, background: 'var(--bg-primary)', color: 'var(--text-primary)',
              border: '1px solid var(--border-accent)', borderRadius: '4px',
              padding: '5px 8px', fontFamily: 'var(--font-mono)', fontSize: '12px',
            }}
          />
          <button
            className="btn"
            style={{ padding: '5px 10px', fontSize: '11px', minWidth: '48px',
              ...(saved[broker] ? { borderColor: 'var(--accent-green)', color: 'var(--accent-green)' } : {}) }}
            onClick={() => handleSave(broker)}
          >
            {saved[broker] ? '✓' : 'Set'}
          </button>
        </div>
      ))}
    </div>
  );
}

function AddActivePositionForm({ onAdd, compact = false }) {
  const empty = {
    symbol: '',
    name: '',
    broker: 'csv',
    asset_type: 'stock',
    quantity: '',
    average_cost: '',
    current_price: '',
  };
  const [form, setForm] = React.useState(empty);
  const [saving, setSaving] = React.useState(false);
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!form.symbol || !form.quantity || !form.average_cost || !form.current_price) return;
    setSaving(true);
    trackAnalyticsEvent('manual_position_add_start', {
      broker: form.broker,
      asset_type: form.asset_type,
    });
    try {
      const res = await fetch(`${API}/api/positions/upsert`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: form.symbol.toUpperCase().trim(),
          name: form.name.trim(),
          broker: form.broker,
          asset_type: form.asset_type,
          quantity: parseFloat(form.quantity),
          average_cost: parseFloat(form.average_cost),
          current_price: parseFloat(form.current_price),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        trackAnalyticsEvent('manual_position_add_failure', {
          broker: form.broker,
          asset_type: form.asset_type,
          status: res.status,
        });
        alert(data.detail || 'Position add failed');
        return;
      }
      trackAnalyticsEvent('manual_position_add_success', {
        broker: form.broker,
        asset_type: form.asset_type,
      });
      setForm(empty);
      onAdd(data);
    } catch {
      trackAnalyticsEvent('manual_position_add_failure', {
        broker: form.broker,
        asset_type: form.asset_type,
        reason: 'network_error',
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} style={{ padding: compact ? '0' : '14px 16px' }}>
      <div className="form-grid-2">
        <div className="form-row">
          <label className="form-label">Symbol *</label>
          <input className="form-input" placeholder="AAPL" value={form.symbol} onChange={e => set('symbol', e.target.value)} required />
        </div>
        <div className="form-row">
          <label className="form-label">Broker *</label>
          <select className="form-input" value={form.broker} onChange={e => set('broker', e.target.value)}>
            {MANUAL_BROKER_OPTIONS.map(option => (
              <option value={option.value} key={option.value}>{option.label}</option>
            ))}
          </select>
        </div>
      </div>
      <div className="form-grid-2">
        <div className="form-row">
          <label className="form-label">Type *</label>
          <select className="form-input" value={form.asset_type} onChange={e => set('asset_type', e.target.value)}>
            <option value="stock">Stock</option>
            <option value="etf">ETF</option>
            <option value="option">Option</option>
            <option value="crypto">Crypto</option>
          </select>
        </div>
        <div className="form-row">
          <label className="form-label">Name</label>
          <input className="form-input" placeholder="Optional" value={form.name} onChange={e => set('name', e.target.value)} />
        </div>
      </div>
      <div className="form-grid-2">
        <div className="form-row">
          <label className="form-label">Qty *</label>
          <input className="form-input" type="number" min="0" step="any" placeholder="0" value={form.quantity} onChange={e => set('quantity', e.target.value)} required />
        </div>
        <div className="form-row">
          <label className="form-label">Avg Cost *</label>
          <input className="form-input" type="number" min="0" step="any" placeholder="0.00" value={form.average_cost} onChange={e => set('average_cost', e.target.value)} required />
        </div>
      </div>
      <div className="form-row">
        <label className="form-label">Current Price *</label>
        <input className="form-input" type="number" min="0" step="any" placeholder="0.00" value={form.current_price} onChange={e => set('current_price', e.target.value)} required />
      </div>
      <button type="submit" className="btn btn-primary" style={{ width:'100%', marginTop:'4px' }} disabled={saving}>
        {saving ? 'Saving...' : 'Add Active Position'}
      </button>
    </form>
  );
}

function TopHoldings({ positions }) {
  const top5 = [...positions]
    .filter(p => p.asset_type !== 'cash')
    .sort((a, b) => b.market_value - a.market_value)
    .slice(0, 5);
  const total = positions.reduce((s, p) => s + p.market_value, 0);

  return (
    <div>
      {top5.map((p, i) => (
        <div className="breakdown-item" key={p.symbol + p.broker}>
          <span className="breakdown-label" style={{minWidth:'52px'}}>
            <span style={{color:'var(--text-primary)', fontWeight:600}}>{p.symbol}</span>
          </span>
          <div className="breakdown-bar-wrap">
            <div
              className="breakdown-bar"
              style={{
                width: total > 0 ? `${(p.market_value/total)*100}%` : '0%',
                background: `hsl(${210 + i*30}, 70%, 60%)`,
              }}
            />
          </div>
          <span className="breakdown-value">
            {total > 0 ? ((p.market_value/total)*100).toFixed(1) + '%' : '—'}
          </span>
        </div>
      ))}
    </div>
  );
}

function daysUntilLongTerm(lot) {
  const acquired = new Date(`${lot.acquired_at}T12:00:00Z`);
  const longTermDate = new Date(acquired);
  longTermDate.setUTCDate(longTermDate.getUTCDate() + 365);
  return Math.max(0, Math.ceil((longTermDate - new Date()) / 86400000));
}

function longTermDateForLot(lot) {
  const acquired = new Date(`${lot.acquired_at}T12:00:00Z`);
  acquired.setUTCDate(acquired.getUTCDate() + 365);
  return acquired.toISOString().slice(0, 10);
}

function formatDateShort(dateString) {
  if (!dateString) return '—';
  const d = new Date(`${dateString}T12:00:00Z`);
  if (isNaN(d.getTime())) return dateString;
  return d.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
}

function enrichTaxLotsForPositions(positions, taxLots) {
  return Object.values(taxLots).flat().map(lot => {
    const position = positions.find(p => p.symbol === lot.symbol && p.broker === lot.broker);
    const currentPrice = position?.current_price || 0;
    const unrealizedGain = position ? (currentPrice - lot.cost_basis) * lot.quantity : 0;
    return {
      ...lot,
      position,
      currentPrice,
      unrealizedGain,
      daysToLong: daysUntilLongTerm(lot),
      longTermDate: longTermDateForLot(lot),
    };
  });
}

function TaxWedgePanel({ positions, taxLots, closedPositions, onSelectPosition, onSignIn, demoMode = false }) {
  const lots = enrichTaxLotsForPositions(positions, taxLots);
  const currentYear = new Date().getFullYear().toString();
  const ytdRealized = closedPositions
    .filter(p => p.closed_at?.startsWith(currentYear))
    .reduce((sum, p) => sum + p.realized_gain, 0);
  const shortLots = lots.filter(lot => lot.holding_period === 'short');
  const longLots = lots.filter(lot => lot.holding_period === 'long');
  const shortUnrealized = shortLots.reduce((sum, lot) => sum + lot.unrealizedGain, 0);
  const longUnrealized = longLots.reduce((sum, lot) => sum + lot.unrealizedGain, 0);
  const lossCandidates = lots
    .filter(lot => lot.unrealizedGain < 0)
    .sort((a, b) => a.unrealizedGain - b.unrealizedGain)
    .slice(0, 3);
  const upcomingLots = shortLots
    .filter(lot => lot.daysToLong > 0)
    .sort((a, b) => a.daysToLong - b.daysToLong)
    .slice(0, 3);

  const selectLot = (lot) => {
    if (lot.position && onSelectPosition) onSelectPosition(lot.position);
  };

  return (
    <div className="tax-wedge-panel">
      <div className="tax-wedge-grid">
        <div>
          <span>Short-term lots</span>
          <strong>{shortLots.length}</strong>
          <em className={shortUnrealized >= 0 ? 'positive' : 'negative'}>{formatMoney(shortUnrealized)}</em>
        </div>
        <div>
          <span>Long-term lots</span>
          <strong>{longLots.length}</strong>
          <em className={longUnrealized >= 0 ? 'positive' : 'negative'}>{formatMoney(longUnrealized)}</em>
        </div>
        <div>
          <span>YTD realized gains</span>
          <strong className={ytdRealized >= 0 ? 'positive' : 'negative'}>{formatMoney(ytdRealized)}</strong>
          <em>{currentYear}</em>
        </div>
      </div>

      <div className="tax-wedge-section">
        <div className="tax-wedge-heading">Tax-loss candidates</div>
        {lossCandidates.length ? lossCandidates.map(lot => (
          <button type="button" className="tax-wedge-row" key={`loss-${lot.id}`} onClick={() => selectLot(lot)}>
            <span>{lot.symbol}</span>
            <strong className="negative">{formatMoney(lot.unrealizedGain)}</strong>
            <em>{lot.holding_period === 'long' ? 'long-term' : 'short-term'} lot</em>
          </button>
        )) : (
          <div className="tax-wedge-empty">No open lots currently show an unrealized loss.</div>
        )}
      </div>

      <div className="tax-wedge-section">
        <div className="tax-wedge-heading">Upcoming long-term dates</div>
        {upcomingLots.length ? upcomingLots.map(lot => (
          <button type="button" className="tax-wedge-row" key={`soon-${lot.id}`} onClick={() => selectLot(lot)}>
            <span>{lot.symbol}</span>
            <strong>{lot.daysToLong} days</strong>
            <em>{formatDateShort(lot.longTermDate)}</em>
          </button>
        )) : (
          <div className="tax-wedge-empty">No short-term lots are waiting on a long-term date.</div>
        )}
      </div>

      {demoMode && (
        <button className="btn demo-panel-cta" onClick={onSignIn}>Use with my portfolio</button>
      )}
    </div>
  );
}

function DemoTaxLotsPanel({ position, lots, onSignIn }) {
  if (!position) {
    return (
      <div className="demo-side-note">
        Click a holding in the table to inspect the sample tax lots behind it.
      </div>
    );
  }

  const lotGain = lot => (position.current_price - lot.cost_basis) * lot.quantity;
  const totalLotGain = lots.reduce((sum, lot) => sum + lotGain(lot), 0);

  return (
    <div className="demo-tax-lots">
      <div className="demo-tax-header">
        <div>
          <strong>{position.symbol}</strong>
          <span className={`broker-tag broker-${position.broker}`}>{position.broker}</span>
        </div>
        <span className={totalLotGain >= 0 ? 'positive' : 'negative'}>{formatMoney(totalLotGain)}</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>Date</th>
            <th>Shares</th>
            <th>Cost</th>
            <th>Unrealized</th>
            <th>Tax Term</th>
            <th>Long-term date</th>
          </tr>
        </thead>
        <tbody>
          {lots.map(lot => {
            const gain = lotGain(lot);
            return (
              <tr key={lot.id}>
                <td>{lot.acquired_at}</td>
                <td>{lot.quantity}</td>
                <td>{formatMoney(lot.cost_basis)}</td>
                <td className={gain >= 0 ? 'positive' : 'negative'}>{formatMoney(gain)}</td>
                <td>
                  <span className={`lot-badge ${lot.holding_period === 'long' ? 'lot-long' : 'lot-short'}`}>
                    {lot.holding_period === 'long' ? 'long-term' : 'short-term'}
                  </span>
                </td>
                <td>{lot.holding_period === 'long' ? 'Qualified' : `${formatDateShort(longTermDateForLot(lot))} (${daysUntilLongTerm(lot)}d)`}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <button className="btn btn-primary demo-panel-cta" onClick={onSignIn}>Track my real lots</button>
    </div>
  );
}

function TaxLotsPanel({ position, lots, onRefresh }) {
  const today = new Date().toISOString().slice(0, 10);
  const [form, setForm] = React.useState({ quantity: '', cost_basis: '', acquired_at: '' });
  const [saving, setSaving] = React.useState(false);
  const [editingId, setEditingId] = React.useState(null);
  const [editForm, setEditForm] = React.useState({ quantity: '', cost_basis: '', acquired_at: '' });
  const [sellingId, setSellingId] = React.useState(null);
  const [sellForm, setSellForm] = React.useState({ quantity: '', close_price: '', closed_at: '' });
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));
  const setEdit = (k, v) => setEditForm(f => ({ ...f, [k]: v }));
  const setSell = (k, v) => setSellForm(f => ({ ...f, [k]: v }));

  const handleAdd = async (e) => {
    e.preventDefault();
    if (!form.quantity || !form.cost_basis || !form.acquired_at) return;
    setSaving(true);
    try {
      await fetch('/api/tax-lots', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: position.symbol,
          broker: position.broker,
          quantity: parseFloat(form.quantity),
          cost_basis: parseFloat(form.cost_basis),
          acquired_at: form.acquired_at,
        }),
      });
      setForm({ quantity: '', cost_basis: '', acquired_at: '' });
      onRefresh();
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id) => {
    await fetch(`/api/tax-lots/${id}`, { method: 'DELETE' });
    onRefresh();
  };

  const startEdit = (lot) => {
    setSellingId(null);
    setEditingId(lot.id);
    setEditForm({
      quantity: String(lot.quantity),
      cost_basis: String(lot.cost_basis),
      acquired_at: lot.acquired_at,
    });
  };

  const handleSaveEdit = async (id) => {
    setSaving(true);
    try {
      await fetch(`/api/tax-lots/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          quantity: parseFloat(editForm.quantity),
          cost_basis: parseFloat(editForm.cost_basis),
          acquired_at: editForm.acquired_at,
        }),
      });
      setEditingId(null);
      onRefresh();
    } finally {
      setSaving(false);
    }
  };

  const startSell = (lot) => {
    setEditingId(null);
    setSellingId(lot.id);
    setSellForm({ quantity: String(lot.quantity), close_price: '', closed_at: today });
  };

  const handleSell = async (id) => {
    if (!sellForm.quantity || !sellForm.close_price) return;
    setSaving(true);
    try {
      const res = await fetch(`/api/tax-lots/${id}/sell`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          quantity: parseFloat(sellForm.quantity),
          close_price: parseFloat(sellForm.close_price),
          closed_at: sellForm.closed_at || today,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        alert(err.detail || 'Sell failed');
        return;
      }
      setSellingId(null);
      setSellForm({ quantity: '', close_price: '', closed_at: '' });
      onRefresh();
    } finally {
      setSaving(false);
    }
  };

  const totalShares = lots.reduce((s, l) => s + l.quantity, 0);
  const ltShares = lots.filter(l => l.holding_period === 'long').reduce((s, l) => s + l.quantity, 0);
  const stShares = lots.filter(l => l.holding_period === 'short').reduce((s, l) => s + l.quantity, 0);
  const lotRows = lots.map(lot => {
    const unrealizedGain = (position.current_price - lot.cost_basis) * lot.quantity;
    return {
      ...lot,
      unrealizedGain,
      daysToLong: daysUntilLongTerm(lot),
      longTermDate: longTermDateForLot(lot),
    };
  });
  const shortUnrealized = lotRows
    .filter(lot => lot.holding_period === 'short')
    .reduce((sum, lot) => sum + lot.unrealizedGain, 0);
  const longUnrealized = lotRows
    .filter(lot => lot.holding_period === 'long')
    .reduce((sum, lot) => sum + lot.unrealizedGain, 0);
  const selectedLossCandidates = lotRows.filter(lot => lot.unrealizedGain < 0).length;

  return (
    <div style={{ padding: '12px 16px' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
        <div>
          <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: '14px' }}>{position.symbol}</span>
          <span className={`broker-tag broker-${position.broker}`} style={{ marginLeft: '8px' }}>{position.broker}</span>
        </div>
        {lots.length > 0 && (
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-muted)', textAlign: 'right' }}>
            {ltShares > 0 && <><span className="lot-badge lot-long">{ltShares.toFixed(4).replace(/\.?0+$/, '')} long-term</span></>}
            {stShares > 0 && <><span className="lot-badge lot-short">{stShares.toFixed(4).replace(/\.?0+$/, '')} short-term</span></>}
          </div>
        )}
      </div>

      {lots.length > 0 && (
        <div className="lot-tax-summary">
          <div>
            <span>Short-term unrealized</span>
            <strong className={shortUnrealized >= 0 ? 'positive' : 'negative'}>{formatMoney(shortUnrealized)}</strong>
          </div>
          <div>
            <span>Long-term unrealized</span>
            <strong className={longUnrealized >= 0 ? 'positive' : 'negative'}>{formatMoney(longUnrealized)}</strong>
          </div>
          <div>
            <span>Loss candidates</span>
            <strong>{selectedLossCandidates}</strong>
          </div>
        </div>
      )}

      {/* Existing lots */}
      {lots.length > 0 && (
        <div style={{ marginBottom: '14px', border: '1px solid var(--border)', borderRadius: '6px', overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th style={{ fontSize: '9px', padding: '7px 10px', textAlign: 'left', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', borderBottom: '1px solid var(--border)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Date</th>
                <th style={{ fontSize: '9px', padding: '7px 10px', textAlign: 'right', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', borderBottom: '1px solid var(--border)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Shares</th>
                <th style={{ fontSize: '9px', padding: '7px 10px', textAlign: 'right', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', borderBottom: '1px solid var(--border)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Cost</th>
                <th style={{ fontSize: '9px', padding: '7px 10px', textAlign: 'right', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', borderBottom: '1px solid var(--border)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Unrealized</th>
                <th style={{ fontSize: '9px', padding: '7px 10px', textAlign: 'center', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', borderBottom: '1px solid var(--border)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Tax Term</th>
                <th style={{ fontSize: '9px', padding: '7px 10px', textAlign: 'left', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', borderBottom: '1px solid var(--border)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Long-term date</th>
                <th style={{ padding: '7px 6px', borderBottom: '1px solid var(--border)' }}></th>
              </tr>
            </thead>
            <tbody>
              {lotRows.map(lot => {
                const isEditing = editingId === lot.id;
                const isSelling = sellingId === lot.id;
                const cellTd = { fontFamily: 'var(--font-mono)', fontSize: '11px', padding: '7px 10px' };
                const inlineInput = { fontFamily: 'var(--font-mono)', fontSize: '11px', padding: '3px 5px', width: '100%', background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '3px', color: 'var(--text-primary)' };
                const iconBtn = { background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', fontSize: '12px', padding: '0 3px' };
                return (
                  <React.Fragment key={lot.id}>
                    <tr style={{ borderBottom: (isEditing || isSelling) ? 'none' : '1px solid rgba(153,174,164,0.32)' }}>
                      {isEditing ? (
                        <>
                          <td style={cellTd}>
                            <input type="date" max={today} value={editForm.acquired_at} onChange={e => setEdit('acquired_at', e.target.value)} style={inlineInput} />
                          </td>
                          <td style={{ ...cellTd, textAlign: 'right' }}>
                            <input type="number" min="0" step="any" value={editForm.quantity} onChange={e => setEdit('quantity', e.target.value)} style={{ ...inlineInput, textAlign: 'right' }} />
                          </td>
                          <td style={{ ...cellTd, textAlign: 'right' }}>
                            <input type="number" min="0" step="any" value={editForm.cost_basis} onChange={e => setEdit('cost_basis', e.target.value)} style={{ ...inlineInput, textAlign: 'right' }} />
                          </td>
                          <td style={{ padding: '7px 10px', textAlign: 'right', color: 'var(--text-muted)', fontSize: '10px' }}>—</td>
                          <td style={{ padding: '7px 10px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '10px' }}>—</td>
                          <td style={{ padding: '7px 10px', color: 'var(--text-muted)', fontSize: '10px' }}>—</td>
                          <td style={{ padding: '7px 6px', textAlign: 'center', whiteSpace: 'nowrap' }}>
                            <button onClick={() => handleSaveEdit(lot.id)} disabled={saving} title="Save" style={{ ...iconBtn, color: 'var(--accent-green)' }}>✓</button>
                            <button onClick={() => setEditingId(null)} title="Cancel" style={iconBtn}>×</button>
                          </td>
                        </>
                      ) : (
                        <>
                          <td style={{ ...cellTd, color: 'var(--text-secondary)' }}>{lot.acquired_at}</td>
                          <td style={{ ...cellTd, textAlign: 'right', color: 'var(--text-primary)' }}>{lot.quantity}</td>
                          <td style={{ ...cellTd, textAlign: 'right', color: 'var(--text-primary)' }}>{formatMoney(lot.cost_basis)}</td>
                          <td style={{ ...cellTd, textAlign: 'right' }} className={lot.unrealizedGain >= 0 ? 'positive' : 'negative'}>{formatMoney(lot.unrealizedGain)}</td>
                          <td style={{ padding: '7px 10px', textAlign: 'center' }}>
                            <span className={`lot-badge ${lot.holding_period === 'long' ? 'lot-long' : 'lot-short'}`}>
                              {lot.holding_period === 'long' ? 'long-term' : 'short-term'}
                            </span>
                          </td>
                          <td style={{ ...cellTd, color: 'var(--text-secondary)', minWidth: '116px' }}>
                            {lot.holding_period === 'long' ? 'Qualified' : `${formatDateShort(lot.longTermDate)} (${lot.daysToLong}d)`}
                          </td>
                          <td style={{ padding: '7px 6px', textAlign: 'center', whiteSpace: 'nowrap' }}>
                            <button onClick={() => startSell(lot)} title="Sell from this lot" style={iconBtn}>$</button>
                            <button onClick={() => startEdit(lot)} title="Edit" style={iconBtn}>✎</button>
                            <button onClick={() => handleDelete(lot.id)} title="Delete" style={{ ...iconBtn, fontSize: '13px' }}>×</button>
                          </td>
                        </>
                      )}
                    </tr>
                    {isSelling && (
                      <tr style={{ borderBottom: '1px solid rgba(153,174,164,0.32)', background: 'rgba(15,138,95,0.045)' }}>
                        <td colSpan={7} style={{ padding: '10px 12px' }}>
                          <div style={{ fontSize: '10px', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '6px' }}>
                            Sell from lot · acquired {lot.acquired_at} · {lot.quantity} avail
                          </div>
                          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px', marginBottom: '6px' }}>
                            <div className="form-row" style={{ marginBottom: 0 }}>
                              <label className="form-label">Shares</label>
                              <input className="form-input" type="number" min="0" max={lot.quantity} step="any" value={sellForm.quantity} onChange={e => setSell('quantity', e.target.value)} />
                            </div>
                            <div className="form-row" style={{ marginBottom: 0 }}>
                              <label className="form-label">Sell Price</label>
                              <input className="form-input" type="number" min="0" step="any" placeholder={String(position.current_price?.toFixed?.(2) || '')} value={sellForm.close_price} onChange={e => setSell('close_price', e.target.value)} />
                            </div>
                          </div>
                          <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: '6px', alignItems: 'end' }}>
                            <div className="form-row" style={{ marginBottom: 0 }}>
                              <label className="form-label">Date</label>
                              <input className="form-input" type="date" max={today} value={sellForm.closed_at} onChange={e => setSell('closed_at', e.target.value)} />
                            </div>
                            <div style={{ display: 'flex', gap: '4px' }}>
                              <button type="button" onClick={() => handleSell(lot.id)} className="btn btn-primary" style={{ fontSize: '11px', padding: '6px 10px' }} disabled={saving || !sellForm.quantity || !sellForm.close_price}>
                                {saving ? '…' : 'Sell'}
                              </button>
                              <button type="button" onClick={() => setSellingId(null)} className="btn" style={{ fontSize: '11px', padding: '6px 10px' }}>✕</button>
                            </div>
                          </div>
                          {sellForm.quantity && sellForm.close_price && (
                            <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-muted)', marginTop: '8px' }}>
                              Realized gain/loss:{' '}
                              <span style={{
                                color: ((parseFloat(sellForm.close_price) - lot.cost_basis) * parseFloat(sellForm.quantity)) >= 0 ? 'var(--accent-green)' : 'var(--accent-red)',
                                fontWeight: 600,
                              }}>
                                {formatMoney((parseFloat(sellForm.close_price) - lot.cost_basis) * parseFloat(sellForm.quantity))}
                              </span>
                              {' · '}
                              {lot.holding_period === 'long' ? 'long-term' : 'short-term'}
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Add lot form */}
      <form onSubmit={handleAdd}>
        <div style={{ fontSize: '10px', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '8px' }}>
          Add Lot
        </div>
        <div className="form-grid-2" style={{ marginBottom: '8px' }}>
          <div className="form-row">
            <label className="form-label">Shares *</label>
            <input className="form-input" type="number" min="0" step="any" placeholder="0" value={form.quantity} onChange={e => set('quantity', e.target.value)} required />
          </div>
          <div className="form-row">
            <label className="form-label">Price Paid *</label>
            <input className="form-input" type="number" min="0" step="any" placeholder="0.00" value={form.cost_basis} onChange={e => set('cost_basis', e.target.value)} required />
          </div>
        </div>
        <div className="form-row" style={{ marginBottom: '10px' }}>
          <label className="form-label">Purchase Date *</label>
          <input className="form-input" type="date" max={today} value={form.acquired_at} onChange={e => set('acquired_at', e.target.value)} required />
        </div>
        <button type="submit" className="btn btn-primary" style={{ width: '100%', fontSize: '11px' }} disabled={saving}>
          {saving ? 'Saving…' : 'Add Lot'}
        </button>
      </form>
    </div>
  );
}

function ClosedPositionsTable({ positions, onDelete }) {
  const [sortCol, setSortCol] = React.useState('closed_at');
  const [sortDir, setSortDir] = React.useState('desc');

  const onSort = col => {
    if (sortCol === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortCol(col); setSortDir('desc'); }
  };

  const colVal = (p, col) => {
    switch (col) {
      case 'symbol': return p.symbol;
      case 'broker': return p.broker;
      case 'quantity': return p.quantity;
      case 'average_cost': return p.average_cost;
      case 'close_price': return p.close_price;
      case 'realized_gain': return p.realized_gain;
      case 'realized_gain_pct': return p.realized_gain_pct;
      case 'acquired_at': return p.acquired_at || '';
      case 'closed_at': return p.closed_at;
      default: return p.closed_at;
    }
  };

  const sorted = [...positions].sort((a, b) => {
    const av = colVal(a, sortCol), bv = colVal(b, sortCol);
    const cmp = typeof av === 'string' ? av.localeCompare(bv) : av - bv;
    return sortDir === 'asc' ? cmp : -cmp;
  });

  if (sorted.length === 0) {
    return (
      <div className="empty-state">
        <h3>No closed positions</h3>
        <p>Log a sold position using the form on the right.</p>
      </div>
    );
  }

  const sp = { sortCol, sortDir, onSort };

  return (
    <table>
      <thead>
        <tr>
          <SortTh label="Symbol" col="symbol" {...sp} />
          <SortTh label="Broker" col="broker" {...sp} />
          <SortTh label="Qty" col="quantity" {...sp} style={{textAlign:'right'}} />
          <SortTh label="Avg Cost" col="average_cost" {...sp} style={{textAlign:'right'}} />
          <SortTh label="Close Price" col="close_price" {...sp} style={{textAlign:'right'}} />
          <SortTh label="Realized Gain" col="realized_gain" {...sp} style={{textAlign:'right'}} />
          <SortTh label="Acquired" col="acquired_at" {...sp} />
          <SortTh label="Closed" col="closed_at" {...sp} />
          <th></th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((p) => (
          <tr key={p.id}>
            <td>
              <div className="symbol-cell">
                <span className="symbol-ticker">{p.symbol}</span>
                <span className="symbol-name">{p.name}</span>
              </div>
            </td>
            <td><span className={`broker-tag broker-${p.broker}`}>{p.broker}</span></td>
            <td style={{color:'var(--text-muted)'}}>{p.quantity}</td>
            <td style={{color:'var(--text-muted)'}}>{formatMoney(p.average_cost)}</td>
            <td style={{color:'var(--text-muted)'}}>{formatMoney(p.close_price)}</td>
            <td className={p.realized_gain >= 0 ? 'positive' : 'negative'}>
              {formatMoney(p.realized_gain)}
              <br/>
              <span style={{fontSize:'10px'}}>{formatPct(p.realized_gain_pct)}</span>
            </td>
            <td style={{color:'var(--text-muted)', fontSize:'11px'}}>{p.acquired_at || '—'}</td>
            <td style={{color:'var(--text-muted)', fontSize:'11px'}}>{p.closed_at}</td>
            <td>
              <button
                onClick={() => onDelete(p.id)}
                style={{
                  background:'none', border:'none', cursor:'pointer',
                  color:'var(--text-muted)', fontSize:'14px', padding:'2px 4px'
                }}
                title="Remove"
              >×</button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function AddClosedPositionForm({ onAdd }) {
  const empty = { symbol:'', name:'', broker:'robinhood', quantity:'', average_cost:'', close_price:'', acquired_at:'', closed_at: new Date().toISOString().slice(0,10) };
  const [form, setForm] = React.useState(empty);
  const [saving, setSaving] = React.useState(false);

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!form.symbol || !form.quantity || !form.average_cost || !form.close_price) return;
    setSaving(true);
    try {
      const res = await fetch('/api/closed-positions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: form.symbol.toUpperCase(),
          name: form.name,
          broker: form.broker,
          quantity: parseFloat(form.quantity),
          average_cost: parseFloat(form.average_cost),
          close_price: parseFloat(form.close_price),
          acquired_at: form.acquired_at || null,
          closed_at: form.closed_at,
        }),
      });
      if (res.ok) {
        setForm(empty);
        onAdd();
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} style={{padding:'14px 16px'}}>
      <div className="form-grid-2">
        <div className="form-row">
          <label className="form-label">Symbol *</label>
          <input className="form-input" placeholder="e.g. AAPL" value={form.symbol} onChange={e => set('symbol', e.target.value)} required />
        </div>
        <div className="form-row">
          <label className="form-label">Broker *</label>
          <select className="form-input" value={form.broker} onChange={e => set('broker', e.target.value)}>
            {MANUAL_BROKER_OPTIONS.map(option => (
              <option value={option.value} key={option.value}>{option.label}</option>
            ))}
          </select>
        </div>
      </div>
      <div className="form-row">
        <label className="form-label">Name (optional)</label>
        <input className="form-input" placeholder="Company name" value={form.name} onChange={e => set('name', e.target.value)} />
      </div>
      <div className="form-grid-2">
        <div className="form-row">
          <label className="form-label">Shares *</label>
          <input className="form-input" type="number" min="0" step="any" placeholder="0" value={form.quantity} onChange={e => set('quantity', e.target.value)} required />
        </div>
        <div className="form-row">
          <label className="form-label">Avg Cost *</label>
          <input className="form-input" type="number" min="0" step="any" placeholder="0.00" value={form.average_cost} onChange={e => set('average_cost', e.target.value)} required />
        </div>
      </div>
      <div className="form-row">
        <label className="form-label">Close Price *</label>
        <input className="form-input" type="number" min="0" step="any" placeholder="Price sold at" value={form.close_price} onChange={e => set('close_price', e.target.value)} required />
      </div>
      <div className="form-grid-2">
        <div className="form-row">
          <label className="form-label">Acquired</label>
          <input className="form-input" type="date" value={form.acquired_at} onChange={e => set('acquired_at', e.target.value)} />
        </div>
        <div className="form-row">
          <label className="form-label">Closed *</label>
          <input className="form-input" type="date" value={form.closed_at} onChange={e => set('closed_at', e.target.value)} required />
        </div>
      </div>
      <button type="submit" className="btn btn-primary" style={{width:'100%', marginTop:'4px'}} disabled={saving}>
        {saving ? 'Saving…' : 'Log Closed Position'}
      </button>
    </form>
  );
}

function AccountDataPanel({ onExportAll, onDeleteAccountData, onSelectTrustPage }) {
  return (
    <div className="account-data-panel">
      <div className="account-action-list">
        <button type="button" className="account-action" onClick={onExportAll}>
          <strong>Export all data</strong>
          <span>Download positions, lots, closed trades, history, and signals as JSON.</span>
        </button>
        <button type="button" className="account-action danger" onClick={onDeleteAccountData}>
          <strong>Delete account data</strong>
          <span>Remove WealthBrief app data for this account.</span>
        </button>
      </div>
      <div className="account-trust-links">
        <TrustLinks onSelectPage={onSelectTrustPage} />
        <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>
      </div>
    </div>
  );
}

function PnLTimeline({ closedPositions }) {
  const [view, setView] = React.useState('year');
  const [expanded, setExpanded] = React.useState(new Set());
  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  const yearMap = React.useMemo(() => {
    const map = {};
    closedPositions.forEach(p => {
      if (!p.closed_at) return;
      const year = p.closed_at.slice(0, 4);
      const ym = p.closed_at.slice(0, 7);
      if (!map[year]) map[year] = { net: 0, gains: 0, losses: 0, count: 0, months: {} };
      if (!map[year].months[ym]) map[year].months[ym] = { net: 0, gains: 0, losses: 0, count: 0 };
      const g = p.realized_gain;
      map[year].net += g; map[year].count += 1;
      if (g >= 0) map[year].gains += g; else map[year].losses += g;
      map[year].months[ym].net += g; map[year].months[ym].count += 1;
      if (g >= 0) map[year].months[ym].gains += g; else map[year].months[ym].losses += g;
    });
    return map;
  }, [closedPositions]);

  if (closedPositions.length === 0) return null;

  const years = Object.keys(yearMap).sort((a, b) => b - a);
  const allNet = closedPositions.reduce((s, p) => s + p.realized_gain, 0);
  const allGains = closedPositions.filter(p => p.realized_gain > 0).reduce((s, p) => s + p.realized_gain, 0);
  const allLosses = closedPositions.filter(p => p.realized_gain < 0).reduce((s, p) => s + p.realized_gain, 0);

  const allMonths = [];
  years.slice().reverse().forEach(yr => {
    Object.keys(yearMap[yr].months).sort().forEach(ym => {
      allMonths.push({ ym, ...yearMap[yr].months[ym] });
    });
  });
  const maxAbs = Math.max(...allMonths.map(m => Math.max(Math.abs(m.gains || 0), Math.abs(m.losses || 0), Math.abs(m.net))), 1);
  const HALF_H = 90;

  const toggleYear = (yr) => setExpanded(prev => {
    const next = new Set(prev);
    if (next.has(yr)) next.delete(yr); else next.add(yr);
    return next;
  });

  return (
    <div className="panel" style={{ marginTop: '20px' }}>
      <div className="panel-header">
        <span className="panel-title">Gain/Loss Timeline</span>
        <div className="tab-bar">
          <button className={`tab-btn${view === 'year' ? ' active' : ''}`} onClick={() => setView('year')}>By Year</button>
          <button className={`tab-btn${view === 'month' ? ' active' : ''}`} onClick={() => setView('month')}>By Month</button>
        </div>
      </div>

      {view === 'year' ? (
        <table>
          <thead>
            <tr>
              <th>Period</th>
              <th>Trades</th>
              <th>Gains</th>
              <th>Losses</th>
              <th>Net Gain/Loss</th>
            </tr>
          </thead>
          <tbody>
            {years.map(yr => (
              <React.Fragment key={yr}>
                <tr onClick={() => toggleYear(yr)} style={{ cursor: 'pointer' }}>
                  <td style={{ fontWeight: 600 }}>
                    <span style={{ marginRight: '8px', color: 'var(--text-muted)', fontSize: '10px', display: 'inline-block', width: '8px' }}>
                      {expanded.has(yr) ? '▼' : '▶'}
                    </span>
                    {yr}
                  </td>
                  <td style={{ color: 'var(--text-muted)' }}>{yearMap[yr].count}</td>
                  <td style={{ color: 'var(--accent-green)' }}>{formatMoney(yearMap[yr].gains)}</td>
                  <td style={{ color: 'var(--accent-red)' }}>{formatMoney(yearMap[yr].losses)}</td>
                  <td className={yearMap[yr].net >= 0 ? 'positive' : 'negative'}>{formatMoney(yearMap[yr].net)}</td>
                </tr>
                {expanded.has(yr) && Object.keys(yearMap[yr].months).sort().reverse().map(ym => {
                  const m = yearMap[yr].months[ym];
                  const monthIdx = parseInt(ym.slice(5, 7), 10) - 1;
                  return (
                    <tr key={ym} style={{ background: 'rgba(15,138,95,0.03)' }}>
                      <td style={{ paddingLeft: '32px', color: 'var(--text-secondary)', fontSize: '12px' }}>
                        {MONTHS[monthIdx]} {yr}
                      </td>
                      <td style={{ color: 'var(--text-muted)', fontSize: '12px' }}>{m.count}</td>
                      <td style={{ color: 'var(--accent-green)', fontSize: '12px' }}>{formatMoney(m.gains)}</td>
                      <td style={{ color: 'var(--accent-red)', fontSize: '12px' }}>{formatMoney(m.losses)}</td>
                      <td className={m.net >= 0 ? 'positive' : 'negative'} style={{ fontSize: '12px' }}>{formatMoney(m.net)}</td>
                    </tr>
                  );
                })}
              </React.Fragment>
            ))}
            <tr style={{ borderTop: '2px solid var(--border-accent)', background: 'rgba(47,111,159,0.045)' }}>
              <td style={{ fontWeight: 700 }}>All Time</td>
              <td style={{ fontWeight: 600, color: 'var(--text-muted)' }}>{closedPositions.length}</td>
              <td style={{ fontWeight: 600, color: 'var(--accent-green)' }}>{formatMoney(allGains)}</td>
              <td style={{ fontWeight: 600, color: 'var(--accent-red)' }}>{formatMoney(allLosses)}</td>
              <td style={{ fontWeight: 700 }} className={allNet >= 0 ? 'positive' : 'negative'}>{formatMoney(allNet)}</td>
            </tr>
          </tbody>
        </table>
      ) : (
        <div style={{ overflowX: 'auto', padding: '20px 16px 16px' }}>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            minWidth: `${allMonths.length * 52}px`,
            height: `${HALF_H * 2 + 32}px`,
            position: 'relative',
          }}>
            {/* Zero line */}
            <div style={{
              position: 'absolute', left: 0, right: 0,
              top: `${HALF_H}px`, height: '1px',
              background: 'var(--border-accent)', zIndex: 0,
            }} />
            {allMonths.map(({ ym, net, count }) => {
              const monthIdx = parseInt(ym.slice(5, 7), 10) - 1;
              const yr = ym.slice(0, 4);
              const label = `${MONTHS[monthIdx]} '${yr.slice(2)}`;
              const isPos = net >= 0;
              const barH = Math.max(2, Math.round((Math.abs(net) / maxAbs) * HALF_H));
              return (
                <div key={ym} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flex: '0 0 46px', position: 'relative', zIndex: 1 }}>
                  {/* Positive half — bars grow upward */}
                  <div style={{ height: `${HALF_H}px`, display: 'flex', alignItems: 'flex-end', justifyContent: 'center' }}>
                    {isPos && (
                      <div
                        style={{ width: '32px', height: `${barH}px`, background: 'var(--accent-green)', borderRadius: '3px 3px 0 0', opacity: 0.85 }}
                        title={`${label}: ${formatMoney(net)} (${count} trade${count !== 1 ? 's' : ''})`}
                      />
                    )}
                  </div>
                  {/* Negative half — bars grow downward */}
                  <div style={{ height: `${HALF_H}px`, display: 'flex', alignItems: 'flex-start', justifyContent: 'center' }}>
                    {!isPos && (
                      <div
                        style={{ width: '32px', height: `${barH}px`, background: 'var(--accent-red)', borderRadius: '0 0 3px 3px', opacity: 0.85 }}
                        title={`${label}: ${formatMoney(net)} (${count} trade${count !== 1 ? 's' : ''})`}
                      />
                    )}
                  </div>
                  <div style={{ fontSize: '9px', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)', textAlign: 'center', marginTop: '4px', width: '46px', overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis' }}>
                    {label}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function GoalProgressBar({ ytdRealized, unrealizedGain }) {
  const [goal, setGoal] = React.useState(150000);
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState('');

  const realized = ytdRealized || 0;
  const unrealized = unrealizedGain || 0;
  const total = realized + unrealized;
  const pct = goal > 0 ? Math.min((total / goal) * 100, 100) : 0;
  const realizedPct = goal > 0 ? Math.min((Math.max(realized, 0) / goal) * 100, 100) : 0;
  const unrealizedPct = goal > 0 ? Math.min((Math.max(unrealized, 0) / goal) * 100, 100 - realizedPct) : 0;
  const remaining = Math.max(goal - total, 0);

  const startEdit = () => { setDraft(goal.toString()); setEditing(true); };
  const commitEdit = () => {
    const v = parseFloat(draft.replace(/[,$]/g, ''));
    if (!isNaN(v) && v > 0) setGoal(v);
    setEditing(false);
  };

  return (
    <div className="goal-bar-panel">
      <div className="goal-bar-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
          <span className="goal-bar-title">2026 Gain Goal</span>
          {editing ? (
            <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--text-muted)' }}>$</span>
              <input
                className="goal-input"
                autoFocus
                value={draft}
                onChange={e => setDraft(e.target.value)}
                onBlur={commitEdit}
                onKeyDown={e => { if (e.key === 'Enter') commitEdit(); if (e.key === 'Escape') setEditing(false); }}
              />
            </span>
          ) : (
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--text-secondary)' }}>
              {formatMoney(goal)}
              <button className="goal-edit-btn" onClick={startEdit}>edit</button>
            </span>
          )}
        </div>
        <span className="goal-bar-pct" style={{ color: pct >= 100 ? 'var(--accent-green)' : 'var(--text-primary)' }}>
          {pct.toFixed(1)}%
        </span>
      </div>

      <div className="goal-bar-track">
        <div className="goal-bar-fill-realized" style={{ width: `${realizedPct}%`, borderRadius: unrealizedPct > 0 ? '6px 0 0 6px' : '6px' }} />
        <div className="goal-bar-fill-unrealized" style={{ left: `${realizedPct}%`, width: `${unrealizedPct}%`, borderRadius: realizedPct > 0 ? '0 6px 6px 0' : '6px' }} />
      </div>

      <div className="goal-bar-stats">
        <div className="goal-stat">
          <span className="goal-stat-label" style={{ color: 'var(--accent-green)' }}>● Realized (YTD)</span>
          <span className="goal-stat-value" style={{ color: realized >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' }}>{formatMoney(realized)}</span>
        </div>
        <div className="goal-stat">
          <span className="goal-stat-label" style={{ color: 'var(--accent-blue)' }}>● Unrealized</span>
          <span className="goal-stat-value" style={{ color: unrealized >= 0 ? 'var(--accent-blue)' : 'var(--accent-red)' }}>{formatMoney(unrealized)}</span>
        </div>
        <div className="goal-stat">
          <span className="goal-stat-label">Total Gain/Loss</span>
          <span className="goal-stat-value" style={{ color: total >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' }}>{formatMoney(total)}</span>
        </div>
        <div className="goal-stat">
          <span className="goal-stat-label">Remaining</span>
          <span className="goal-stat-value" style={{ color: remaining > 0 ? 'var(--text-secondary)' : 'var(--accent-green)' }}>
            {remaining > 0 ? formatMoney(remaining) : '🎯 Goal reached!'}
          </span>
        </div>
      </div>
    </div>
  );
}

function PortfolioHistoryChart({ currentValue, historySeed = null, snapshotsSeed = null, readOnly = false }) {
  const [history, setHistory] = React.useState(historySeed || []);
  const [snaps, setSnaps] = React.useState(snapshotsSeed || []);
  const [showForm, setShowForm] = React.useState(false);
  const [showEntries, setShowEntries] = React.useState(false);
  const [form, setForm] = React.useState({ date: '', total_value: '', label: '', is_estimate: true });
  const [saving, setSaving] = React.useState(false);
  const setF = (k, v) => setForm(f => ({ ...f, [k]: v }));

  const loadData = async () => {
    try {
      const [h, d] = await Promise.all([
        fetch('/api/portfolio-history').then(r => r.json()).catch(() => []),
        fetch('/api/snapshots/daily?days=500').then(r => r.json()).catch(() => []),
      ]);
      setHistory(Array.isArray(h) ? h : []);
      setSnaps(Array.isArray(d) ? d : []);
    } catch(e) { console.error('HistoryChart:', e); }
  };

  useEffect(() => {
    if (!readOnly) loadData();
  }, [readOnly]);

  const combined = React.useMemo(() => {
    const map = {};
    history.forEach(h => {
      map[h.date] = { date: h.date, value: h.total_value, label: h.label || '', isEstimate: !!h.is_estimate, source: 'history' };
    });
    snaps.forEach(s => {
      map[s.date] = { date: s.date, value: s.total_value, label: '', isEstimate: false, source: 'snap' };
    });
    if (currentValue > 0) {
      const today = new Date().toISOString().slice(0, 10);
      map[today] = { date: today, value: currentValue, label: 'live', isEstimate: false, source: 'live' };
    }
    return Object.values(map).sort((a, b) => a.date < b.date ? -1 : 1);
  }, [history, snaps, currentValue]);

  const SP500_YR = { 2022: -0.1811, 2023: 0.2424, 2024: 0.25, 2025: 0.179, 2026: 0.06 };
  const sp500Line = React.useMemo(() => {
    if (combined.length < 2) return [];
    const base = combined[0];
    return combined.map(pt => {
      const from = new Date(base.date + 'T12:00:00Z');
      const to = new Date(pt.date + 'T12:00:00Z');
      if (to <= from) return { date: pt.date, value: base.value };
      let mult = 1, cur = new Date(from);
      while (cur < to) {
        const yr = cur.getUTCFullYear();
        const rate = SP500_YR[yr] !== undefined ? SP500_YR[yr] : 0.10;
        const yrEnd = new Date(Date.UTC(yr + 1, 0, 1, 12));
        const segEnd = to < yrEnd ? to : yrEnd;
        const daysInYr = (Date.UTC(yr + 1, 0, 1) - Date.UTC(yr, 0, 1)) / 86400000;
        const daysInSeg = (segEnd - cur) / 86400000;
        mult *= Math.pow(1 + rate, daysInSeg / daysInYr);
        cur = new Date(segEnd);
      }
      return { date: pt.date, value: base.value * mult };
    });
  }, [combined]);

  const handleAdd = async (e) => {
    e.preventDefault();
    if (!form.date || !form.total_value) return;
    setSaving(true);
    try {
      await fetch('/api/portfolio-history', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date: form.date, total_value: parseFloat(form.total_value), label: form.label, is_estimate: form.is_estimate }),
      });
      setForm({ date: '', total_value: '', label: '', is_estimate: true });
      setShowForm(false);
      loadData();
    } finally { setSaving(false); }
  };

  const handleDelete = async (id) => {
    await fetch(`/api/portfolio-history/${id}`, { method: 'DELETE' });
    loadData();
  };

  const W = 900, H = 260;
  const PAD = { top: 24, right: 24, bottom: 40, left: 74 };
  const cW = W - PAD.left - PAD.right;
  const cH = H - PAD.top - PAD.bottom;

  const allVals = [...combined.map(p => p.value), ...sp500Line.map(p => p.value)].filter(v => v > 0);
  const minV = allVals.length ? Math.min(...allVals) * 0.95 : 0;
  const maxV = allVals.length ? Math.max(...allVals) * 1.05 : 1;
  const allTs = combined.map(p => new Date(p.date + 'T12:00:00Z').getTime());
  const minT = allTs.length ? Math.min(...allTs) : 0;
  const maxT = allTs.length ? Math.max(...allTs) : 1;
  const tSpan = maxT - minT || 1;
  const vSpan = maxV - minV || 1;

  const sx = t => PAD.left + ((t - minT) / tSpan) * cW;
  const sy = v => PAD.top + cH - ((v - minV) / vSpan) * cH;
  const ts = d => new Date(d + 'T12:00:00Z').getTime();

  const portPts = combined.map(p => `${sx(ts(p.date)).toFixed(1)},${sy(p.value).toFixed(1)}`).join(' ');
  const spPts   = sp500Line.map(p => `${sx(ts(p.date)).toFixed(1)},${sy(p.value).toFixed(1)}`).join(' ');

  const yTicks = [0, 0.25, 0.5, 0.75, 1.0].map(f => minV + vSpan * f);

  const yearSet = new Set();
  combined.forEach(p => yearSet.add(p.date.slice(0, 4)));
  const xLabels = [];
  yearSet.forEach(yr => {
    const t = Date.UTC(parseInt(yr), 0, 1, 12);
    if (t >= minT && t <= maxT) xLabels.push({ yr, x: sx(t) });
  });

  const annotations = history.map(h => ({
    x: sx(ts(h.date)), y: sy(h.total_value),
    label: h.label || h.date.slice(0, 7), value: h.total_value,
  }));

  let totalGainPct = null, sp500GainPct = null;
  if (combined.length >= 2) {
    totalGainPct = ((combined[combined.length-1].value - combined[0].value) / combined[0].value * 100).toFixed(1);
  }
  if (sp500Line.length >= 2) {
    sp500GainPct = ((sp500Line[sp500Line.length-1].value - sp500Line[0].value) / sp500Line[0].value * 100).toFixed(1);
  }

  const noData = combined.length < 2;
  const lastPt = combined.length > 0 ? combined[combined.length - 1] : null;

  return (
    <div className="hist-panel">
      <div className="panel-header">
        <span className="panel-title">Portfolio History</span>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
          {totalGainPct !== null && (
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: parseFloat(totalGainPct) >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' }}>
              Portfolio {parseFloat(totalGainPct) >= 0 ? '+' : ''}{totalGainPct}%
            </span>
          )}
          {sp500GainPct !== null && (
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--accent-amber)' }}>
              vs S&P {parseFloat(sp500GainPct) >= 0 ? '+' : ''}{sp500GainPct}%
            </span>
          )}
          {history.length > 0 && (
            <button className="tab-btn" onClick={() => setShowEntries(v => !v)} style={{ fontSize: '10px', padding: '3px 8px' }}>
              {showEntries ? 'Hide' : 'Entries'} ({history.length})
            </button>
          )}
          {!readOnly && (
            <button className="btn" style={{ fontSize: '11px', padding: '4px 10px' }} onClick={() => setShowForm(v => !v)}>
              {showForm ? 'Cancel' : '+ Add'}
            </button>
          )}
        </div>
      </div>

      {noData ? (
        <div style={{ padding: '40px 20px', textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--text-muted)' }}>
          Add historical data points to visualize your portfolio growth vs S&P 500
        </div>
      ) : (
        <svg viewBox={`0 0 ${W} ${H}`} className="hist-chart" style={{ height: '260px' }}>
          <defs>
            <linearGradient id="portGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#2f6f9f" stopOpacity="0.18" />
              <stop offset="100%" stopColor="#2f6f9f" stopOpacity="0.01" />
            </linearGradient>
          </defs>

          {/* Horizontal grid */}
          {yTicks.map((v, i) => (
            <line key={i} x1={PAD.left} y1={sy(v).toFixed(1)} x2={W-PAD.right} y2={sy(v).toFixed(1)}
              stroke="rgba(80,100,92,0.12)" strokeWidth="1" />
          ))}

          {/* Year markers */}
          {xLabels.map(({ yr, x }) => (
            <g key={yr}>
              <line x1={x.toFixed(1)} y1={PAD.top} x2={x.toFixed(1)} y2={H-PAD.bottom}
                stroke="rgba(80,100,92,0.16)" strokeWidth="1" strokeDasharray="3,3" />
              <text x={x.toFixed(1)} y={H-PAD.bottom+15} textAnchor="middle"
                fill="rgba(80,100,92,0.82)" fontFamily="JetBrains Mono,monospace" fontSize="10">{yr}</text>
            </g>
          ))}

          {/* Y-axis labels */}
          {yTicks.map((v, i) => (
            <text key={i} x={PAD.left-6} y={(sy(v)+4).toFixed(1)} textAnchor="end"
              fill="rgba(80,100,92,0.82)" fontFamily="JetBrains Mono,monospace" fontSize="10">
              {v >= 1e6 ? `$${(v/1e6).toFixed(1)}M` : `$${Math.round(v/1000)}K`}
            </text>
          ))}

          {/* S&P 500 reference line */}
          {sp500Line.length > 1 && (
            <polyline points={spPts} fill="none" stroke="rgba(185,130,23,0.66)" strokeWidth="1.5" strokeDasharray="5,3" />
          )}

          {/* Portfolio fill area */}
          {combined.length > 1 && (
            <polygon
              points={`${PAD.left},${H-PAD.bottom} ${portPts} ${(W-PAD.right).toFixed(1)},${(H-PAD.bottom).toFixed(1)}`}
              fill="url(#portGrad)"
            />
          )}

          {/* Portfolio line */}
          {combined.length > 1 && (
            <polyline points={portPts} fill="none" stroke="#2f6f9f" strokeWidth="2.2" strokeLinejoin="round" />
          )}

          {/* History entry dots */}
          {annotations.map((a, i) => (
            <circle key={i} cx={a.x.toFixed(1)} cy={a.y.toFixed(1)} r="4"
              fill="var(--bg-card)" stroke="#2f6f9f" strokeWidth="2" />
          ))}

          {/* Live dot with pulse ring */}
          {lastPt && (() => {
            const lx = sx(ts(lastPt.date)), ly = sy(lastPt.value);
            return (
              <g>
                <circle cx={lx.toFixed(1)} cy={ly.toFixed(1)} r="9" fill="none" stroke="#2f6f9f" strokeWidth="1" opacity="0.28" />
                <circle cx={lx.toFixed(1)} cy={ly.toFixed(1)} r="5" fill="#2f6f9f" />
              </g>
            );
          })()}

          {/* Legend */}
          <g transform={`translate(${PAD.left+8},${PAD.top+7})`}>
            <line x1="0" y1="6" x2="18" y2="6" stroke="#2f6f9f" strokeWidth="2.2" />
            <text x="22" y="10" fill="rgba(80,100,92,0.9)" fontFamily="JetBrains Mono,monospace" fontSize="10">Portfolio</text>
            <line x1="84" y1="6" x2="102" y2="6" stroke="rgba(185,130,23,0.76)" strokeWidth="1.5" strokeDasharray="5,3" />
            <text x="106" y="10" fill="rgba(80,100,92,0.9)" fontFamily="JetBrains Mono,monospace" fontSize="10">S&P 500 (est.)</text>
          </g>
        </svg>
      )}

      {/* Manual entries list */}
      {showEntries && history.length > 0 && (
        <div className="hist-entry-list">
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '6px' }}>
            Manual Entries
          </div>
          {history.map(h => (
            <div key={h.id} className="hist-entry-item">
              <span style={{ color: 'var(--text-muted)', minWidth: '90px' }}>{h.date}</span>
              <span style={{ color: 'var(--accent-blue)', fontWeight: 600 }}>{formatMoney(h.total_value)}</span>
              {h.label && <span style={{ color: 'var(--text-secondary)' }}>{h.label}</span>}
              <span style={{ color: 'var(--text-muted)', fontSize: '9px', textTransform: 'uppercase' }}>
                {h.is_estimate ? 'est.' : 'actual'}
              </span>
              {!readOnly && (
                <button onClick={() => handleDelete(h.id)}
                  style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', fontSize: '13px', padding: '0 2px', marginLeft: 'auto' }}>×</button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Add entry form */}
      {showForm && !readOnly && (
        <form onSubmit={handleAdd} className="hist-form-row">
          <div className="form-row" style={{ flex: '0 0 140px', marginBottom: 0 }}>
            <label className="form-label">Date *</label>
            <input className="form-input" type="date" value={form.date} onChange={e => setF('date', e.target.value)} required />
          </div>
          <div className="form-row" style={{ flex: '0 0 160px', marginBottom: 0 }}>
            <label className="form-label">Total Value ($) *</label>
            <input className="form-input" type="number" min="0" step="any" placeholder="e.g. 380000"
              value={form.total_value} onChange={e => setF('total_value', e.target.value)} required />
          </div>
          <div className="form-row" style={{ flex: '0 0 140px', marginBottom: 0 }}>
            <label className="form-label">Label</label>
            <input className="form-input" placeholder="e.g. Dec 2024" value={form.label} onChange={e => setF('label', e.target.value)} />
          </div>
          <div className="form-row" style={{ flex: '0 0 100px', marginBottom: 0 }}>
            <label className="form-label">Type</label>
            <select className="form-input" value={form.is_estimate ? 'est' : 'actual'} onChange={e => setF('is_estimate', e.target.value === 'est')}>
              <option value="est">Estimate</option>
              <option value="actual">Actual</option>
            </select>
          </div>
          <button type="submit" className="btn btn-primary" style={{ alignSelf: 'flex-end', padding: '6px 14px', fontSize: '12px' }} disabled={saving}>
            {saving ? '...' : 'Save'}
          </button>
        </form>
      )}
    </div>
  );
}

// ── Signal type metadata ──────────────────────────────────────
const SIG_TYPE_LABEL = { technical:'TECH', options_flow:'OPT', insider:'INSID', sentiment:'SENT', custom:'CUST' };
const SIG_TYPE_COLOR = {
  technical: 'var(--accent-blue)',
  options_flow: 'var(--accent-amber)',
  insider: '#c084fc',
  sentiment: '#fb7185',
  custom: 'var(--text-secondary)',
};
function dirColor(d) {
  return d === 'bullish' ? 'var(--accent-green)' : d === 'bearish' ? 'var(--accent-red)' : 'var(--text-secondary)';
}
function dirBg(d) {
  return d === 'bullish' ? 'rgba(15,138,95,0.12)' : d === 'bearish' ? 'rgba(194,65,59,0.12)' : 'rgba(80,100,92,0.08)';
}
const DIR_ARROW = { bullish: '▲', bearish: '▼', neutral: '─' };

// ── SignalCard ────────────────────────────────────────────────
function SignalCard({ summary, showIndicators, onToggleIndicators, onRemove }) {
  const { symbol, signals = [], indicators = {}, composite_score = 0, direction = 'neutral', signal_count = 0 } = summary;
  const dc = dirColor(direction);
  const db = dirBg(direction);

  return (
    <div className="signal-card">
      {/* Header */}
      <div className="signal-card-header">
        <div style={{ display:'flex', alignItems:'center', gap:'8px', marginBottom:'8px' }}>
          <span style={{ fontFamily:'var(--font-mono)', fontSize:'15px', fontWeight:700, color:'var(--text-primary)' }}>{symbol}</span>
          <span style={{ fontSize:'10px', fontWeight:700, padding:'2px 8px', borderRadius:'4px', fontFamily:'var(--font-mono)', letterSpacing:'0.5px', background:db, color:dc }}>
            {DIR_ARROW[direction]} {direction.toUpperCase()}
          </span>
          <span style={{ fontSize:'10px', color:'var(--text-muted)', fontFamily:'var(--font-mono)', marginLeft:'auto' }}>
            {signal_count} signal{signal_count !== 1 ? 's' : ''}
          </span>
          {onRemove && (
            <button onClick={onRemove} title={`Remove ${symbol}`}
              style={{ marginLeft:'6px', background:'none', border:'none', cursor:'pointer', color:'var(--text-muted)',
                fontSize:'14px', lineHeight:1, padding:'0 2px', borderRadius:'3px', flexShrink:0,
                transition:'color 0.15s' }}
              onMouseEnter={e => e.currentTarget.style.color='var(--accent-red)'}
              onMouseLeave={e => e.currentTarget.style.color='var(--text-muted)'}
            >×</button>
          )}
        </div>
        {/* Score bar */}
        <div style={{ display:'flex', alignItems:'center', gap:'8px' }}>
          <span style={{ fontSize:'9px', fontFamily:'var(--font-mono)', color:'var(--text-muted)', minWidth:'20px' }}>−10</span>
          <div style={{ flex:1, height:'4px', background:'rgba(123,140,132,0.16)', borderRadius:'2px', position:'relative' }}>
            <div style={{ position:'absolute', left:'50%', top:'-2px', width:'1px', height:'8px', background:'rgba(80,100,92,0.22)' }} />
            <div style={{
              position:'absolute',
              left: composite_score >= 0 ? '50%' : `${((composite_score + 10) / 20) * 100}%`,
              width:`${(Math.abs(composite_score) / 20) * 100}%`,
              height:'100%', borderRadius:'2px',
              background: composite_score >= 3 ? 'var(--accent-green)' : composite_score <= -3 ? 'var(--accent-red)' : 'rgba(80,100,92,0.44)',
              transition:'width 0.3s ease',
            }} />
          </div>
          <span style={{ fontSize:'9px', fontFamily:'var(--font-mono)', color:'var(--text-muted)', minWidth:'20px', textAlign:'right' }}>+10</span>
          <span style={{ fontSize:'13px', fontFamily:'var(--font-mono)', fontWeight:700, color: composite_score >= 3 ? 'var(--accent-green)' : composite_score <= -3 ? 'var(--accent-red)' : 'var(--text-secondary)', minWidth:'38px', textAlign:'right' }}>
            {composite_score >= 0 ? '+' : ''}{composite_score.toFixed(1)}
          </span>
        </div>
      </div>

      {/* Signal rows */}
      {signals.length === 0 ? (
        <div style={{ padding:'16px', fontFamily:'var(--font-mono)', fontSize:'11px', color:'var(--text-muted)', textAlign:'center' }}>
          No signals fired
        </div>
      ) : (
        <div>
          {signals.map((sig, i) => {
            const sc = dirColor(sig.direction);
            return (
              <div key={i} className="signal-row">
                {/* Direction + type */}
                <div style={{ display:'flex', flexDirection:'column', alignItems:'center', gap:'4px', paddingTop:'1px', minWidth:'36px' }}>
                  <span style={{ fontSize:'12px', color:sc, fontWeight:700, lineHeight:1 }}>{DIR_ARROW[sig.direction]}</span>
                  <span style={{ fontSize:'8px', fontFamily:'var(--font-mono)', fontWeight:700, color:SIG_TYPE_COLOR[sig.signal_type], letterSpacing:'0.3px' }}>
                    {SIG_TYPE_LABEL[sig.signal_type]}
                  </span>
                </div>
                {/* Name + description */}
                <div style={{ flex:1, minWidth:0 }}>
                  <div style={{ fontFamily:'var(--font-mono)', fontSize:'11px', fontWeight:600, color:'var(--text-primary)', marginBottom:'3px' }}>{sig.name}</div>
                  <div style={{ fontSize:'11px', color:'var(--text-secondary)', lineHeight:1.45 }}>{sig.description}</div>
                </div>
                {/* Conviction dots */}
                <div style={{ display:'flex', gap:'3px', alignItems:'center', paddingTop:'2px', flexShrink:0 }}>
                  {[1,2,3,4,5].map(n => (
                    <div key={n} style={{ width:'6px', height:'6px', borderRadius:'50%', background: n <= sig.conviction ? sc : 'rgba(123,140,132,0.18)' }} />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Indicators section */}
      {Object.keys(indicators).length > 0 && (
        <>
          <button className="signal-indicators-toggle" onClick={onToggleIndicators}>
            <span>Indicators</span>
            <span>{showIndicators ? '▲' : '▼'}</span>
          </button>
          {showIndicators && (
            <div className="signal-indicators-grid">
              {[
                { label:'Price',  val:indicators.price,    fmt: v => `$${v.toFixed(2)}` },
                { label:'RSI',    val:indicators.rsi,      fmt: v => v.toFixed(1),
                  color: indicators.rsi < 30 ? 'var(--accent-green)' : indicators.rsi > 70 ? 'var(--accent-red)' : undefined },
                { label:'SMA 20', val:indicators.sma20,    fmt: v => `$${v.toFixed(2)}` },
                { label:'SMA 50', val:indicators.sma50,    fmt: v => `$${v.toFixed(2)}` },
                { label:'SMA 200',val:indicators.sma200,   fmt: v => `$${v.toFixed(2)}` },
                { label:'MACD Δ', val:indicators.macd_hist, fmt: v => (v >= 0 ? '+' : '') + v.toFixed(3),
                  color: indicators.macd_hist >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' },
                { label:'BB Up',  val:indicators.bb_upper,  fmt: v => `$${v.toFixed(2)}` },
                { label:'BB Lo',  val:indicators.bb_lower,  fmt: v => `$${v.toFixed(2)}` },
                { label:'ATR',    val:indicators.atr,       fmt: v => v.toFixed(2) },
              ].filter(x => x.val != null).map(({ label, val, fmt, color }) => (
                <div key={label} className="ind-item">
                  <span className="ind-label">{label}</span>
                  <span className="ind-value" style={color ? { color } : {}}>{fmt(val)}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── SignalsView ───────────────────────────────────────────────
function SignalsView({ portfolioSymbols = [] }) {
  const [results, setResults]             = React.useState({});
  const [watchlist, setWatchlist]         = React.useState('');
  const [scanning, setScanning]           = React.useState(false);
  const [scanTarget, setScanTarget]       = React.useState(null); // 'portfolio' | 'watchlist'
  const [sortBy, setSortBy]               = React.useState('score');
  const [filterDir, setFilterDir]         = React.useState('all');
  const [expandedSet, setExpandedSet]     = React.useState(new Set());
  const [lastScan, setLastScan]           = React.useState(null);
  const [scanError, setScanError]         = React.useState(null);
  // Load any cached results on mount
  React.useEffect(() => {
    fetch(`${API}/api/signals`)
      .then(r => r.json())
      .then(data => { if (Object.keys(data).length) setResults(data); })
      .catch(() => {});
  }, []);

  const runScan = async (url, body) => {
    setScanning(true); setScanError(null);
    try {
      const opts = { method:'POST' };
      if (body) { opts.headers = { 'Content-Type':'application/json' }; opts.body = JSON.stringify(body); }
      const res = await fetch(`${API}${url}`, opts);
      const data = await res.json();
      if (data.error) { setScanError(data.error); return; }
      setResults(prev => ({ ...prev, ...data }));
      setLastScan(new Date());
    } catch (e) {
      setScanError('Scan failed — check backend logs');
    } finally {
      setScanning(false); setScanTarget(null);
    }
  };

  const scanPortfolio = () => { setScanTarget('portfolio'); runScan('/api/signals/scan-portfolio'); };

  const scanWatchlist = () => {
    const syms = watchlist.split(/[\s,]+/).map(s => s.trim().toUpperCase()).filter(Boolean);
    if (!syms.length) return;
    setScanTarget('watchlist');
    runScan('/api/signals/scan', { symbols: syms });
  };

  const removeSymbol = sym =>
    setResults(prev => { const copy = { ...prev }; delete copy[sym]; return copy; });

  const toggleIndicators = sym => setExpandedSet(prev => {
    const next = new Set(prev);
    next.has(sym) ? next.delete(sym) : next.add(sym);
    return next;
  });

  // Filter + sort
  let entries = Object.entries(results);
  if (filterDir !== 'all') entries = entries.filter(([, s]) => s.direction === filterDir);
  if (sortBy === 'score') entries.sort((a, b) => b[1].composite_score - a[1].composite_score);
  else entries.sort((a, b) => a[0].localeCompare(b[0]));

  const bullCount = Object.values(results).filter(s => s.direction === 'bullish').length;
  const bearCount = Object.values(results).filter(s => s.direction === 'bearish').length;
  const neutCount = Object.values(results).filter(s => s.direction === 'neutral').length;

  return (
    <div>
      {/* Toolbar */}
      <div className="signals-toolbar">
        <button className="btn btn-primary" disabled={scanning} onClick={scanPortfolio}
          style={{ fontSize:'11px', padding:'6px 14px', flexShrink:0 }}>
          {scanning && scanTarget === 'portfolio'
            ? <span className="scan-progress">● Scanning portfolio…</span>
            : '⟳ Scan Portfolio'}
        </button>

        <div style={{ display:'flex', gap:'6px', alignItems:'center', flex:1, minWidth:0 }}>
          <input className="form-input" placeholder="Watchlist: MSFT, NVDA, SPY…" value={watchlist}
            onChange={e => setWatchlist(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && scanWatchlist()}
            style={{ maxWidth:'260px', fontSize:'12px' }} />
          <button className="btn" disabled={scanning || !watchlist.trim()} onClick={scanWatchlist}
            style={{ fontSize:'11px', padding:'6px 12px', flexShrink:0 }}>
            {scanning && scanTarget === 'watchlist' ? <span className="scan-progress">…</span> : 'Scan'}
          </button>
        </div>

        <div style={{ display:'flex', gap:'8px', alignItems:'center', marginLeft:'auto', flexShrink:0 }}>
          {lastScan && (
            <span style={{ fontSize:'10px', color:'var(--text-muted)', fontFamily:'var(--font-mono)' }}>
              {lastScan.toLocaleTimeString()}
            </span>
          )}
          <select className="form-input" value={sortBy} onChange={e => setSortBy(e.target.value)}
            style={{ width:'auto', fontSize:'11px', padding:'4px 8px' }}>
            <option value="score">Sort: Score</option>
            <option value="symbol">Sort: Symbol</option>
          </select>
          <div style={{ display:'flex', gap:'3px' }}>
            {['all','bullish','bearish'].map(d => (
              <button key={d} className={`tab-btn${filterDir === d ? ' active' : ''}`}
                onClick={() => setFilterDir(d)}
                style={{ fontSize:'10px', padding:'4px 9px', textTransform:'capitalize' }}>
                {d}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Summary bar */}
      {Object.keys(results).length > 0 && (
        <div style={{ padding:'10px 16px', borderBottom:'1px solid var(--border)', display:'flex', gap:'24px', alignItems:'center' }}>
          <span style={{ fontSize:'10px', fontFamily:'var(--font-mono)', color:'var(--text-muted)' }}>
            {Object.keys(results).length} symbol{Object.keys(results).length !== 1 ? 's' : ''} scanned
          </span>
          <span style={{ fontSize:'11px', fontFamily:'var(--font-mono)', color:'var(--accent-green)', fontWeight:600 }}>▲ {bullCount} bullish</span>
          <span style={{ fontSize:'11px', fontFamily:'var(--font-mono)', color:'var(--accent-red)', fontWeight:600 }}>▼ {bearCount} bearish</span>
          <span style={{ fontSize:'11px', fontFamily:'var(--font-mono)', color:'var(--text-muted)' }}>─ {neutCount} neutral</span>
        </div>
      )}

      {/* Error */}
      {scanError && (
        <div style={{ margin:'12px 16px', padding:'10px 14px', background:'var(--accent-red-bg)', border:'1px solid var(--accent-red)', borderRadius:'6px', fontFamily:'var(--font-mono)', fontSize:'12px', color:'var(--accent-red)' }}>
          {scanError}
        </div>
      )}

      {/* Cards grid or empty state */}
      {entries.length === 0 ? (
        <div className="empty-state">
          <h3>No signals yet</h3>
          <p>Scan your portfolio or enter tickers in the watchlist above</p>
        </div>
      ) : (
        <div className="signals-grid">
          {entries.map(([sym, summary]) => (
            <SignalCard key={sym} summary={summary}
              showIndicators={expandedSet.has(sym)}
              onToggleIndicators={() => toggleIndicators(sym)}
              onRemove={() => removeSymbol(sym)} />
          ))}
        </div>
      )}
    </div>
  );
}

function App({ authUser, onSignOut, demoMode = false, demoData = null, onExitDemo = null, onSignIn = null, onViewDemo = null, onSelectTrustPage = () => {} }) {
  const [portfolio, setPortfolio] = useState(demoMode ? demoData.portfolio : null);
  const [closedPositions, setClosedPositions] = useState(demoMode ? demoData.closedPositions : []);
  const [taxLots, setTaxLots] = useState(demoMode ? demoData.taxLots : {});        // key: "SYMBOL-broker" → lot[]
  const [priceHistory, setPriceHistory] = useState(demoMode ? demoData.priceHistory : {});  // symbol → close[] oldest→newest
  const [selectedPosition, setSelectedPosition] = useState(null);
  const [loading, setLoading] = useState(!demoMode);
  const [toast, setToast] = useState(null);
  const [activeTab, setActiveTab] = useState('active');
  const [dateRange, setDateRange] = useState('1M');
  const [refreshState, setRefreshState] = useState({
    active: false,
    source: null,
    error: null,
    lastCompletedAt: null,
  });
  const autoRefreshStarted = useRef(false);
  const positions = portfolio?.positions || [];
  const hasData = positions.length > 0;
  const hasPricedPositions = positions.some(p => p.asset_type !== 'cash');
  const hasAnyData = hasData || closedPositions.length > 0;

  useEffect(() => {
    trackAnalyticsEvent(demoMode ? 'demo_portfolio_view' : 'dashboard_view');
  }, [demoMode]);

  const fetchPortfolio = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/portfolio`);
      const data = await res.json();
      setPortfolio(data);
    } catch (err) {
      console.error('Fetch failed:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchClosed = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/closed-positions`);
      setClosedPositions(await res.json());
    } catch (err) {
      console.error('Closed positions fetch failed:', err);
    }
  }, []);

  const fetchTaxLots = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/tax-lots`);
      const all = await res.json();
      // Group by "SYMBOL-broker"
      const grouped = {};
      all.forEach(lot => {
        const k = `${lot.symbol}-${lot.broker}`;
        if (!grouped[k]) grouped[k] = [];
        grouped[k].push(lot);
      });
      setTaxLots(grouped);
    } catch (err) {
      console.error('Tax lots fetch failed:', err);
    }
  }, []);

  const fetchPriceHistory = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/price-history`);
      setPriceHistory(await res.json());
    } catch (err) {
      console.error('Price history fetch failed:', err);
    }
  }, []);

  useEffect(() => {
    if (demoMode) return;
    fetchPortfolio();
    fetchClosed();
    fetchTaxLots();
    fetchPriceHistory();
  }, [demoMode, fetchPortfolio, fetchClosed, fetchTaxLots, fetchPriceHistory]);

  const refreshPortfolio = useCallback(async ({ source = 'manual', notify = false } = {}) => {
    if (demoMode) {
      if (notify) setToast({ message: 'Demo data is already loaded', type: 'success' });
      return;
    }
    setLoading(true);
    setRefreshState(prev => ({ ...prev, active: true, source, error: null }));
    try {
      await fetch(`${API}/api/refresh`, { method: 'POST' });
      await fetchPortfolio();
      await fetchPriceHistory();
      setRefreshState({
        active: false,
        source,
        error: null,
        lastCompletedAt: new Date().toISOString(),
      });
      if (notify) setToast({ message: 'Portfolio refreshed', type: 'success' });
    } catch {
      setRefreshState(prev => ({
        ...prev,
        active: false,
        source,
        error: 'Price refresh failed',
      }));
      if (notify) setToast({ message: 'Refresh failed', type: 'error' });
    } finally {
      setLoading(false);
    }
  }, [demoMode, fetchPortfolio, fetchPriceHistory]);

  useEffect(() => {
    if (demoMode || !hasPricedPositions) return undefined;

    if (!autoRefreshStarted.current) {
      autoRefreshStarted.current = true;
      refreshPortfolio({ source: 'auto' });
    }

    const id = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        refreshPortfolio({ source: 'auto' });
      }
    }, AUTO_REFRESH_INTERVAL_MS);

    return () => window.clearInterval(id);
  }, [demoMode, hasPricedPositions, refreshPortfolio]);

  // Scroll Tax Lots panel into view when a position is selected
  useEffect(() => {
    if (selectedPosition) {
      const el = document.getElementById('tax-lots-panel');
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [selectedPosition]);

  const handleRefresh = async () => {
    refreshPortfolio({ source: 'manual', notify: true });
  };

  const handleUpload = async (formData, broker) => {
    if (demoMode) {
      trackAnalyticsEvent('csv_import_blocked', { broker, reason: 'demo_mode' });
      setToast({ message: 'Sign in to import your own CSV', type: 'success' });
      return;
    }
    trackAnalyticsEvent('csv_import_start', { broker });
    try {
      const res = await fetch(`${API}/api/import/csv?broker=${broker}`, {
        method: 'POST', body: formData,
      });
      const data = await res.json();
      if (res.ok) {
        trackAnalyticsEvent('csv_import_success', {
          broker,
          imported: data.imported || 0,
        });
        setToast({ message: `Imported ${data.imported} positions`, type: 'success' });
        fetchPortfolio();
      } else {
        trackAnalyticsEvent('csv_import_failure', { broker, status: res.status });
        setToast({ message: data.detail || 'Import failed', type: 'error' });
      }
    } catch {
      trackAnalyticsEvent('csv_import_failure', { broker, reason: 'network_error' });
      setToast({ message: 'Upload failed', type: 'error' });
    }
  };

  const handleExport = () => {
    if (demoMode) {
      setToast({ message: 'Sign in to export your own records', type: 'success' });
      return;
    }
    window.open(`${API}/api/export/csv`, '_blank');
  };

  const handleExportAll = () => {
    if (demoMode) {
      setToast({ message: 'Sign in to export your own records', type: 'success' });
      return;
    }
    window.open(`${API}/api/export/all`, '_blank');
  };

  const handleDeleteAccountData = async () => {
    if (demoMode) {
      setToast({ message: 'Demo data is read-only', type: 'success' });
      return;
    }
    const confirmed = window.confirm(
      'Delete all WealthBrief app data for this account? This removes positions, tax lots, closed trades, history, and signals. This cannot be undone.'
    );
    if (!confirmed) return;
    try {
      const res = await fetch(`${API}/api/account/data`, { method: 'DELETE' });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        setToast({ message: err.detail || 'Delete failed', type: 'error' });
        return;
      }
      setPortfolio({ positions: [], total_value: 0, total_cost: 0, total_gain: 0, total_gain_pct: 0, broker_breakdown: {} });
      setClosedPositions([]);
      setTaxLots({});
      setPriceHistory({});
      setSelectedPosition(null);
      setToast({ message: 'Account data deleted', type: 'success' });
    } catch {
      setToast({ message: 'Delete failed', type: 'error' });
    }
  };

  const handleDeleteClosed = async (id) => {
    if (demoMode) {
      setToast({ message: 'Demo trades are read-only', type: 'success' });
      return;
    }
    await fetch(`${API}/api/closed-positions/${id}`, { method: 'DELETE' });
    fetchClosed();
  };

  const handleAddActive = async (position) => {
    if (demoMode) {
      trackAnalyticsEvent('manual_position_add_blocked', { reason: 'demo_mode' });
      setToast({ message: 'Sign in to add real positions', type: 'success' });
      return;
    }
    await fetchPortfolio();
    await fetchPriceHistory();
    setActiveTab('active');
    setToast({ message: `Added ${position.symbol}`, type: 'success' });
  };

  const totalCash = positions.filter(p => p.asset_type === 'cash').reduce((s, p) => s + p.market_value, 0);

  const latestPriceIso = (() => {
    const timestamps = positions
      .filter(p => p.asset_type !== 'cash' && p.updated_at)
      .map(p => p.updated_at)
      .map(value => ({ value, time: new Date(value).getTime() }))
      .filter(item => !isNaN(item.time));
    if (!timestamps.length) return null;
    timestamps.sort((a, b) => b.time - a.time);
    return timestamps[0].value;
  })();
  const pricesAsOf = formatPriceTimestamp(latestPriceIso);
  const lastRefreshCompleted = formatPriceTimestamp(refreshState.lastCompletedAt);
  const refreshStatusText = demoMode
    ? 'Sample prices'
    : refreshState.active
      ? 'Auto-refreshing prices...'
      : refreshState.error
        ? `Auto-refresh failed${pricesAsOf ? `, latest shown ${pricesAsOf}` : ''}`
        : pricesAsOf
          ? `Auto-refresh on, latest prices ${pricesAsOf}`
          : lastRefreshCompleted
            ? `Last refresh ${lastRefreshCompleted}`
            : 'Prices update automatically every 5 minutes';
  const totalRealized = closedPositions.reduce((s, p) => s + p.realized_gain, 0);
  const currentYear = new Date().getFullYear().toString();
  const ytdRealized = closedPositions
    .filter(p => p.closed_at?.startsWith(currentYear))
    .reduce((s, p) => s + p.realized_gain, 0);
  const showPortfolioOverview = activeTab !== 'signals' && (hasAnyData || demoMode);

  return (
    <div className="app-container">
      {/* Header */}
      <div className="header">
        <div className="header-left">
          <h1><span>▸</span> {demoMode ? 'WealthBrief Demo' : 'WealthBrief'}</h1>
          <div className="subtitle">
            {hasData
              ? `${positions.length} positions across ${Object.keys(portfolio.broker_breakdown || {}).length} broker(s)`
              : 'No positions loaded'
            }
            <span className={`prices-as-of${refreshState.active ? ' is-refreshing' : ''}${refreshState.error ? ' is-error' : ''}`}>
              {' · '}{refreshStatusText}
            </span>
          </div>
        </div>
        <div className="header-actions">
          {demoMode ? (
            <>
              <button className="btn" onClick={onExitDemo}>{authUser ? 'Back to portfolio' : 'Back to site'}</button>
              <button className="btn btn-primary" onClick={onSignIn}>Use my portfolio</button>
            </>
          ) : (
            <>
              {authUser && <button className="btn" onClick={onSignOut}>Sign out</button>}
              <button className="btn" onClick={handleExport}>Export CSV</button>
              <button className="btn btn-primary" onClick={handleRefresh}>
                {loading ? '...' : 'Refresh'}
              </button>
            </>
          )}
        </div>
      </div>

      {/* Stats — hidden when Signals tab is active */}
      {showPortfolioOverview && <div className="stats-grid">
        <StatCard
          label="Total Value"
          value={formatMoney(portfolio?.total_value)}
        />
        <StatCard
          label="Unrealized Gain/Loss"
          value={formatMoney(portfolio?.total_gain)}
          subClass={portfolio?.total_gain >= 0 ? 'positive' : 'negative'}
          sub={formatPct(portfolio?.total_gain_pct)}
        />
        <StatCard
          label="Realized Gain/Loss"
          value={formatMoney(totalRealized)}
          subClass={ytdRealized >= 0 ? 'positive' : 'negative'}
          sub={`${formatMoney(ytdRealized)} YTD`}
        />
        <StatCard
          label="Cash"
          value={formatMoney(totalCash)}
          sub={`${positions.filter(p => p.asset_type !== 'cash').length} active positions`}
        />
      </div>}

      {/* Goal Progress Bar — hidden when Signals tab is active */}
      {showPortfolioOverview && <GoalProgressBar ytdRealized={ytdRealized} unrealizedGain={portfolio?.total_gain} />}

      {/* Portfolio trend — hidden when Signals tab is active */}
      {showPortfolioOverview && <OverallTrend30D positions={positions} priceHistory={priceHistory} dateRange={dateRange} onRangeChange={setDateRange} />}

      {/* Main content */}
      <div className={`content-grid${activeTab === 'signals' ? ' signals-full' : ''}`}>
        {/* Positions panel with tabs */}
        <div className="panel">
          <div className="panel-header">
            <div className="tab-bar">
              <button
                className={`tab-btn${activeTab === 'active' ? ' active' : ''}`}
                onClick={() => setActiveTab('active')}
              >
                Active ({positions.filter(p => p.asset_type !== 'cash').length})
              </button>
              <button
                className={`tab-btn${activeTab === 'closed' ? ' active' : ''}`}
                onClick={() => setActiveTab('closed')}
              >
                Closed ({closedPositions.length})
              </button>
              {!demoMode && (
                <button
                  className={`tab-btn${activeTab === 'signals' ? ' active' : ''}`}
                  onClick={() => { setActiveTab('signals'); window.scrollTo({ top: 0, behavior: 'instant' }); }}
                  style={{ color: activeTab === 'signals' ? 'var(--accent-blue)' : undefined }}
                >
                  ◈ Signals
                </button>
              )}
            </div>
          </div>
          {activeTab === 'active' ? (
            hasData ? (
              <PositionsTable
                positions={positions}
                taxLots={taxLots}
                selectedKey={selectedPosition ? `${selectedPosition.symbol}-${selectedPosition.broker}` : null}
                onSelectPosition={setSelectedPosition}
                priceHistory={priceHistory}
                dateRange={dateRange}
              />
            ) : !hasAnyData ? (
              <OnboardingPanel
                onUpload={handleUpload}
                onAdd={handleAddActive}
                onViewDemo={onViewDemo}
              />
            ) : (
              <div className="empty-state">
                <h3>No active positions</h3>
                <p>Add a current holding manually or import a CSV when you are ready.</p>
              </div>
            )
          ) : activeTab === 'closed' ? (
            <ClosedPositionsTable positions={closedPositions} onDelete={handleDeleteClosed} />
          ) : (
            <SignalsView
              portfolioSymbols={positions.filter(p => p.asset_type !== 'cash').map(p => p.symbol)}
            />
          )}
        </div>

        {/* Sidebar — hidden when signals tab is active */}
        {activeTab !== 'signals' && <div className="sidebar-panels">
          {/* Tax lots — always first so clicking a row scrolls straight to it */}
          <div className="panel" id="tax-lots-panel">
            <div className="panel-header">
              <span className="panel-title">Tax Lots</span>
              {selectedPosition && (
                <button
                  onClick={() => setSelectedPosition(null)}
                  style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', fontSize: '16px', lineHeight: 1 }}
                >×</button>
              )}
            </div>
            {selectedPosition ? (
              demoMode ? (
                <DemoTaxLotsPanel
                  position={selectedPosition}
                  lots={taxLots[`${selectedPosition.symbol}-${selectedPosition.broker}`] || []}
                  onSignIn={onSignIn}
                />
              ) : (
                <TaxLotsPanel
                  position={selectedPosition}
                  lots={taxLots[`${selectedPosition.symbol}-${selectedPosition.broker}`] || []}
                  onRefresh={() => { fetchTaxLots(); fetchPortfolio(); fetchClosed(); }}
                />
              )
            ) : (
              <div style={{ padding: '20px 16px', fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--text-muted)', textAlign: 'center' }}>
                {demoMode ? 'Click a sample holding to inspect its tax lots' : 'Click a position to manage its tax lots'}
              </div>
            )}
          </div>

          {hasData && (
            <div className="panel">
              <div className="panel-header">
                <span className="panel-title">Tax Wedge</span>
              </div>
              <TaxWedgePanel
                positions={positions}
                taxLots={taxLots}
                closedPositions={closedPositions}
                onSelectPosition={setSelectedPosition}
                onSignIn={onSignIn}
                demoMode={demoMode}
              />
            </div>
          )}

          {/* Broker breakdown */}
          {hasData && (
            <div className="panel">
              <div className="panel-header">
                <span className="panel-title">Broker Allocation</span>
              </div>
              <BrokerBreakdown
                breakdown={portfolio.broker_breakdown || {}}
                total={portfolio.total_value || 0}
              />
            </div>
          )}

          {!demoMode && (
            <>
              {/* Account and trust */}
              <div className="panel">
                <div className="panel-header">
                  <span className="panel-title">Account & Data</span>
                </div>
                <AccountDataPanel
                  onExportAll={handleExportAll}
                  onDeleteAccountData={handleDeleteAccountData}
                  onSelectTrustPage={onSelectTrustPage}
                />
              </div>

              {/* Import CSV */}
              <div className="panel">
                <div className="panel-header">
                  <span className="panel-title">Import CSV</span>
                </div>
                <UploadPanel onUpload={handleUpload} />
              </div>

              {/* Cash balances */}
              <div className="panel">
                <div className="panel-header">
                  <span className="panel-title">Cash Balances</span>
                </div>
                <CashPanel onSave={fetchPortfolio} />
              </div>

              {/* Add active position */}
              <div className="panel">
                <div className="panel-header">
                  <span className="panel-title">Add Active Position</span>
                </div>
                <AddActivePositionForm onAdd={handleAddActive} />
              </div>

              {/* Log closed position */}
              <div className="panel">
                <div className="panel-header">
                  <span className="panel-title">Log Closed Position</span>
                </div>
                <AddClosedPositionForm onAdd={() => { fetchClosed(); setActiveTab('closed'); }} />
              </div>
            </>
          )}

          {/* Top holdings */}
          {hasData && (
            <div className="panel">
              <div className="panel-header">
                <span className="panel-title">Top 5 Holdings</span>
              </div>
              <TopHoldings positions={positions} />
            </div>
          )}
        </div>}
      </div>

      {/* Gain/loss timeline — full width below the main grid */}
      {showPortfolioOverview && <PnLTimeline closedPositions={closedPositions} />}

      {/* Portfolio History Chart — growth vs S&P 500 */}
      {showPortfolioOverview && (
        <PortfolioHistoryChart
          currentValue={portfolio?.total_value}
          historySeed={demoMode ? demoData.historyEntries : null}
          snapshotsSeed={demoMode ? demoData.snapshots : null}
          readOnly={demoMode}
        />
      )}

      {toast && <Toast {...toast} onClose={() => setToast(null)} />}
    </div>
  );
}

createRoot(document.getElementById('root')).render(<AuthGate />);
