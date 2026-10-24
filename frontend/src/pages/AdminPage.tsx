import { useState } from 'react';
import Box from '@mui/material/Box';
import Tab from '@mui/material/Tab';
import Tabs from '@mui/material/Tabs';
import PageHeader from '../components/PageHeader';
import CustomersTab from '../features/admin/CustomersTab';
import FraudRulesTab from '../features/admin/FraudRulesTab';
import AuditLogsTab from '../features/admin/AuditLogsTab';
import UsersTab from '../features/admin/UsersTab';
import DeadLettersTab from '../features/admin/DeadLettersTab';
import ErrorBoundary from '../components/ErrorBoundary';

export function AdminPage() {
  const [tab, setTab] = useState(0);

  return (
    <>
      <PageHeader
        eyebrow="OPERATIONS"
        title="Admin"
        subtitle="Customers, users, fraud rules, dead letters and the audit trail. Restricted to administrator roles."
      />
      <section className="panel">
        <Box sx={{ borderBottom: 1, borderColor: 'divider', mb: 2 }}>
          <Tabs value={tab} onChange={(_, v: number) => setTab(v)}>
            <Tab label="Customers" />
            <Tab label="Users" />
            <Tab label="Fraud rules" />
            <Tab label="Dead letters" />
            <Tab label="Audit logs" />
          </Tabs>
        </Box>
        {/* Keyed by tab: a tab that throws shows an error in place, and switching tabs resets it. */}
        <ErrorBoundary key={tab}>
          {tab === 0 ? <CustomersTab /> : null}
          {tab === 1 ? <UsersTab /> : null}
          {tab === 2 ? <FraudRulesTab /> : null}
          {tab === 3 ? <DeadLettersTab /> : null}
          {tab === 4 ? <AuditLogsTab /> : null}
        </ErrorBoundary>
      </section>
    </>
  );
}

export default AdminPage;
