import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import Button from '@mui/material/Button';
import { fetchAccounts } from '../api/endpoints';
import { getBackend } from '../api/client';
import PageHeader from '../components/PageHeader';
import DataPage, { TableSkeleton } from '../components/DataPage';
import StatusChip from '../components/StatusChip';
import OpenAccountDialog from '../features/accounts/OpenAccountDialog';
import { formatDate, formatMoney } from '../utils/format';

const bk = () => getBackend();

const TYPE_LABEL: Record<string, string> = {
  checking: 'Checking',
  savings: 'Savings',
  wallet: 'Wallet',
  merchant: 'Merchant',
};

export function AccountsPage() {
  const [dialogOpen, setDialogOpen] = useState(false);
  const accounts = useQuery({ queryKey: ['accounts', bk()], queryFn: fetchAccounts });

  return (
    <>
      <PageHeader
        eyebrow="TREASURY"
        title="Accounts"
        subtitle="Ledger and available balances per account."
        actions={
          <Button variant="contained" onClick={() => setDialogOpen(true)}>
            Open account
          </Button>
        }
      />
      <DataPage
        isLoading={accounts.isLoading}
        isError={accounts.isError}
        error={accounts.error}
        onRetry={() => void accounts.refetch()}
        isEmpty={Boolean(accounts.data) && (accounts.data?.items.length ?? 0) === 0}
        emptyTitle="No accounts yet"
        emptyHint="Open your first account to start moving money."
        emptyAction={
          <Button variant="contained" onClick={() => setDialogOpen(true)}>
            Open account
          </Button>
        }
        loading={<TableSkeleton rows={4} columns={6} />}
      >
        <section className="panel panel-table">
          <table className="fc-table">
            <thead>
              <tr>
                <th>Account</th>
                <th>Type</th>
                <th>Ledger balance</th>
                <th>Available</th>
                <th>Currency</th>
                <th>Status</th>
                <th>Opened</th>
              </tr>
            </thead>
            <tbody>
              {(accounts.data?.items ?? []).map((account) => (
                <tr key={account.id}>
                  <td>
                    <Link to={`/accounts/${account.id}`} className="fc-link fc-mono">
                      {account.id.slice(0, 8)}…
                    </Link>
                  </td>
                  <td>{TYPE_LABEL[account.accountType] ?? account.accountType}</td>
                  <td>{formatMoney(account.ledgerBalanceMinor, account.currency)}</td>
                  <td className="fc-strong">{formatMoney(account.availableBalanceMinor, account.currency)}</td>
                  <td>{account.currency}</td>
                  <td>
                    <StatusChip status={account.status} />
                  </td>
                  <td className="fc-cell-muted">{formatDate(account.createdAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </DataPage>
      <OpenAccountDialog open={dialogOpen} onClose={() => setDialogOpen(false)} />
    </>
  );
}

export default AccountsPage;
