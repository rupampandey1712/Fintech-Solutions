import { lazy, Suspense } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import AppLayout from '../layouts/AppLayout';
import ProtectedRoute from './ProtectedRoute';
import { TableSkeleton } from '../components/DataPage';

// Each page is its own chunk, so the first load only pays for the screen being opened.
const LoginPage = lazy(() => import('../pages/LoginPage'));
const RegisterPage = lazy(() => import('../pages/RegisterPage'));
const DashboardPage = lazy(() => import('../pages/DashboardPage'));
const AccountsPage = lazy(() => import('../pages/AccountsPage'));
const AccountDetailPage = lazy(() => import('../pages/AccountDetailPage'));
const PaymentsPage = lazy(() => import('../pages/PaymentsPage'));
const NewPaymentPage = lazy(() => import('../pages/NewPaymentPage'));
const PaymentDetailPage = lazy(() => import('../pages/PaymentDetailPage'));
const TransactionsPage = lazy(() => import('../pages/TransactionsPage'));
const TransactionDetailPage = lazy(() => import('../pages/TransactionDetailPage'));
const BeneficiariesPage = lazy(() => import('../pages/BeneficiariesPage'));
const KycPage = lazy(() => import('../pages/KycPage'));
const NotificationsPage = lazy(() => import('../pages/NotificationsPage'));
const FraudPage = lazy(() => import('../pages/FraudPage'));
const AmlPage = lazy(() => import('../pages/AmlPage'));
const ReconciliationPage = lazy(() => import('../pages/ReconciliationPage'));
const ReportsPage = lazy(() => import('../pages/ReportsPage'));
const AdminPage = lazy(() => import('../pages/AdminPage'));
const TreasuryPage = lazy(() => import('../pages/TreasuryPage'));

export function AppRoutes() {
  return (
    <Suspense fallback={<TableSkeleton rows={4} />}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route
          element={
            <ProtectedRoute>
              <AppLayout />
            </ProtectedRoute>
          }
        >
          <Route path="/" element={<DashboardPage />} />
          <Route path="/accounts" element={<AccountsPage />} />
          <Route path="/accounts/:id" element={<AccountDetailPage />} />
          <Route path="/payments" element={<PaymentsPage />} />
          <Route path="/payments/new" element={<NewPaymentPage />} />
          <Route path="/payments/:id" element={<PaymentDetailPage />} />
          <Route path="/transactions" element={<TransactionsPage />} />
          <Route path="/transactions/:id" element={<TransactionDetailPage />} />
          <Route path="/beneficiaries" element={<BeneficiariesPage />} />
          <Route path="/kyc" element={<KycPage />} />
          <Route path="/notifications" element={<NotificationsPage />} />
          <Route path="/fraud" element={<FraudPage />} />
          <Route path="/aml" element={<AmlPage />} />
          <Route path="/reconciliation" element={<ReconciliationPage />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/treasury" element={<TreasuryPage />} />
          <Route path="/admin" element={<AdminPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}

export default AppRoutes;
