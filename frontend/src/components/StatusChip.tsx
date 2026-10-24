import Chip from '@mui/material/Chip';

const GREEN = ['completed', 'active', 'approved', 'sent', 'verified', 'allow', 'credit', 'matched', 'ready', 'ok', 'won'];
const AMBER = [
  'pending',
  'pending_verification',
  'cooling',
  'under_review',
  'initiated',
  'processing',
  'queued',
  'investigating',
  'open',
  'review',
  'uploaded',
  'escalated',
  'delayed',
];
const RED = ['failed', 'rejected', 'blocked', 'critical', 'frozen', 'lost', 'partial_failure', 'timeout'];
const GRAY = ['disabled', 'cancelled', 'canceled', 'expired', 'closed', 'false_positive', 'reversed', 'replayed', 'duplicate'];

export type ChipTone = 'green' | 'amber' | 'red' | 'gray' | 'blue';

const TONE_CLASS: Record<ChipTone, string> = {
  green: 'fc-chip-green',
  amber: 'fc-chip-amber',
  red: 'fc-chip-red',
  gray: 'fc-chip-gray',
  blue: 'fc-chip-blue',
};

export function toneForStatus(status: string | null | undefined): ChipTone {
  const s = (status ?? '').toLowerCase();
  if (GREEN.includes(s)) return 'green';
  if (AMBER.includes(s)) return 'amber';
  if (RED.includes(s)) return 'red';
  if (GRAY.includes(s)) return 'gray';
  return 'blue';
}

export function humanizeStatus(status: string | null | undefined): string {
  if (!status) return '—';
  return status.replaceAll('_', ' ').replace(/^\w/, (c) => c.toUpperCase());
}

export function StatusChip({
  status,
  tone,
  size = 'small',
  label,
}: {
  status: string | null | undefined;
  tone?: ChipTone;
  size?: 'small' | 'medium';
  label?: string;
}) {
  const resolvedTone = tone ?? toneForStatus(status);
  return (
    <Chip
      className={`fc-chip ${TONE_CLASS[resolvedTone]}`}
      label={label ?? humanizeStatus(status)}
      size={size}
      variant="outlined"
    />
  );
}

export default StatusChip;
