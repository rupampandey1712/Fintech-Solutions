using FinCore.Application;
using FinCore.Infrastructure;
using FluentAssertions;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Xunit;

namespace FinCore.Tests;

[Collection("postgres")]
public class MessagingTests(DatabaseFixture fixture)
{
    private static IAccessTokenIssuer Issuer() =>
        new JwtAccessTokenIssuer(new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?> { ["JWT_SIGNING_KEY"] = new string('k', 40) })
            .Build());

    private static async Task<Guid> RegisterAsync(FinCoreDbContext db)
    {
        var auth = new AuthenticationService(new CustomerIdentityRepository(db), new CustomerPasswordHasher(), Issuer());
        var customer = await auth.RegisterAsync($"msg-{Guid.NewGuid():N}@example.com", "integration-password-123", default);
        return customer.Id;
    }

    private static OutboxEventRecord KycApproved(Guid customerId) => new()
    {
        AggregateId = Guid.NewGuid(),
        EventType = "kyc.approved.v1",
        PayloadJson = $$"""{"customerId":"{{customerId}}"}"""
    };

    [RequiresPostgresFact]
    public async Task Dispatched_events_are_marked_published_and_not_redelivered()
    {
        await using var db = fixture.CreateContext();
        var customerId = await RegisterAsync(db);
        var item = KycApproved(customerId);
        db.OutboxEvents.Add(item);
        await db.SaveChangesAsync();

        var delivered = new List<Guid>();
        Task Record(FinCoreDbContext _, OutboxEventRecord record, CancellationToken __)
        {
            delivered.Add(record.Id);
            return Task.CompletedTask;
        }

        await using (var worker = fixture.CreateContext())
            await OutboxDispatcherWorker.DispatchBatchAsync(worker, Record, 1000, 5, default);
        await using (var worker = fixture.CreateContext())
            await OutboxDispatcherWorker.DispatchBatchAsync(worker, Record, 1000, 5, default);

        delivered.Count(id => id == item.Id).Should().Be(1);
        await using var verify = fixture.CreateContext();
        (await verify.OutboxEvents.SingleAsync(e => e.Id == item.Id)).PublishedAt.Should().NotBeNull();
    }

    [RequiresPostgresFact]
    public async Task Failing_inline_event_is_dead_lettered_without_losing_the_rest_of_the_batch()
    {
        await using var db = fixture.CreateContext();
        var customerId = await RegisterAsync(db);
        var bad = KycApproved(Guid.NewGuid()); // unknown customer: notification FK fails
        var good = KycApproved(customerId);
        db.OutboxEvents.Add(bad);
        await db.SaveChangesAsync();
        db.OutboxEvents.Add(good);
        await db.SaveChangesAsync();

        await using (var worker = fixture.CreateContext())
            await OutboxDispatcherWorker.DispatchBatchAsync(worker, OutboxDispatcherWorker.InlineDeliveryAsync, 1000, 1, default);

        await using var verify = fixture.CreateContext();
        // One notification per channel for the surviving event.
        (await verify.Notifications.CountAsync(n => n.CustomerId == customerId)).Should().Be(4);
        (await verify.OutboxEvents.SingleAsync(e => e.Id == good.Id)).PublishedAt.Should().NotBeNull();
        (await verify.OutboxDeadLetters.AnyAsync(d => d.OutboxEventId == bad.Id)).Should().BeTrue();
    }

    [RequiresPostgresFact]
    public async Task Redelivered_message_is_applied_once()
    {
        await using var db = fixture.CreateContext();
        var customerId = await RegisterAsync(db);
        var messageId = Guid.NewGuid().ToString();
        var payload = $$"""{"customerId":"{{customerId}}"}""";

        bool first, second;
        await using (var consumer = fixture.CreateContext())
            first = await ServiceBusConsumerWorker.ApplyOnceAsync(consumer, "notifications", messageId, "kyc.approved.v1", payload, default);
        await using (var consumer = fixture.CreateContext())
            second = await ServiceBusConsumerWorker.ApplyOnceAsync(consumer, "notifications", messageId, "kyc.approved.v1", payload, default);

        first.Should().BeTrue();
        second.Should().BeFalse();
        await using var verify = fixture.CreateContext();
        // One notification per channel — in_app, email, sms, push — not one per redelivery.
        (await verify.Notifications.CountAsync(n => n.CustomerId == customerId)).Should().Be(4);
    }
}
