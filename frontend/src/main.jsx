import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Area, AreaChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Activity, AlertTriangle, ArrowDownRight, ArrowUpRight, CalendarDays, CheckCircle2, Clock3, Download, RefreshCw, Wind } from 'lucide-react';
import './style.css';

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000';
const fmt = (n, digits=1) => n == null || !Number.isFinite(Number(n)) ? 'N/A' : Number(n).toLocaleString('en-GB', {maximumFractionDigits:digits, minimumFractionDigits:digits});
const shortTime = (iso) => iso ? new Date(iso).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',timeZone:'UTC'}) : 'Missing';
function App() {
  const [day, setDay] = useState('2026-10-24');
  const [cutoff, setCutoff] = useState('11:00');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState(null);
  async function load() {
    setLoading(true); setError('');
    try {
      const [h,m] = cutoff.split(':').map(Number);
      const res = await fetch(`${API}/api/analysis?date=${day}&cutoff_hour=${h}&cutoff_minute=${m}`);
      if (!res.ok) throw new Error((await res.json()).detail || `Request failed (${res.status})`);
      const json = await res.json(); setData(json); setLastUpdated(new Date());
    } catch (e) { setError(e.message || 'Unable to load data'); }
    finally { setLoading(false); }
  }
  useEffect(() => { load(); }, []);
  const chartRows = useMemo(() => (data?.rows || []).map(r => ({
    ...r, label: shortTime(r.startTime), forecast: r.forecast == null ? null : Number(r.forecast),
    actual: Number(r.actual), error: r.error == null ? null : Number(r.error)
  })), [data]);
  const exportCsv = () => {
    const rows = data?.rows || [];
    const header = ['startTime','cutoffTime','publishTime','actual','forecast','error','absoluteError'];
    const csv = [header.join(','), ...rows.map(r => header.map(k => r[k] ?? '').join(','))].join('\n');
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv], {type:'text/csv'})); a.download = `wind-analysis-${day}.csv`; a.click(); URL.revokeObjectURL(a.href);
  };
  const m = data?.metrics || {}, c = data?.coverage || {};
  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-icon"><Wind size={22}/></div><div><strong>WINDWISE</strong><span>FORECAST INTELLIGENCE</span></div></div>
      <div className="nav-label">WORKSPACE</div>
      <div className="nav-item active"><Activity size={17}/> Forecast overview</div>
      <div className="side-bottom"><div className="source-dot"/><div><b>Elexon BMRS</b><small>Configured data source</small></div></div>
    </aside>
    <main className="main">
      <header className="topbar"><div><div className="eyebrow">ENERGY TRADING / ANALYTICS</div><h1>Wind forecast overview</h1><p>Understand forecast availability and quantify forecast error before procurement decisions.</p></div><div className="live-pill"><span/> HISTORICAL ANALYSIS</div></header>
      <section className="filters panel">
        <div className="filter-title"><CalendarDays size={18}/><div><b>Analysis settings</b><small>{new Date(`${day}T12:00:00Z`).toLocaleDateString('en-GB', { day: '2-digit', month: 'long', year: 'numeric', timeZone: 'UTC' })} delivery periods</small></div></div>
        <label>Delivery date<input type="date" value={day} onChange={e=>setDay(e.target.value)}/></label>
        <label>Decision cutoff (UTC)<select value={cutoff} onChange={e=>setCutoff(e.target.value)}>{['08:00','09:00','10:00','11:00','12:00','16:00','23:00'].map(t=><option key={t}>{t}</option>)}</select></label>
        <button className="primary" onClick={load} disabled={loading}>{loading?<RefreshCw className="spin" size={16}/>:<RefreshCw size={16}/>} {loading?'Loading':'Refresh analysis'}</button>
      </section>
      {error && <div className="error"><AlertTriangle size={18}/><div><b>Could not load Elexon data</b><p>{error}</p><small>Check the backend is running and review its logs. The dashboard does not substitute invented data.</small></div></div>}
      <section className="metric-grid">
        <Metric title="Mean absolute error" value={fmt(m.mae_mw)} unit="MW" desc="Typical absolute miss" icon={<Activity/>}/>
        <Metric title="Root mean squared error" value={fmt(m.rmse_mw)} unit="MW" desc="Penalises larger misses" icon={<AlertTriangle/>}/>
        <Metric title="Forecast bias" value={fmt(m.bias_mw)} unit="MW" desc={m.bias_mw > 0 ? 'Average overforecast' : 'Average underforecast'} icon={m.bias_mw > 0 ? <ArrowUpRight/> : <ArrowDownRight/>} />
        <Metric title="Forecast coverage" value={fmt(c.forecast_coverage_pct)} unit="%" desc={`${c.matched ?? 0} of ${c.actual_periods ?? 0} actual periods matched`} icon={<CheckCircle2/>}/>
      </section>
      <section className="panel chart-panel">
        <div className="section-head"><div><div className="eyebrow">GENERATION PROFILE</div><h2>Forecast vs actual wind generation</h2><p>Hourly national generation · MW</p></div><div className="source-label"><span/> Elexon source data</div></div>
        {chartRows.length ? <div className="chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={chartRows} margin={{top:12,right:20,left:4,bottom:8}}><CartesianGrid strokeDasharray="3 3" stroke="#e8edf4"/><XAxis dataKey="label" interval={3} tick={{fontSize:11,fill:'#718096'}}/><YAxis tick={{fontSize:11,fill:'#718096'}} tickFormatter={v=>`${Math.round(v/1000)}k`}/><Tooltip labelFormatter={v=>`${v} UTC`} formatter={(v,n)=>[`${fmt(v,0)} MW`,n==='actual'?'Actual generation':'Eligible forecast']}/><Legend/><Line type="monotone" dataKey="actual" name="Actual generation" stroke="#0d9488" strokeWidth={2.5} dot={false} connectNulls={false}/><Line type="monotone" dataKey="forecast" name="Eligible forecast" stroke="#6366f1" strokeWidth={2.5} dot={false} connectNulls={false}/></LineChart></ResponsiveContainer></div> : <div className="empty"><Wind size={28}/><b>{loading?'Loading generation profile…':'No generation records to display'}</b><span>Choose a date and refresh to query Elexon.</span></div>}
        <div className="chart-foot"><span><Clock3 size={14}/> Cutoff: {cutoff} UTC on the previous day</span><span>{lastUpdated ? `Retrieved ${lastUpdated.toLocaleTimeString()}` : 'Awaiting data'}</span></div>
      </section>
      <section className="lower-grid">
        <div className="panel table-panel"><div className="section-head"><div><div className="eyebrow">PERIOD DETAIL</div><h2>Hourly comparison</h2><p>Forecasts published after cutoff are excluded.</p></div><button className="secondary" onClick={exportCsv} disabled={!chartRows.length}><Download size={15}/> Export CSV</button></div>
          <div className="table-wrap"><table><thead><tr><th>Time (UTC)</th><th>Actual MW</th><th>Forecast MW</th><th>Error MW</th><th>Published UTC</th></tr></thead><tbody>{chartRows.slice(0,24).map(r=><tr key={r.startTime}><td>{shortTime(r.startTime)}</td><td>{fmt(r.actual,0)}</td><td>{fmt(r.forecast,0)}</td><td className={r.error == null ? '' : r.error > 0 ? 'positive' : 'negative'}>{fmt(r.error,0)}</td><td>{r.publishTime ? new Date(r.publishTime).toLocaleString('en-GB',{timeZone:'UTC',hour:'2-digit',minute:'2-digit',day:'2-digit',month:'short'}) : 'No eligible forecast'}</td></tr>)}</tbody></table></div>
          <div className="table-note">Showing returned hourly periods. CSV export contains all returned periods.</div>
        </div>
        <div className="panel insight-panel"><div className="eyebrow">HOW TO READ THIS</div><h2>Procurement context</h2><div className="insight"><div className="insight-icon teal"><ArrowUpRight size={18}/></div><div><b>Positive bias</b><p>Forecast wind generation exceeded actual generation on average. Other things equal, this can leave a larger residual requirement than expected.</p></div></div><div className="insight"><div className="insight-icon purple"><ArrowDownRight size={18}/></div><div><b>Negative bias</b><p>Actual wind generation exceeded the forecast on average. Other things equal, less additional generation may be needed than expected.</p></div></div><div className="note"><AlertTriangle size={16}/><p><b>Scope limitation</b><br/>Wind forecast error alone is not a procurement volume. Demand, other generation, interconnector flows and market constraints are not included.</p></div><div className="method"><b>Selection methodology</b><p>{data?.methodology?.selection || 'Latest publishTime at or before cutoff.'}</p><p>All timestamps shown in UTC.</p></div></div>
      </section>
      <footer>WINDWISE · Pilot analytics · Data source: Elexon BMRS · Not for automated trading</footer>
    </main>
  </div>;
}
function Metric({title,value,unit,desc,icon}) { return <div className="metric panel"><div className="metric-top"><span>{title}</span><div className="metric-icon">{icon}</div></div><div className="metric-value">{value}<small>{unit}</small></div><div className="metric-desc">{desc}</div></div> }
createRoot(document.getElementById('root')).render(<App/>);
