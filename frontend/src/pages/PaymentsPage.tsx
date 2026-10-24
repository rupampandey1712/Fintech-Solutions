import { useState } from 'react';
import { Link } from 'react-router-dom';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import Button from '@mui/material/Button';
import { fetchPayments } from '../api/endpoints';
import { getBackend } from '../api/client';
import type { Payment } from '../types';
import PageHeader from '../components/PageHeader';
import DataPage, { TableSkeleton } from '../components/DataPage';
import LoadMoreButton from '../components/LoadMoreButton';
import StatusChip from '../components/StatusChip';
import { formatDate, formatMoney } from '../utils/format';

const bk = () => getBackend();

const STATUS_FILTERS = [
  { value: '', label: 'All' },
  { value: 'initiated', label: 'Initiated' },
  { value: 'pending', label: 'Pending' },
  { value: 'processing', label: 'Processing' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'cancelled', label: 'Cancelled' },
  { value: 'expired', label: 'Expired' },
  { value: 'reversed', label: 'Reversed' },
] as const;

const PAGE_SIZE = 20;

export function PaymentsPage() {
  const [status, setStatus] = useState<string>('');
  const [cursor, setCursor] = useState<string | null>(null);
  const [accumulated, setAccumulated] = useState<Payment[]>([]);

  const query = useQuery({
    queryKey: ['payments', bk(), status, cursor],
    queryFn: () => fetchPayments({ status: status || undefined, limit: PAGE_SIZE, cursor }),
    placeholderData: keepPreviousData,
  });

  function changeStatus(next: string) {
    setStatus(next);
    setCursor(null);
    setAccumulated([]);
  }

  function loadMore() {
    if (query.data?.nextCursor) {
      setAccumulated((prev) => [...prev, ...(query.data?.items ?? [])]);
      setCursor(query.data.nextCursor);
    }
  }

  const payments: Payment[] = [...accumulated, ...(query.data?.items ?? [])];
  const hasNext = Boolean(query.data?.nextCursor);

  return (
    <>
      <PageHeader
        eyebrow="MOVEMENTS"
        title="Payments"
        subtitle="Every payment attempt with live risk decisions."
        actions={
          <Button variant="contained" component={Link} to="/payments/new">
            New payment
          </Button>
        }
      />

      <div className="filter-chips" role="tablist" aria-label="Filter by status">
        {STATUS_FILTERS.map((f) => (
          <button
            key={f.value}
            className={`filter-chip${status === f.value ? ' active' : ''}`}
            onClick={() => changeStatus(f.value)}
          >
            {f.label}
          </button>
        ))}
      </div>

      <DataPage
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        onRetry={() => void query.refetch()}
        isEmpty={payments.length === 0}
        emptyTitle={status ? `No ${status} payments` : 'No payments yet'}
        emptyHint="Send your first payment to see it listed here."
        emptyAction={
          <Button variant="contained" component={Link} to="/payments/new">
            New payment
          </Button>
        }
        loading={<TableSkeleton rows={6} columns={6} />}
      >
        <section className="panel panel-table">
          <table className="fc-table">
            <thead>
              <tr>
                <th>Payment</th>
                <th>Amount</th>
                <th>Currency</th>
                <th>Status</th>
                <th>Risk</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {payments.map((payment) => (
                <tr key={payment.id}>
                  <td>
                    <Link to={`/payments/${payment.id}`} className="fc-link fc-mono">
                      {payment.id.slice(0, 8)}…
                    </Link>
                  </td>
                  <td className="fc-strong">{formatMoney(payment.amountMinor, payment.currency)}</td>
                  <td>{payment.currency}</td>
                  <td>
                    <StatusChip status={payment.status} />
                  </td>
                  <td>
                    {payment.risk.decision !== 'allow' ? (
                      <StatusChip
                        status={payment.risk.decision}
                        tone={payment.risk.decision === 'block' ? 'red' : 'amber'}
                        label={`${payment.risk.decision} · ${payment.risk.score}`}
                      />
                    ) : (
                      <span className="fc-cell-muted">allow · {payment.risk.score}</span>
                    )}
                  </td>
                  <td className="fc-cell-muted">{formatDate(payment.createdAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
        <LoadMoreButton hasNext={hasNext} isLoading={query.isFetching} onClick={loadMore} />
      </DataPage>
    </>
  );
}

export default PaymentsPage;
