import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Tooltip from '@mui/material/Tooltip';
import { fetchAgentStatus, fetchFraudCases, investigateFraudCase, reviewFraudCase } from '../api/endpoints';
import { getBackend } from '../api/client';
import PageHeader from '../components/PageHeader';
import DataPage, { TableSkeleton } from '../components/DataPage';
import StatusChip from '../components/StatusChip';
import ReviewCaseDialog from '../features/fraud/ReviewCaseDialog';
import InvestigationPanel from '../features/fraud/InvestigationPanel';
import { useAuth } from '../hooks/useAuth';
import { formatDate, problemMessage } from '../utils/format';
import type { FraudCase, ReviewResolution } from '../types';

const bk = () => getBackend();

const LEVEL_TONE = { low: 'green', medium: 'amber', high: 'red', critical: 'red' } as const;

const REASON_LABELS: Record<string, string> = {
  large_amount: 'Large amount',
  velocity_burst: 'Many payments quickly',
  new_beneficiary_high_amount: 'New beneficiary',
  young_account_large_transfer: 'New account',
  failed_attempts: 'Failed attempts',
};

/** Rule codes come from the configurable rules engine, so unknown ones fall back to their code. */
function reasonLabel(code: string): string {
  return REASON_LABELS[code] ?? code.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

const VISIBLE_REASONS = 2;

export function FraudPage() {
  const queryClient = useQueryClient();
  const { roles } = useAuth();
  // Mirrors the backend: only admins and fraud analysts may review a case.
  const canReview = ['admin', 'fraud_analyst'].some((role) => roles.includes(role));
  const isStaff = roles.some((role) => role !== 'customer');
  const [reviewing, setReviewing] = useState<FraudCase | null>(null);
  const [reviewDefaults, setReviewDefaults] = useState<{ resolution: ReviewResolution; note: string } | null>(null);
  const [investigating, setInvestigating] = useState<FraudCase | null>(null);
  // Only asked of reviewers: for anyone else the investigator is never offered.
  const agent = useQuery({ queryKey: ['fraud', 'agent', bk()], queryFn: fetchAgentStatus, enabled: canReview, staleTime: 60_000 });
  const investigation = useMutation({ mutationFn: (fc: FraudCase) => investigateFraudCase(fc.id) });

  function startInvestigation(fc: FraudCase) {
    setInvestigating(fc);
    investigation.mutate(fc);
  }
  const [actionError, setActionError] = useState<string | null>(null);

  const query = useQuery({ queryKey: ['fraud', 'cases', bk()], queryFn: () => fetchFraudCases({ limit: 50 }) });

  const reviewMutation = useMutation({
    mutationFn: (input: { id: string; resolution: 'closed' | 'false_positive' | 'escalated'; note?: string }) =>
      reviewFraudCase(input.id, { resolution: input.resolution, ...(input.note ? { note: input.note } : {}) }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['fraud'] });
      setReviewing(null);
      setReviewDefaults(null);
    },
    onError: (e) => {
      setReviewing(null);
      setActionError(problemMessage(e, 'Review failed.'));
    },
  });

  return (
    <>
      <PageHeader
        eyebrow="RISK"
        title={isStaff ? 'Fraud cases' : 'Payment reviews'}
        subtitle={
          isStaff
            ? 'Flagged payments awaiting analyst review.'
            : 'Payments of yours held for a security check. Held funds are reserved until a reviewer decides.'
        }
      />

      {actionError ? (
        <Alert severity="error" className="page-alert" onClose={() => setActionError(null)}>
          {actionError}
        </Alert>
      ) : null}

      <DataPage
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        onRetry={() => void query.refetch()}
        isEmpty={(query.data?.items.length ?? 0) === 0}
        emptyTitle={isStaff ? 'No fraud cases' : 'No payments under review'}
        emptyHint={
          isStaff
            ? 'Payments blocked or flagged for review will appear here.'
            : 'If a payment is held for a security check, it will appear here.'
        }
        loading={<TableSkeleton rows={4} columns={6} />}
      >
        <section className="panel panel-table">
          <table className="fc-table">
            <thead>
              <tr>
                <th>Case</th>
                <th>Payment</th>
                <th>Score</th>
                <th>Level</th>
                <th>Status</th>
                <th>Reasons</th>
                <th>Created</th>
                <th>{canReview ? 'Actions' : 'Outcome'}</th>
              </tr>
            </thead>
            <tbody>
              {(query.data?.items ?? []).map((fc) => {
                const reviewable = canReview && (fc.status === 'open' || fc.status === 'investigating');
                const hidden = fc.reasons.slice(VISIBLE_REASONS);
                return (
                  <tr key={fc.id}>
                    <td className="fc-mono fc-cell-muted">{fc.id.slice(0, 8)}…</td>
                    <td>
                      {fc.paymentId ? (
                        <Link to={`/payments/${fc.paymentId}`} className="fc-link fc-mono">
                          {fc.paymentId.slice(0, 8)}…
                        </Link>
                      ) : (
                        <span className="fc-cell-muted">—</span>
                      )}
                    </td>
                    <td className="fc-strong">{fc.score}</td>
                    <td>
                      <StatusChip status={fc.level} tone={LEVEL_TONE[fc.level]} label={fc.level} />
                    </td>
                    <td>
                      <StatusChip status={fc.status} />
                    </td>
                    <td>
                      {fc.reasons.length > 0 ? (
                        <span className="reasons-cell">
                          {fc.reasons.slice(0, VISIBLE_REASONS).map((reason) => (
                            <span key={reason} className="reason-tag" title={reason}>
                              {reasonLabel(reason)}
                            </span>
                          ))}
                          {hidden.length > 0 ? (
                            <Tooltip title={hidden.map(reasonLabel).join(' · ')}>
                              <span className="reason-tag more">+{hidden.length}</span>
                            </Tooltip>
                          ) : null}
                        </span>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td className="fc-cell-muted">{formatDate(fc.createdAt)}</td>
                    <td>
                      {reviewable ? (
                        <span className="row-actions">
                          {agent.data?.enabled ? (
                            <Button size="small" variant="text" className="investigate-button" onClick={() => startInvestigation(fc)}>
                              Investigate
                            </Button>
                          ) : null}
                          <Button size="small" variant="outlined" onClick={() => setReviewing(fc)}>
                            Review
                          </Button>
                        </span>
                      ) : (
                        <span className="fc-cell-muted">
                          {fc.resolution ?? (fc.status === 'open' || fc.status === 'investigating' ? 'Awaiting review' : '—')}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      </DataPage>

      <InvestigationPanel
        fraudCase={investigating}
        investigation={investigation.variables?.id === investigating?.id ? investigation.data : undefined}
        isRunning={investigation.isPending}
        error={investigation.error}
        onClose={() => setInvestigating(null)}
        onRetry={() => investigating && investigation.mutate(investigating)}
        onUseRecommendation={(resolution, note) => {
          setReviewDefaults({ resolution, note });
          setReviewing(investigating);
          setInvestigating(null);
        }}
      />

      <ReviewCaseDialog
        open={reviewing !== null}
        loading={reviewMutation.isPending}
        initialResolution={reviewDefaults?.resolution}
        initialNote={reviewDefaults?.note}
        onClose={() => {
          setReviewing(null);
          setReviewDefaults(null);
        }}
        onConfirm={(resolution, note) => {
          if (reviewing) reviewMutation.mutate({ id: reviewing.id, resolution, note });
        }}
      />
    </>
  );
}

export default FraudPage;
