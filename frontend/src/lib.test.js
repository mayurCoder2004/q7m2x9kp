import { describe, expect, it, vi } from 'vitest';
import { buildAnalysisUrl, fetchAnalysis, fmt, isJanuary2026, signed, tableRows, toCsv, ukTime } from './lib.js';
import { analysisResponse } from './fixtures.js';

const json = (status, body) => ({ ok: status < 400, status, json: async () => body });

describe('formatting', () => {
  it('shows unavailable values as N/A, never zero', () => {
    expect(fmt(null)).toBe('N/A');
    expect(fmt(undefined)).toBe('N/A');
    expect(fmt(0)).toBe('0.0');
    expect(signed(12.4)).toBe('+12');
    expect(signed(-12.6)).toBe('-13');
  });
  it('formats UTC timestamps in UK time', () => {
    expect(ukTime('2026-01-15T13:00:00Z')).toBe('13:00');
    expect(ukTime('2026-07-15T13:00:00Z')).toBe('14:00'); // BST
  });
  it('restricts delivery dates to January 2026', () => {
    expect(isJanuary2026('2026-01-01')).toBe(true);
    expect(isJanuary2026('2026-01-15')).toBe(true);
    expect(isJanuary2026('2026-01-31')).toBe(true);
    expect(isJanuary2026('2025-12-31')).toBe(false);
    expect(isJanuary2026('2026-02-01')).toBe(false);
    expect(isJanuary2026('2026-01-00')).toBe(false);
    expect(isJanuary2026('2026-01-32')).toBe(false);
    expect(isJanuary2026('')).toBe(false);
    expect(isJanuary2026('not-a-date')).toBe(false);
  });
});

describe('API integration', () => {
  it('builds daily and monthly URLs', () => {
    expect(buildAnalysisUrl({ scope: 'day', day: '2026-01-15', cutoff: '09:30' }))
      .toBe('http://localhost:8000/api/analysis?cutoff_hour=9&cutoff_minute=30&date=2026-01-15');
    expect(buildAnalysisUrl({ scope: 'month', day: '2026-01-15', cutoff: '11:00' }))
      .toBe('http://localhost:8000/api/summary?cutoff_hour=11&cutoff_minute=0');
  });
  it('returns the parsed body on success', async () => {
    const body = analysisResponse();
    const fetchImpl = vi.fn().mockResolvedValue(json(200, body));
    await expect(fetchAnalysis({ scope: 'day', day: '2026-01-15', cutoff: '11:00' }, { fetchImpl })).resolves.toBe(body);
  });
  it('surfaces the backend error detail', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(json(502, { detail: 'Elexon API returned HTTP 503 for /x' }));
    await expect(fetchAnalysis({ scope: 'day', day: '2026-01-15', cutoff: '11:00' }, { fetchImpl })).rejects.toThrow('HTTP 503');
  });
  it('joins FastAPI validation errors', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(json(422, { detail: [{ msg: 'bad hour' }] }));
    await expect(fetchAnalysis({ scope: 'month', cutoff: '11:00' }, { fetchImpl })).rejects.toThrow('bad hour');
  });
  it('explains network failures', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    await expect(fetchAnalysis({ scope: 'month', cutoff: '11:00' }, { fetchImpl })).rejects.toThrow('Cannot reach the Windwise API');
  });
  it('rejects non-January dates without calling the API', async () => {
    const fetchImpl = vi.fn();
    await expect(fetchAnalysis({ scope: 'day', day: '2026-02-01', cutoff: '11:00' }, { fetchImpl })).rejects.toThrow('January 2026');
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

describe('CSV export', () => {
  it('contains exactly the table rows, in order, with matching values', () => {
    const data = analysisResponse();
    const table = tableRows(data);
    const lines = toCsv(data).split('\n');
    const meta = lines.filter((l) => l.startsWith('#'));
    const [header, ...body] = lines.filter((l) => !l.startsWith('#'));
    expect(header.split(',')).toEqual([
      'period_start_utc', 'delivery_date_uk', 'actual_mw', 'forecast_mw', 'error_mw_forecast_minus_actual',
      'absolute_error_mw', 'forecast_published_utc', 'decision_cutoff_utc', 'lead_time_hours', 'status',
    ]);
    expect(body).toHaveLength(table.length);
    body.forEach((line, i) => {
      const cells = line.split(',');
      expect(cells[0]).toBe(table[i].startTime);
      expect(cells[2]).toBe(String(table[i].actual));
      expect(cells[3]).toBe(table[i].forecast == null ? '' : String(table[i].forecast));
      expect(cells[4]).toBe(table[i].error == null ? '' : String(table[i].error));
      expect(cells[9]).toBe(table[i].status);
    });
    expect(meta.join('\n')).toContain('MAE_MW=150');
    expect(meta.join('\n')).toContain('Coverage: 2/3 periods (66.7%)');
  });
  it('writes NA for unavailable metrics', () => {
    const data = analysisResponse({ metrics: { mae_mw: null, rmse_mw: null, bias_mw: null, wape_pct: null } });
    expect(toCsv(data)).toContain('MAE_MW=NA');
  });
  it('quotes values containing commas', () => {
    const data = analysisResponse();
    data.rows[0].status = 'a,b';
    expect(toCsv(data)).toContain('"a,b"');
  });
});
