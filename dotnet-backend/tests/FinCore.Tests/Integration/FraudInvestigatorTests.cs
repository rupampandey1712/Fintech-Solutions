using System.Text.Json;
using FinCore.Application;
using FinCore.Infrastructure;
using FluentAssertions;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.AI;
using Xunit;

namespace FinCore.Tests;

/// <summary>
/// The fraud investigator agent, driven by a scripted chat client in place of Gemini. The real
/// Agent Framework agent and function-invocation loop run around it, so tool calling, the
/// submit_report termination and the iteration cap are exercised for real.
/// </summary>
public partial class HoldsAndTreasuryTests
{
    private static readonly Dictionary<string, object?> ValidReport = new()
    {
        ["recommendation"] = "false_positive", ["confidence"] = "medium",
        ["summary"] = "The customer pays this beneficiary regularly and the amount is typical.",
        ["key_findings"] = new[] { "Two prior completed payments to this beneficiary." },
        ["suggested_note"] = "Regular beneficiary, typical amount"
    };

    private sealed class ScriptedChatClient(params FunctionCallContent[][] turns) : IChatClient
    {
        private int _turn;
        public List<List<ChatMessage>> Seen { get; } = [];

        public Task<ChatResponse> GetResponseAsync(IEnumerable<ChatMessage> messages, ChatOptions? options = null,
            CancellationToken cancellationToken = default)
        {
            Seen.Add(messages.ToList());
            var calls = turns[Math.Min(_turn++, turns.Length - 1)];
            var message = calls.Length == 0
                ? new ChatMessage(ChatRole.Assistant, "I think it is fine.")
                : new ChatMessage(ChatRole.Assistant, calls.Select(AIContent (call) =>
                    new FunctionCallContent($"call-{Guid.NewGuid():N}", call.Name, call.Arguments)).ToList());
            return Task.FromResult(new ChatResponse(message));
        }

        public IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(IEnumerable<ChatMessage> messages,
            ChatOptions? options = null, CancellationToken cancellationToken = default) =>
            throw new NotSupportedException("The investigator does not stream.");

        public object? GetService(Type serviceType, object? serviceKey = null) => null;
        public void Dispose() { }

        public IEnumerable<FunctionResultContent> Results =>
            Seen[^1].SelectMany(message => message.Contents).OfType<FunctionResultContent>();
    }

    private static FunctionCallContent Call(string name, Dictionary<string, object?>? args = null) => new("", name, args);

    private async Task<(InvestigationResult Result, ScriptedChatClient Model)> InvestigateAsync(Guid caseId,
        int maxTurns, params FunctionCallContent[][] turns)
    {
        await using var db = fixture.CreateContext();
        var fraudCase = await db.FraudCases.AsNoTracking().SingleAsync(item => item.Id == caseId);
        var model = new ScriptedChatClient(turns);
        var result = await FraudInvestigator.RunAsync(model, new InvestigationFacts(db, fraudCase), maxTurns, default);
        return (result, model);
    }

    [RequiresPostgresFact]
    public async Task Investigator_gathers_evidence_then_reports_without_changing_anything()
    {
        var (_, aliceAccount, _, payment, caseId) = await HeldPaymentAsync();
        var (result, model) = await InvestigateAsync(caseId, 8,
            [Call("get_case_overview"), Call("get_payment_history", new() { ["days"] = 30 })],
            [Call("get_beneficiary_profile")],
            [Call("submit_report", ValidReport)]);

        result.Report.Should().NotBeNull();
        result.Report!.Recommendation.Should().Be("false_positive");
        result.Report.KeyFindings.Should().Equal("Two prior completed payments to this beneficiary.");
        result.Steps.Select(step => step.Tool).Should().Equal("get_case_overview", "get_payment_history", "get_beneficiary_profile");
        result.ModelTurns.Should().Be(3, "submit_report ends the loop without another model call");

        // The tools returned real facts about this case, with the same keys as the Python agent.
        var overview = JsonDocument.Parse(model.Seen[1].SelectMany(message => message.Contents)
            .OfType<FunctionResultContent>().First().Result!.ToString()!).RootElement;
        overview.GetProperty("payment").GetProperty("amount").GetString().Should().Be("300.00 USD");
        overview.GetProperty("case").GetProperty("triggeredRules").GetArrayLength().Should().BeGreaterThan(0);

        // The agent only drafts: the payment is still held and the funds still reserved.
        await using var verify = fixture.CreateContext();
        (await verify.Payments.SingleAsync(item => item.Id == payment.Id)).Status.Should().Be(FinCore.Domain.PaymentStatus.Pending);
        (await BalanceAsync(aliceAccount)).Should().Be(70_000);
    }

    [RequiresPostgresFact]
    public async Task Investigator_returns_customer_typed_text_only_as_marked_data()
    {
        var (_, _, _, _, caseId) = await HeldPaymentAsync();
        var (result, model) = await InvestigateAsync(caseId, 8,
            [Call("get_case_overview")],
            [Call("submit_report", new(ValidReport) { ["recommendation"] = "escalated" })]);
        result.Report!.Recommendation.Should().Be("escalated");
        var overview = JsonDocument.Parse(model.Seen[1].SelectMany(message => message.Contents)
            .OfType<FunctionResultContent>().First().Result!.ToString()!).RootElement;
        var beneficiary = overview.GetProperty("beneficiary");
        beneficiary.GetProperty("customerSuppliedText").GetProperty("label").GetString().Should().Be("bob");
        beneficiary.TryGetProperty("label", out _).Should().BeFalse("customer text appears only inside the marked field");
        overview.GetProperty("payment").TryGetProperty("reference", out _).Should().BeFalse();
    }

    [RequiresPostgresFact]
    public async Task Investigator_hands_an_invalid_report_back_to_the_model()
    {
        var (_, _, _, _, caseId) = await HeldPaymentAsync();
        var (result, model) = await InvestigateAsync(caseId, 8,
            [Call("submit_report", new(ValidReport) { ["recommendation"] = "approve" })],
            [Call("submit_report", ValidReport)]);
        result.Report!.Recommendation.Should().Be("false_positive");
        model.Seen[1].SelectMany(message => message.Contents).OfType<FunctionResultContent>()
            .Single().Result!.ToString().Should().Contain("invalid");
    }

    [RequiresPostgresFact]
    public async Task Investigator_without_a_report_or_past_its_turn_budget_returns_no_report()
    {
        var (_, _, _, _, caseId) = await HeldPaymentAsync();
        // One turn with no tool calls: the model answers in prose and never submits.
        var (silent, _) = await InvestigateAsync(caseId, 8, Array.Empty<FunctionCallContent>());
        silent.Report.Should().BeNull();

        var (looping, _) = await InvestigateAsync(caseId, 3, [Call("get_case_overview")]);
        looping.Report.Should().BeNull();
        looping.ModelTurns.Should().BeLessThanOrEqualTo(4, "the iteration cap stops a model that never finishes");
    }

    [Fact]
    public void Investigator_instructions_load_from_the_shared_resource()
    {
        FraudInvestigator.Instructions.Should().StartWith("You are a fraud investigator");
        FraudInvestigator.Instructions.Should().Contain("customerSuppliedText");
    }
}
