import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import TextField from '@mui/material/TextField';
import {
  fetchCustomerDeposits,
  fetchCustomerPayments,
  refundPayment,
  reverseJournalEntry,
  reversePayment,
} from '../../api/endpoints';
import { getBackend } from '../../api/client';
import DataPage, { TableSkeleton } from '../../components/DataPage';
import StatusChip from '../../components/StatusChip';
import { useToast } from '../../hooks/useToast';
import type { AdminDeposit, AdminPayment } from '../../types';
import { formatDate, formatMoney, majorToMinor, problemMessage } from '../../utils/format';

type Action =
  | { kind: 'refund'; payment: AdminPayment }
  | { kind: 'reverse-payment'; payment: AdminPayment }
  | { kind: 'reverse-deposit'; deposit: AdminDeposit };

/** Staff corrections for one customer: refunds and reversals of their payments, reversals of deposits. */
export function CorrectionsPanel({ customerId }: { customerId: string }) {
  const [action, setAction] = useState<Action | null>(null);
  const payments = useQuery({
    queryKey: ['admin', 'customer-payments', customerId, getBackend()],
    queryFn: () => fetchCustomerPayments(customerId),
  });
  const deposits = useQuery({
    queryKey: ['admin', 'customer-deposits', customerId, getBackend()],
    queryFn: () => fetchCustomerDeposits(customerId),
  });

  return (
    <>
      <section className="panel panel-table">
        <div className="panel-title-row" style={{ padding: '0 16px' }}>
          <h3>Outgoing payments</h3>
        </div>
        <DataPage
          isLoading={payments.isLoading}
          isError={payments.isError}
          error={payments.error}
          isEmpty={(payments.data?.items.length ?? 0) === 0}
          emptyTitle="No payments"
          loading={<TableSkeleton rows={3} columns={4} />}
        >
          <table className="fc-table">
            <thead>
              <tr>
                <th>Amount</th>
                <th>Refunded</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(payments.data?.items ?? []).map((payment) => (
                <tr key={payment.id}>
                  <td>
                    <span className="fc-strong fc-money">{formatMoney(payment.amountMinor, payment.currency)}</span>
                    <div className="fc-cell-muted">{payment.reference ?? formatDate(payment.createdAt)}</div>
                  </td>
                  <td className="fc-money">{payment.refundedMinor ? formatMoney(payment.refundedMinor, payment.currency) : '—'}</td>
                  <td>
                    <StatusChip status={payment.status} />
                  </td>
                  <td>
                    <div className="row-actions">
                      <button
                        type="button"
                        className="fc-save-button"
                        disabled={payment.status !== 'completed' || payment.refundedMinor >= payment.amountMinor}
                        onClick={() => setAction({ kind: 'refund', payment })}
                      >
                        Refund
                      </button>
                      <button
                        type="button"
                        className="fc-save-button"
                        disabled={payment.status !== 'completed' || payment.refundedMinor > 0}
                        onClick={() => setAction({ kind: 'reverse-payment', payment })}
                      >
                        Reverse
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </DataPage>
      </section>

      <section className="panel panel-table">
        <div className="panel-title-row" style={{ padding: '0 16px' }}>
          <h3>Deposits</h3>
        </div>
        <DataPage
          isLoading={deposits.isLoading}
          isError={deposits.isError}
          error={deposits.error}
          isEmpty={(deposits.data?.items.length ?? 0) === 0}
          emptyTitle="No deposits"
          loading={<TableSkeleton rows={2} columns={3} />}
        >
          <table className="fc-table">
            <tbody>
              {(deposits.data?.items ?? []).map((deposit) => (
                <tr key={deposit.journalId}>
                  <td>
                    <span className="fc-strong fc-money">{formatMoney(deposit.amountMinor, deposit.currency)}</span>
                    <div className="fc-cell-muted">{formatDate(deposit.postedAt)}</div>
                  </td>
                  <td>{deposit.reversed ? <StatusChip status="reversed" /> : <StatusChip status="completed" label="Posted" />}</td>
                  <td>
                    <button
                      type="button"
                      className="fc-save-button"
                      disabled={deposit.reversed}
                      onClick={() => setAction({ kind: 'reverse-deposit', deposit })}
                    >
                      Reverse
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </DataPage>
      </section>

      {action ? <ActionDialog action={action} customerId={customerId} onClose={() => setAction(null)} /> : null}
    </>
  );
}

function ActionDialog({ action, customerId, onClose }: { action: Action; customerId: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const { toastSuccess } = useToast();
  const isRefund = action.kind === 'refund';
  const remaining = isRefund ? action.payment.amountMinor - action.payment.refundedMinor : 0;
  const currency = action.kind === 'reverse-deposit' ? action.deposit.currency : action.payment.currency;
  const [amount, setAmount] = useState(isRefund ? (remaining / 100).toFixed(2) : '');
  const [reason, setReason] = useState('');
  // One key per dialog: a retry after a timeout replays the first refund instead of refunding twice.
  const [idempotencyKey] = useState(() => crypto.randomUUID());
  const amountMinor = majorToMinor(amount);
  const amountValid = !isRefund || (amountMinor !== null && amountMinor > 0 && amountMinor <= remaining);
  const reasonValid = isRefund || reason.trim().length >= 3;

  const mutation = useMutation({
    mutationFn: async () => {
      if (action.kind === 'refund')
        return refundPayment(action.payment.id, { amountMinor: amountMinor!, reason: reason.trim() || undefined }, idempotencyKey);
      if (action.kind === 'reverse-payment')
        return reversePayment(action.payment.id, reason.trim(), idempotencyKey);
      return reverseJournalEntry(action.deposit.journalId, reason.trim(), idempotencyKey);
    },
    onSuccess: () => {
      toastSuccess(isRefund ? `Refunded ${formatMoney(amountMinor, currency)}` : 'Reversal posted');
      void queryClient.invalidateQueries({ queryKey: ['admin', 'customer-payments', customerId] });
      void queryClient.invalidateQueries({ queryKey: ['admin', 'customer-deposits', customerId] });
      void queryClient.invalidateQueries({ queryKey: ['admin', 'customer-accounts'] });
      onClose();
    },
  });

  const title = isRefund ? 'Refund payment' : action.kind === 'reverse-payment' ? 'Reverse payment' : 'Reverse deposit';
  const effect = isRefund
    ? `Moves money back from the recipient to the payer. Up to ${formatMoney(remaining, currency)} can still be refunded; the recipient must have the funds.`
    : action.kind === 'reverse-payment'
      ? 'Corrects a payment that should not have happened: every posting is mirrored and the full amount returns to the payer, even if the recipient is left with a negative balance. The originals are kept and the payment becomes "reversed".'
      : 'Corrects a deposit posted in error: the treasury entry is mirrored and the amount leaves the customer account. The original entry is kept.';

  return (
    <Dialog open onClose={mutation.isPending ? undefined : onClose} maxWidth="xs" fullWidth>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent className="dialog-stack">
        <Alert severity={isRefund ? 'info' : 'warning'}>{effect}</Alert>
        {mutation.isError ? <Alert severity="error">{problemMessage(mutation.error, 'The correction failed.')}</Alert> : null}
        {isRefund ? (
          <TextField
            label={`Amount (${currency})`}
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            inputProps={{ inputMode: 'decimal' }}
            error={!amountValid}
            helperText={amountValid ? ' ' : `Enter an amount up to ${formatMoney(remaining, currency)}`}
            fullWidth
          />
        ) : null}
        <TextField
          label={isRefund ? 'Reason (optional)' : 'Reason (required, kept in the audit log)'}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          inputProps={{ maxLength: 200 }}
          fullWidth
          multiline
          minRows={2}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={mutation.isPending}>
          Cancel
        </Button>
        <Button
          variant="contained"
          color={isRefund ? 'primary' : 'error'}
          disabled={!amountValid || !reasonValid || mutation.isPending}
          onClick={() => mutation.mutate()}
        >
          {mutation.isPending ? 'Posting…' : isRefund ? 'Refund' : 'Reverse'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export default CorrectionsPanel;
