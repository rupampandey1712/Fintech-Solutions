import { useState } from 'react';
import Alert from '@mui/material/Alert';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Button from '@mui/material/Button';
import TextField from '@mui/material/TextField';
import { fetchDispute, fetchPayment, fetchPaymentRefunds, openDispute } from '../api/endpoints';
import { getBackend } from '../api/client';
import PageHeader from '../components/PageHeader';
import DataPage from '../components/DataPage';
import ErrorState from '../components/ErrorState';
import StatusChip from '../components/StatusChip';
import PaymentRiskPanel from '../features/payments/PaymentRiskPanel';
import { useToast } from '../hooks/useToast';
import { formatDate, formatMoney, problemMessage } from '../utils/format';

const bk = () => getBackend();

const TIMELINE: { key: string; label: string }[] = [
  { key: 'initiated', label: 'Initiated' },
  { key: 'pending', label: 'Pending' },
  { key: 'processing', label: 'Processing' },
  { key: 'completed', label: 'Completed' },
];

function timelineIndex(status: string): number {
  const order = ['initiated', 'pending', 'processing', 'completed'];
  const idx = order.indexOf(status);
  if (idx >= 0) return idx;
  // Terminal failure-ish states render the timeline as halted.
  return 1;
}

export function PaymentDetailPage() {
  const { id = '' } = useParams();
  const query = useQuery({
    queryKey: ['payments', bk(), id],
    queryFn: () => fetchPayment(id),
    retry: (failureCount, error) => {
      const status = (error as { response?: { status?: number } })?.response?.status;
      return status !== 404 && failureCount < 1;
    },
  });

  if (query.isError) {
    const status = (query.error as { response?: { status?: number } })?.response?.status;
    if (status === 404) {
      return (
        <>
          <PageHeader eyebrow="MOVEMENTS" title="Payment" subtitle="Not found" />
          <ErrorState error={query.error} hint="this payment does not exist" />
        </>
      );
    }
  }

  const p = query.data;
  const terminalBad = p ? ['failed', 'rejected', 'cancelled', 'expired', 'reversed'].includes(p.status) : false;

  return (
    <>
      <PageHeader
        eyebrow="MOVEMENTS"
        title={`Payment ${p ? p.id.slice(0, 8) + '…' : ''}`}
        subtitle="Status, amount and risk decision."
        actions={
          <Button variant="outlined" component={Link} to="/payments">
            ← All payments
          </Button>
        }
      />
      <DataPage
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        onRetry={() => void query.refetch()}
        loading={null}
        isEmpty={false}
      >
        {p ? (
          <div className="detail-grid">
            <div className="detail-main">
              <section className="panel">
                <div className="panel-title-row">
                  <h3>Overview</h3>
                  <StatusChip status={p.status} size="medium" />
                </div>
                <dl className="fc-dl">
                  <div>
                    <dt>Amount</dt>
                    <dd className="fc-strong">{formatMoney(p.amountMinor, p.currency)}</dd>
                  </div>
                  <div>
                    <dt>Reference</dt>
                    <dd>{p.reference ?? '—'}</dd>
                  </div>
                  <div>
                    <dt>Payment ID</dt>
                    <dd className="fc-mono">{p.id}</dd>
                  </div>
                  <div>
                    <dt>Created</dt>
                    <dd>{formatDate(p.createdAt)}</dd>
                  </div>
                </dl>
              </section>

              {p.status === 'pending' && p.risk?.decision === 'review' ? (
                <Alert severity="warning" className="page-alert">
                  Held for fraud review. The amount has left your available balance and will reach the beneficiary once
                  a reviewer releases it, or be returned if the payment is rejected.
                </Alert>
              ) : null}
              <section className="panel">
                <h3>Status timeline</h3>
                <div className="timeline">
                  {TIMELINE.map((step, i) => {
                    const reached = !terminalBad && i <= timelineIndex(p.status);
                    const isCurrent = !terminalBad && step.key === p.status;
                    return (
                      <div key={step.key} className={`timeline-step${reached ? ' reached' : ''}${isCurrent ? ' current' : ''}`}>
                        <span className="timeline-dot" />
                        <span className="timeline-label">{step.label}</span>
                      </div>
                    );
                  })}
                </div>
                {terminalBad ? <p className="panel-muted">Payment ended in “{p.status.replaceAll('_', ' ')}”.</p> : null}
              </section>
            </div>
            <div className="detail-side">
              <PaymentRiskPanel risk={p.risk} />
              {p.status === 'completed' ? <DisputePanel paymentId={p.id} /> : null}
              <RefundsPanel paymentId={p.id} amountMinor={p.amountMinor} currency={p.currency} status={p.status} />
            </div>
          </div>
        ) : null}
      </DataPage>
    </>
  );
}

/** Chargeback simulation (spec section 61): the payer opens a dispute, staff investigate. */
function DisputePanel({ paymentId }: { paymentId: string }) {
  const queryClient = useQueryClient();
  const { toastSuccess } = useToast();
  const query = useQuery({
    queryKey: ['payments', paymentId, 'dispute', getBackend()],
    queryFn: () => fetchDispute(paymentId),
    retry: (failureCount, error) => {
      const status = (error as { response?: { status?: number } })?.response?.status;
      return status !== 404 && failureCount < 1;
    },
  });
  const [reason, setReason] = useState('');

  const open = useMutation({
    mutationFn: () => openDispute(paymentId, reason.trim()),
    onSuccess: () => {
      toastSuccess('Dispute opened — our team will investigate');
      setReason('');
      void queryClient.invalidateQueries({ queryKey: ['payments', paymentId, 'dispute'] });
    },
  });

  const dispute = query.data;
  if (query.isError) {
    const status = (query.error as { response?: { status?: number } })?.response?.status;
    if (status !== 404) return null; // an unexpected error stays silent here
  }
  if (dispute) {
    return (
      <section className="panel">
        <div className="panel-title-row">
          <h3>Dispute</h3>
          <StatusChip status={dispute.status} />
        </div>
        <dl className="fc-dl">
          <div>
            <dt>Reason</dt>
            <dd>{dispute.reason}</dd>
          </div>
          <div>
            <dt>Evidence entries</dt>
            <dd>{dispute.evidence.length}</dd>
          </div>
          <div>
            <dt>Opened</dt>
            <dd>{formatDate(dispute.createdAt)}</dd>
          </div>
          {dispute.resolution ? (
            <div>
              <dt>Decision</dt>
              <dd>{dispute.resolution}</dd>
            </div>
          ) : null}
        </dl>
      </section>
    );
  }
  return (
    <section className="panel">
      <h3>Dispute this payment</h3>
      <p className="panel-muted">
        Did not authorise this transfer? Open a dispute and our team will investigate (a chargeback review).
      </p>
      <form
        className="dialog-stack"
        onSubmit={(e) => {
          e.preventDefault();
          if (reason.trim().length >= 3) open.mutate();
        }}
      >
        {open.isError ? <Alert severity="error">{problemMessage(open.error, 'Could not open the dispute.')}</Alert> : null}
        <TextField
          label="Reason"
          size="small"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          inputProps={{ maxLength: 200 }}
          error={reason !== '' && reason.trim().length < 3}
          helperText={reason !== '' && reason.trim().length < 3 ? 'At least 3 characters' : ' '}
          fullWidth
        />
        <Button
          type="submit"
          variant="outlined"
          disabled={reason.trim().length < 3 || open.isPending}
        >
          {open.isPending ? 'Opening…' : 'Open dispute'}
        </Button>
      </form>
    </section>
  );
}

/** Refund history for the payer. Refunds and reversals themselves are issued by staff (Treasury). */
function RefundsPanel({ paymentId, amountMinor, currency, status }: { paymentId: string; amountMinor: number; currency: string; status: string }) {
  const refunds = useQuery({
    queryKey: ['payments', paymentId, 'refunds', getBackend()],
    queryFn: () => fetchPaymentRefunds(paymentId),
    enabled: status === 'completed' || status === 'reversed',
  });
  if (status === 'reversed') {
    return (
      <section className="panel">
        <h3>Correction</h3>
        <Alert severity="info">
          This payment was reversed by operations as a correction. Its original ledger entries are kept, with mirror
          entries returning {formatMoney(amountMinor, currency)} to your account.
        </Alert>
      </section>
    );
  }
  const data = refunds.data;
  if (!data || data.items.length === 0) return null;
  return (
    <section className="panel">
      <div className="panel-title-row">
        <h3>Refunds</h3>
        <StatusChip status={data.remainingMinor === 0 ? 'completed' : 'pending'} label={data.remainingMinor === 0 ? 'Fully refunded' : 'Partially refunded'} />
      </div>
      <ul className="fc-list">
        {data.items.map((refund) => (
          <li key={refund.id} className="fc-list-item" style={{ cursor: 'default' }}>
            <strong className="fc-money">{formatMoney(refund.amountMinor, refund.currency)}</strong>
            <span className="fc-cell-muted">
              {formatDate(refund.createdAt)}
              {refund.reason ? ` · ${refund.reason}` : ''}
            </span>
          </li>
        ))}
      </ul>
      <p className="panel-muted">
        Refunded {formatMoney(data.refundedTotalMinor, currency)} of {formatMoney(amountMinor, currency)}
      </p>
    </section>
  );
}

export default PaymentDetailPage;
