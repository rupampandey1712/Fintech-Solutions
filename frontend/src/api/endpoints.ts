import { http } from './client';
import type {
  AgentStatus,
  Investigation,
  Account,
  AdminAccount,
  AdminCustomer,
  AdminDeposit,
  AdminPayment,
  DeadLetter,
  Deposit,
  Refund,
  RefundPage,
  AmlAlert,
  AuditLog,
  Beneficiary,
  Customer,
  DashboardReport,
  FraudCase,
  FraudRule,
  KycApplication,
  KycDocument,
  KycDocumentType,
  LedgerAccount,
  Notification,
  Payment,
  PaymentDispute,
  ReconciliationRun,
  ReconciliationRunSummary,
  RegisterResponse,
  SettlementBatch,
  SettlementBatchDetail,
  TokenResponse,
  Transaction,
  TransactionSeriesPoint,
} from '../types';

const v1 = '/api/v1';

// ---------------------------------------------------------------------------
// Health

export async function fetchHealth(): Promise<{ status: string }> {
  const { data } = await http.get<{ status: string }>('/health');
  return data;
}

export async function fetchReadiness(): Promise<{ status: string }> {
  const { data } = await http.get<{ status: string }>('/ready');
  return data;
}

// ---------------------------------------------------------------------------
// Auth

export async function register(body: { email: string; password: string }): Promise<RegisterResponse> {
  const { data } = await http.post<RegisterResponse>(`${v1}/auth/register`, body);
  return data;
}

export async function login(body: { email: string; password: string }): Promise<TokenResponse> {
  const { data } = await http.post<TokenResponse>(`${v1}/auth/login`, body);
  return data;
}

export async function logout(refreshToken: string): Promise<void> {
  await http.post(`${v1}/auth/logout`, { refreshToken });
}

export async function fetchMe(): Promise<Customer> {
  const { data } = await http.get<Customer>(`${v1}/customers/me`);
  return data;
}

// ---------------------------------------------------------------------------
// Accounts

export async function fetchAccounts(): Promise<{ items: Account[]; nextCursor: string | null }> {
  const { data } = await http.get(`${v1}/accounts`);
  return data;
}

export async function fetchAccount(id: string): Promise<Account> {
  const { data } = await http.get<Account>(`${v1}/accounts/${id}`);
  return data;
}

export async function createAccount(body: { accountType: string; currency: string }): Promise<Account> {
  const { data } = await http.post<Account>(`${v1}/accounts`, body);
  return data;
}

export async function fetchLedger(accountId: string): Promise<LedgerAccount> {
  const { data } = await http.get<LedgerAccount>(`${v1}/ledger/accounts/${accountId}`);
  return data;
}

// ---------------------------------------------------------------------------
// Beneficiaries

export async function fetchBeneficiaries(): Promise<{ items: Beneficiary[]; nextCursor: string | null }> {
  const { data } = await http.get(`${v1}/beneficiaries`);
  return data;
}

export async function createBeneficiary(body: { destinationAccountId: string; label: string }): Promise<Beneficiary> {
  const { data } = await http.post<Beneficiary>(`${v1}/beneficiaries`, body);
  return data;
}

export async function verifyBeneficiary(id: string): Promise<Beneficiary> {
  const { data } = await http.post<Beneficiary>(`${v1}/beneficiaries/${id}/verify`);
  return data;
}

export async function activateBeneficiary(id: string): Promise<Beneficiary> {
  const { data } = await http.post<Beneficiary>(`${v1}/beneficiaries/${id}/activate`);
  return data;
}

export async function disableBeneficiary(id: string): Promise<Beneficiary> {
  const { data } = await http.post<Beneficiary>(`${v1}/beneficiaries/${id}/disable`);
  return data;
}

// ---------------------------------------------------------------------------
// Payments

export interface CreatePaymentBody {
  sourceAccountId: string;
  beneficiaryId: string;
  amountMinor: number;
  currency: string;
  reference?: string;
}

export async function createPayment(body: CreatePaymentBody, idempotencyKey: string): Promise<Payment> {
  const { data } = await http.post<Payment>(`${v1}/payments`, body, {
    headers: { 'Idempotency-Key': idempotencyKey },
  });
  return data;
}

export async function fetchPayments(params: {
  status?: string;
  limit?: number;
  cursor?: string | null;
}): Promise<{ items: Payment[]; nextCursor: string | null }> {
  const { data } = await http.get(`${v1}/payments`, { params });
  return data;
}

export async function fetchPayment(id: string): Promise<Payment> {
  const { data } = await http.get<Payment>(`${v1}/payments/${id}`);
  return data;
}

// ---------------------------------------------------------------------------
// Transactions

export async function fetchTransactions(params: {
  accountId?: string;
  limit?: number;
  cursor?: string | null;
}): Promise<{ items: Transaction[]; nextCursor: string | null }> {
  const { data } = await http.get(`${v1}/transactions`, { params });
  return data;
}

export async function fetchTransaction(id: string): Promise<Transaction> {
  const { data } = await http.get<Transaction>(`${v1}/transactions/${id}`);
  return data;
}

// ---------------------------------------------------------------------------
// KYC

export async function createKycApplication(body: { fullName: string; country: string }): Promise<KycApplication> {
  const { data } = await http.post<KycApplication>(`${v1}/kyc`, body);
  return data;
}

export async function fetchKycApplications(): Promise<{ items: KycApplication[] }> {
  const { data } = await http.get(`${v1}/kyc`);
  return data;
}

export async function fetchKycApplication(id: string): Promise<KycApplication> {
  const { data } = await http.get<KycApplication>(`${v1}/kyc/${id}`);
  return data;
}

export async function uploadKycDocument(
  id: string,
  body: { documentType: KycDocumentType; contentBase64: string },
): Promise<KycDocument> {
  const { data } = await http.post<KycDocument>(`${v1}/kyc/${id}/documents`, body);
  return data;
}

export async function submitKyc(id: string): Promise<KycApplication> {
  const { data } = await http.post<KycApplication>(`${v1}/kyc/${id}/submit`);
  return data;
}

export async function reviewKyc(id: string, body: { approve: boolean; reason?: string }): Promise<KycApplication> {
  const { data } = await http.post<KycApplication>(`${v1}/kyc/${id}/review`, body);
  return data;
}

// ---------------------------------------------------------------------------
// Notifications

export async function fetchNotifications(params: {
  limit?: number;
  cursor?: string | null;
}): Promise<{ items: Notification[]; nextCursor: string | null }> {
  const { data } = await http.get(`${v1}/notifications`, { params });
  return data;
}

// ---------------------------------------------------------------------------
// Fraud

export async function fetchFraudCases(params: {
  status?: string;
  limit?: number;
}): Promise<{ items: FraudCase[]; nextCursor: string | null }> {
  const { data } = await http.get(`${v1}/fraud/cases`, { params });
  return data;
}

export async function fetchAgentStatus(): Promise<AgentStatus> {
  const { data } = await http.get<AgentStatus>(`${v1}/fraud/agent/status`);
  return data;
}

/** Runs the fraud investigator agent on a case. Read-only: it drafts, it does not decide. */
export async function investigateFraudCase(id: string): Promise<Investigation> {
  const { data } = await http.post<Investigation>(`${v1}/fraud/cases/${id}/investigate`, undefined, {
    // An investigation makes several model calls; allow for the server's own time budget.
    timeout: 120_000,
  });
  return data;
}

export async function reviewFraudCase(
  id: string,
  body: { resolution: 'closed' | 'false_positive' | 'escalated'; note?: string },
): Promise<FraudCase> {
  const { data } = await http.post<FraudCase>(`${v1}/fraud/cases/${id}/review`, body);
  return data;
}

// ---------------------------------------------------------------------------
// AML

export async function fetchAmlAlerts(params: {
  limit?: number;
}): Promise<{ items: AmlAlert[]; nextCursor: string | null }> {
  const { data } = await http.get(`${v1}/aml/alerts`, { params });
  return data;
}

// ---------------------------------------------------------------------------
// Reconciliation

export async function runReconciliation(): Promise<ReconciliationRun> {
  const { data } = await http.post<ReconciliationRun>(`${v1}/reconciliation/run`);
  return data;
}

export async function fetchReconciliationRuns(params: {
  limit?: number;
}): Promise<{ items: ReconciliationRunSummary[]; nextCursor: string | null }> {
  const { data } = await http.get(`${v1}/reconciliation/runs`, { params });
  return data;
}

export async function fetchReconciliationRun(id: string): Promise<ReconciliationRun> {
  const { data } = await http.get<ReconciliationRun>(`${v1}/reconciliation/runs/${id}`);
  return data;
}

// ---------------------------------------------------------------------------
// Reports

export async function fetchDashboardReport(): Promise<DashboardReport> {
  const { data } = await http.get<DashboardReport>(`${v1}/reports/dashboard`);
  return data;
}

export async function fetchTransactionSeries(params: { days: number }): Promise<{ series: TransactionSeriesPoint[] }> {
  const { data } = await http.get(`${v1}/reports/transactions`, { params });
  return data;
}

// ---------------------------------------------------------------------------
// Admin

export async function fetchAdminCustomers(params: {
  limit?: number;
}): Promise<{ items: AdminCustomer[] }> {
  const { data } = await http.get(`${v1}/admin/customers`, { params });
  return data;
}

export async function patchAdminCustomerStatus(id: string, status: string): Promise<AdminCustomer> {
  const { data } = await http.patch<AdminCustomer>(`${v1}/admin/customers/${id}/status`, { status });
  return data;
}

export async function fetchFraudRules(): Promise<{ items: FraudRule[] }> {
  const { data } = await http.get(`${v1}/admin/fraud-rules`);
  return data;
}

export async function patchFraudRule(
  code: string,
  body: { enabled?: boolean; weight?: number; thresholdMinor?: number | null },
): Promise<FraudRule> {
  const { data } = await http.patch<FraudRule>(`${v1}/admin/fraud-rules/${code}`, body);
  return data;
}

export async function fetchAuditLogs(params: {
  limit?: number;
}): Promise<{ items: AuditLog[] }> {
  const { data } = await http.get(`${v1}/admin/audit-logs`, { params });
  return data;
}

export async function patchAdminAccountStatus(id: string, status: string): Promise<{ id: string; status: string }> {
  const { data } = await http.patch<{ id: string; status: string }>(`${v1}/admin/accounts/${id}/status`, { status });
  return data;
}

// ---------------------------------------------------------------------------
// Treasury (admin / finance_user)

export async function fetchCustomerAccounts(customerId: string): Promise<{ items: AdminAccount[] }> {
  const { data } = await http.get(`${v1}/admin/customers/${customerId}/accounts`);
  return data;
}

export async function createDeposit(
  accountId: string,
  body: { amountMinor: number; reference?: string },
  idempotencyKey: string,
): Promise<Deposit> {
  const { data } = await http.post<Deposit>(`${v1}/admin/accounts/${accountId}/deposits`, body, {
    headers: { 'Idempotency-Key': idempotencyKey },
  });
  return data;
}

// ---------------------------------------------------------------------------
// Refunds and reversals (staff), refund history (payer and staff)

export async function fetchPaymentRefunds(paymentId: string): Promise<RefundPage> {
  const { data } = await http.get<RefundPage>(`${v1}/payments/${paymentId}/refunds`);
  return data;
}

export async function refundPayment(
  paymentId: string,
  body: { amountMinor: number; reason?: string },
  idempotencyKey: string,
): Promise<Refund & { refundedTotalMinor: number; remainingMinor: number }> {
  const { data } = await http.post(`${v1}/payments/${paymentId}/refund`, body, {
    headers: { 'Idempotency-Key': idempotencyKey },
  });
  return data;
}

export async function reversePayment(
  paymentId: string,
  reason: string,
  idempotencyKey: string,
): Promise<{ status: string }> {
  const { data } = await http.post(`${v1}/payments/${paymentId}/reversal`, { reason }, {
    headers: { 'Idempotency-Key': idempotencyKey },
  });
  return data;
}

export async function reverseJournalEntry(
  journalId: string,
  reason: string,
  idempotencyKey: string,
): Promise<{ reversalJournalId: string }> {
  const { data } = await http.post(`${v1}/admin/journal-entries/${journalId}/reversal`, { reason }, {
    headers: { 'Idempotency-Key': idempotencyKey },
  });
  return data;
}

export async function fetchCustomerPayments(customerId: string): Promise<{ items: AdminPayment[] }> {
  const { data } = await http.get(`${v1}/admin/customers/${customerId}/payments`);
  return data;
}

export async function fetchCustomerDeposits(customerId: string): Promise<{ items: AdminDeposit[] }> {
  const { data } = await http.get(`${v1}/admin/customers/${customerId}/deposits`);
  return data;
}

// ---------------------------------------------------------------------------
// Chargeback disputes (customer opens, staff investigate and decide)

export async function openDispute(paymentId: string, reason: string): Promise<PaymentDispute> {
  const { data } = await http.post<PaymentDispute>(`${v1}/payments/${paymentId}/dispute`, { reason });
  return data;
}

export async function fetchDispute(paymentId: string): Promise<PaymentDispute> {
  const { data } = await http.get<PaymentDispute>(`${v1}/payments/${paymentId}/dispute`);
  return data;
}

export async function fetchDisputes(params: { status?: string; limit?: number }): Promise<{ items: PaymentDispute[] }> {
  const { data } = await http.get(`${v1}/admin/disputes`, { params });
  return data;
}

export async function addDisputeEvidence(disputeId: string, note: string): Promise<PaymentDispute> {
  const { data } = await http.post<PaymentDispute>(`${v1}/admin/disputes/${disputeId}/evidence`, { note });
  return data;
}

export async function decideDispute(
  disputeId: string,
  outcome: 'won' | 'lost',
  resolution?: string,
): Promise<PaymentDispute> {
  const { data } = await http.post<PaymentDispute>(`${v1}/admin/disputes/${disputeId}/decision`, {
    outcome,
    resolution,
  });
  return data;
}

// ---------------------------------------------------------------------------
// Settlement batches (staff)

export async function fetchSettlementBatches(): Promise<{ items: SettlementBatch[] }> {
  const { data } = await http.get(`${v1}/admin/settlement-batches`);
  return data;
}

export async function fetchSettlementBatch(id: string): Promise<SettlementBatchDetail> {
  const { data } = await http.get<SettlementBatchDetail>(`${v1}/admin/settlement-batches/${id}`);
  return data;
}

export async function createSettlementBatch(body: { businessDate: string; currency: string }): Promise<SettlementBatchDetail> {
  const { data } = await http.post<SettlementBatchDetail>(`${v1}/admin/settlement-batches`, body);
  return data;
}

export async function processSettlementBatch(id: string): Promise<SettlementBatchDetail> {
  const { data } = await http.post<SettlementBatchDetail>(`${v1}/admin/settlement-batches/${id}/process`);
  return data;
}

/** Returns the batch's payout file; the backend sets Content-Disposition. */
export async function fetchSettlementFile(id: string): Promise<{ blob: Blob; filename: string }> {
  const response = await http.get(`${v1}/admin/settlement-batches/${id}/file`, { responseType: 'blob' });
  const disposition: string = response.headers['content-disposition'] ?? '';
  const match = /filename="([^"]+)"/.exec(disposition);
  return { blob: response.data as Blob, filename: match?.[1] ?? `settlement-${id}.csv` };
}

// ---------------------------------------------------------------------------
// Admin: user roles (spec section 25) and outbox dead letters (spec sections 18-19)

export async function fetchUsers(params: { limit?: number }): Promise<{ items: AdminCustomer[] }> {
  const { data } = await http.get(`${v1}/admin/users`, { params });
  return data;
}

export async function patchUserRoles(id: string, roles: string[]): Promise<AdminCustomer> {
  const { data } = await http.patch<AdminCustomer>(`${v1}/admin/users/${id}/roles`, { roles });
  return data;
}

export async function fetchDeadLetters(params: { limit?: number }): Promise<{ items: DeadLetter[] }> {
  const { data } = await http.get(`${v1}/admin/outbox/dead-letters`, { params });
  return data;
}

export async function replayDeadLetter(id: string): Promise<{ deadLetterId: string; newOutboxEventId: string }> {
  const { data } = await http.post(`${v1}/admin/outbox/dead-letters/${id}/replay`);
  return data;
}

// ---------------------------------------------------------------------------
// AML alert review (spec section 41)

export async function patchAmlAlertStatus(
  alertId: string,
  body: { status: string; note?: string },
): Promise<{ id: string; status: string }> {
  const { data } = await http.patch<{ id: string; status: string }>(`${v1}/aml/alerts/${alertId}/status`, body);
  return data;
}

// ---------------------------------------------------------------------------
// Report CSV exports (spec section 62)

export const REPORT_TYPES = [
  'daily-transactions',
  'payment-success',
  'fraud',
  'aml',
  'reconciliation',
  'ledger',
  'settlement',
] as const;

export type ReportType = (typeof REPORT_TYPES)[number];

export async function exportReport(type: ReportType): Promise<{ blob: Blob; filename: string }> {
  const response = await http.get(`${v1}/reports/${type}/export`, { responseType: 'blob' });
  const disposition: string = response.headers['content-disposition'] ?? '';
  const match = /filename="([^"]+)"/.exec(disposition);
  return { blob: response.data as Blob, filename: match?.[1] ?? `${type}.csv` };
}
