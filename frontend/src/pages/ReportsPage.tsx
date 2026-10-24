import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Cell,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from 'recharts';
import Alert from '@mui/material/Alert';
import { exportReport, fetchDashboardReport, fetchTransactionSeries, REPORT_TYPES } from '../api/endpoints';
import { getBackend } from '../api/client';
import PageHeader from '../components/PageHeader';
import { CurrencyPicker, useReportCurrency } from '../components/CurrencyPicker';
import DataPage, { CardsSkeleton } from '../components/DataPage';
import { useColorMode } from '../hooks/useColorMode';
import { formatMoney, problemMessage } from '../utils/format';

const bk = () => getBackend();

function download({ blob, filename }: { blob: Blob; filename: string }) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

/** CSV export of every report type (spec section 62); the generated file is also blob-stored. */
function ExportRow() {
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  const exportOne = async (type: (typeof REPORT_TYPES)[number]) => {
    setPending(type);
    setError(null);
    try {
      download(await exportReport(type));
    } catch (cause) {
      setError(cause);
    } finally {
      setPending(null);
    }
  };

  return (
    <section className="panel" style={{ padding: 16 }}>
      <h3>CSV exports</h3>
      <p className="panel-muted">Download any report as CSV. Fraud, AML, reconciliation and settlement exports require a staff role.</p>
      <div className="fc-inline-form" style={{ flexWrap: 'wrap' }}>
        {REPORT_TYPES.map((type) => (
          <button
            key={type}
            type="button"
            className="fc-save-button"
            disabled={pending !== null}
            onClick={() => void exportOne(type)}
          >
            {pending === type ? 'Exporting…' : type.replaceAll('-', ' ')}
          </button>
        ))}
      </div>
      {error ? <Alert severity="error">{problemMessage(error, 'Export failed — check your role.')}</Alert> : null}
    </section>
  );
}

export function ReportsPage() {
  const { charts } = useColorMode();
  const dashboard = useQuery({ queryKey: ['reports', 'dashboard', bk()], queryFn: fetchDashboardReport });
  const series = useQuery({
    queryKey: ['reports', 'transactions', bk()],
    queryFn: () => fetchTransactionSeries({ days: 30 }),
  });

  const d = dashboard.data;
  const successRate = d?.successRate ?? null;
  const pieData = successRate === null ? [] : [
    { name: 'Completed', value: Math.round(successRate * 10) / 10 },
    { name: 'Not completed', value: Math.round((100 - successRate) * 10) / 10 },
  ];

  // Counts can be added across currencies; volumes cannot, so they follow the chosen currency.
  const monthly = d?.monthlyVolume ?? [];
  const points = series.data?.series ?? [];
  const { currency, currencies, setCurrency } = useReportCurrency([...monthly, ...points]);
  const monthlyData = monthly
    .filter((m) => m.currency === currency)
    .map((m) => ({ month: m.month.slice(5) + '/' + m.month.slice(2, 4), volumeMajor: m.volumeMinor / 100, count: m.count }));
  const dailyByDay = new Map<string, { day: string; count: number; volumeMajor: number }>();
  for (const p of points) {
    const row = dailyByDay.get(p.day) ?? { day: p.day.slice(5), count: 0, volumeMajor: 0 };
    row.count += p.count;
    if (p.currency === currency) row.volumeMajor += p.volumeMinor / 100;
    dailyByDay.set(p.day, row);
  }
  const dailyData = [...dailyByDay.values()];

  return (
    <>
      <PageHeader
        eyebrow="ANALYTICS"
        title="Reports"
        subtitle={`Volume, success rate and daily activity${currency ? `. Volumes in ${currency}.` : '.'}`}
        actions={<CurrencyPicker currency={currency} currencies={currencies} onChange={setCurrency} />}
      />

      <DataPage
        isLoading={dashboard.isLoading}
        isError={dashboard.isError}
        error={dashboard.error}
        onRetry={() => void dashboard.refetch()}
        isEmpty={Boolean(d) && (d?.totals.payments ?? 0) === 0}
        emptyTitle="No report data"
        emptyHint="Metrics appear once payments are processed."
        loading={<CardsSkeleton count={3} />}
      >
        <div className="metrics-grid">
          <article className="metric-card">
            <div className="metric-top"><span>TOTAL PAYMENTS</span></div>
            <strong className="metric-value">{d?.totals.payments ?? '—'}</strong>
            <div className="metric-foot">{d?.totals.completed ?? '—'} completed · {d?.totals.failed ?? '—'} failed</div>
          </article>
          <article className="metric-card accent-card">
            <div className="metric-top"><span>SUCCESS RATE</span></div>
            <strong className="metric-value">{successRate === null ? '—' : `${successRate.toFixed(1)}%`}</strong>
            <div className="metric-foot">Completed vs total</div>
          </article>
          <article className="metric-card">
            <div className="metric-top"><span>BLOCKED</span></div>
            <strong className="metric-value">{d?.totals.blocked ?? '—'}</strong>
            <div className="metric-foot">Payments stopped by risk rules</div>
          </article>
        </div>

        <div className="charts-grid">
          <section className="panel panel-chart">
            <h3>Monthly volume</h3>
            {monthlyData.length === 0 ? (
              <p className="panel-muted">No monthly data yet.</p>
            ) : (
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={monthlyData}>
                  <CartesianGrid strokeDasharray="3 3" stroke={charts.grid} vertical={false} />
                  <XAxis dataKey="month" tick={{ fontSize: 11, fill: charts.axis }} tickLine={false} axisLine={{ stroke: charts.grid }} />
                  <YAxis tick={{ fontSize: 11, fill: charts.axis }} tickLine={false} axisLine={false} />
                  <ChartTooltip formatter={(v: unknown) => formatMoney(Math.round(Number(v) * 100), currency)} />
                  <Legend />
                  <Bar name="Volume" dataKey="volumeMajor" fill={charts.primary} radius={[6, 6, 0, 0]} maxBarSize={36} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </section>

          <section className="panel panel-chart">
            <h3>Success rate</h3>
            {pieData.length === 0 ? (
              <p className="panel-muted">No payments yet.</p>
            ) : (
              <ResponsiveContainer width="100%" height={260}>
                <PieChart>
                  <Pie data={pieData} dataKey="value" nameKey="name" innerRadius={60} outerRadius={95} paddingAngle={3}>
                    <Cell fill={charts.primary} />
                    <Cell fill={charts.track} />
                  </Pie>
                  <ChartTooltip formatter={(v: unknown) => `${v}%`} />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            )}
          </section>
        </div>
      </DataPage>

      <div className="section-heading">
        <div>
          <h2>Transactions per day</h2>
          <p>Last 30 days.</p>
        </div>
      </div>
      <DataPage
        isLoading={series.isLoading}
        isError={series.isError}
        error={series.error}
        onRetry={() => void series.refetch()}
        isEmpty={dailyData.length === 0}
        emptyTitle="No daily data"
        emptyHint="Daily series appears after transactions are recorded."
        loading={<div className="panel panel-chart"><p className="panel-muted">Loading…</p></div>}
      >
        <section className="panel panel-chart">
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={dailyData} margin={{ top: 8, right: 16, left: 8, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={charts.grid} vertical={false} />
              <XAxis dataKey="day" tick={{ fontSize: 10, fill: charts.axis }} tickLine={false} axisLine={{ stroke: charts.grid }} />
              <YAxis tick={{ fontSize: 11, fill: charts.axis }} tickLine={false} axisLine={false} allowDecimals={false} />
              <ChartTooltip />
              <Legend />
              <Line name="Transactions" type="monotone" dataKey="count" stroke={charts.primary} strokeWidth={2} dot={false} />
              <Line name={currency ? `Volume (${currency})` : 'Volume'} type="monotone" dataKey="volumeMajor" stroke={charts.secondary} strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </section>
      </DataPage>

      <ExportRow />
    </>
  );
}

export default ReportsPage;
