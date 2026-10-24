import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import TextField from '@mui/material/TextField';
import Alert from '@mui/material/Alert';
import { createAccount } from '../../api/endpoints';
import { useToast } from '../../hooks/useToast';
import { problemMessage } from '../../utils/format';

const schema = z.object({
  accountType: z.enum(['checking', 'savings', 'wallet', 'merchant'], { message: 'Choose an account type' }),
  currency: z.string().regex(/^[A-Z]{3}$/, 'Use a 3-letter uppercase currency code (e.g. USD)'),
});

type Values = z.infer<typeof schema>;

export function OpenAccountDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const { toastSuccess, toastError } = useToast();
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { accountType: 'checking', currency: 'USD' },
  });
  const [serverError, setServerError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: createAccount,
    onSuccess: (account) => {
      void queryClient.invalidateQueries({ queryKey: ['accounts'] });
      toastSuccess(`Account ${account.id.slice(0, 8)}… opened`);
      reset();
      setServerError(null);
      onClose();
    },
    onError: (error) => {
      const message = problemMessage(error, 'Could not open the account.');
      setServerError(message);
      toastError(message);
    },
  });

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <form onSubmit={handleSubmit((v) => mutation.mutateAsync(v))} noValidate>
        <DialogTitle>Open account</DialogTitle>
        <DialogContent className="dialog-stack">
          {serverError ? <Alert severity="error">{serverError}</Alert> : null}
          <TextField
            select
            label="Account type"
            fullWidth
            defaultValue="checking"
            error={Boolean(errors.accountType)}
            helperText={errors.accountType?.message}
            SelectProps={{ native: true }}
            inputProps={register('accountType')}
          >
            <option value="checking">Checking</option>
            <option value="savings">Savings</option>
            <option value="wallet">Wallet</option>
            <option value="merchant">Merchant</option>
          </TextField>
          <TextField
            label="Currency"
            fullWidth
            placeholder="USD"
            inputProps={{ style: { textTransform: 'uppercase' } }}
            error={Boolean(errors.currency)}
            helperText={errors.currency?.message ?? 'ISO 4217 code, e.g. USD, EUR, GBP'}
            {...register('currency', { setValueAs: (v: string) => v.toUpperCase().trim() })}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="contained" disabled={mutation.isPending}>
            {mutation.isPending ? 'Opening…' : 'Open account'}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}

export default OpenAccountDialog;
