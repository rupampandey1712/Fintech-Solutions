import { useEffect, useState } from 'react';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import TextField from '@mui/material/TextField';
import { useToast } from '../../hooks/useToast';
import { problemMessage } from '../../utils/format';

// Flagged payments are held (status "pending") until this decision settles them.
const HOLD_EFFECT: Record<'closed' | 'false_positive' | 'escalated', string> = {
  false_positive: 'The held funds are released to the beneficiary and the payment completes.',
  closed: 'The held funds are returned to the customer and the payment is rejected.',
  escalated: 'No money moves. The payment stays on hold until a final decision.',
};

export function ReviewCaseDialog({
  open,
  loading,
  onClose,
  onConfirm,
  initialResolution = 'false_positive',
  initialNote = '',
}: {
  open: boolean;
  loading: boolean;
  /** Pre-filled from the investigator's draft; the reviewer can change anything before confirming. */
  initialResolution?: 'closed' | 'false_positive' | 'escalated';
  initialNote?: string;
  onClose: () => void;
  onConfirm: (resolution: 'closed' | 'false_positive' | 'escalated', note?: string) => void;
}) {
  const { toastError } = useToast();
  const [resolution, setResolution] = useState<'closed' | 'false_positive' | 'escalated'>(initialResolution);
  const [note, setNote] = useState(initialNote);

  // Each opening starts from the values it was opened with.
  useEffect(() => {
    if (open) {
      setResolution(initialResolution);
      setNote(initialNote);
    }
  }, [open, initialResolution, initialNote]);

  function submit() {
    try {
      onConfirm(resolution, note.trim() || undefined);
      setNote('');
    } catch (e) {
      toastError(problemMessage(e, 'Review failed.'));
    }
  }

  return (
    <Dialog open={open} onClose={loading ? undefined : onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Review fraud case</DialogTitle>
      <DialogContent className="dialog-stack">
        <TextField
          select
          label="Resolution"
          fullWidth
          value={resolution}
          onChange={(e) => setResolution(e.target.value as typeof resolution)}
          SelectProps={{ native: true }}
        >
          <option value="false_positive">False positive — release the payment</option>
          <option value="closed">Confirmed fraud — reject and refund</option>
          <option value="escalated">Escalate — keep the payment on hold</option>
        </TextField>
        <Alert severity={resolution === 'closed' ? 'warning' : resolution === 'false_positive' ? 'success' : 'info'}>
          {HOLD_EFFECT[resolution]}
        </Alert>
        <TextField
          label="Note (optional)"
          fullWidth
          multiline
          minRows={3}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
        <Alert severity="info" variant="outlined">
          Requires admin or fraud-analyst permissions; otherwise the API returns 403.
        </Alert>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={loading}>
          Cancel
        </Button>
        <Button onClick={submit} variant="contained" disabled={loading}>
          {loading ? 'Saving…' : 'Submit review'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export default ReviewCaseDialog;
