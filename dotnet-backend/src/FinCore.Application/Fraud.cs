using System.Text.Json;

namespace FinCore.Application;

public sealed record FraudCaseView(Guid Id, Guid? PaymentId, Guid CustomerId, int Score, string Level,
    string Status, IReadOnlyList<string> Reasons, string? Resolution, DateTimeOffset? ReviewedAt,
    DateTimeOffset CreatedAt);

public sealed record AmlAlertView(Guid Id, Guid CustomerId, Guid? PaymentId, string RuleCode, string Status,
    JsonElement Details, DateTimeOffset CreatedAt);

public sealed class FraudException(string code, string message, int statusCode) : Exception(message)
{
    public string Code { get; } = code;
    public int StatusCode { get; } = statusCode;
}
