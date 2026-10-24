import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import { fetchDeadLetters, replayDeadLetter } from '../../api/endpoints';
import { getBackend } from '../../api/client';
import { StatusChip } from '../../components/StatusChip';
import { useToast } from '../../hooks/useToast';
import { formatDate, problemMessage } from '../../utils/format';
import Illustration from '../../components/Illustration';

const bk = () => getBackend();

/** Outbox events that exhausted their retries (spec sections 18-19), with manual replay. */
export function DeadLettersTab() {
  const queryClient = useQueryClient();
  const { toastSuccess } = useToast();
  const query = useQuery({
    queryKey: ['admin', 'dead-letters', bk()],
    queryFn: () => fetchDeadLetters({ limit: 200 }),
  });

  const replay = useMutation({
    mutationFn: (id: string) => replayDeadLetter(id),
    onSuccess: () => {
      toastSuccess('Event re-queued for delivery');
      void queryClient.invalidateQueries({ queryKey: ['admin', 'dead-letters'] });
    },
  });

  if (query.isError) {
    const status = (query.error as { response?: { status?: number } })?.response?.status;
    if (status === 403) {
      return (
        <div className="fc-error" role="alert">
          <Illustration name="forbidden" />
          <strong>You do not have permission</strong>
          <p>Dead letters require admin or operations access.</p>
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
            <TableCell>Event</TableCell>
            <TableCell>Outbox id</TableCell>
            <TableCell>Failure</TableCell>
            <TableCell>When</TableCell>
            <TableCell>Replay</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {(query.data?.items ?? []).map((letter) => (
            <TableRow key={letter.id}>
              <TableCell>
                <StatusChip status={letter.eventType} tone="blue" label={letter.eventType} />
              </TableCell>
              <TableCell className="fc-mono fc-cell-muted">{letter.outboxEventId.slice(0, 8)}…</TableCell>
              <TableCell className="fc-cell-muted">{letter.failureReason}</TableCell>
              <TableCell className="fc-cell-muted">{formatDate(letter.createdAt)}</TableCell>
              <TableCell>
                {letter.replayed ? (
                  <StatusChip status="replayed" label="Replayed" />
                ) : (
                  <button
                    type="button"
                    className="fc-save-button"
                    disabled={replay.isPending}
                    onClick={() => replay.mutate(letter.id)}
                  >
                    {replay.isPending ? 'Replaying…' : 'Replay'}
                  </button>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {replay.isError ? <Alert severity="error">{problemMessage(replay.error, 'Replay failed.')}</Alert> : null}
    </TableContainer>
  );
}

export default DeadLettersTab;
