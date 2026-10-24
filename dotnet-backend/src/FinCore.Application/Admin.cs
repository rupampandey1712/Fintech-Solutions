namespace FinCore.Application;

public sealed record AdminCustomerView(Guid Id, string Email, string Status, IReadOnlyList<string> Roles,
    DateTimeOffset CreatedAt);

public sealed record FraudRuleView(Guid Id, string Code, string Description, long? ThresholdMinor, int Weight,
    bool Enabled);

public sealed record AuditLogView(Guid Id, Guid? ActorId, string Action, string EntityType, Guid? EntityId,
    string? IpAddress, string? CorrelationId, DateTimeOffset CreatedAt);
