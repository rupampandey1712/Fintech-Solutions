import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import MenuItem from '@mui/material/MenuItem';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import {
  createSettlementBatch,
  fetchSettlementBatches,
  fetchSettlementFile,
  processSettlementBatch,
} from '../../api/endpoints';
import { getBackend } from '../../api/client';
import { StatusChip } from '../../components/StatusChip';
import { useToast } from '../../hooks/useToast';
import { formatDate, formatMoney, problemMessage } from '../../utils/format';

const bk = () => getBackend();

function download({ blob, filename }: { blob: Blob; filename: string }) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

/** Settlement batches (spec section 58): one day's provider-settled payments, net of fees. */
export function SettlementsTab() {
  const queryClient = useQueryClient();
  const { toastSuccess } = useToast();
  const query = useQuery({
    queryKey: ['admin', 'settlement-batches', bk()],
    queryFn: () => fetchSettlementBatches(),
  });
  const [businessDate, setBusinessDate] = useState(new Date().toISOString().slice(0, 10));
  const [currency, setCurrency] = useState('USD');

  const create = useMutation({
    mutationFn: () => createSettlementBatch({ businessDate, currency }),
    onSuccess: (batch) => {
      toastSuccess(`Batch created with ${batch.paymentCount} payments`);
      void queryClient.invalidateQueries({ queryKey: ['admin', 'settlement-batches'] });
    },
  });
  const process = useMutation({
    mutationFn: (id: string) => processSettlementBatch(id),
    onSuccess: (batch) => {
      toastSuccess(`Batch ${batch.status} — net ${formatMoney(batch.netMinor, batch.currency)}`);
      void queryClient.invalidateQueries({ queryKey: ['admin', 'settlement-batches'] });
    },
  });
  const file = useMutation({
    mutationFn: (id: string) => fetchSettlementFile(id),
    onSuccess: download,
  });

  return (
    <>
      <section className="panel" style={{ padding: 16 }}>
        <h3>New settlement batch</h3>
        <p className="panel-muted">
          Groups one business day's provider-settled payments into a payout batch. Processing computes the
          net amount, renders the settlement file and marks the payments as settled.
        </p>
        <form
          className="fc-inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <TextField
            size="small"
            type="date"
            label="Business date"
            value={businessDate}
            onChange={(e) => setBusinessDate(e.target.value)}
            sx={{ minWidth: 180 }}
          />
          <TextField
            size="small"
            select
            label="Currency"
            value={currency}
            onChange={(e) => setCurrency(e.target.value)}
            sx={{ width: 120 }}
          >
            {['USD', 'EUR', 'GBP'].map((code) => (
              <MenuItem key={code} value={code}>
                {code}
              </MenuItem>
            ))}
          </TextField>
          <button type="submit" className="fc-save-button" disabled={create.isPending}>
            {create.isPending ? 'Creating…' : 'Create batch'}
          </button>
        </form>
        {create.isError ? <Alert severity="error">{problemMessage(create.error, 'Batch creation failed.')}</Alert> : null}
      </section>

      <TableContainer>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Business date</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Payments</TableCell>
              <TableCell>Gross</TableCell>
              <TableCell>Fees</TableCell>
              <TableCell>Net</TableCell>
              <TableCell>Processed</TableCell>
              <TableCell>Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {(query.data?.items ?? []).map((batch) => (
              <TableRow key={batch.id}>
                <TableCell>{batch.businessDate}</TableCell>
                <TableCell>
                  <StatusChip status={batch.status} />
                </TableCell>
                <TableCell>{batch.paymentCount}</TableCell>
                <TableCell className="fc-money">{formatMoney(batch.totalMinor, batch.currency)}</TableCell>
                <TableCell className="fc-money fc-cell-muted">{formatMoney(batch.feeMinor, batch.currency)}</TableCell>
                <TableCell className="fc-money fc-strong">{formatMoney(batch.netMinor, batch.currency)}</TableCell>
                <TableCell className="fc-cell-muted">
                  {batch.processedAt ? formatDate(batch.processedAt) : '—'}
                </TableCell>
                <TableCell>
                  <div className="fc-inline-form">
                    {batch.status === 'pending' ? (
                      <button
                        type="button"
                        className="fc-save-button"
                        disabled={process.isPending}
                        onClick={() => process.mutate(batch.id)}
                      >
                        {process.isPending ? 'Processing…' : 'Process'}
                      </button>
                    ) : null}
                    {batch.status === 'completed' ? (
                      <button
                        type="button"
                        className="fc-save-button"
                        disabled={file.isPending}
                        onClick={() => file.mutate(batch.id)}
                      >
                        {file.isPending ? 'Preparing…' : 'Download file'}
                      </button>
                    ) : null}
                    {batch.status === 'failed' ? (
                      <span className="fc-cell-muted">{batch.failureReason ?? 'Failed'}</span>
                    ) : null}
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {(query.data?.items.length ?? 0) === 0 && !query.isLoading ? (
          <p className="panel-muted" style={{ padding: 12 }}>
            No settlement batches yet.
          </p>
        ) : null}
      </TableContainer>
      {process.isError ? <Alert severity="error">{problemMessage(process.error, 'Processing failed.')}</Alert> : null}
      {file.isError ? <Alert severity="error">{problemMessage(file.error, 'File download failed.')}</Alert> : null}
    </>
  );
}

export default SettlementsTab;
