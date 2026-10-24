import type { ReactNode } from 'react';

/**
 * Line-art spot illustrations for empty, error and loading states. Inline SVG coloured through
 * CSS variables (--ill-*), so they follow the light and dark themes without separate assets.
 */
export type IllustrationName =
  | 'payments'
  | 'transactions'
  | 'accounts'
  | 'beneficiaries'
  | 'fraud'
  | 'aml'
  | 'notifications'
  | 'reports'
  | 'kyc'
  | 'reconciliation'
  | 'treasury'
  | 'admin'
  | 'investigate'
  | 'error'
  | 'forbidden'
  | 'empty';

const S = { stroke: 'var(--ill-line)', strokeWidth: 2.4, strokeLinecap: 'round', strokeLinejoin: 'round', fill: 'none' } as const;
const PAPER = { ...S, fill: 'var(--ill-paper)' };
const ACCENT = { ...S, stroke: 'var(--ill-accent)' };
const SOFT = { fill: 'var(--ill-soft)' };

const SCENES: Record<IllustrationName, ReactNode> = {
  payments: (
    <>
      <rect x="30" y="38" width="78" height="50" rx="9" {...PAPER} />
      <rect x="42" y="50" width="16" height="12" rx="3" {...SOFT} />
      <path d="M42 74h30M42 80h18" {...S} />
      <path className="ill-draw" d="M100 46c18-8 30-2 34 12" {...ACCENT} />
      <path d="M128 52l6 6 4-8" {...ACCENT} />
    </>
  ),
  transactions: (
    <>
      <rect x="34" y="28" width="92" height="66" rx="9" {...PAPER} />
      <path d="M48 46h40M48 62h32M48 78h44" {...S} />
      <path className="ill-draw" d="M106 42v12m0 0l-5-5m5 5l5-5" {...ACCENT} />
      <path className="ill-draw" d="M106 82V70m0 0l-5 5m5-5l5 5" {...S} stroke="var(--ill-accent-2)" />
    </>
  ),
  accounts: (
    <>
      <path d="M34 46a8 8 0 018-8h70a8 8 0 018 8v38a8 8 0 01-8 8H42a8 8 0 01-8-8z" {...PAPER} />
      <path d="M100 58h26v18h-26a9 9 0 010-18z" {...PAPER} />
      <circle cx="104" cy="67" r="3" fill="var(--ill-accent)" />
      <path className="ill-draw" d="M46 38l40-14 10 14" {...ACCENT} />
    </>
  ),
  beneficiaries: (
    <>
      <circle cx="56" cy="52" r="12" {...PAPER} />
      <path d="M36 86c2-12 10-18 20-18s18 6 20 18" {...S} />
      <circle cx="108" cy="52" r="12" {...PAPER} />
      <path d="M88 86c2-12 10-18 20-18s18 6 20 18" {...S} />
      <path className="ill-draw" d="M70 50c8-6 16-6 24 0" {...ACCENT} />
    </>
  ),
  fraud: (
    <>
      <path d="M80 24l32 12v22c0 20-14 32-32 38-18-6-32-18-32-38V36z" {...PAPER} />
      <path className="ill-draw" d="M66 60l10 10 20-22" {...ACCENT} strokeWidth={3.2} />
    </>
  ),
  aml: (
    <>
      <rect x="38" y="26" width="58" height="72" rx="8" {...PAPER} />
      <path d="M50 44h34M50 56h28M50 68h20" {...S} />
      <circle cx="98" cy="70" r="16" {...PAPER} />
      <path className="ill-draw" d="M110 82l14 14" {...ACCENT} strokeWidth={3.2} />
    </>
  ),
  notifications: (
    <>
      <rect x="32" y="40" width="84" height="54" rx="9" {...PAPER} />
      <path className="ill-draw" d="M34 44l40 28 40-28" {...S} />
      <circle cx="118" cy="40" r="9" fill="var(--ill-accent)" />
    </>
  ),
  reports: (
    <>
      <rect x="34" y="28" width="92" height="66" rx="9" {...PAPER} />
      <path d="M52 80V64M72 80V52M92 80V58M112 80V44" {...S} strokeWidth={6} stroke="var(--ill-soft-strong)" />
      <path className="ill-draw" d="M50 58l22-14 20 8 20-16" {...ACCENT} />
    </>
  ),
  kyc: (
    <>
      <rect x="28" y="34" width="104" height="60" rx="9" {...PAPER} />
      <rect x="40" y="46" width="28" height="34" rx="5" {...SOFT} />
      <circle cx="54" cy="58" r="5" fill="var(--ill-paper)" />
      <path d="M80 52h38M80 64h30M80 76h22" {...S} />
      <path className="ill-draw" d="M108 22l6 6 10-12" {...ACCENT} />
    </>
  ),
  reconciliation: (
    <>
      <rect x="26" y="34" width="46" height="58" rx="7" {...PAPER} />
      <rect x="88" y="34" width="46" height="58" rx="7" {...PAPER} />
      <path d="M36 50h26M36 62h20M98 50h26M98 62h20" {...S} />
      <path className="ill-draw" d="M70 76c6 8 14 8 20 0" {...ACCENT} />
    </>
  ),
  treasury: (
    <>
      <ellipse cx="80" cy="84" rx="30" ry="9" {...PAPER} />
      <path d="M50 84V70m60 14V70" {...S} />
      <ellipse cx="80" cy="70" rx="30" ry="9" {...PAPER} />
      <path d="M50 70V56m60 14V56" {...S} />
      <ellipse className="ill-coin" cx="80" cy="56" rx="30" ry="9" {...PAPER} stroke="var(--ill-accent)" />
    </>
  ),
  admin: (
    <>
      <rect x="34" y="28" width="92" height="66" rx="9" {...PAPER} />
      <path d="M52 48h56M52 62h56M52 76h56" {...S} />
      <circle cx="68" cy="48" r="5" fill="var(--ill-accent)" />
      <circle cx="94" cy="62" r="5" fill="var(--ill-accent-2)" />
      <circle cx="78" cy="76" r="5" fill="var(--ill-accent)" />
    </>
  ),
  investigate: (
    <>
      <rect x="30" y="24" width="72" height="80" rx="9" {...PAPER} />
      <path d="M44 42h44M44 56h36M44 70h40M44 84h24" {...S} />
      <rect className="ill-scan" x="34" y="30" width="64" height="8" rx="4" fill="var(--ill-accent)" opacity="0.35" />
      <circle cx="108" cy="74" r="18" {...PAPER} />
      <path d="M121 87l13 13" {...ACCENT} strokeWidth={3.4} />
    </>
  ),
  error: (
    <>
      <path d="M80 26l42 72H38z" {...PAPER} />
      <path d="M80 52v20" {...S} strokeWidth={3.4} stroke="var(--ill-danger)" />
      <circle cx="80" cy="84" r="2.6" fill="var(--ill-danger)" />
    </>
  ),
  forbidden: (
    <>
      <rect x="46" y="54" width="68" height="46" rx="9" {...PAPER} />
      <path d="M58 54V42a22 22 0 0144 0v12" {...S} />
      <circle cx="80" cy="74" r="5" fill="var(--ill-accent)" />
      <path d="M80 79v9" {...ACCENT} />
    </>
  ),
  empty: (
    <>
      <path d="M30 70l14-34h72l14 34" {...S} />
      <path d="M30 70h30l6 10h28l6-10h30v22a6 6 0 01-6 6H36a6 6 0 01-6-6z" {...PAPER} />
    </>
  ),
};

export function Illustration({ name, size = 132, className }: { name: IllustrationName; size?: number; className?: string }) {
  return (
    <svg
      className={`fc-illustration${className ? ` ${className}` : ''}`}
      viewBox="0 0 160 120"
      width={size}
      height={(size * 120) / 160}
      aria-hidden="true"
      focusable="false"
    >
      <ellipse cx="80" cy="64" rx="66" ry="48" className="ill-halo" />
      {SCENES[name]}
    </svg>
  );
}

/** The illustration for a route's section, e.g. /payments/new -> payments. */
export function illustrationForPath(pathname: string): IllustrationName {
  const section = pathname.split('/')[1] ?? '';
  const known: Record<string, IllustrationName> = {
    payments: 'payments', transactions: 'transactions', accounts: 'accounts', beneficiaries: 'beneficiaries',
    fraud: 'fraud', aml: 'aml', notifications: 'notifications', reports: 'reports', kyc: 'kyc',
    reconciliation: 'reconciliation', treasury: 'treasury', admin: 'admin', '': 'reports',
  };
  return known[section] ?? 'empty';
}

export default Illustration;
