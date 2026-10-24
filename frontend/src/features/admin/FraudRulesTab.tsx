import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Switch from '@mui/material/Switch';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import { fetchFraudRules, patchFraudRule } from '../../api/endpoints';
import { getBackend } from '../../api/client';
import { useToast } from '../../hooks/useToast';
import { problemMessage } from '../../utils/format';
import Illustration from '../../components/Illustration';

const bk = () => getBackend();

export function FraudRulesTab() {
  const queryClient = useQueryClient();
  const { toastSuccess, toastError } = useToast();
  const [error, setError] = useState<string | null>(null);

  const query = useQuery({ queryKey: ['admin', 'fraud-rules', bk()], queryFn: fetchFraudRules });

  const mutation = useMutation({
    mutationFn: (input: { code: string; body: { enabled?: boolean; weight?: number; thresholdMinor?: number | null } }) =>
      patchFraudRule(input.code, input.body),
    onSuccess: (rule) => {
      void queryClient.invalidateQueries({ queryKey: ['admin', 'fraud-rules'] });
      toastSuccess(`Rule ${rule.code} updated`);
      setError(null);
    },
    onError: (e) => {
      const msg = problemMessage(e, 'Rule update failed (admin/analyst only).');
      setError(msg);
      toastError(msg);
      void query.refetch();
    },
  });

  // Local editable draft state keyed by rule code.
  const [drafts, setDrafts] = useState<Record<string, { weight: string; threshold: string }>>({});
  useEffect(() => {
    const next: Record<string, { weight: string; threshold: string }> = {};
    for (const rule of query.data?.items ?? []) {
      next[rule.code] = {
        weight: String(rule.weight),
        threshold: rule.thresholdMinor === null ? '' : String(rule.thresholdMinor / 100),
      };
    }
    setDrafts(next);
  }, [query.data]);

  if (query.isError) {
    const status = (query.error as { response?: { status?: number } })?.response?.status;
    if (status === 403) {
      return (
        <div className="fc-error" role="alert">
          <Illustration name="forbidden" />
          <strong>You do not have permission</strong>
          <p>Fraud rules require admin or fraud-analyst access.</p>
        </div>
      );
    }
    return <Alert severity="error">{problemMessage(query.error)}</Alert>;
  }

  function commit(code: string) {
    const draft = drafts[code];
    if (!draft) return;
    const weight = Number(draft.weight);
    const body: { enabled?: boolean; weight?: number; thresholdMinor?: number | null } = {};
    if (Number.isFinite(weight)) body.weight = Math.round(weight);
    if (draft.threshold.trim() === '') body.thresholdMinor = null;
    else {
      const threshold = Number(draft.threshold);
      if (Number.isFinite(threshold)) body.thresholdMinor = Math.round(threshold * 100);
    }
    mutation.mutate({ code, body });
  }

  return (
    <>
      {error ? (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      ) : null}
      <TableContainer>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Code</TableCell>
              <TableCell>Description</TableCell>
              <TableCell>Enabled</TableCell>
              <TableCell>Weight</TableCell>
              <TableCell>Threshold (major)</TableCell>
              <TableCell></TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {(query.data?.items ?? []).map((rule) => {
              const draft = drafts[rule.code] ?? { weight: String(rule.weight), threshold: '' };
              return (
                <TableRow key={rule.id}>
                  <TableCell className="fc-strong">{rule.code}</TableCell>
                  <TableCell className="fc-cell-muted">{rule.description}</TableCell>
                  <TableCell>
                    <Switch
                      checked={rule.enabled}
                      disabled={mutation.isPending}
                      onChange={(e) => mutation.mutate({ code: rule.code, body: { enabled: e.target.checked } })}
                    />
                  </TableCell>
                  <TableCell>
                    <TextField
                      size="small"
                      type="number"
                      value={draft.weight}
                      sx={{ width: 90 }}
                      onChange={(e) => setDrafts({ ...drafts, [rule.code]: { ...draft, weight: e.target.value } })}
                    />
                  </TableCell>
                  <TableCell>
                    <TextField
                      size="small"
                      type="number"
                      placeholder="No limit"
                      value={draft.threshold}
                      sx={{ width: 120 }}
                      onChange={(e) => setDrafts({ ...drafts, [rule.code]: { ...draft, threshold: e.target.value } })}
                    />
                  </TableCell>
                  <TableCell>
                    <button className="fc-save-button" disabled={mutation.isPending} onClick={() => commit(rule.code)}>
                      Save
                    </button>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </TableContainer>
    </>
  );
}

export default FraudRulesTab;
