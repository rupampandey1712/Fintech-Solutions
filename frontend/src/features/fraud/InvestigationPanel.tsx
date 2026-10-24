import Button from '@mui/material/Button';
import Drawer from '@mui/material/Drawer';
import IconButton from '@mui/material/IconButton';
import Illustration from '../../components/Illustration';
import { problemCode } from '../../utils/format';
import type { FraudCase, Investigation, ReviewResolution } from '../../types';

/** The evidence the investigator can check, in the order an analyst would read it. */
const AREAS: { tool: string; label: string }[] = [
  { tool: 'get_case_overview', label: 'Flagged payment and triggered rules' },
  { tool: 'get_payment_history', label: "Customer's payment history" },
  { tool: 'get_beneficiary_profile', label: 'Beneficiary and destination account' },
  { tool: 'get_compliance_status', label: 'KYC status and AML alerts' },
  { tool: 'get_account_activity', label: 'Account age, sign-ins and other cases' },
];

const RECOMMENDATION: Record<ReviewResolution, { title: string; effect: string; tone: string }> = {
  false_positive: { title: 'Release the payment', effect: 'Likely legitimate. Releasing sends the held funds to the beneficiary.', tone: 'release' },
  closed: { title: 'Confirm fraud', effect: 'Likely fraudulent. Confirming returns the held funds to the customer.', tone: 'fraud' },
  escalated: { title: 'Escalate the case', effect: 'The evidence is mixed. Escalating keeps the payment on hold for a senior reviewer.', tone: 'escalate' },
};

const CONFIDENCE_LEVELS = ['low', 'medium', 'high'] as const;

function errorMessage(error: unknown): string {
  switch (problemCode(error)) {
    case 'agent_not_configured':
      return 'The investigator is switched off. Add GEMINI_API_KEY to the API environment and restart it.';
    case 'agent_timeout':
      return 'The investigation took too long. Try again.';
    case 'agent_failed':
      return "The AI provider didn't respond. Try again in a moment.";
    case 'agent_incomplete':
      return 'The investigator stopped without a recommendation. Try again, or review the case yourself.';
    case 'rate_limited':
      return "You've run several investigations in the last minute. Wait a minute, then try again.";
    case 'invalid_state':
    case 'conflict':
      return 'This case is already resolved.';
    default:
      return 'The investigation could not be completed. Try again.';
  }
}

export function InvestigationPanel({
  fraudCase,
  investigation,
  isRunning,
  error,
  onClose,
  onRetry,
  onUseRecommendation,
}: {
  fraudCase: FraudCase | null;
  investigation: Investigation | undefined;
  isRunning: boolean;
  error: unknown;
  onClose: () => void;
  onRetry: () => void;
  onUseRecommendation: (resolution: ReviewResolution, note: string) => void;
}) {
  const checked = new Set(investigation?.steps.map((step) => step.tool) ?? []);
  const recommendation = investigation ? RECOMMENDATION[investigation.recommendation] : null;
  const state = isRunning ? 'running' : investigation ? 'done' : error ? 'failed' : 'idle';

  return (
    <Drawer
      anchor="right"
      open={fraudCase !== null}
      onClose={onClose}
      PaperProps={{ className: 'investigation-drawer' }}
    >
      <header className="investigation-head">
        <div>
          <h2>Investigation</h2>
          <p>
            Case <span className="fc-mono">{fraudCase?.id.slice(0, 8)}</span>, risk score {fraudCase?.score}
          </p>
        </div>
        <IconButton aria-label="Close investigation" onClick={onClose} size="small">
          ✕
        </IconButton>
      </header>

      <div className={`investigation-body is-${state}`} aria-live="polite" aria-busy={isRunning}>
        {state === 'running' ? (
          <div className="investigation-intro">
            <Illustration name="investigate" size={120} />
            <strong>Gathering evidence</strong>
            <p>The investigator reads this case and decides what else to check. This usually takes 10 to 30 seconds.</p>
          </div>
        ) : null}

        {state === 'failed' ? (
          <div className="investigation-intro">
            <Illustration name="error" size={110} />
            <strong>No recommendation</strong>
            <p>{errorMessage(error)}</p>
            <Button variant="outlined" onClick={onRetry}>
              Try again
            </Button>
          </div>
        ) : null}

        {state === 'running' || state === 'done' ? (
          <ol className="evidence-list">
            {AREAS.map((area, index) => {
              const status = state === 'running' ? 'scanning' : checked.has(area.tool) ? 'checked' : 'skipped';
              return (
                <li key={area.tool} className={`evidence-item ${status}`} style={{ ['--i' as string]: index }}>
                  <span className="evidence-mark" aria-hidden="true" />
                  <span className="evidence-label">{area.label}</span>
                  <span className="evidence-state">
                    {status === 'scanning' ? '' : status === 'checked' ? 'Checked' : 'Not needed'}
                  </span>
                </li>
              );
            })}
          </ol>
        ) : null}

        {investigation && recommendation ? (
          <section className={`recommendation tone-${recommendation.tone}`}>
            <div className="recommendation-head">
              <span className="recommendation-kicker">Recommendation</span>
              <span className="confidence" aria-label={`${investigation.confidence} confidence`}>
                {CONFIDENCE_LEVELS.map((level, index) => (
                  <i key={level} className={index <= CONFIDENCE_LEVELS.indexOf(investigation.confidence) ? 'on' : ''} />
                ))}
                <small>{investigation.confidence} confidence</small>
              </span>
            </div>
            <h3>{recommendation.title}</h3>
            <p className="recommendation-effect">{recommendation.effect}</p>
            <p className="recommendation-summary">{investigation.summary}</p>
            {investigation.keyFindings.length > 0 ? (
              <ul className="findings">
                {investigation.keyFindings.map((finding) => (
                  <li key={finding}>{finding}</li>
                ))}
              </ul>
            ) : null}
            <div className="recommendation-actions">
              <Button
                variant="contained"
                onClick={() => onUseRecommendation(investigation.recommendation, investigation.suggestedNote)}
              >
                Review with this recommendation
              </Button>
              <Button variant="text" onClick={onRetry}>
                Investigate again
              </Button>
            </div>
            <p className="recommendation-foot">
              Drafted by {investigation.model}. It can be wrong: check the evidence before you decide.
            </p>
          </section>
        ) : null}
      </div>
    </Drawer>
  );
}

export default InvestigationPanel;
