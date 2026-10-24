namespace FinCore.Domain;

public readonly record struct Money
{
    public long AmountMinor { get; }
    public string Currency { get; }

    public Money(long amountMinor, string currency)
    {
        if (currency is null || currency.Length != 3 || currency.Any(c => c is < 'A' or > 'Z'))
            throw new ArgumentException("Currency must be a three-letter uppercase ISO code.", nameof(currency));
        AmountMinor = amountMinor;
        Currency = currency;
    }

    public Money Add(Money other)
    {
        EnsureSameCurrency(other);
        return new Money(checked(AmountMinor + other.AmountMinor), Currency);
    }

    public Money Subtract(Money other)
    {
        EnsureSameCurrency(other);
        return new Money(checked(AmountMinor - other.AmountMinor), Currency);
    }

    private void EnsureSameCurrency(Money other)
    {
        if (Currency != other.Currency)
            throw new InvalidOperationException("Money values with different currencies cannot be combined.");
    }
}
