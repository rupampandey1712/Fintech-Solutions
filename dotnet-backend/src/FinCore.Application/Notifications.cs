namespace FinCore.Application;

public sealed record NotificationView(Guid Id, string Channel, string EventType, string Subject, string Body,
    string Status, DateTimeOffset? SentAt, DateTimeOffset CreatedAt);
