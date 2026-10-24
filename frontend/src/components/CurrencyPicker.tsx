import { useMemo, useState } from 'react';

/**
 * Report series carry one row per period per currency, because amounts in different currencies
 * are never added together. This picks the currency a chart shows: the busiest one by default,
 * switchable when the window contains more than one.
 */
export function useReportCurrency(points: { currency: string; count: number }[]) {
  const currencies = useMemo(() => {
    const counts = new Map<string, number>();
    for (const point of points) counts.set(point.currency, (counts.get(point.currency) ?? 0) + point.count);
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([code]) => code);
  }, [points]);
  const [chosen, setChosen] = useState<string | null>(null);
  const currency = chosen && currencies.includes(chosen) ? chosen : (currencies[0] ?? null);
  return { currency, currencies, setCurrency: setChosen };
}

export function CurrencyPicker({
  currency,
  currencies,
  onChange,
}: {
  currency: string | null;
  currencies: string[];
  onChange: (currency: string) => void;
}) {
  if (currencies.length < 2) return null;
  return (
    <div className="filter-chips currency-picker" role="tablist" aria-label="Currency">
      {currencies.map((code) => (
        <button
          key={code}
          type="button"
          role="tab"
          aria-selected={code === currency}
          className={`filter-chip${code === currency ? ' active' : ''}`}
          onClick={() => onChange(code)}
        >
          {code}
        </button>
      ))}
    </div>
  );
}

export default CurrencyPicker;
