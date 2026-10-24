import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import { fetchAdminCustomers, patchAdminCustomerStatus } from '../../api/endpoints';
import { getBackend } from '../../api/client';
import { useToast } from '../../hooks/useToast';
import { StatusChip } from '../../components/StatusChip';
import { formatDate, problemMessage } from '../../utils/format';
import Illustration from '../../components/Illustration';

const bk = () => getBackend();

const CUSTOMER_STATUSES = ['pending', 'active', 'suspended', 'blocked', 'closed'];

export function CustomersTab() {
  const queryClient = useQueryClient();
  const { toastSuccess, toastError } = useToast();
  const [rowError, setRowError] = useState<string | null>(null);

  const query = useQuery({ queryKey: ['admin', 'customers', bk()], queryFn: () => fetchAdminCustomers({ limit: 50 }) });

  const mutation = useMutation({
    mutationFn: (input: { id: string; status: string }) => patchAdminCustomerStatus(input.id, input.status),
    onSuccess: (customer) => {
      void queryClient.invalidateQueries({ queryKey: ['admin', 'customers'] });
      toastSuccess(`Customer ${customer.email} → ${customer.status}`);
      setRowError(null);
    },
    onError: (e) => {
      setRowError(problemMessage(e, 'Status change failed (admin only).'));
      toastError(problemMessage(e, 'Status change failed (admin only).'));
      void query.refetch();
    },
  });

  if (query.isError) {
    const status = (query.error as { response?: { status?: number } })?.response?.status;
    return status === 403 ? <PermissionNotice /> : <Alert severity="error">{problemMessage(query.error)}</Alert>;
  }

  return (
    <>
      {rowError ? (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setRowError(null)}>
          {rowError}
        </Alert>
      ) : null}
      <TableContainer>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Customer</TableCell>
              <TableCell>Email</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Roles</TableCell>
              <TableCell>Created</TableCell>
              <TableCell>Set status</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {(query.data?.items ?? []).map((c) => (
              <TableRow key={c.id}>
                <TableCell className="fc-mono">{c.id.slice(0, 8)}…</TableCell>
                <TableCell>{c.email}</TableCell>
                <TableCell>
                  <StatusChip status={c.status} />
                </TableCell>
                <TableCell>
                  <div className="role-chips">
                    {c.roles.length === 0 ? <span className="fc-cell-muted">—</span> : c.roles.map((r) => <Chip key={r} label={r} />)}
                  </div>
                </TableCell>
                <TableCell className="fc-cell-muted">{formatDate(c.createdAt)}</TableCell>
                <TableCell>
                  <TextField
                    select
                    size="small"
                    value={c.status}
                    disabled={mutation.isPending}
                    onChange={(e) => mutation.mutate({ id: c.id, status: e.target.value })}
                    SelectProps={{ native: true }}
                    sx={{ minWidth: 130 }}
                  >
                    {CUSTOMER_STATUSES.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </TextField>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </>
  );
}

function Chip({ label }: { label: string }) {
  return <span className="fc-chip fc-chip-blue role-chip">{label}</span>;
}

export function PermissionNotice() {
  return (
    <div className="fc-error" role="alert">
      <Illustration name="forbidden" />
      <strong>You do not have permission</strong>
      <p>This section requires administrator access. Sign in with an admin account.</p>
    </div>
  );
}

export default CustomersTab;
