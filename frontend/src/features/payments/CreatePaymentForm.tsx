import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import MenuItem from '@mui/material/MenuItem';
import TextField from '@mui/material/TextField';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemText from '@mui/material/ListItemText';
import { createPayment, fetchAccounts, fetchBeneficiaries } from '../../api/endpoints';
import { getBackend } from '../../api/client';
import { useToast } from '../../hooks/useToast';
import { formatMoney, problemCode, problemMessage } from '../../utils/format';

const bk = () => getBackend();

const schema = z.object({
  sourceAccountId: z.string().min(1, 'Select a source account'),
  beneficiaryId: z.string().min(1, 'Select an active beneficiary'),
  amount: z
    .string()
    .min(1, 'Amount is required')
    .refine((v) => /^\d+(\.\d{1,2})?$/.test(v.trim()), 'Enter an amount like 12.34')
    .refine((v) => Number(v) > 0, 'Amount must be greater than zero'),
  reference: z.string().max(140, 'Max 140 characters').optional(),
});

type Values = z.infer<typeof schema>;

export function CreatePaymentForm() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { toastSuccess, toastError } = useToast();
  const [serverError, setServerError] = useState<string | null>(null);
  const [riskReasons, setRiskReasons] = useState<string[] | null>(null);

  const accounts = useQuery({ queryKey: ['accounts', bk()], queryFn: fetchAccounts });
  const beneficiaries = useQuery({ queryKey: ['beneficiaries', bk()], queryFn: fetchBeneficiaries });

  const {
    register,
    handleSubmit,
    watch,
    resetField,
    formState: { errors },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { sourceAccountId: '', beneficiaryId: '', amount: '', reference: '' },
  });

  const sourceAccountId = watch('sourceAccountId');
  const amount = watch('amount');

  const sourceAccount = useMemo(
    () => accounts.data?.items.find((a) => a.id === sourceAccountId) ?? null,
    [accounts.data, sourceAccountId],
  );
  const currency = sourceAccount?.currency ?? '';

  const activeBeneficiaries = useMemo(
    () => (beneficiaries.data?.items ?? []).filter((b) => b.status === 'active'),
    [beneficiaries.data],
  );

  const amountMinorPreview = useMemo(() => {
    if (!/^\d+(\.\d{1,2})?$/.test(amount.trim())) return null;
    return Math.round(Number(amount) * 100);
  }, [amount]);
  // A hint only: the backend rechecks the balance under the account lock and stays authoritative.
  const exceedsBalance =
    sourceAccount !== null && amountMinorPreview !== null && amountMinorPreview > sourceAccount.availableBalanceMinor;

  const mutation = useMutation({
    mutationFn: (values: Values) => {
      const body = {
        sourceAccountId: values.sourceAccountId,
        beneficiaryId: values.beneficiaryId,
        amountMinor: Math.round(Number(values.amount) * 100),
        currency,
        ...(values.reference ? { reference: values.reference.trim() } : {}),
      };
      // One fresh Idempotency-Key per submission.
      return createPayment(body, crypto.randomUUID());
    },
    onSuccess: (payment) => {
      void queryClient.invalidateQueries({ queryKey: ['payments'] });
      void queryClient.invalidateQueries({ queryKey: ['transactions'] });
      void queryClient.invalidateQueries({ queryKey: ['accounts'] });
      toastSuccess(
        payment.status === 'pending'
          ? `Payment ${payment.id.slice(0, 8)}… is held for review — funds are reserved`
          : `Payment ${payment.id.slice(0, 8)}… created`,
      );
      navigate(`/payments/${payment.id}`);
    },
    onError: (error) => {
      const message = problemMessage(error, 'Payment failed.');
      setServerError(message);
      if (problemCode(error) === 'fraud_blocked') {
        const data = (error as { response?: { data?: { errors?: unknown } } })?.response?.data;
        const reasons = (data as { errors?: string[] } | undefined)?.errors;
        setRiskReasons(Array.isArray(reasons) ? reasons : null);
      }
      toastError(message);
    },
  });

  return (
    <form
      className="panel form-panel"
      onSubmit={handleSubmit((v) => {
        setServerError(null);
        setRiskReasons(null);
        mutation.mutate(v);
      })}
      noValidate
    >
      {serverError ? <Alert severity="error">{serverError}</Alert> : null}
      {riskReasons && riskReasons.length > 0 ? (
        <Alert severity="warning">
          <strong>Blocked by fraud rules.</strong>
          <List dense disablePadding>
            {riskReasons.map((reason, i) => (
              <ListItem key={i} disableGutters>
                <ListItemText primary={`• ${reason}`} />
              </ListItem>
            ))}
          </List>
        </Alert>
      ) : null}

      <TextField
        select
        label="Source account"
        fullWidth
        disabled={accounts.isLoading || accounts.data?.items.length === 0}
        error={Boolean(errors.sourceAccountId)}
        helperText={
          errors.sourceAccountId?.message ??
          (accounts.data?.items.length === 0
            ? 'No accounts available'
            : sourceAccount
              ? `Available balance: ${formatMoney(sourceAccount.availableBalanceMinor, sourceAccount.currency)}`
              : undefined)
        }
        value={sourceAccountId}
        {...register('sourceAccountId', {
          onChange: () => {
            resetField('beneficiaryId');
          },
        })}
      >
        {(accounts.data?.items ?? []).map((account) => (
          <MenuItem key={account.id} value={account.id}>
            {account.id.slice(0, 8)}… · {account.accountType} · {formatMoney(account.availableBalanceMinor, account.currency)}
          </MenuItem>
        ))}
      </TextField>

      <TextField
        select
        label="Beneficiary (active only)"
        fullWidth
        disabled={activeBeneficiaries.length === 0}
        error={Boolean(errors.beneficiaryId)}
        helperText={
          errors.beneficiaryId?.message ??
          (activeBeneficiaries.length === 0 ? 'No active beneficiaries — verify and activate one first' : undefined)
        }
        value={watch('beneficiaryId')}
        {...register('beneficiaryId')}
      >
        {activeBeneficiaries.map((b) => (
          <MenuItem key={b.id} value={b.id}>
            {b.label} · {b.destinationAccountId.slice(0, 8)}…
          </MenuItem>
        ))}
      </TextField>

      <TextField
        label="Amount (major units)"
        fullWidth
        placeholder="100.00"
        error={Boolean(errors.amount) || exceedsBalance}
        helperText={
          errors.amount?.message ??
          (exceedsBalance && sourceAccount
            ? `More than the available ${formatMoney(sourceAccount.availableBalanceMinor, sourceAccount.currency)}`
            : amountMinorPreview !== null
            ? `You will send ${formatMoney(amountMinorPreview, currency)}`
            : 'Entered in major units; the minor-unit amount is computed automatically')
        }
        {...register('amount')}
      />

      <TextField
        label="Currency"
        fullWidth
        value={currency}
        disabled
        helperText={currency ? 'Auto-filled from the source account' : 'Choose a source account first'}
      />

      <TextField
        label="Reference (optional)"
        fullWidth
        placeholder="Invoice #42"
        error={Boolean(errors.reference)}
        helperText={errors.reference?.message}
        {...register('reference')}
      />

      <Button type="submit" variant="contained" size="large" disabled={mutation.isPending || !currency || exceedsBalance}>
        {mutation.isPending ? 'Sending…' : 'Send payment'}
      </Button>
    </form>
  );
}

export default CreatePaymentForm;
