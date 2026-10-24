import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import Button from '@mui/material/Button';
import { fetchAccounts } from '../api/endpoints';
import { getBackend } from '../api/client';
import PageHeader from '../components/PageHeader';
import { DataPage } from '../components/DataPage';
import CreatePaymentForm from '../features/payments/CreatePaymentForm';

const bk = () => getBackend();

export function NewPaymentPage() {
  const accounts = useQuery({ queryKey: ['accounts', bk()], queryFn: fetchAccounts });

  return (
    <>
      <PageHeader
        eyebrow="MOVEMENTS"
        title="New payment"
        subtitle="Send funds to a verified beneficiary."
        actions={
          <Button variant="outlined" component={Link} to="/payments">
            ← All payments
          </Button>
        }
      />
      <DataPage
        isLoading={accounts.isLoading}
        isError={accounts.isError}
        error={accounts.error}
        onRetry={() => void accounts.refetch()}
        isEmpty={(accounts.data?.items.length ?? 0) === 0}
        emptyTitle="You need an account first"
        emptyHint="Open an account before sending payments."
        emptyAction={
          <Button variant="contained" component={Link} to="/accounts">
            Go to accounts
          </Button>
        }
        loading={null}
      >
        <CreatePaymentForm />
      </DataPage>
    </>
  );
}

export default NewPaymentPage;
