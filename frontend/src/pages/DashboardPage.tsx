import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from 'recharts';
import Button from '@mui/material/Button';
import { fetchDashboardReport, fetchTransactions } from '../api/endpoints';
import { getBackend } from '../api/client';
import PageHeader from '../components/PageHeader';
import { DataPage, CardsSkeleton, TableSkeleton } from '../components/DataPage';
import StatusChip from '../components/StatusChip';
import CurrencyText from '../components/CurrencyText';
import { CurrencyPicker, useReportCurrency } from '../components/CurrencyPicker';
import { useAuth } from '../hooks/useAuth';
import { useColorMode } from '../hooks/useColorMode';
import { useCountUp } from '../hooks/useCountUp';
import { brand } from '../theme';
import { formatCompact, formatDate, formatMoney } from '../utils/format';

const bk = () => getBackend();

type IconTone = 'brand' | 'violet' | 'sky' | 'amber' | 'rose';

function MetricCard({
  label,
  value,
  format = (n) => Math.round(n).toLocaleString(),
  suffix,
  foot,
  icon,
  iconTone = 'brand',
  tone,
  progress,
}: {
  label: string;
  value: number | null | undefined;
  format?: (value: number) => string;
  suffix?: string;
  foot: string;
  icon: string;
  iconTone?: IconTone;
  tone?: 'accent';
  progress?: number | null;
}) {
  const animated = useCountUp(value);
  return (
    <article className={`metric-card${tone ? ` ${tone}-card` : ''}`}>
      <div className="metric-top">
        <span>{label}</span>
        <span className={`metric-icon${iconTone === 'brand' ? '' : ` ${iconTone}`}`} aria-hidden="true">
          {icon}
        </span>
      </div>
      <strong className="metric-value">
        {animated === null ? '—' : format(animated)}
        {suffix && animated !== null ? <small style={{ fontSize: 15, color: brand.muted, marginLeft: 4 }}>{suffix}</small> : null}
      </strong>
      <div className="metric-foot">{foot}</div>
      {progress !== undefined && progress !== null ? (
        <div className="metric-bar" aria-hidden="true">
          <span style={{ width: `${Math.max(0, Math.min(1, progress)) * 100}%` }} />
        </div>
      ) : null}
    </article>
  );
}

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}

export function DashboardPage() {
  const navigate = useNavigate();
  const { customer, roles } = useAuth();
  // The dashboard report covers the whole platform for admins and only the caller's own
  // payments for everyone else; the recent transactions are always the caller's own.
  const platformScope = roles.includes('admin');
  const { charts } = useColorMode();
  const report = useQuery({ queryKey: ['reports', 'dashboard', bk()], queryFn: fetchDashboardReport });
  const recent = useQuery({
    queryKey: ['transactions', 'recent', bk()],
    queryFn: () => fetchTransactions({ limit: 5 }),
  });

  const d = report.data;
  const successRate = d?.successRate ?? null;

  const monthly = d?.monthlyVolume ?? [];
  const { currency, currencies, setCurrency } = useReportCurrency(monthly);
  const chartData = monthly.filter((m) => m.currency === currency).map((m) => ({
    month: m.month,
    label: m.month.slice(5) + '/' + m.month.slice(2, 4),
    volumeMajor: m.volumeMinor / 100,
    count: m.count,
  }));
  const totalVolumeMinor = monthly
    .filter((m) => m.currency === currency)
    .reduce((sum, m) => sum + m.volumeMinor, 0);
  const animatedVolume = useCountUp(d ? totalVolumeMinor / 100 : null, 1200);
  const name = (customer?.email ?? '').split('@')[0];
  const recentItems = recent.data?.items ?? [];

  return (
    <>
      <PageHeader
        eyebrow="OVERVIEW"
        title="Dashboard"
        subtitle={
          platformScope
            ? 'Platform-wide payments, risk and operations at a glance.'
            : 'Your payments, risk and activity at a glance.'
        }
        actions={
          <>
            <Button variant="outlined" onClick={() => navigate('/beneficiaries')}>
              Add beneficiary
            </Button>
            <Button variant="contained" onClick={() => navigate('/payments/new')}>
              New payment
            </Button>
          </>
        }
      />

      <section className="hero">
        <span className="hero-orb" aria-hidden="true" />
        <div>
          <h2>
            {greeting()}
            {name ? `, ${name}` : ''} 👋
          </h2>
          <p>
            {platformScope
              ? 'You are viewing platform-wide figures across every customer.'
              : 'Every transfer is posted to a double-entry ledger, scored for fraud and announced over the event bus.'}
          </p>
        </div>
        <div className="hero-stat">
          <small>{platformScope ? 'PLATFORM VOLUME · 6 MONTHS' : 'YOUR VOLUME · 6 MONTHS'}</small>
          <strong>{animatedVolume === null ? '—' : formatMoney(Math.round(animatedVolume * 100), currency)}</strong>
        </div>
      </section>

      <DataPage
        isLoading={report.isLoading}
        isError={report.isError}
        error={report.error}
        onRetry={() => void report.refetch()}
        emptyTitle="No report data yet"
        emptyHint="Once payments flow through the platform, metrics appear here."
        emptyIllustration="reports"
        emptyAction={
          platformScope ? undefined : (
            <Button variant="contained" onClick={() => navigate('/payments/new')}>
              Make your first payment
            </Button>
          )
        }
        loading={<CardsSkeleton count={6} />}
        isEmpty={Boolean(d) && (d?.totals.payments ?? 0) === 0 && chartData.length === 0}
      >
        <div className="metrics-grid">
          <MetricCard label="TOTAL PAYMENTS" icon="↗" value={d?.totals.payments} foot="All payment attempts" />
          <MetricCard label="COMPLETED" icon="✓" iconTone="sky" value={d?.totals.completed} foot="Settled successfully" tone="accent" />
          <MetricCard label="FAILED" icon="✕" iconTone="rose" value={d?.totals.failed} foot="Rejected or expired" />
          <MetricCard
            label="SUCCESS RATE"
            icon="◔"
            iconTone="violet"
            value={successRate}
            format={(n) => n.toFixed(1)}
            suffix="%"
            foot="Completed / total"
            progress={successRate === null ? null : successRate / 100}
          />
          <MetricCard
            label="OPEN FRAUD CASES"
            icon="⚑"
            iconTone="amber"
            value={d?.fraudCases.open}
            foot={`${d?.fraudCases.total ?? 0} cases in total`}
          />
          <MetricCard
            label="OPEN AML ALERTS"
            icon="⚠"
            iconTone="rose"
            value={d?.amlAlerts.open}
            foot={`${d?.amlAlerts.total ?? 0} alerts in total`}
          />
        </div>

        <div className="section-heading">
          <div>
            <h2>Monthly volume</h2>
            <p>
              {platformScope ? 'Payment volume per month across the platform' : 'Your payment volume per month'}
              {currency ? `, in ${currency}.` : '.'}
            </p>
          </div>
          <CurrencyPicker currency={currency} currencies={currencies} onChange={setCurrency} />
        </div>
        <section className="panel panel-chart">
          {chartData.length === 0 ? (
            <p className="panel-muted">No volume data yet.</p>
          ) : (
            <ResponsiveContainer width="100%" height={290}>
              <BarChart data={chartData} margin={{ top: 12, right: 16, left: 8, bottom: 0 }} barCategoryGap="28%">
                <defs>
                  <linearGradient id="volumeFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={brand.mint} />
                    <stop offset="100%" stopColor={brand.teal} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="4 6" stroke={charts.grid} vertical={false} />
                <XAxis dataKey="label" tick={{ fontSize: 11, fill: charts.axis }} tickLine={false} axisLine={false} />
                <YAxis
                  tick={{ fontSize: 11, fill: charts.axis }}
                  tickLine={false}
                  axisLine={false}
                  width={56}
                  tickFormatter={(value: number) => formatCompact(value)}
                />
                <ChartTooltip
                  cursor={{ fill: charts.grid, opacity: 0.5 }}
                  contentStyle={charts.tooltip}
                  formatter={(value: unknown, _name: unknown, item: { payload?: { count?: number } }) => [
                    `${formatMoney(Math.round(Number(value) * 100), currency)} · ${item.payload?.count ?? 0} payments`,
                    'Volume',
                  ]}
                  labelFormatter={(label: unknown) => `Month ${String(label)}`}
                />
                <Bar
                  dataKey="volumeMajor"
                  fill="url(#volumeFill)"
                  radius={[8, 8, 0, 0]}
                  maxBarSize={56}
                  animationDuration={900}
                />
              </BarChart>
            </ResponsiveContainer>
          )}
        </section>
      </DataPage>

      <div className="section-heading">
        <div>
          <h2>{platformScope ? 'Your recent transactions' : 'Recent transactions'}</h2>
          <p>The five most recent movements on your own accounts.</p>
        </div>
        <Link to="/transactions" className="link-more">
          View all <span aria-hidden="true">→</span>
        </Link>
      </div>
      <DataPage
        isLoading={recent.isLoading}
        isError={recent.isError}
        error={recent.error}
        onRetry={() => void recent.refetch()}
        isEmpty={Boolean(recent.data) && recentItems.length === 0}
        emptyTitle="No transactions yet"
        emptyHint={
          platformScope
            ? 'This user has no accounts of its own; the figures above cover every customer.'
            : 'Transactions appear here after your first payment.'
        }
        emptyIllustration="transactions"
        loading={<TableSkeleton rows={4} columns={5} />}
      >
        <section className="panel panel-table">
          <table className="fc-table">
            <thead>
              <tr>
                <th>Occurred</th>
                <th>Direction</th>
                <th>Amount</th>
                <th>Status</th>
                <th>Reference</th>
              </tr>
            </thead>
            <tbody>
              {recentItems.map((tx) => (
                <tr key={tx.id}>
                  <td>
                    <Link to={`/transactions/${tx.id}`} className="fc-link">
                      {formatDate(tx.occurredAt)}
                    </Link>
                  </td>
                  <td>
                    <StatusChip status={tx.direction} label={tx.direction === 'debit' ? '↗ Debit' : '↙ Credit'} />
                  </td>
                  <td>
                    <CurrencyText amountMinor={tx.amountMinor} currency={tx.currency} />
                  </td>
                  <td>
                    <StatusChip status={tx.status} />
                  </td>
                  <td className="fc-cell-muted">{tx.reference ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </DataPage>
    </>
  );
}

export default DashboardPage;
