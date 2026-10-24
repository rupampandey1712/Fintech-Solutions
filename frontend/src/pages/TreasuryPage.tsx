import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Tab from '@mui/material/Tab';
import Tabs from '@mui/material/Tabs';
import TextField from '@mui/material/TextField';
import { createDeposit, fetchAdminCustomers, fetchCustomerAccounts } from '../api/endpoints';
import { getBackend } from '../api/client';
import PageHeader from '../components/PageHeader';
import DataPage, { TableSkeleton } from '../components/DataPage';
import EmptyState from '../components/EmptyState';
import StatusChip from '../components/StatusChip';
import { CorrectionsPanel } from '../features/treasury/CorrectionsPanel';
import { SettlementsTab } from '../features/treasury/SettlementsTab';
import { useToast } from '../hooks/useToast';
import { formatMoney, majorToMinor, problemMessage } from '../utils/format';

const bk = () => getBackend();
const newKey = () => crypto.randomUUID();

export function TreasuryPage() {
  const queryClient = useQueryClient();
  const { toastSuccess } = useToast();
  const [tab, setTab] = useState(0);
  const [search, setSearch] = useState('');
  const [customerId, setCustomerId] = useState<string | null>(null);
  const [accountId, setAccountId] = useState<string | null>(null);
  const [amount, setAmount] = useState('');
  const [reference, setReference] = useState('');
  // One key per deposit intent: re-submitting after a timeout reuses it, so the API replays the
  // first result instead of crediting twice. It changes only after success or a form edit.
  const [idempotencyKey, setIdempotencyKey] = useState(newKey);

  const customers = useQuery({ queryKey: ['admin', 'customers', bk()], queryFn: () => fetchAdminCustomers({ limit: 500 }) });
  const accounts = useQuery({
    queryKey: ['admin', 'customer-accounts', customerId, bk()],
    queryFn: () => fetchCustomerAccounts(customerId!),
    enabled: customerId !== null,
  });

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    const items = customers.data?.items ?? [];
    return term ? items.filter((item) => item.email.toLowerCase().includes(term)) : items;
  }, [customers.data, search]);
  const selectedAccount = accounts.data?.items.find((item) => item.id === accountId) ?? null;
  const amountMinor = majorToMinor(amount);
  const amountValid = amountMinor !== null && amountMinor > 0;

  const edit = <T,>(setter: (value: T) => void) => (value: T) => {
    setter(value);
    setIdempotencyKey(newKey());
  };

  const deposit = useMutation({
    mutationFn: () => createDeposit(accountId!, { amountMinor: amountMinor!, reference: reference.trim() || undefined }, idempotencyKey),
    onSuccess: (result) => {
      toastSuccess(`Deposited ${formatMoney(result.amountMinor, result.currency)} — new balance ${formatMoney(result.balanceMinor, result.currency)}`);
      setAmount('');
      setReference('');
      setIdempotencyKey(newKey());
      void queryClient.invalidateQueries({ queryKey: ['admin', 'customer-accounts'] });
    },
  });

  return (
    <>
      <PageHeader
        eyebrow="OPERATIONS"
        title="Treasury"
        subtitle="Fund customer accounts, refund payments and reverse postings made in error. Every action is a balanced, audited ledger entry."
      />
      <div className="detail-grid treasury-grid">
        <section className="panel">
          <div className="panel-title-row">
            <h3>Customers</h3>
          </div>
          <TextField
            size="small"
            fullWidth
            placeholder="Search by email"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            sx={{ mb: 1.5 }}
          />
          <DataPage
            isLoading={customers.isLoading}
            isError={customers.isError}
            error={customers.error}
            onRetry={() => void customers.refetch()}
            isEmpty={filtered.length === 0}
            emptyTitle="No customers found"
            loading={<TableSkeleton rows={4} columns={1} />}
          >
            <ul className="fc-list treasury-customers">
              {filtered.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className={`fc-list-item${item.id === customerId ? ' selected' : ''}`}
                    onClick={() => {
                      setCustomerId(item.id);
                      setAccountId(null);
                      setIdempotencyKey(newKey());
                    }}
                  >
                    <strong>{item.email}</strong>
                    <span className="fc-cell-muted">{item.roles.join(' · ')}</span>
                  </button>
                </li>
              ))}
            </ul>
          </DataPage>
        </section>

        <section className="detail-side">
          <Tabs value={tab} onChange={(_, value: number) => setTab(value)} sx={{ mb: 2, minHeight: 40 }}>
            <Tab label="Deposits" sx={{ minHeight: 40 }} />
            <Tab label="Refunds & reversals" sx={{ minHeight: 40 }} />
            <Tab label="Settlements" sx={{ minHeight: 40 }} />
          </Tabs>
          {tab === 1 ? (
            customerId === null ? (
              <section className="panel">
                <p className="panel-muted">Select a customer to see their payments and deposits.</p>
              </section>
            ) : (
              <CorrectionsPanel customerId={customerId} />
            )
          ) : tab === 2 ? (
            <SettlementsTab />
          ) : (
            <>
              <section className="panel">
                <h3>Accounts</h3>
                {customerId === null ? (
                  <p className="panel-muted">Select a customer to see their accounts.</p>
                ) : (
                  <DataPage
                    isLoading={accounts.isLoading}
                    isError={accounts.isError}
                    error={accounts.error}
                    onRetry={() => void accounts.refetch()}
                    isEmpty={(accounts.data?.items.length ?? 0) === 0}
                    emptyTitle="No accounts"
                    emptyHint="This customer has not opened an account yet."
                    loading={<TableSkeleton rows={2} columns={3} />}
                  >
                    <ul className="fc-list">
                      {(accounts.data?.items ?? []).map((item) => (
                        <li key={item.id}>
                          <button
                            type="button"
                            className={`fc-list-item${item.id === accountId ? ' selected' : ''}`}
                            onClick={() => edit(setAccountId)(item.id)}
                            disabled={item.status !== 'active'}
                          >
                            <strong className="fc-money">{formatMoney(item.balanceMinor, item.currency)}</strong>
                            <span className="fc-cell-muted">
                              {item.accountType} · <span className="fc-mono">{item.id.slice(0, 8)}</span>{' '}
                              <StatusChip status={item.status} />
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </DataPage>
                )}
              </section>

              <section className="panel">
                <h3>Deposit</h3>
                {selectedAccount === null ? (
                  <EmptyState illustration="treasury" title="Choose an account" hint="Deposits credit the selected account from the treasury ledger." />
                ) : (
                  <form
                    className="dialog-stack"
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (amountValid) deposit.mutate();
                    }}
                  >
                    {deposit.isError ? <Alert severity="error">{problemMessage(deposit.error, 'Deposit failed.')}</Alert> : null}
                    <TextField
                      label={`Amount (${selectedAccount.currency})`}
                      value={amount}
                      onChange={(e) => edit(setAmount)(e.target.value)}
                      inputProps={{ inputMode: 'decimal' }}
                      error={amount !== '' && !amountValid}
                      helperText={amount !== '' && !amountValid ? 'Enter a positive amount' : ' '}
                      fullWidth
                    />
                    <TextField
                      label="Reference (optional)"
                      value={reference}
                      onChange={(e) => edit(setReference)(e.target.value)}
                      inputProps={{ maxLength: 140 }}
                      fullWidth
                    />
                    <Button type="submit" variant="contained" size="large" disabled={!amountValid || deposit.isPending}>
                      {deposit.isPending
                        ? 'Posting…'
                        : `Deposit ${amountValid ? formatMoney(amountMinor, selectedAccount.currency) : ''}`.trim()}
                    </Button>
                  </form>
                )}
              </section>
            </>
          )}
        </section>
      </div>
    </>
  );
}

export default TreasuryPage;
