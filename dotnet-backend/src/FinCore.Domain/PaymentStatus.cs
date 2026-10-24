namespace FinCore.Domain;

public enum PaymentStatus
{
    Initiated,
    Pending,
    Processing,
    Completed,
    Failed,
    Cancelled,
    Rejected,
    Expired,
    Reversed
}

public static class PaymentStateMachine
{
    private static readonly IReadOnlyDictionary<PaymentStatus, PaymentStatus[]> Allowed =
        new Dictionary<PaymentStatus, PaymentStatus[]>
        {
            [PaymentStatus.Initiated] = [PaymentStatus.Pending, PaymentStatus.Rejected, PaymentStatus.Cancelled],
            // Pending -> Rejected: a payment held for fraud review is refused by the reviewer.
            [PaymentStatus.Pending] = [PaymentStatus.Processing, PaymentStatus.Cancelled, PaymentStatus.Expired, PaymentStatus.Rejected],
            [PaymentStatus.Processing] = [PaymentStatus.Completed, PaymentStatus.Failed],
            // A completed payment posted in error is corrected by reversing its journal entries.
            [PaymentStatus.Completed] = [PaymentStatus.Reversed]
        };

    public static bool CanTransition(PaymentStatus from, PaymentStatus to) =>
        Allowed.TryGetValue(from, out var next) && next.Contains(to);

    public static PaymentStatus Transition(PaymentStatus from, PaymentStatus to)
    {
        if (!CanTransition(from, to))
            throw new InvalidOperationException($"Payment transition {from} -> {to} is not allowed.");

        return to;
    }
}
