import Box from '@mui/material/Box';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import LinearProgress from '@mui/material/LinearProgress';
import StatusChip from '../../components/StatusChip';
import type { PaymentRisk } from '../../types';

const LEVEL_TONE = { low: 'green', medium: 'amber', high: 'red', critical: 'red' } as const;
const DECISION_TONE = { allow: 'green', review: 'amber', block: 'red' } as const;

export function PaymentRiskPanel({ risk }: { risk: PaymentRisk }) {
  return (
    <section className="panel">
      <div className="panel-title-row">
        <h3>Risk assessment</h3>
        <StatusChip status={risk.decision} tone={DECISION_TONE[risk.decision]} label={risk.decision} />
      </div>
      <Box sx={{ mt: 1 }}>
        <div className="risk-score-row">
          <span className="risk-score-value">{risk.score}</span>
          <span className="risk-score-label">/ 100 risk score</span>
          <StatusChip status={risk.level} tone={LEVEL_TONE[risk.level]} label={risk.level} />
        </div>
        <LinearProgress
          variant="determinate"
          value={Math.min(100, Math.max(0, risk.score))}
          color={risk.score >= 70 ? 'error' : risk.score >= 40 ? 'warning' : 'success'}
          sx={{ mt: 1 }}
        />
      </Box>
      {risk.reasons.length > 0 ? (
        <>
          <h4 className="panel-subheading">Reasons</h4>
          <List dense disablePadding>
            {risk.reasons.map((reason, i) => (
              <ListItem key={i} disableGutters>
                <span className="risk-reason">• {reason}</span>
              </ListItem>
            ))}
          </List>
        </>
      ) : (
        <p className="panel-muted">No risk reasons recorded.</p>
      )}
    </section>
  );
}

export default PaymentRiskPanel;
