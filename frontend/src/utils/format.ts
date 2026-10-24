import type { Problem } from '../types';

const MINOR_UNITS = 100;

/** Format minor-unit amounts as "1,234.56 USD". */
export function formatMoney(amountMinor: number | null | undefined, currency: string | null | undefined): string {
  if (amountMinor === null || amountMinor === undefined || Number.isNaN(amountMinor)) return '—';
  const value = amountMinor / MINOR_UNITS;
  const formatted = new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
  return `${formatted} ${currency ?? ''}`.trim();
}

/** Format a major-unit amount (e.g. live preview while typing). */
export function formatMajor(amount: number, currency: string | null | undefined): string {
  const formatted = new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount);
  return `${formatted} ${currency ?? ''}`.trim();
}

/** Short axis label for a major-unit amount: 600000 -> "600K", 1250000 -> "1.3M". */
export function formatCompact(amount: number): string {
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(amount);
}

/** Parse a major-unit string ("12.34") into minor units (1234). */
export function majorToMinor(amount: string): number | null {
  const value = Number(amount);
  if (!Number.isFinite(value)) return null;
  return Math.round(value * MINOR_UNITS);
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString();
}

export function formatDateOnly(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleDateString();
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';
  const diffMs = then - Date.now();
  const abs = Math.abs(diffMs);
  const units: [number, Intl.RelativeTimeFormatUnit][] = [
    [60_000, 'minute'],
    [3_600_000, 'hour'],
    [86_400_000, 'day'],
  ];
  const rtf = new Intl.RelativeTimeFormat('en', { numeric: 'auto' });
  if (abs < 60_000) return 'just now';
  if (abs < 3_600_000) return rtf.format(Math.round(diffMs / 60_000), 'minute');
  if (abs < 86_400_000) return rtf.format(Math.round(diffMs / 3_600_000), 'hour');
  void units;
  return rtf.format(Math.round(diffMs / 86_400_000), 'day');
}

/** Append a cursor param to query params without mutating the original. */
export function withCursor<T extends Record<string, unknown>>(
  params: T,
  cursor: string | null | undefined,
): T & { cursor?: string } {
  return cursor ? { ...params, cursor } : { ...params };
}

/** Extract a human-readable message from a problem+json error response. */
export function problemMessage(error: unknown, fallback = 'Something went wrong'): string {
  if (!error) return fallback;
  const data = (error as { response?: { data?: unknown } }).response?.data;
  if (data && typeof data === 'object') {
    const problem = data as Problem;
    const parts: string[] = [];
    if (problem.detail) parts.push(problem.detail);
    else if (problem.title) parts.push(problem.title);
    else if (problem.code) parts.push(problem.code);
    if (problem.errors?.length) {
      const fieldErrors = problem.errors.map((e) => `${e.field}: ${e.code}`).join('; ');
      if (fieldErrors) parts.push(fieldErrors);
    }
    if (parts.length) return parts.join(' — ');
  }
  if (error instanceof Error && error.message) {
    if (error.message === 'Network Error') return 'Network error — is the backend running?';
    return error.message;
  }
  return fallback;
}

/** Extract the problem code from an error response, if present. */
export function problemCode(error: unknown): string | undefined {
  const data = (error as { response?: { data?: unknown } })?.response?.data;
  if (data && typeof data === 'object' && 'code' in (data as Problem)) {
    return (data as Problem).code;
  }
  return undefined;
}

export function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value.trim());
}

/** Read a File as base64 (without the data: prefix). */
export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('Could not read file'));
    reader.onload = () => {
      const result = String(reader.result ?? '');
      const commaIndex = result.indexOf(',');
      resolve(commaIndex >= 0 ? result.slice(commaIndex + 1) : result);
    };
    reader.readAsDataURL(file);
  });
}
