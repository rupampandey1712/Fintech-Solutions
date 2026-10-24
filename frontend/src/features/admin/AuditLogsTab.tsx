import { useQuery } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import { fetchAuditLogs } from '../../api/endpoints';
import { getBackend } from '../../api/client';
import { StatusChip } from '../../components/StatusChip';
import { formatDate, problemMessage } from '../../utils/format';
import Illustration from '../../components/Illustration';

const bk = () => getBackend();

/** First eight characters of an id, or a dash when there is none. */
function shortId(id: string | null): string {
  return id ? `${id.slice(0, 8)}…` : '—';
}

export function AuditLogsTab() {
  const query = useQuery({ queryKey: ['admin', 'audit-logs', bk()], queryFn: () => fetchAuditLogs({ limit: 100 }) });

  if (query.isError) {
    const status = (query.error as { response?: { status?: number } })?.response?.status;
    if (status === 403) {
      return (
        <div className="fc-error" role="alert">
          <Illustration name="forbidden" />
          <strong>You do not have permission</strong>
          <p>Audit logs require administrator access.</p>
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
            <TableCell>When</TableCell>
            <TableCell>Action</TableCell>
            <TableCell>Entity</TableCell>
            <TableCell>Actor</TableCell>
            <TableCell>IP</TableCell>
            <TableCell>Correlation</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {(query.data?.items ?? []).map((log) => (
            <TableRow key={log.id}>
              <TableCell className="fc-cell-muted">{formatDate(log.createdAt)}</TableCell>
              <TableCell>
                <StatusChip status={log.action} tone="blue" label={log.action} />
              </TableCell>
              <TableCell>
                <span className="fc-cell-muted">{log.entityType}</span>{' '}
                <span className="fc-mono">{log.entityId ? shortId(log.entityId) : null}</span>
              </TableCell>
              {/* No actor means nobody was identified, e.g. a failed login for an unknown email. */}
              <TableCell className="fc-mono fc-cell-muted">{log.actorId ? shortId(log.actorId) : 'anonymous'}</TableCell>
              <TableCell className="fc-cell-muted">{log.ipAddress ?? '—'}</TableCell>
              <TableCell className="fc-mono fc-cell-muted">{shortId(log.correlationId)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

export default AuditLogsTab;
