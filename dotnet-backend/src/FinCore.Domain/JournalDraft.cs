namespace FinCore.Domain;

public enum EntrySide { Debit, Credit }

public sealed record JournalLineDraft(Guid LedgerAccountId, EntrySide Side, Money Amount);

public sealed class JournalDraft
{
    public string BusinessReference { get; }
    public string Currency { get; }
    public IReadOnlyList<JournalLineDraft> Lines { get; }

    private JournalDraft(string businessReference, string currency, IReadOnlyList<JournalLineDraft> lines)
    {
        BusinessReference = businessReference;
        Currency = currency;
        Lines = lines;
    }

    public static JournalDraft Create(string businessReference, string currency, IEnumerable<JournalLineDraft> lines)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(businessReference);
        var materialized = lines.ToArray();
        if (materialized.Length < 2)
            throw new ArgumentException("A journal requires at least two lines.", nameof(lines));
        if (materialized.Any(line => line.Amount.AmountMinor <= 0 || line.Amount.Currency != currency))
            throw new ArgumentException("Every journal line must have a positive amount in the journal currency.", nameof(lines));

        long debits = 0;
        long credits = 0;
        foreach (var line in materialized)
        {
            if (line.Side == EntrySide.Debit) debits = checked(debits + line.Amount.AmountMinor);
            else credits = checked(credits + line.Amount.AmountMinor);
        }
        if (debits != credits)
            throw new InvalidOperationException($"Journal is unbalanced: debit={debits}, credit={credits}.");

        return new JournalDraft(businessReference, currency, Array.AsReadOnly(materialized));
    }
}
