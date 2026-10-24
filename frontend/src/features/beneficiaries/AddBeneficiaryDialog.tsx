import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import TextField from '@mui/material/TextField';
import { createBeneficiary } from '../../api/endpoints';
import { useToast } from '../../hooks/useToast';
import { isUuid, problemMessage } from '../../utils/format';

const schema = z.object({
  destinationAccountId: z
    .string()
    .min(1, 'Account ID is required')
    .refine((v) => isUuid(v), 'Enter a valid account ID (UUID)'),
  label: z.string().min(1, 'Label is required').max(80, 'Max 80 characters'),
});

type Values = z.infer<typeof schema>;

export function AddBeneficiaryDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const { toastSuccess, toastError } = useToast();
  const [serverError, setServerError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { destinationAccountId: '', label: '' },
  });

  const mutation = useMutation({
    mutationFn: createBeneficiary,
    onSuccess: (b) => {
      void queryClient.invalidateQueries({ queryKey: ['beneficiaries'] });
      toastSuccess(`Beneficiary “${b.label}” added`);
      reset();
      setServerError(null);
      onClose();
    },
    onError: (error) => {
      const message = problemMessage(error, 'Could not add the beneficiary.');
      const status = (error as { response?: { status?: number } })?.response?.status;
      if (status === 404) {
        setError('destinationAccountId', { message: 'No account found with this ID' });
      }
      setServerError(message);
      toastError(message);
    },
  });

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <form onSubmit={handleSubmit((v) => mutation.mutateAsync(v))} noValidate>
        <DialogTitle>Add beneficiary</DialogTitle>
        <DialogContent className="dialog-stack">
          <Alert severity="info">
            Paste any account ID below — the platform verifies ownership, then a cooling period applies before
            activation.
          </Alert>
          {serverError ? <Alert severity="error">{serverError}</Alert> : null}
          <TextField
            label="Destination account ID"
            fullWidth
            placeholder="00000000-0000-0000-0000-000000000000"
            error={Boolean(errors.destinationAccountId)}
            helperText={errors.destinationAccountId?.message}
            {...register('destinationAccountId')}
          />
          <TextField
            label="Label"
            fullWidth
            placeholder="Rent · Landlord"
            error={Boolean(errors.label)}
            helperText={errors.label?.message}
            {...register('label')}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="contained" disabled={mutation.isPending}>
            {mutation.isPending ? 'Adding…' : 'Add beneficiary'}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}

export default AddBeneficiaryDialog;
