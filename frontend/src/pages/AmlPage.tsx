import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import MenuItem from '@mui/material/MenuItem';
import TextField from '@mui/material/TextField';
import { fetchAmlAlerts, patchAmlAlertStatus } from '../api/endpoints';
import { getBackend } from '../api/client';
import PageHeader from '../components/PageHeader';
import DataPage, { TableSkeleton } from '../components/DataPage';
import StatusChip from '../components/StatusChip';
import { useAuth } from '../hooks/useAuth';
import { useToast } from '../hooks/useToast';
import { formatDate, problemMessage } from '../utils/format';
import type { AmlAlert } from '../types';

const bk = () => getBackend();

const NEXT_STATUSES = ['investigating', 'escalated', 'closed', 'false_positive'] as const;

export function AmlPage() {
  const [expanded, setExpanded] = useState<string | null>(null);
  const { roles } = useAuth();
  const canReview = ['admin', 'fraud_analyst', 'compliance_officer'].some((role) => roles.includes(role));
  const query = useQuery({ queryKey: ['aml', 'alerts', bk()], queryFn: () => fetchAmlAlerts({ limit: 50 }) });

  return (
    <>
      <PageHeader
        eyebrow="COMPLIANCE"
        title="AML alerts"
        subtitle="Anti-money-laundering rule hits requiring attention."
      />
      <DataPage
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        onRetry={() => void query.refetch()}
        isEmpty={(query.data?.items.length ?? 0) === 0}
        emptyTitle="No AML alerts"
        emptyHint="Rule violations detected on payments will appear here."
        loading={<TableSkeleton rows={4} columns={5} />}
      >
        <section className="panel panel-table">
          <table className="fc-table">
            <thead>
              <tr>
                <th></th>
                <th>Alert</th>
                <th>Rule</th>
                <th>Customer</th>
                <th>Payment</th>
                <th>Status</th>
                {canReview ? <th>Review</th> : null}
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {(query.data?.items ?? []).map((alert) => (
                <AlertRow
                  key={alert.id}
                  alert={alert}
                  expanded={expanded === alert.id}
                  onToggle={() => setExpanded(expanded === alert.id ? null : alert.id)}
                  canReview={canReview}
                />
              ))}
            </tbody>
          </table>
        </section>
      </DataPage>
    </>
  );
}

function AlertRow({
  alert,
  expanded,
  onToggle,
  canReview,
}: {
  alert: AmlAlert;
  expanded: boolean;
  onToggle: () => void;
  canReview: boolean;
}) {
  const queryClient = useQueryClient();
  const { toastSuccess } = useToast();
  const [status, setStatus] = useState('');
  const [note, setNote] = useState('');

  const review = useMutation({
    mutationFn: () => patchAmlAlertStatus(alert.id, { status, note: note.trim() || undefined }),
    onSuccess: (result) => {
      toastSuccess(`Alert moved to ${result.status}`);
      setStatus('');
      setNote('');
      void queryClient.invalidateQueries({ queryKey: ['aml', 'alerts'] });
    },
  });

  const columns = canReview ? 8 : 7;
  return (
    <>
      <tr className="expandable-row" onClick={onToggle}>
        <td className="expand-cell">
          <button className="expand-button" aria-label="Toggle details">
            {expanded ? '▾' : '▸'}
          </button>
        </td>
        <td className="fc-mono fc-cell-muted">{alert.id.slice(0, 8)}…</td>
        <td>
          <StatusChip status={alert.ruleCode} tone="blue" label={alert.ruleCode} />
        </td>
        <td className="fc-mono fc-cell-muted">{alert.customerId.slice(0, 8)}…</td>
        <td>
          {alert.paymentId ? (
            <Link to={`/payments/${alert.paymentId}`} className="fc-link fc-mono" onClick={(e) => e.stopPropagation()}>
              {alert.paymentId.slice(0, 8)}…
            </Link>
          ) : (
            <span className="fc-cell-muted">—</span>
          )}
        </td>
        <td>
          <StatusChip status={alert.status} />
        </td>
        {canReview ? (
          <td onClick={(e) => e.stopPropagation()}>
            {alert.status === 'closed' || alert.status === 'false_positive' ? (
              <StatusChip status={alert.status} label="Reviewed" />
            ) : (
              <form
                className="fc-inline-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (status) review.mutate();
                }}
              >
                <TextField
                  select
                  size="small"
                  value={status}
                  label="Move to"
                  onChange={(e) => setStatus(e.target.value)}
                  sx={{ minWidth: 150 }}
                >
                  {NEXT_STATUSES.map((value) => (
                    <MenuItem key={value} value={value}>
                      {value.replaceAll('_', ' ')}
                    </MenuItem>
                  ))}
                </TextField>
                <TextField
                  size="small"
                  label="Note (optional)"
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  sx={{ minWidth: 180 }}
                />
                <button type="submit" className="fc-save-button" disabled={!status || review.isPending}>
                  {review.isPending ? 'Saving…' : 'Update'}
                </button>
              </form>
            )}
            {review.isError ? (
              <div className="fc-cell-muted">{problemMessage(review.error, 'Status update failed.')}</div>
            ) : null}
          </td>
        ) : null}
        <td className="fc-cell-muted">{formatDate(alert.createdAt)}</td>
      </tr>
      {expanded ? (
        <tr className="expanded-row">
          <td colSpan={columns}>
            <pre className="fc-json">{JSON.stringify(alert.details, null, 2)}</pre>
          </td>
        </tr>
      ) : null}
    </>
  );
}

export default AmlPage;
