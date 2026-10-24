import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import Button from '@mui/material/Button';
import { fetchTransaction } from '../api/endpoints';
import { getBackend } from '../api/client';
import PageHeader from '../components/PageHeader';
import DataPage from '../components/DataPage';
import ErrorState from '../components/ErrorState';
import StatusChip from '../components/StatusChip';
import { formatDate, formatMoney } from '../utils/format';

const bk = () => getBackend();

export function TransactionDetailPage() {
  const { id = '' } = useParams();
  const query = useQuery({
    queryKey: ['transactions', bk(), id],
    queryFn: () => fetchTransaction(id),
    retry: (failureCount, error) => {
      const status = (error as { response?: { status?: number } })?.response?.status;
      return status !== 404 && failureCount < 1;
    },
  });

  if (query.isError) {
    const status = (query.error as { response?: { status?: number } })?.response?.status;
    if (status === 404) {
      return (
        <>
          <PageHeader eyebrow="LEDGER" title="Transaction" subtitle="Not found" />
          <ErrorState error={query.error} hint="this transaction does not exist" />
        </>
      );
    }
  }

  const tx = query.data;

  return (
    <>
      <PageHeader
        eyebrow="LEDGER"
        title={`Transaction ${tx ? tx.id.slice(0, 8) + '…' : ''}`}
        subtitle="Full ledger movement detail."
        actions={
          <Button variant="outlined" component={Link} to="/transactions">
            ← All transactions
          </Button>
        }
      />
      <DataPage
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        onRetry={() => void query.refetch()}
        loading={null}
        isEmpty={false}
      >
        {tx ? (
          <section className="panel">
            <div className="panel-title-row">
              <h3>Details</h3>
              <StatusChip status={tx.direction} tone={tx.direction === 'debit' ? 'red' : 'green'} label={tx.direction === 'debit' ? '↗ Debit' : '↙ Credit'} />
            </div>
            <dl className="fc-dl">
              <div>
                <dt>Amount</dt>
                <dd className="fc-strong">{formatMoney(tx.amountMinor, tx.currency)}</dd>
              </div>
              <div>
                <dt>Status</dt>
                <dd>
                  <StatusChip status={tx.status} />
                </dd>
              </div>
              <div>
                <dt>Account</dt>
                <dd>
                  <Link to={`/accounts/${tx.accountId}`} className="fc-link fc-mono">
                    {tx.accountId}
                  </Link>
                </dd>
              </div>
              <div>
                <dt>Counterparty</dt>
                <dd>
                  {tx.counterpartyAccountId ? (
                    <Link to={`/accounts/${tx.counterpartyAccountId}`} className="fc-link fc-mono">
                      {tx.counterpartyAccountId}
                    </Link>
                  ) : (
                    '—'
                  )}
                </dd>
              </div>
              <div>
                <dt>Payment</dt>
                <dd>
                  {tx.paymentId ? (
                    <Link to={`/payments/${tx.paymentId}`} className="fc-link fc-mono">
                      {tx.paymentId}
                    </Link>
                  ) : (
                    '—'
                  )}
                </dd>
              </div>
              <div>
                <dt>Reference</dt>
                <dd>{tx.reference ?? '—'}</dd>
              </div>
              <div>
                <dt>Occurred at</dt>
                <dd>{formatDate(tx.occurredAt)}</dd>
              </div>
              <div>
                <dt>Transaction ID</dt>
                <dd className="fc-mono">{tx.id}</dd>
              </div>
            </dl>
          </section>
        ) : null}
      </DataPage>
    </>
  );
}

export default TransactionDetailPage;
