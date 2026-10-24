import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import Button from '@mui/material/Button';
import { fetchAccount, fetchLedger, fetchTransactions } from '../api/endpoints';
import { getBackend } from '../api/client';
import PageHeader from '../components/PageHeader';
import DataPage, { TableSkeleton } from '../components/DataPage';
import ErrorState from '../components/ErrorState';
import StatusChip from '../components/StatusChip';
import { formatDate, formatMoney } from '../utils/format';

const bk = () => getBackend();

export function AccountDetailPage() {
  const { id = '' } = useParams();

  const account = useQuery({
    queryKey: ['accounts', bk(), id],
    queryFn: () => fetchAccount(id),
    retry: (failureCount, error) => {
      const status = (error as { response?: { status?: number } })?.response?.status;
      return status !== 404 && failureCount < 1;
    },
  });
  const transactions = useQuery({
    queryKey: ['transactions', 'account', bk(), id],
    queryFn: () => fetchTransactions({ accountId: id, limit: 10 }),
  });
  const ledger = useQuery({
    queryKey: ['ledger', bk(), id],
    queryFn: () => fetchLedger(id),
  });

  if (account.isError) {
    const status = (account.error as { response?: { status?: number } })?.response?.status;
    if (status === 404) {
      return (
        <>
          <PageHeader eyebrow="TREASURY" title="Account" subtitle="Not found" />
          <ErrorState error={account.error} hint="this account does not exist" />
        </>
      );
    }
  }

  const a = account.data;

  return (
    <>
      <PageHeader
        eyebrow="TREASURY"
        title={`Account ${a ? a.id.slice(0, 8) + '…' : ''}`}
        subtitle="Balances, recent movements and journal entries."
        actions={
          <Button variant="outlined" component={Link} to="/accounts">
            ← All accounts
          </Button>
        }
      />
      <DataPage
        isLoading={account.isLoading}
        isError={account.isError}
        error={account.error}
        onRetry={() => void account.refetch()}
        loading={
          <div className="metrics-grid">
            <div className="metric-card">
              <strong className="metric-value">…</strong>
            </div>
          </div>
        }
        isEmpty={false}
      >
        {a ? (
          <>
            <div className="metrics-grid">
              <article className="metric-card accent-card">
                <div className="metric-top">
                  <span>LEDGER BALANCE</span>
                  <StatusChip status={a.status} />
                </div>
                <strong className="metric-value">{formatMoney(a.ledgerBalanceMinor, a.currency)}</strong>
                <div className="metric-foot">{a.accountType} · {a.currency}</div>
              </article>
              <article className="metric-card">
                <div className="metric-top">
                  <span>AVAILABLE BALANCE</span>
                </div>
                <strong className="metric-value">{formatMoney(a.availableBalanceMinor, a.currency)}</strong>
                <div className="metric-foot">Withdrawable funds</div>
              </article>
              <article className="metric-card">
                <div className="metric-top">
                  <span>ACCOUNT ID</span>
                </div>
                <strong className="metric-value fc-mono fc-small-value">{a.id}</strong>
                <div className="metric-foot">Opened {formatDate(a.createdAt)}</div>
              </article>
            </div>

            <div className="section-heading">
              <div>
                <h2>Recent transactions</h2>
                <p>Movements for this account.</p>
              </div>
            </div>
            <DataPage
              isLoading={transactions.isLoading}
              isError={transactions.isError}
              error={transactions.error}
              onRetry={() => void transactions.refetch()}
              isEmpty={(transactions.data?.items.length ?? 0) === 0}
              emptyTitle="No transactions for this account"
              emptyHint="Send a payment from this account to see movements."
              loading={<TableSkeleton rows={3} columns={5} />}
            >
              <section className="panel panel-table">
                <table className="fc-table">
                  <thead>
                    <tr>
                      <th>Occurred</th>
                      <th>Direction</th>
                      <th>Amount</th>
                      <th>Status</th>
                      <th>Reference</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(transactions.data?.items ?? []).slice(0, 10).map((tx) => (
                      <tr key={tx.id}>
                        <td>
                          <Link to={`/transactions/${tx.id}`} className="fc-link">
                            {formatDate(tx.occurredAt)}
                          </Link>
                        </td>
                        <td>
                          <StatusChip status={tx.direction} label={tx.direction === 'debit' ? '↗ Debit' : '↙ Credit'} />
                        </td>
                        <td>{formatMoney(tx.amountMinor, tx.currency)}</td>
                        <td>
                          <StatusChip status={tx.status} />
                        </td>
                        <td className="fc-cell-muted">{tx.reference ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            </DataPage>

            <div className="section-heading">
              <div>
                <h2>Ledger entries</h2>
                <p>Double-entry journal postings for this account.</p>
              </div>
              <span className="fc-cell-muted">
                {ledger.data ? `${formatMoney(ledger.data.balanceMinor, ledger.data.currency)} balance` : ''}
              </span>
            </div>
            <DataPage
              isLoading={ledger.isLoading}
              isError={ledger.isError}
              error={ledger.error}
              onRetry={() => void ledger.refetch()}
              isEmpty={(ledger.data?.entries.length ?? 0) === 0}
              emptyTitle="No journal entries"
              emptyHint="Ledger entries appear once payments post to this account."
              loading={<TableSkeleton rows={3} columns={4} />}
            >
              <section className="panel panel-table">
                <table className="fc-table">
                  <thead>
                    <tr>
                      <th>Journal</th>
                      <th>Posted</th>
                      <th>Side</th>
                      <th>Amount</th>
                      <th>Reference</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(ledger.data?.entries ?? []).map((entry) => (
                      <tr key={entry.journalId}>
                        <td className="fc-mono">{entry.journalId.slice(0, 8)}…</td>
                        <td className="fc-cell-muted">{formatDate(entry.postedAt)}</td>
                        <td>
                          <StatusChip
                            status={entry.side}
                            tone={entry.side.toLowerCase() === 'debit' ? 'red' : 'green'}
                            label={entry.side}
                          />
                        </td>
                        <td>{formatMoney(entry.amountMinor, ledger.data?.currency)}</td>
                        <td className="fc-cell-muted">{entry.businessReference ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            </DataPage>
          </>
        ) : null}
      </DataPage>
    </>
  );
}

export default AccountDetailPage;
