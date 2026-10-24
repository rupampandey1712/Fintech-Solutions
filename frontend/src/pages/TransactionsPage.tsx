import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import MenuItem from '@mui/material/MenuItem';
import TextField from '@mui/material/TextField';
import { fetchAccounts, fetchTransactions } from '../api/endpoints';
import { getBackend } from '../api/client';
import PageHeader from '../components/PageHeader';
import DataPage, { TableSkeleton } from '../components/DataPage';
import LoadMoreButton from '../components/LoadMoreButton';
import StatusChip from '../components/StatusChip';
import { formatDate, formatMoney } from '../utils/format';

const bk = () => getBackend();
const PAGE_SIZE = 25;

export function TransactionsPage() {
  const [accountId, setAccountId] = useState<string>('');
  const [cursor, setCursor] = useState<string | null>(null);

  const accounts = useQuery({ queryKey: ['accounts', bk()], queryFn: fetchAccounts });
  const query = useQuery({
    queryKey: ['transactions', bk(), accountId, cursor],
    queryFn: () => fetchTransactions({ accountId: accountId || undefined, limit: PAGE_SIZE, cursor }),
  });

  const items = query.data?.items ?? [];

  function changeAccount(next: string) {
    setAccountId(next);
    setCursor(null);
  }

  return (
    <>
      <PageHeader
        eyebrow="LEDGER"
        title="Transactions"
        subtitle="Individual ledger movements, incoming and outgoing."
      />

      <div className="filter-row">
        <TextField
          select
          label="Filter by account"
          size="small"
          sx={{ minWidth: 320 }}
          value={accountId}
          onChange={(e) => changeAccount(e.target.value)}
        >
          <MenuItem value="">All accounts</MenuItem>
          {(accounts.data?.items ?? []).map((a) => (
            <MenuItem key={a.id} value={a.id}>
              {a.id.slice(0, 8)}… · {a.accountType} · {a.currency}
            </MenuItem>
          ))}
        </TextField>
      </div>

      <DataPage
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        onRetry={() => void query.refetch()}
        isEmpty={items.length === 0}
        emptyTitle="No transactions"
        emptyHint={accountId ? 'No movements for this account yet.' : 'Transactions appear after your first payment.'}
        loading={<TableSkeleton rows={6} columns={6} />}
      >
        <section className="panel panel-table">
          <table className="fc-table">
            <thead>
              <tr>
                <th>Occurred</th>
                <th>Direction</th>
                <th>Amount</th>
                <th>Account</th>
                <th>Status</th>
                <th>Reference</th>
              </tr>
            </thead>
            <tbody>
              {items.map((tx) => (
                <tr key={tx.id}>
                  <td>
                    <Link to={`/transactions/${tx.id}`} className="fc-link">
                      {formatDate(tx.occurredAt)}
                    </Link>
                  </td>
                  <td>
                    <StatusChip
                      status={tx.direction}
                      tone={tx.direction === 'debit' ? 'red' : 'green'}
                      label={tx.direction === 'debit' ? '↗ Debit' : '↙ Credit'}
                    />
                  </td>
                  <td className="fc-strong">{formatMoney(tx.amountMinor, tx.currency)}</td>
                  <td>
                    {tx.accountId ? (
                      <Link to={`/accounts/${tx.accountId}`} className="fc-link fc-mono">
                        {tx.accountId.slice(0, 8)}…
                      </Link>
                    ) : (
                      <span className="fc-cell-muted">—</span>
                    )}
                  </td>
                  <td>
                    <StatusChip status={tx.status} />
                  </td>
                  <td className="fc-cell-muted">{tx.reference ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
        <LoadMoreButton
          hasNext={Boolean(query.data?.nextCursor)}
          isLoading={query.isFetching}
          onClick={() => query.data?.nextCursor && setCursor(query.data.nextCursor)}
        />
      </DataPage>
    </>
  );
}

export default TransactionsPage;
