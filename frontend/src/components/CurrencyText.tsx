import { formatMoney } from '../utils/format';

export function CurrencyText({
  amountMinor,
  currency,
}: {
  amountMinor: number | null | undefined;
  currency: string | null | undefined;
}) {
  return <span className="fc-money">{formatMoney(amountMinor, currency)}</span>;
}

export default CurrencyText;
