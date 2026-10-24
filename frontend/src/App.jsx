import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Activity, AlertTriangle, ArrowDownRight, ArrowUpRight, BookOpen, CalendarDays, CheckCircle2, Clock3, Download, FileText, Gauge, Percent, RefreshCw, Table2, Wind } from 'lucide-react';
import { API, CUTOFFS, FIRST_DAY, LAST_DAY, STATUS_LABEL, fetchAnalysis, fmt, isJanuary2026, signed, tableRows, toCsv, ukDate, ukTime } from './lib.js';

const COLORS = { actual: '#0d9488', forecast: '#6366f1', error: '#64748b', grid: '#e8edf4', tick: '#718096' };
const kMW = (v) => `${(v / 1000).toLocaleString('en-GB', { maximumFractionDigits: 1 })}k`;

export default function App() {
  const [scope, setScope] = useState('day');
  const [day, setDay] = useState('2026-01-15');
  const [cutoff, setCutoff] = useState('11:00');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState(null);
  const controller = useRef(null);

  const load = useCallback(async (query) => {
    controller.current?.abort();
    const ctrl = new AbortController();
    controller.current = ctrl;
    setLoading(true); setError('');
    try {
      const json = await fetchAnalysis(query, { signal: ctrl.signal });
      setData(json); setLastUpdated(new Date());
    } catch (e) {
      if (e.name === 'AbortError') return;
      setData(null); setError(e.message || 'Unable to load data');
    } finally {
      if (controller.current === ctrl) setLoading(false);
    }
  }, []);

  useEffect(() => { load({ scope: 'day', day: '2026-01-15', cutoff: '11:00' }); return () => controller.current?.abort(); }, [load]);

  const dayValid = scope === 'month' || isJanuary2026(day);
  const run = (e) => { e.preventDefault(); if (dayValid) load({ scope, day, cutoff }); };

  const rows = useMemo(() => tableRows(data), [data]);
  const isMonth = data?.scope === 'month';
  const chartRows = useMemo(() => (data?.rows || []).map((r) => ({ t: r.startTime, actual: r.actual, forecast: r.forecast, error: r.error })), [data]);
  const errorRows = useMemo(() => (isMonth
    ? (data?.daily || []).map((d) => ({ label: d.date.slice(8), date: d.date, value: d.metrics.mae_mw }))
    : chartRows.map((r) => ({ label: ukTime(r.t), t: r.t, value: r.error }))), [data, isMonth, chartRows]);

  const exportCsv = () => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([toCsv(data)], { type: 'text/csv;charset=utf-8' }));
    a.download = `windwise-${isMonth ? '2026-01' : data.date}-cutoff-${cutoff.replace(':', '')}.csv`;
    a.click(); URL.revokeObjectURL(a.href);
  };

  const m = data?.metrics || {}, c = data?.coverage || {};
  const hasMetrics = m.mae_mw != null;
  const periodLabel = data ? (isMonth ? 'January 2026 (all delivery days)' : ukDate(data.date)) : scope === 'month' ? 'January 2026' : isJanuary2026(day) ? ukDate(day) : 'Invalid date';
  const vintages = data?.vintages_used || [];

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-icon"><Wind size={22}/></div><div><strong>WINDWISE</strong><span>FORECAST INTELLIGENCE</span></div></div>
      <div className="nav-label">WORKSPACE</div>
      <nav aria-label="Sections">
        <a className="nav-item active" href="#overview"><Activity size={17}/> Forecast overview</a>
        <a className="nav-item" href="#summary"><FileText size={17}/> Executive summary</a>
        <a className="nav-item" href="#detail"><Table2 size={17}/> Period detail</a>
        <a className="nav-item" href="#methodology"><BookOpen size={17}/> Methodology</a>
      </nav>
      <div className="side-bottom"><div className="source-dot"/><div><b>Elexon BMRS</b><small>FUELHH outturn · WINDFOR forecast</small></div></div>
    </aside>
    <main className="main" id="overview">
      <header className="topbar"><div><div className="eyebrow">ENERGY TRADING / ANALYTICS</div><h1>Wind forecast overview</h1><p>How accurate was the GB national wind forecast available at the day-ahead decision cutoff? January 2026 delivery, hourly resolution.</p></div><div className="live-pill"><span/> HISTORICAL ANALYSIS</div></header>

      <form className="filters panel" onSubmit={run} aria-label="Analysis settings">
        <div className="filter-title"><CalendarDays size={18}/><div><b>Analysis settings</b><small>{periodLabel}</small></div></div>
        <fieldset className="segmented"><legend>Scope</legend><div className="segmented-row">
          <label className={scope === 'day' ? 'on' : ''}><input type="radio" name="scope" value="day" checked={scope === 'day'} onChange={() => setScope('day')}/>Single day</label>
          <label className={scope === 'month' ? 'on' : ''}><input type="radio" name="scope" value="month" checked={scope === 'month'} onChange={() => setScope('month')}/>Full month</label>
        </div></fieldset>
        <label>Delivery date (UK)<input type="date" value={day} min={FIRST_DAY} max={LAST_DAY} disabled={scope === 'month'} aria-invalid={!dayValid} onChange={(e) => setDay(e.target.value)}/></label>
        <label>Decision cutoff, day before (UK time)<select value={cutoff} onChange={(e) => setCutoff(e.target.value)}>{CUTOFFS.map((t) => <option key={t} value={t}>{t}</option>)}</select></label>
        <button type="submit" className="primary" disabled={loading || !dayValid}><RefreshCw className={loading ? 'spin' : ''} size={16}/> {loading ? 'Loading' : 'Run analysis'}</button>
        {!dayValid && <p className="field-error" role="alert">Choose a delivery date between 1 and 31 January 2026.</p>}
      </form>

      {error && <div className="error" role="alert"><AlertTriangle size={18}/><div><b>Could not load the analysis</b><p>{error}</p><small>The dashboard never substitutes invented data. Check the backend at {API} and its logs.</small></div></div>}

      <section className="metric-grid" aria-label="Key metrics" aria-busy={loading}>
        <Metric title="Mean absolute error" value={fmt(m.mae_mw, 0)} unit={hasMetrics ? 'MW' : ''} desc="Typical size of a miss" icon={<Activity/>}/>
        <Metric title="Root mean squared error" value={fmt(m.rmse_mw, 0)} unit={hasMetrics ? 'MW' : ''} desc="Penalises large misses" icon={<Gauge/>}/>
        <Metric title="Forecast bias" value={signed(m.bias_mw)} unit={hasMetrics ? 'MW' : ''} desc={!hasMetrics ? 'Unavailable' : m.bias_mw > 0 ? 'Positive = overforecast' : m.bias_mw < 0 ? 'Negative = underforecast' : 'No net bias'} icon={m.bias_mw > 0 ? <ArrowUpRight/> : <ArrowDownRight/>}/>
        <Metric title="WAPE" value={fmt(m.wape_pct)} unit={m.wape_pct != null ? '%' : ''} desc="Σ|error| ÷ Σ actual" icon={<Percent/>}/>
        <Metric title="Forecast coverage" value={fmt(c.forecast_coverage_pct)} unit={c.forecast_coverage_pct != null ? '%' : ''} desc={data ? `${c.matched} of ${c.actual_periods} actual periods matched` : 'Awaiting data'} icon={<CheckCircle2/>}/>
      </section>

      <section className="panel chart-panel" aria-labelledby="gen-title">
        <div className="section-head"><div><div className="eyebrow">GENERATION PROFILE</div><h2 id="gen-title">Forecast vs actual wind generation</h2><p>Hourly GB wind generation, MW · times in UK local time (GMT in January)</p></div><div className="source-label"><span/> Elexon BMRS</div></div>
        {chartRows.length ? <div className="chart" role="img" aria-label={`Line chart of actual and forecast wind generation, ${periodLabel}. Values are in the table below.`}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartRows} margin={{ top: 12, right: 16, left: 4, bottom: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={COLORS.grid} vertical={false}/>
              <XAxis dataKey="t" tickFormatter={(t) => isMonth ? ukTime(t, true).slice(0, 6) : ukTime(t)} interval="preserveStartEnd" minTickGap={24} tick={{ fontSize: 11, fill: COLORS.tick }}/>
              <YAxis tick={{ fontSize: 11, fill: COLORS.tick }} tickFormatter={kMW} width={44} label={{ value: 'MW', angle: -90, position: 'insideLeft', fontSize: 11, fill: COLORS.tick }}/>
              <Tooltip labelFormatter={(t) => `${ukTime(t, true)} UK`} formatter={(v, name) => [v == null ? 'Missing' : `${fmt(v, 0)} MW`, name]}/>
              <Legend wrapperStyle={{ fontSize: 12 }}/>
              <Line type="monotone" dataKey="actual" name="Actual generation" stroke={COLORS.actual} strokeWidth={2} dot={false} connectNulls={false} isAnimationActive={false}/>
              <Line type="monotone" dataKey="forecast" name="Forecast at cutoff" stroke={COLORS.forecast} strokeWidth={2} strokeDasharray="6 3" dot={false} connectNulls={false} isAnimationActive={false}/>
            </LineChart>
          </ResponsiveContainer>
        </div> : <Empty loading={loading} hasError={!!error}/>}
        <div className="chart-foot"><span><Clock3 size={14}/> Cutoff: {data?.methodology?.cutoff || `${cutoff} UK time on the day before delivery`}</span><span>{lastUpdated ? `Retrieved ${lastUpdated.toLocaleTimeString('en-GB')}` : 'Awaiting data'}</span></div>
      </section>

      {errorRows.length > 0 && <section className="panel chart-panel" aria-labelledby="err-title">
        <div className="section-head"><div><div className="eyebrow">ERROR PROFILE</div><h2 id="err-title">{isMonth ? 'Daily mean absolute error' : 'Hourly forecast error'}</h2><p>{isMonth ? 'MAE per delivery day, MW' : 'Forecast minus actual, MW · above zero = overforecast'}</p></div></div>
        <div className="chart chart-small" role="img" aria-label={isMonth ? 'Bar chart of daily MAE in January 2026' : 'Bar chart of signed hourly forecast error'}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={errorRows} margin={{ top: 12, right: 16, left: 4, bottom: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={COLORS.grid} vertical={false}/>
              <XAxis dataKey="label" interval="preserveStartEnd" minTickGap={16} tick={{ fontSize: 11, fill: COLORS.tick }}/>
              <YAxis tick={{ fontSize: 11, fill: COLORS.tick }} tickFormatter={kMW} width={44}/>
              <ReferenceLine y={0} stroke="#94a3b8"/>
              <Tooltip labelFormatter={(l, p) => isMonth ? ukDate(p?.[0]?.payload?.date || FIRST_DAY) : `${l} UK`} formatter={(v) => [v == null ? 'Unavailable' : `${isMonth ? fmt(v, 0) : signed(v)} MW`, isMonth ? 'MAE' : 'Error']}/>
              <Bar dataKey="value" fill={COLORS.error} radius={[3, 3, 0, 0]} isAnimationActive={false}/>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </section>}

      <section className="lower-grid" id="summary">
        <div className="panel insight-panel">
          <div className="eyebrow">EXECUTIVE SUMMARY</div><h2>What the data shows</h2>
          {data ? <ul className="summary-list">{data.executive_summary.map((s) => <li key={s}>{s}</li>)}</ul> : <p className="muted">Run an analysis to generate a summary from the calculated results.</p>}
          {data?.warnings?.length > 0 && <div className="note" role="status"><AlertTriangle size={16}/><div><b>Data-quality warnings</b><ul>{data.warnings.map((w) => <li key={w}>{w}</li>)}</ul></div></div>}
          {data && !data.warnings?.length && <div className="ok-note"><CheckCircle2 size={15}/> No data-quality issues detected: every hour had both half-hours of actual data{c.missing_forecast ? '' : ' and an eligible forecast'}.</div>}
        </div>
        <div className="panel insight-panel">
          <div className="eyebrow">HOW TO READ THIS</div><h2>Procurement context</h2>
          <div className="insight"><div className="insight-icon teal"><ArrowUpRight size={18}/></div><div><b>Positive bias (overforecast)</b><p>Forecast wind exceeded actual wind on average. Other things equal, more replacement generation than planned may have been needed.</p></div></div>
          <div className="insight"><div className="insight-icon purple"><ArrowDownRight size={18}/></div><div><b>Negative bias (underforecast)</b><p>Actual wind exceeded the forecast on average. Other things equal, less additional generation may have been needed than planned.</p></div></div>
          <div className="note"><AlertTriangle size={16}/><p><b>Scope limitation</b><br/>Forecast error alone is not evidence of balancing procurement cost, financial savings or market impact. Demand, other generation, interconnectors, curtailment and prices are not included.</p></div>
          {vintages.length > 0 && <div className="method"><b>Forecast vintages used</b><p>{vintages.length === 1 ? `Published ${ukTime(vintages[0].publishTime, true)} UK for all ${vintages[0].periods} periods.` : `${vintages.length} vintages, e.g. ${ukTime(vintages[0].publishTime, true)} UK. Each row in the table shows its own publish time.`}</p></div>}
        </div>
      </section>

      <section className="panel table-panel" id="detail" aria-labelledby="detail-title">
        <div className="section-head"><div><div className="eyebrow">PERIOD DETAIL</div><h2 id="detail-title">Hourly comparison</h2><p>{rows.length ? `${rows.length} hourly periods. Forecasts published after the cutoff are excluded.` : 'No periods to show.'}</p></div><button type="button" className="secondary" onClick={exportCsv} disabled={!rows.length}><Download size={15}/> Export CSV</button></div>
        <div className="table-wrap" tabIndex={0} aria-label="Hourly comparison table">
          <table><thead><tr><th scope="col">Period (UK)</th><th scope="col">Actual MW</th><th scope="col">Forecast MW</th><th scope="col">Error MW</th><th scope="col">Abs. error MW</th><th scope="col">Forecast published (UK)</th><th scope="col">Lead time</th><th scope="col">Status</th></tr></thead>
            <tbody>{rows.map((r) => <tr key={r.startTime}>
              <td>{r.period}</td><td>{fmt(r.actual, 0)}</td><td>{fmt(r.forecast, 0)}</td>
              <td className={r.error == null ? '' : r.error > 0 ? 'positive' : 'negative'}>{signed(r.error)}</td><td>{fmt(r.absoluteError, 0)}</td>
              <td>{r.publishTime ? ukTime(r.publishTime, true) : '—'}</td><td>{r.leadTimeHours == null ? '—' : `${fmt(r.leadTimeHours, 1)} h`}</td>
              <td><span className={`badge ${r.status}`}>{STATUS_LABEL[r.status] || r.status}</span></td>
            </tr>)}</tbody></table>
        </div>
        <div className="table-note">The CSV export contains exactly these rows, with UTC timestamps, units and the metrics in a header block.</div>
      </section>

      <section className="panel method-panel" id="methodology" aria-labelledby="method-title">
        <div className="eyebrow">METHODOLOGY &amp; LIMITATIONS</div><h2 id="method-title">How the numbers are produced</h2>
        <div className="method-grid">
          <div><b>Decision cutoff</b><p>{data?.methodology?.cutoff || `${cutoff} UK local time on the day before each delivery date`}. Each delivery day has its own cutoff.</p></div>
          <div><b>Forecast vintage</b><p>{data?.methodology?.selection || 'Latest WINDFOR forecast published at or before the cutoff.'}</p></div>
          <div><b>Matching</b><p>{data?.methodology?.matching || 'Hourly forecast startTime matched to the hourly mean of two FUELHH half-hours.'}</p></div>
          <div><b>Time and units</b><p>{data?.methodology?.timezone || 'Timestamps are UTC; dates are UK local days.'} Values are {data?.methodology?.units || 'MW'}.</p></div>
          <div><b>Metric definitions</b><p>MAE = mean |forecast − actual|. RMSE = √mean (forecast − actual)². Bias = mean (forecast − actual); positive = overforecast. WAPE = Σ|error| ÷ Σ actual × 100 (unavailable if Σ actual = 0). Coverage = matched ÷ periods with complete actuals.</p></div>
          <div><b>Limitations</b><p>Only national wind is analysed. FUELHH reflects metered outturn, which can be reduced by curtailment, so part of any overforecast may reflect constraint actions rather than forecast skill. Missing periods are reported, never imputed. This is a pilot, not an operational trading system.</p></div>
        </div>
        <p className="sources">Sources: {data?.source ? `${data.source.actuals}; ${data.source.forecasts}. Retrieved ${new Date(data.source.retrieved_at).toLocaleString('en-GB')}.` : 'Elexon BMRS FUELHH and WINDFOR.'}</p>
      </section>

      <footer>WINDWISE · Pilot analytics · Data: Elexon BMRS (Insights API) · Not for automated trading</footer>
    </main>
  </div>;
}

function Metric({ title, value, unit, desc, icon }) {
  return <div className="metric panel"><div className="metric-top"><span>{title}</span><div className="metric-icon" aria-hidden="true">{icon}</div></div><div className="metric-value">{value}<small>{unit}</small></div><div className="metric-desc">{desc}</div></div>;
}

function Empty({ loading, hasError }) {
  return <div className="empty"><Wind size={28}/><b>{loading ? 'Loading generation profile…' : hasError ? 'No data loaded' : 'No generation records to display'}</b><span>{loading ? 'Querying Elexon via the Windwise API.' : 'Choose settings and run the analysis.'}</span></div>;
}
