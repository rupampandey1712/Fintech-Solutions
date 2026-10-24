using System.Collections.Concurrent;
using System.Diagnostics;
using System.Diagnostics.Metrics;
using FinCore.Infrastructure;
using FluentAssertions;
using Microsoft.EntityFrameworkCore;
using Xunit;

namespace FinCore.Tests;

[Collection("postgres")]
public sealed class TelemetryTests(DatabaseFixture fixture) : IDisposable
{
    private readonly ConcurrentBag<Activity> _activities = [];
    private readonly ActivityListener _listener = new()
    {
        ShouldListenTo = source => source.Name == FinCoreTelemetry.Name,
        Sample = (ref ActivityCreationOptions<ActivityContext> _) => ActivitySamplingResult.AllDataAndRecorded,
    };

    private void Listen()
    {
        _listener.ActivityStopped = activity => _activities.Add(activity);
        ActivitySource.AddActivityListener(_listener);
    }

    public void Dispose() => _listener.Dispose();

    [RequiresPostgresFact]
    public async Task Outbox_rows_carry_the_writers_trace_and_the_dispatcher_continues_it()
    {
        Listen();
        var item = new OutboxEventRecord { AggregateId = Guid.NewGuid(), EventType = "unknown.v1", PayloadJson = "{}" };
        ActivityTraceId requestTrace;
        using (var request = FinCoreTelemetry.Source.StartActivity("test request"))
        {
            request.Should().NotBeNull();
            requestTrace = request!.TraceId;
            await using var db = fixture.CreateContext();
            db.OutboxEvents.Add(item);
            await db.SaveChangesAsync();
        }

        await using (var verify = fixture.CreateContext())
        {
            var stored = await verify.OutboxEvents.SingleAsync(e => e.Id == item.Id);
            stored.TraceContext.Should().NotBeNull().And.Contain(requestTrace.ToHexString());
        }

        string? seenByDelivery = null;
        Task Capture(FinCoreDbContext _, OutboxEventRecord record, CancellationToken __)
        {
            if (record.Id == item.Id) seenByDelivery = FinCoreTelemetry.CurrentTraceparent();
            return Task.CompletedTask;
        }
        await using (var worker = fixture.CreateContext())
            await OutboxDispatcherWorker.DispatchBatchAsync(worker, Capture, 1000, 5, default);

        seenByDelivery.Should().Contain(requestTrace.ToHexString(), "the publish runs inside the writer's trace");
        _activities.Should().Contain(a => a.DisplayName == "outbox publish unknown.v1" && a.TraceId == requestTrace
            && a.Kind == ActivityKind.Producer);
    }

    [Fact]
    public void Continuing_a_trace_from_a_traceparent_keeps_its_trace_id()
    {
        Listen();
        const string traceparent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01";
        ActivityTraceId continued;
        using (var consumer = FinCoreTelemetry.StartContinuing("servicebus process x", ActivityKind.Consumer, traceparent))
        {
            consumer!.TraceId.ToHexString().Should().Be("0af7651916cd43dd8448eb211c80319c");
            consumer.ParentSpanId.ToHexString().Should().Be("b7ad6b7169203331");
            continued = consumer.TraceId;
        }
        // No carried context and nothing ambient: a new trace (with an ambient activity it would nest under it).
        using var fresh = FinCoreTelemetry.StartContinuing("no parent", ActivityKind.Consumer, null);
        fresh!.TraceId.Should().NotBe(continued);
        fresh.Parent.Should().BeNull();
    }

    [Fact]
    public async Task Cache_lookups_are_measured()
    {
        var measurements = new ConcurrentBag<(string Name, long Value, string? Result)>();
        using var meterListener = new MeterListener
        {
            InstrumentPublished = (instrument, listener) =>
            {
                if (instrument.Meter.Name == FinCoreTelemetry.Name) listener.EnableMeasurementEvents(instrument);
            }
        };
        meterListener.SetMeasurementEventCallback<long>((instrument, value, tags, _) =>
        {
            string? result = null;
            foreach (var tag in tags)
                if (tag.Key == "result") result = tag.Value as string;
            measurements.Add((instrument.Name, value, result));
        });
        meterListener.Start();

        await new FinCoreCache(null).GetOrLoadAsync(FinCoreCache.FraudRulesKey, TimeSpan.FromSeconds(5),
            () => Task.FromResult<string?>("rules"));

        measurements.Should().Contain(m => m.Name == "cache_requests_total" && m.Result == "miss" && m.Value == 1);
    }
}
