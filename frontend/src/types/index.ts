// Shared types mirroring the FinCore API contract (camelCase JSON fields).

export interface Problem {
  type?: string;
  title?: string;
  status?: number;
  code?: string;
  detail?: string;
  traceId?: string;
  errors?: { field: string; code: string }[];
}

export interface Customer {
  id: string;
  email: string;
  status: string;
}

export interface TokenResponse {
  accessToken: string;
  tokenType: string;
  expiresIn: number;
  refreshToken: string;
}

export interface RegisterResponse {
  id: string;
  email: string;
  status: string;
}

export type AccountType = 'checking' | 'savings' | 'wallet' | 'merchant';

export interface Account {
  id: string;
  accountType: AccountType;
  currency: string;
  status: string;
  ledgerBalanceMinor: number;
  availableBalanceMinor: number;
  createdAt: string;
}

export type BeneficiaryStatus =
  | 'pending_verification'
  | 'cooling'
  | 'active'
  | 'disabled';

export interface Beneficiary {
  id: string;
  destinationAccountId: string;
  label: string;
  status: BeneficiaryStatus;
  verifiedAt: string | null;
  coolingUntil: string | null;
  createdAt: string;
}

export type PaymentStatus =
  | 'initiated'
  | 'pending'
  | 'processing'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'rejected'
  | 'expired'
  | 'reversed';

export type RiskLevel = 'low' | 'medium' | 'high' | 'critical';
export type RiskDecision = 'allow' | 'review' | 'block';

export interface PaymentRisk {
  score: number;
  level: RiskLevel;
  decision: RiskDecision;
  reasons: string[];
}

export interface Payment {
  id: string;
  status: PaymentStatus;
  amountMinor: number;
  currency: string;
  createdAt: string;
  reference: string | null;
  providerReference: string | null;
  providerStatus: string | null;
  risk: PaymentRisk;
}

export type TxDirection = 'debit' | 'credit';

export interface Transaction {
  id: string;
  paymentId: string | null;
  accountId: string | null;
  direction: TxDirection;
  counterpartyAccountId: string | null;
  amountMinor: number;
  currency: string;
  occurredAt: string;
  status: string;
  reference: string | null;
}

export interface LedgerEntry {
  journalId: string;
  postedAt: string;
  businessReference: string | null;
  side: string;
  amountMinor: number;
}

export interface LedgerAccount {
  accountId: string;
  currency: string;
  balanceMinor: number;
  entries: LedgerEntry[];
}

export type KycApplicationStatus =
  | 'pending'
  | 'under_review'
  | 'approved'
  | 'rejected'
  | 'expired';

export type KycDocumentType = 'passport' | 'national_id' | 'proof_of_address';

export interface KycDocument {
  id: string;
  documentType: KycDocumentType;
  blobPath: string;
  status: 'uploaded' | 'verified' | 'rejected';
  uploadedAt: string;
}

export interface KycApplication {
  id: string;
  status: KycApplicationStatus;
  fullName: string;
  country: string;
  rejectionReason: string | null;
  submittedAt: string | null;
  decidedAt: string | null;
  createdAt: string;
  documents: KycDocument[];
}

export interface Notification {
  id: string;
  channel: 'email' | 'sms' | 'push' | 'in_app';
  eventType: string;
  subject: string;
  body: string;
  status: 'queued' | 'sent' | 'failed';
  sentAt: string | null;
  createdAt: string;
}

export type FraudCaseStatus =
  | 'open'
  | 'investigating'
  | 'escalated'
  | 'closed'
  | 'false_positive';

export type ReviewResolution = 'closed' | 'false_positive' | 'escalated';

/** Whether the fraud investigator agent can be used by the caller. */
export interface AgentStatus {
  enabled: boolean;
  provider: string;
  model: string;
}

export interface InvestigationStep {
  tool: string;
  label: string;
}

/** The fraud investigator agent's draft. A reviewer still makes the decision. */
export interface Investigation {
  caseId: string;
  recommendation: ReviewResolution;
  confidence: 'low' | 'medium' | 'high';
  summary: string;
  keyFindings: string[];
  suggestedNote: string;
  steps: InvestigationStep[];
  model: string;
  generatedAt: string;
}

export interface FraudCase {
  id: string;
  paymentId: string | null;
  customerId: string;
  score: number;
  level: RiskLevel;
  status: FraudCaseStatus;
  reasons: string[];
  resolution: string | null;
  reviewedAt: string | null;
  createdAt: string;
}

export interface AmlAlert {
  id: string;
  customerId: string;
  paymentId: string | null;
  ruleCode: string;
  status: string;
  details: Record<string, unknown>;
  createdAt: string;
}

export interface ReconciliationRecord {
  paymentId: string;
  status: string;
  internalStatus: string;
  externalStatus: string | null;
  internalAmountMinor: number;
  externalAmountMinor: number | null;
  externalReference: string | null;
}

export interface ReconciliationRun {
  runId: string;
  matched: number;
  amountMismatch: number;
  statusMismatch: number;
  missingExternal: number;
  missingInternal: number;
  status?: string;
  records: ReconciliationRecord[];
}

export interface ReconciliationRunSummary {
  id: string;
  startedAt: string;
  finishedAt: string | null;
  matched: number;
  amountMismatch: number;
  statusMismatch: number;
  missingExternal: number;
  missingInternal: number;
}

export interface DashboardTotals {
  payments: number;
  completed: number;
  failed: number;
  blocked: number;
}

export interface DashboardReport {
  totals: DashboardTotals;
  /** Percentage, 0–100 (both APIs). */
  successRate: number | null;
  fraudCases: { open: number; total: number };
  amlAlerts: { open: number; total: number };
  /** One row per month per currency; amounts in different currencies are never summed. */
  monthlyVolume: { month: string; currency: string; count: number; volumeMinor: number }[];
}

export interface TransactionSeriesPoint {
  day: string;
  currency: string;
  count: number;
  volumeMinor: number;
}

export interface AdminCustomer {
  id: string;
  email: string;
  status: string;
  roles: string[];
  createdAt: string;
}

export interface AdminAccount {
  id: string;
  accountType: string;
  currency: string;
  status: string;
  balanceMinor: number;
  createdAt: string;
}

export interface Deposit {
  journalId: string;
  accountId: string;
  amountMinor: number;
  currency: string;
  reference: string | null;
  balanceMinor: number;
  postedAt: string;
}

export interface Refund {
  id: string;
  paymentId: string;
  amountMinor: number;
  currency: string;
  reason: string | null;
  journalId: string;
  createdAt: string;
}

export interface RefundPage {
  items: Refund[];
  refundedTotalMinor: number;
  remainingMinor: number;
}

export interface AdminPayment {
  id: string;
  status: PaymentStatus;
  amountMinor: number;
  currency: string;
  reference: string | null;
  refundedMinor: number;
  createdAt: string;
}

export interface AdminDeposit {
  journalId: string;
  accountId: string;
  amountMinor: number;
  currency: string;
  postedAt: string;
  reversed: boolean;
}

export interface FraudRule {
  id: string;
  code: string;
  description: string;
  thresholdMinor: number | null;
  weight: number;
  enabled: boolean;
}

export interface AuditLog {
  id: string;
  /** Null when nobody is identified, e.g. a failed login for an unknown email. */
  actorId: string | null;
  action: string;
  entityType: string;
  /** Null when the action has no single entity, e.g. a payment blocked before it was created. */
  entityId: string | null;
  ipAddress: string | null;
  correlationId: string | null;
  createdAt: string;
}

// ---------------------------------------------------------------------------
// Chargeback disputes (spec section 61)

export type DisputeStatus = 'open' | 'under_review' | 'won' | 'lost';

export interface DisputeEvidence {
  note: string;
  addedBy: string;
  addedAt: string;
}

export interface PaymentDispute {
  id: string;
  paymentId: string;
  customerId: string;
  status: DisputeStatus;
  reason: string;
  evidence: DisputeEvidence[];
  decidedBy: string | null;
  decidedAt: string | null;
  resolution: string | null;
  createdAt: string;
}

// ---------------------------------------------------------------------------
// Settlement batches (spec section 58)

export interface SettlementItem {
  paymentId: string;
  amountMinor: number;
  feeMinor: number;
}

export interface SettlementBatch {
  id: string;
  businessDate: string;
  currency: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  totalMinor: number;
  feeMinor: number;
  netMinor: number;
  paymentCount: number;
  failureReason: string | null;
  processedAt: string | null;
  createdAt: string;
}

export interface SettlementBatchDetail extends SettlementBatch {
  items: SettlementItem[];
}

// ---------------------------------------------------------------------------
// Outbox dead letters (spec sections 18-19)

export interface DeadLetter {
  id: string;
  outboxEventId: string;
  eventType: string;
  failureReason: string;
  replayed: boolean;
  createdAt: string;
}
