using System.ClientModel;
using System.ComponentModel;
using System.Text.Json;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using OpenAI;

namespace FinCore.Infrastructure;

/// <summary>Builds the chat model the investigator runs on, or none when no API key is configured.</summary>
public interface IInvestigatorChatClientFactory
{
    string Provider { get; }
    string Model { get; }
    /// <summary>Null when the agent is not configured.</summary>
    IChatClient? Create();
}

/// <summary>
/// Gemini through Google's OpenAI-compatible endpoint, using the official OpenAI .NET client and its
/// Microsoft.Extensions.AI adapter (the same path Agent Framework uses for OpenAI models).
/// </summary>
public sealed class GeminiChatClientFactory(string? apiKey, string model, TimeSpan timeout)
    : IInvestigatorChatClientFactory
{
    public static readonly Uri Endpoint = new("https://generativelanguage.googleapis.com/v1beta/openai/");

    public string Provider => "gemini";
    public string Model => model;

    public IChatClient? Create() => string.IsNullOrWhiteSpace(apiKey)
        ? null
        : new OpenAIClient(new ApiKeyCredential(apiKey), new OpenAIClientOptions { Endpoint = Endpoint, NetworkTimeout = timeout })
            .GetChatClient(model).AsIChatClient();
}

public sealed record InvestigationReport(string Recommendation, string Confidence, string Summary,
    IReadOnlyList<string> KeyFindings, string SuggestedNote);

public sealed record InvestigationStep(string Tool, string Label);

public sealed record InvestigationResult(InvestigationReport? Report, IReadOnlyList<InvestigationStep> Steps, int ModelTurns);

/// <summary>
/// Fraud case investigator: a Microsoft Agent Framework agent that gathers evidence with read-only
/// tools and drafts a recommendation. The Python counterpart is a LangGraph graph
/// (<c>app.agents.fraud_investigator</c>); both use the same instructions, tools and report shape.
/// </summary>
/// <remarks>
/// The tools are closures over one case's <see cref="InvestigationFacts"/>, so the agent can neither
/// change anything nor look beyond that case. It finishes by calling <c>submit_report</c>, which
/// ends the function-calling loop; the analyst then decides with the existing review endpoint.
/// </remarks>
public static class FraudInvestigator
{
    private const string PromptResource = "FinCore.Infrastructure.Resources.fraud_investigator_prompt.txt";

    public static readonly IReadOnlyDictionary<string, string> ToolLabels = new Dictionary<string, string>
    {
        ["get_case_overview"] = "Read the flagged payment and triggered rules",
        ["get_payment_history"] = "Compared with the customer's payment history",
        ["get_beneficiary_profile"] = "Checked the beneficiary and destination account",
        ["get_compliance_status"] = "Checked KYC status and AML alerts",
        ["get_account_activity"] = "Reviewed account age, sign-ins and other cases",
    };

    private static readonly string[] Recommendations = ["false_positive", "closed", "escalated"];
    private static readonly string[] Confidences = ["low", "medium", "high"];
    private static readonly Lazy<string> Prompt = new(ReadPrompt);

    /// <summary>The shared instructions; identical to python-backend/app/resources/fraud_investigator_prompt.txt.</summary>
    public static string Instructions => Prompt.Value;

    private static string ReadPrompt()
    {
        using var stream = typeof(FraudInvestigator).Assembly.GetManifestResourceStream(PromptResource)
            ?? throw new InvalidOperationException($"Embedded resource {PromptResource} is missing.");
        using var reader = new StreamReader(stream);
        return reader.ReadToEnd().Trim();
    }

    public static async Task<InvestigationResult> RunAsync(IChatClient model, InvestigationFacts facts, int maxTurns,
        CancellationToken cancellationToken)
    {
        InvestigationReport? report = null;
        var steps = new List<InvestigationStep>();

        void Track(string tool)
        {
            if (ToolLabels.TryGetValue(tool, out var label) && steps.All(step => step.Tool != tool))
                steps.Add(new InvestigationStep(tool, label));
        }

        // Tool output goes to the model as JSON text, matching the Python agent's json.dumps.
        static string Json(object value) => JsonSerializer.Serialize(value, JsonSerializerOptions.Web);

        AITool[] tools =
        [
            AIFunctionFactory.Create(async () => { Track("get_case_overview"); return Json(await facts.OverviewAsync(cancellationToken)); },
                "get_case_overview", "The flagged payment, its beneficiary, the risk score and which fraud rules triggered."),
            AIFunctionFactory.Create(async ([Description("Look-back window in days.")] int days = 90) =>
                {
                    Track("get_payment_history");
                    return Json(await facts.PaymentHistoryAsync(days, cancellationToken));
                },
                "get_payment_history",
                "The customer's other payments in a window: count by status, typical and largest amounts, and how many were at least as large as this one."),
            AIFunctionFactory.Create(async () => { Track("get_beneficiary_profile"); return Json(await facts.BeneficiaryProfileAsync(cancellationToken)); },
                "get_beneficiary_profile",
                "Prior payments to this beneficiary and their outcomes, the destination account's age, and how many customers pay that account."),
            AIFunctionFactory.Create(async () => { Track("get_compliance_status"); return Json(await facts.ComplianceAsync(cancellationToken)); },
                "get_compliance_status", "The customer's KYC status and recent AML alerts."),
            AIFunctionFactory.Create(async () => { Track("get_account_activity"); return Json(await facts.AccountActivityAsync(cancellationToken)); },
                "get_account_activity",
                "Account ages, successful and failed sign-ins in the last 7 days, and the customer's other fraud cases."),
            AIFunctionFactory.Create(
                ([Description("false_positive releases the payment, closed confirms fraud, escalated hands it to a senior reviewer.")] string recommendation,
                 [Description("low, medium or high.")] string confidence,
                 [Description("Two or three sentences for the analyst.")] string summary,
                 [Description("Short sentences, each naming its evidence.")] string[] key_findings,
                 [Description("A review note the analyst can reuse, at most 200 characters.")] string suggested_note) =>
                {
                    var problem = Validate(recommendation, confidence, summary, key_findings, suggested_note);
                    if (problem is not null)
                        return $"The report was invalid, fix it and submit again: {problem}";
                    report = new InvestigationReport(recommendation, confidence, summary, key_findings, suggested_note);
                    // Ends the function-calling loop: the investigation is complete.
                    if (FunctionInvokingChatClient.CurrentContext is { } context) context.Terminate = true;
                    return "Report received.";
                },
                "submit_report", "Submit the final recommendation. Call exactly once, when the evidence is in."),
        ];

        var turns = new TurnCounter(model);
        var pipeline = new FunctionInvokingChatClient(turns) { MaximumIterationsPerRequest = maxTurns };
        var agent = pipeline.AsAIAgent(new ChatClientAgentOptions
        {
            Name = "fraud-investigator",
            ChatOptions = new ChatOptions { Instructions = Instructions, Tools = tools, Temperature = 0 },
            // The pipeline above already invokes functions, with our iteration cap.
            UseProvidedChatClientAsIs = true,
        });
        await agent.RunAsync("Investigate this fraud case and submit your report.", cancellationToken: cancellationToken);
        return new InvestigationResult(report, steps, turns.Count);
    }

    private static string? Validate(string recommendation, string confidence, string summary, string[]? findings, string note)
    {
        if (!Recommendations.Contains(recommendation)) return "recommendation must be false_positive, closed or escalated.";
        if (!Confidences.Contains(confidence)) return "confidence must be low, medium or high.";
        if (string.IsNullOrWhiteSpace(summary) || summary.Length > 800) return "summary must be 1-800 characters.";
        if (findings is null || findings.Length > 8) return "key_findings must have at most 8 entries.";
        if (note is null || note.Length > 200) return "suggested_note must be at most 200 characters.";
        return null;
    }

    /// <summary>Counts model calls, so the audit trail records how many turns an investigation took.</summary>
    private sealed class TurnCounter(IChatClient inner) : DelegatingChatClient(inner)
    {
        public int Count { get; private set; }

        public override Task<ChatResponse> GetResponseAsync(IEnumerable<ChatMessage> messages, ChatOptions? options = null,
            CancellationToken cancellationToken = default)
        {
            Count++;
            return base.GetResponseAsync(messages, options, cancellationToken);
        }
    }
}
