import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import {
  fetchReconciliationRun,
  fetchReconciliationRuns,
  runReconciliation,
} from '../api/endpoints';
import { getBackend } from '../api/client';
import PageHeader from '../components/PageHeader';
import DataPage, { TableSkeleton } from '../components/DataPage';
import ErrorState from '../components/ErrorState';
import StatusChip from '../components/StatusChip';
import { useToast } from '../hooks/useToast';
import { formatDate, formatMoney, problemMessage } from '../utils/format';
import type { ReconciliationRecord, ReconciliationRun, ReconciliationRunSummary } from '../types';

const bk = () => getBackend();

function StatBadges({ run }: { run: { matched: number; amountMismatch: number; statusMismatch: number; missingExternal: number } }) {
  return (
    <div className="stat-badges">
      <span className="stat-badge green">✓ {run.matched} matched</span>
      <span className="stat-badge red">≈ {run.amountMismatch} amount mismatch</span>
      <span className="stat-badge amber">⇄ {run.statusMismatch} status mismatch</span>
      <span className="stat-badge gray">∅ {run.missingExternal} missing external</span>
    </div>
  );
}

function RecordsTable({ records }: { records: ReconciliationRecord[] }) {
  return (
    <table className="fc-table">
      <thead>
        <tr>
          <th>Payment</th>
          <th>Status</th>
          <th>Internal</th>
          <th>External</th>
          <th>Internal amount</th>
          <th>External amount</th>
          <th>External reference</th>
        </tr>
      </thead>
      <tbody>
        {records.map((r) => (
          <tr key={r.paymentId}>
            <td>
              <Link to={`/payments/${r.paymentId}`} className="fc-link fc-mono">
                {r.paymentId.slice(0, 8)}…
              </Link>
            </td>
            <td>
              <StatusChip status={r.status} />
            </td>
            <td className="fc-cell-muted">{r.internalStatus}</td>
            <td className="fc-cell-muted">{r.externalStatus}</td>
            <td>{formatMoney(r.internalAmountMinor, undefined)}</td>
            <td>{formatMoney(r.externalAmountMinor, undefined)}</td>
            <td className="fc-cell-muted fc-mono">{r.externalReference ?? '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ReconciliationPage() {
  const queryClient = useQueryClient();
  const { toastSuccess, toastError } = useToast();
  const [lastRun, setLastRun] = useState<ReconciliationRun | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  const runs = useQuery({
    queryKey: ['reconciliation', 'runs', bk()],
    queryFn: () => fetchReconciliationRuns({ limit: 50 }),
  });
  const detail = useQuery({
    queryKey: ['reconciliation', 'run', bk(), expandedId],
    queryFn: () => fetchReconciliationRun(expandedId!),
    enabled: expandedId !== null,
  });

  const runMutation = useMutation({
    mutationFn: runReconciliation,
    onSuccess: (run) => {
      setLastRun(run);
      setRunError(null);
      void queryClient.invalidateQueries({ queryKey: ['reconciliation'] });
      toastSuccess(`Run ${run.runId.slice(0, 8)}… finished: ${run.matched} matched`);
    },
    onError: (e) => {
      const msg = problemMessage(e, 'Reconciliation run failed.');
      setRunError(msg);
      toastError(msg);
    },
  });

  return (
    <>
      <PageHeader
        eyebrow="FINANCE OPS"
        title="Reconciliation"
        subtitle="Compare internal payment records with external processor data."
        actions={
          <Button variant="contained" disabled={runMutation.isPending} onClick={() => runMutation.mutate()}>
            {runMutation.isPending ? 'Running…' : 'Run reconciliation'}
          </Button>
        }
      />

      {runError ? (
        <Alert severity="error" className="page-alert" onClose={() => setRunError(null)}>
          {runError}
        </Alert>
      ) : null}

      {lastRun ? (
        <section className="panel run-result">
          <div className="panel-title-row">
            <h3>Latest run {lastRun.runId.slice(0, 8)}…</h3>
            <StatBadges run={lastRun} />
          </div>
          {lastRun.records.length === 0 ? (
            <p className="panel-muted">No records compared.</p>
          ) : (
            <RecordsTable records={lastRun.records} />
          )}
        </section>
      ) : null}

      <div className="section-heading">
        <div>
          <h2>Run history</h2>
          <p>Expand a run to load its compared records.</p>
        </div>
      </div>
      <DataPage
        isLoading={runs.isLoading}
        isError={runs.isError}
        error={runs.error}
        onRetry={() => void runs.refetch()}
        isEmpty={(runs.data?.items.length ?? 0) === 0}
        emptyTitle="No reconciliation runs yet"
        emptyHint="Trigger the first run with the button above."
        loading={<TableSkeleton rows={3} columns={5} />}
      >
        <section className="panel panel-table">
          <table className="fc-table">
            <thead>
              <tr>
                <th></th>
                <th>Run</th>
                <th>Started</th>
                <th>Finished</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {(runs.data?.items ?? []).map((run: ReconciliationRunSummary) => (
                <RunRow
                  key={run.id}
                  run={run}
                  expanded={expandedId === run.id}
                  onToggle={() => setExpandedId(expandedId === run.id ? null : run.id)}
                />
              ))}
            </tbody>
          </table>
        </section>
      </DataPage>

      {expandedId !== null ? (
        <section className="panel run-result">
          <div className="panel-title-row">
            <h3>Run {expandedId.slice(0, 8)}…</h3>
          </div>
          {detail.isLoading ? <TableSkeleton rows={3} columns={5} /> : null}
          {detail.isError ? <ErrorState error={detail.error} onRetry={() => void detail.refetch()} /> : null}
          {detail.data ? (
            <>
              <StatBadges run={detail.data} />
              {(detail.data.records?.length ?? 0) === 0 ? (
                <p className="panel-muted">No records stored for this run.</p>
              ) : (
                <RecordsTable records={detail.data.records} />
              )}
            </>
          ) : null}
        </section>
      ) : null}
    </>
  );
}

function RunRow({
  run,
  expanded,
  onToggle,
}: {
  run: ReconciliationRunSummary;
  expanded: boolean;
  onToggle: () => void;
}) {
  const mismatch = run.amountMismatch + run.statusMismatch + run.missingExternal;
  return (
    <tr className="expandable-row" onClick={onToggle} title={`${mismatch} mismatches total`}>
      <td className="expand-cell">
        <button className="expand-button" aria-label="Toggle run details">
          {expanded ? '▾' : '▸'}
        </button>
      </td>
      <td className="fc-mono">{run.id.slice(0, 8)}…</td>
      <td className="fc-cell-muted">{formatDate(run.startedAt)}</td>
      <td className="fc-cell-muted">{formatDate(run.finishedAt)}</td>
      <td>
        <StatBadges run={run} />
      </td>
    </tr>
  );
}

export default ReconciliationPage;
