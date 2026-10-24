import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Tooltip from '@mui/material/Tooltip';
import {
  activateBeneficiary,
  disableBeneficiary,
  fetchBeneficiaries,
  verifyBeneficiary,
} from '../api/endpoints';
import { getBackend } from '../api/client';
import PageHeader from '../components/PageHeader';
import DataPage, { TableSkeleton } from '../components/DataPage';
import StatusChip from '../components/StatusChip';
import ConfirmDialog from '../components/ConfirmDialog';
import AddBeneficiaryDialog from '../features/beneficiaries/AddBeneficiaryDialog';
import { formatDate, problemMessage, timeAgo } from '../utils/format';
import type { Beneficiary } from '../types';

const bk = () => getBackend();

function coolingOver(b: Beneficiary): boolean {
  if (!b.coolingUntil) return false;
  return new Date(b.coolingUntil).getTime() <= Date.now();
}

export function BeneficiariesPage() {
  const queryClient = useQueryClient();
  const [addOpen, setAddOpen] = useState(false);
  const [disabling, setDisabling] = useState<Beneficiary | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [, setTick] = useState(0);

  // Re-render every 30s so "Activate" unlocks once coolingUntil passes.
  useEffect(() => {
    const t = setInterval(() => setTick((v) => v + 1), 30_000);
    return () => clearInterval(t);
  }, []);

  const query = useQuery({ queryKey: ['beneficiaries', bk()], queryFn: fetchBeneficiaries });

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ['beneficiaries'] });

  const verifyMutation = useMutation({
    mutationFn: verifyBeneficiary,
    onSuccess: invalidate,
    onError: (e) => setActionError(problemMessage(e, 'Verification failed.')),
  });
  const activateMutation = useMutation({
    mutationFn: activateBeneficiary,
    onSuccess: invalidate,
    onError: (e) => setActionError(problemMessage(e, 'Activation failed.')),
  });
  const disableMutation = useMutation({
    mutationFn: (id: string) => disableBeneficiary(id),
    onSuccess: () => {
      invalidate();
      setDisabling(null);
    },
    onError: (e) => setActionError(problemMessage(e, 'Disable failed.')),
  });

  const busy = verifyMutation.isPending || activateMutation.isPending || disableMutation.isPending;

  return (
    <>
      <PageHeader
        eyebrow="PAYEES"
        title="Beneficiaries"
        subtitle="Verified destination accounts for outgoing payments."
        actions={
          <Button variant="contained" onClick={() => setAddOpen(true)}>
            Add beneficiary
          </Button>
        }
      />

      <Alert severity="info" className="page-alert">
        <strong>Cooling period:</strong> newly verified beneficiaries stay in “cooling” until the configured period
        elapses. Activation unlocks automatically once the timer passes — use the Activate button then.
      </Alert>

      {actionError ? (
        <Alert severity="error" className="page-alert" onClose={() => setActionError(null)}>
          {actionError}
        </Alert>
      ) : null}

      <DataPage
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        onRetry={() => void query.refetch()}
        isEmpty={(query.data?.items.length ?? 0) === 0}
        emptyTitle="No beneficiaries yet"
        emptyHint="Add a destination account to start sending payments."
        emptyAction={
          <Button variant="contained" onClick={() => setAddOpen(true)}>
            Add beneficiary
          </Button>
        }
        loading={<TableSkeleton rows={4} columns={6} />}
      >
        <section className="panel panel-table">
          <table className="fc-table">
            <thead>
              <tr>
                <th>Label</th>
                <th>Destination account</th>
                <th>Status</th>
                <th>Cooling until</th>
                <th>Added</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {(query.data?.items ?? []).map((b) => {
                const over = coolingOver(b);
                return (
                  <tr key={b.id}>
                    <td className="fc-strong">{b.label}</td>
                    <td className="fc-mono fc-cell-muted">{b.destinationAccountId.slice(0, 8)}…</td>
                    <td>
                      <StatusChip status={b.status} />
                    </td>
                    <td className="fc-cell-muted">
                      {b.coolingUntil ? `${formatDate(b.coolingUntil)} (${timeAgo(b.coolingUntil)})` : '—'}
                    </td>
                    <td className="fc-cell-muted">{formatDate(b.createdAt)}</td>
                    <td>
                      <div className="row-actions">
                        {b.status === 'pending_verification' ? (
                          <Button size="small" variant="outlined" disabled={busy} onClick={() => verifyMutation.mutate(b.id)}>
                            Verify
                          </Button>
                        ) : null}
                        {b.status === 'cooling' ? (
                          <Tooltip title={over ? 'Cooling period has passed' : `Cooling until ${formatDate(b.coolingUntil)}`}>
                            <span>
                              <Button
                                size="small"
                                variant="outlined"
                                disabled={busy || !over}
                                onClick={() => activateMutation.mutate(b.id)}
                              >
                                Activate
                              </Button>
                            </span>
                          </Tooltip>
                        ) : null}
                        {b.status === 'active' ? (
                          <Button size="small" color="error" variant="outlined" disabled={busy} onClick={() => setDisabling(b)}>
                            Disable
                          </Button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      </DataPage>

      <AddBeneficiaryDialog open={addOpen} onClose={() => setAddOpen(false)} />
      <ConfirmDialog
        open={disabling !== null}
        title="Disable beneficiary"
        message={disabling ? <>Disable “{disabling.label}”? It can no longer receive payments.</> : ''}
        confirmLabel="Disable"
        confirmColor="error"
        loading={disableMutation.isPending}
        onConfirm={() => disabling && disableMutation.mutate(disabling.id)}
        onClose={() => setDisabling(null)}
      />
    </>
  );
}

export default BeneficiariesPage;
