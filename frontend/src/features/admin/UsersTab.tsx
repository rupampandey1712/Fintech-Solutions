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
import { fetchUsers, patchUserRoles } from '../../api/endpoints';
import { getBackend } from '../../api/client';
import { StatusChip } from '../../components/StatusChip';
import { useAuth } from '../../hooks/useAuth';
import { useToast } from '../../hooks/useToast';
import { formatDate, problemMessage } from '../../utils/format';
import type { AdminCustomer } from '../../types';
import Illustration from '../../components/Illustration';

const bk = () => getBackend();

export const KNOWN_ROLES = [
  'customer',
  'admin',
  'operations_user',
  'fraud_analyst',
  'compliance_officer',
  'finance_user',
];

export function UsersTab() {
  const query = useQuery({ queryKey: ['admin', 'users', bk()], queryFn: () => fetchUsers({ limit: 200 }) });

  if (query.isError) {
    const status = (query.error as { response?: { status?: number } })?.response?.status;
    if (status === 403) {
      return (
        <div className="fc-error" role="alert">
          <Illustration name="forbidden" />
          <strong>You do not have permission</strong>
          <p>User management requires administrator access.</p>
        </div>
      );
    }
    return <Alert severity="error">{problemMessage(query.error)}</Alert>;
  }

  return (
    <TableContainer>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Email</TableCell>
            <TableCell>Status</TableCell>
            <TableCell>Roles</TableCell>
            <TableCell>Created</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {(query.data?.items ?? []).map((user) => (
            <UserRow key={user.id} user={user} />
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

function UserRow({ user }: { user: AdminCustomer }) {
  const queryClient = useQueryClient();
  const { toastSuccess } = useToast();
  const { customer } = useAuth();
  // An admin cannot drop their own admin role (backend refuses it too).
  const isSelf = customer?.email === user.email;
  const isAdmin = user.roles.includes('admin');
  const [roles, setRoles] = useState<string[]>(user.roles);

  const save = useMutation({
    mutationFn: () => patchUserRoles(user.id, roles),
    onSuccess: (result) => {
      toastSuccess(`Roles updated for ${result.email}`);
      void queryClient.invalidateQueries({ queryKey: ['admin', 'users'] });
      void queryClient.invalidateQueries({ queryKey: ['admin', 'customers'] });
    },
  });

  return (
    <TableRow>
      <TableCell>{user.email}</TableCell>
      <TableCell>
        <StatusChip status={user.status} />
      </TableCell>
      <TableCell>
        <form
          className="fc-inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <TextField
            select
            size="small"
            SelectProps={{ multiple: true }}
            value={roles}
            onChange={(e) => setRoles(typeof e.target.value === 'string' ? [e.target.value] : e.target.value)}
            sx={{ minWidth: 260 }}
            disabled={isSelf && isAdmin}
            helperText={isSelf && isAdmin ? 'You cannot change your own admin roles.' : ' '}
          >
            {KNOWN_ROLES.map((role) => (
              <MenuItem key={role} value={role}>
                {role}
              </MenuItem>
            ))}
          </TextField>
          <button type="submit" className="fc-save-button" disabled={save.isPending || (isSelf && isAdmin)}>
            {save.isPending ? 'Saving…' : 'Save roles'}
          </button>
        </form>
        {save.isError ? <Alert severity="error">{problemMessage(save.error, 'Role update failed.')}</Alert> : null}
      </TableCell>
      <TableCell className="fc-cell-muted">{formatDate(user.createdAt)}</TableCell>
    </TableRow>
  );
}

export default UsersTab;
