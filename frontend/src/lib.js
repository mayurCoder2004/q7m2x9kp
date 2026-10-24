// Pure helpers shared by the dashboard and its tests.

export const API = (import.meta.env?.VITE_API_URL || 'http://localhost:8000').replace(/\/$/, '');
export const FIRST_DAY = '2026-01-01';
export const LAST_DAY = '2026-01-31';
export const CUTOFFS = ['08:00', '09:00', '10:00', '11:00', '12:00', '16:00', '23:00'];
const TZ = 'Europe/London';

export const fmt = (n, digits = 1) =>
  n == null || !Number.isFinite(Number(n))
    ? 'N/A'
    : Number(n).toLocaleString('en-GB', { maximumFractionDigits: digits, minimumFractionDigits: digits });

export const signed = (n, digits = 0) =>
  n == null || !Number.isFinite(Number(n)) ? 'N/A' : `${n > 0 ? '+' : ''}${fmt(n, digits)}`;

export const ukTime = (iso, withDate = false) =>
  iso
    ? new Date(iso).toLocaleString('en-GB', {
        timeZone: TZ,
        hour: '2-digit',
        minute: '2-digit',
        ...(withDate ? { day: '2-digit', month: 'short' } : {}),
      })
    : null;

export const ukDate = (isoDate) =>
  new Date(`${isoDate}T12:00:00Z`).toLocaleDateString('en-GB', { day: '2-digit', month: 'long', year: 'numeric', timeZone: 'UTC' });

export const isJanuary2026 = (day) => /^\d{4}-\d{2}-\d{2}$/.test(day) && day >= FIRST_DAY && day <= LAST_DAY;

export function buildAnalysisUrl({ scope, day, cutoff }) {
  const [hour, minute] = cutoff.split(':').map(Number);
  const params = new URLSearchParams({ cutoff_hour: String(hour), cutoff_minute: String(minute) });
  if (scope === 'day') params.set('date', day);
  return `${API}/api/${scope === 'day' ? 'analysis' : 'summary'}?${params}`;
}

export async function fetchAnalysis(query, { fetchImpl = fetch, signal } = {}) {
  if (query.scope === 'day' && !isJanuary2026(query.day)) {
    throw new Error('Choose a delivery date in January 2026.');
  }
  let res;
  try {
    res = await fetchImpl(buildAnalysisUrl(query), { signal });
  } catch (e) {
    if (e?.name === 'AbortError') throw e;
    throw new Error(`Cannot reach the Windwise API at ${API}. Is the backend running?`);
  }
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON error body */ }
  if (!res.ok) {
    const detail = body?.detail;
    const message = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join('; ') : null;
    throw new Error(message || `Request failed (HTTP ${res.status})`);
  }
  if (!body || !Array.isArray(body.rows)) throw new Error('The API returned an unexpected response.');
  return body;
}

export const STATUS_LABEL = {
  matched: 'Matched',
  missing_forecast: 'No eligible forecast',
  missing_actual: 'No actual data',
  incomplete_actual: 'Incomplete actual',
};

// One row model for the table and the CSV, so the two cannot disagree.
export const tableRows = (data) =>
  (data?.rows || []).map((r) => ({
    startTime: r.startTime,
    deliveryDate: r.deliveryDate,
    period: ukTime(r.startTime, data.scope === 'month'),
    actual: r.actual,
    forecast: r.forecast,
    error: r.error,
    absoluteError: r.absoluteError,
    publishTime: r.publishTime,
    cutoffTime: r.cutoffTime,
    leadTimeHours: r.leadTimeHours,
    status: r.status,
  }));

const CSV_COLUMNS = [
  ['startTime', 'period_start_utc'],
  ['deliveryDate', 'delivery_date_uk'],
  ['actual', 'actual_mw'],
  ['forecast', 'forecast_mw'],
  ['error', 'error_mw_forecast_minus_actual'],
  ['absoluteError', 'absolute_error_mw'],
  ['publishTime', 'forecast_published_utc'],
  ['cutoffTime', 'decision_cutoff_utc'],
  ['leadTimeHours', 'lead_time_hours'],
  ['status', 'status'],
];

const csvCell = (v) => {
  if (v == null) return '';
  const s = String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

export function toCsv(data) {
  const m = data.metrics || {};
  const c = data.coverage || {};
  const meta = [
    `# Windwise forecast analysis (${data.scope === 'month' ? 'January 2026' : data.date})`,
    `# Decision cutoff: ${data.methodology?.cutoff ?? ''}`,
    `# Sources: ${data.source?.actuals ?? ''}; ${data.source?.forecasts ?? ''}`,
    `# Retrieved: ${data.source?.retrieved_at ?? ''}`,
    `# MAE_MW=${m.mae_mw ?? 'NA'} RMSE_MW=${m.rmse_mw ?? 'NA'} BIAS_MW=${m.bias_mw ?? 'NA'} WAPE_PCT=${m.wape_pct ?? 'NA'}`,
    `# Coverage: ${c.matched ?? 0}/${c.actual_periods ?? 0} periods (${c.forecast_coverage_pct ?? 'NA'}%)`,
  ];
  const header = CSV_COLUMNS.map(([, name]) => name).join(',');
  const body = tableRows(data).map((r) => CSV_COLUMNS.map(([key]) => csvCell(r[key])).join(','));
  return [...meta, header, ...body].join('\n');
}
