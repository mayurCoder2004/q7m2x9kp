import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import App from './App.jsx';
import { analysisResponse } from './fixtures.js';

const reply = (status, body) => Promise.resolve({ ok: status < 400, status, json: async () => body });

afterEach(() => vi.unstubAllGlobals());

describe('App', () => {
  it('loads 15 January by default and renders KPIs, warnings and the table', async () => {
    const fetchMock = vi.fn(() => reply(200, analysisResponse()));
    vi.stubGlobal('fetch', fetchMock);
    render(<App/>);

    expect(screen.getByRole('button', { name: /loading/i }).disabled).toBe(true);
    expect(fetchMock.mock.calls[0][0]).toContain('/api/analysis?cutoff_hour=11&cutoff_minute=0&date=2026-01-15');

    await screen.findByText(/Across 2 matched hourly periods/);
    const kpis = screen.getByRole('region', { name: 'Key metrics' });
    expect(within(kpis).getByText('150')).toBeTruthy();
    expect(within(kpis).getByText('66.7')).toBeTruthy();
    expect(within(kpis).getByText('2 of 3 actual periods matched')).toBeTruthy();
    expect(screen.getByText(/had no forecast published/)).toBeTruthy();

    const table = screen.getByRole('table');
    expect(within(table).getAllByRole('row')).toHaveLength(1 + 3);
    expect(within(table).getByText('No eligible forecast')).toBeTruthy();
    expect(screen.getByRole('button', { name: /export csv/i }).disabled).toBe(false);
  });

  it('shows unavailable metrics as N/A, not zero', async () => {
    vi.stubGlobal('fetch', vi.fn(() => reply(200, analysisResponse({
      rows: [], metrics: { mae_mw: null, rmse_mw: null, bias_mw: null, wape_pct: null, n_periods: 0 },
      coverage: { matched: 0, actual_periods: 0, forecast_coverage_pct: null }, warnings: ['No data'], vintages_used: [],
      executive_summary: ['No forecast/actual pairs could be matched, so accuracy metrics are unavailable.'],
    }))));
    render(<App/>);
    await screen.findByText(/accuracy metrics are unavailable/);
    const kpis = screen.getByRole('region', { name: 'Key metrics' });
    expect(within(kpis).getAllByText('N/A')).toHaveLength(5);
    expect(screen.getByText('No generation records to display')).toBeTruthy();
    expect(screen.getByRole('button', { name: /export csv/i }).disabled).toBe(true);
  });

  it('shows the API error and no stale data', async () => {
    vi.stubGlobal('fetch', vi.fn(() => reply(502, { detail: 'Elexon API returned HTTP 503 for /datasets/FUELHH/stream' })));
    render(<App/>);
    expect(await screen.findByText(/Elexon API returned HTTP 503/)).toBeTruthy();
    expect(screen.getByRole('alert')).toBeTruthy();
    expect(screen.getByRole('table').querySelectorAll('tbody tr')).toHaveLength(0);
  });

  it('restricts the date picker to January 2026 and blocks invalid dates', async () => {
    vi.stubGlobal('fetch', vi.fn(() => reply(200, analysisResponse())));
    render(<App/>);
    await screen.findByText(/Across 2 matched/);
    const input = screen.getByLabelText(/delivery date/i);
    expect(input.min).toBe('2026-01-01');
    expect(input.max).toBe('2026-01-31');
    expect(input.title).toContain('January 2026');
    expect(screen.getByText(/Jan 2026 only/i)).toBeTruthy();

    fireEvent.change(input, { target: { value: '2026-02-03' } });
    expect(screen.getByText(/Choose a delivery date between/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /run analysis/i }).disabled).toBe(true);
  });

  it('requests the chosen date, cutoff and month scope', async () => {
    const fetchMock = vi.fn(() => reply(200, analysisResponse()));
    vi.stubGlobal('fetch', fetchMock);
    render(<App/>);
    await screen.findByText(/Across 2 matched/);

    fireEvent.change(screen.getByLabelText(/delivery date/i), { target: { value: '2026-01-20' } });
    fireEvent.change(screen.getByLabelText(/decision cutoff/i), { target: { value: '09:00' } });
    fireEvent.click(screen.getByRole('button', { name: /run analysis/i }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1][0]).toContain('/api/analysis?cutoff_hour=9&cutoff_minute=0&date=2026-01-20');

    fireEvent.click(screen.getByLabelText('Full month'));
    expect(screen.getByLabelText(/delivery date/i).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: /run analysis/i }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(fetchMock.mock.calls[2][0]).toContain('/api/summary?cutoff_hour=9&cutoff_minute=0');
  });
});
