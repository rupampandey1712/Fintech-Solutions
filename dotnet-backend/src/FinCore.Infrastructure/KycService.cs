using FinCore.Application;
using FinCore.Domain;
using Microsoft.AspNetCore.Http;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace FinCore.Infrastructure;

public sealed class KycService(FinCoreDbContext db, KycDocumentStore documentStore, IConfiguration configuration,
    IHttpContextAccessor? httpContextAccessor = null) : IKycService
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        Converters = { new JsonStringEnumConverter(JsonNamingPolicy.CamelCase) }
    };

    public async Task<KycApplicationView> CreateAsync(Guid customerId, string fullName, string country,
        CancellationToken cancellationToken)
    {
        var trimmed = fullName?.Trim() ?? string.Empty;
        if (trimmed.Length is 0 or > 120)
            throw new KycException("validation_error", "fullName must be between 1 and 120 characters.", 422);
        if (country is null || country.Length != 2 || country.Any(c => c is < 'A' or > 'Z'))
            throw new KycException("validation_error", "country must be a two-letter uppercase ISO code.", 422);

        var application = new KycApplicationRecord { CustomerId = customerId, FullName = trimmed, Country = country };
        db.KycApplications.Add(application);
        await db.SaveChangesAsync(cancellationToken);
        return Map(application);
    }

    public async Task<KycDocumentView> AddDocumentAsync(Guid customerId, Guid applicationId, string documentType,
        string contentBase64, CancellationToken cancellationToken)
    {
        if (documentType is not ("passport" or "national_id" or "proof_of_address"))
            throw new KycException("validation_error", "documentType is invalid.", 422);
        var application = await db.KycApplications.SingleOrDefaultAsync(
            item => item.Id == applicationId && item.CustomerId == customerId, cancellationToken)
            ?? throw new KycException("not_found", "KYC application not found.", 404);
        if (application.Status is not ("pending" or "under_review"))
            throw new KycException("invalid_state", "KYC application can no longer accept documents.", 409);

        var blobPath = await documentStore.SaveAsync(contentBase64, cancellationToken);
        var document = new KycDocumentRecord
        {
            KycApplicationId = application.Id,
            DocumentType = documentType,
            BlobPath = blobPath,
            UploadedAt = DateTimeOffset.UtcNow
        };
        db.KycDocuments.Add(document);
        await db.SaveChangesAsync(cancellationToken);
        return new KycDocumentView(document.Id, document.DocumentType, document.BlobPath, document.Status,
            document.UploadedAt);
    }

    public async Task<KycApplicationView> SubmitAsync(Guid customerId, Guid applicationId,
        CancellationToken cancellationToken)
    {
        var application = await db.KycApplications.Include(item => item.Documents).SingleOrDefaultAsync(
            item => item.Id == applicationId && item.CustomerId == customerId, cancellationToken)
            ?? throw new KycException("not_found", "KYC application not found.", 404);
        if (application.Status is not ("pending" or "under_review"))
            throw new KycException("invalid_state", "KYC application has already been decided.", 409);
        if (application.Documents.Count == 0)
            throw new KycException("kyc_documents_required", "At least one document is required.", 422);

        var now = DateTimeOffset.UtcNow;
        application.SubmittedAt = now;
        // Spec section 17: KycSubmitted announces the review hand-off (the compliance reviewer's
        // queue subscribes to it), independent of the screening decision below.
        db.OutboxEvents.Add(new OutboxEventRecord
        {
            AggregateId = application.Id,
            EventType = "kyc.submitted.v1",
            SchemaVersion = 1,
            PayloadJson = System.Text.Json.JsonSerializer.Serialize(new
            {
                customerId = application.CustomerId, kycApplicationId = application.Id,
                fullName = application.FullName, country = application.Country,
                documentCount = application.Documents.Count
            }),
            CreatedAt = now
        });
        if (SanctionsList.ScreenName(application.FullName, SanctionsExtras()) == SanctionsList.PotentialMatch)
        {
            application.Status = "rejected";
            application.RejectionReason = "Sanctions screening potential match";
            application.DecidedAt = now;
        }
        else
        {
            application.Status = "approved";
            application.DecidedAt = now;
            await ApproveCustomerAsync(application, now, cancellationToken);
        }
        await db.SaveChangesAsync(cancellationToken);
        return Map(application);
    }

    public async Task<KycApplicationView?> GetAsync(Guid customerId, Guid applicationId,
        CancellationToken cancellationToken)
    {
        var application = await db.KycApplications.AsNoTracking().Include(item => item.Documents).SingleOrDefaultAsync(
            item => item.Id == applicationId && item.CustomerId == customerId, cancellationToken);
        return application is null ? null : Map(application);
    }

    public async Task<IReadOnlyList<KycApplicationView>> ListAsync(Guid customerId, CancellationToken cancellationToken)
    {
        var rows = await db.KycApplications.AsNoTracking().Include(item => item.Documents)
            .Where(item => item.CustomerId == customerId)
            .OrderByDescending(item => item.CreatedAt).ThenByDescending(item => item.Id)
            .Take(100).ToListAsync(cancellationToken);
        return rows.Select(Map).ToArray();
    }

    public async Task<KycApplicationView?> ReviewAsync(Guid applicationId, Guid reviewerId, bool approve,
        string? reason, CancellationToken cancellationToken)
    {
        var application = await db.KycApplications.Include(item => item.Documents).SingleOrDefaultAsync(
            item => item.Id == applicationId, cancellationToken);
        if (application is null) return null;
        if (application.Status is not ("pending" or "under_review"))
            throw new KycException("invalid_state", "KYC application has already been decided.", 409);

        var now = DateTimeOffset.UtcNow;
        var previous = application.Status;
        application.DecidedAt = now;
        application.DecidedBy = reviewerId;
        if (approve)
        {
            application.Status = "approved";
            application.RejectionReason = null;
            await ApproveCustomerAsync(application, now, cancellationToken);
        }
        else
        {
            application.Status = "rejected";
            application.RejectionReason = TruncateReason(reason);
        }
        await AuditService.RecordAuditAsync(db, reviewerId, "kyc.reviewed", "kyc_application", application.Id,
            CorrelationId(), oldValue: JsonSerializer.Serialize(new { status = previous }, JsonOptions),
            newValue: JsonSerializer.Serialize(new { status = application.Status, approve,
                rejectionReason = application.RejectionReason }, JsonOptions), cancellationToken: cancellationToken);
        await db.SaveChangesAsync(cancellationToken);
        return Map(application);
    }

    private async Task ApproveCustomerAsync(KycApplicationRecord application, DateTimeOffset now,
        CancellationToken cancellationToken)
    {
        var customer = await db.Customers.SingleOrDefaultAsync(item => item.Id == application.CustomerId, cancellationToken);
        if (customer is not null) customer.Status = "active";
        db.OutboxEvents.Add(new OutboxEventRecord
        {
            AggregateId = application.Id,
            EventType = "kyc.approved.v1",
            SchemaVersion = 1,
            PayloadJson = JsonSerializer.Serialize(new
            {
                customerId = application.CustomerId,
                kycApplicationId = application.Id,
                decidedAt = now
            }, JsonOptions),
            CreatedAt = now
        });
        await AuditService.RecordAuditAsync(db, application.DecidedBy, "kyc.approved", "kyc_application",
            application.Id, CorrelationId(), cancellationToken: cancellationToken);
    }

    private IEnumerable<string>? SanctionsExtras()
    {
        var raw = configuration["SANCTIONS_NAMES"];
        return string.IsNullOrWhiteSpace(raw)
            ? null
            : raw.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
    }

    private string? CorrelationId() => httpContextAccessor?.HttpContext is { } http
        ? http.Items.TryGetValue("CorrelationId", out var value) && value is string text ? text : http.TraceIdentifier
        : null;

    private static string TruncateReason(string? reason)
    {
        var trimmed = string.IsNullOrWhiteSpace(reason) ? "Rejected by reviewer" : reason.Trim();
        return trimmed.Length <= 300 ? trimmed : trimmed[..300];
    }

    private static KycApplicationView Map(KycApplicationRecord application) =>
        new(application.Id, application.Status, application.FullName, application.Country, application.RejectionReason,
            application.SubmittedAt, application.DecidedAt, application.CreatedAt,
            application.Documents.OrderBy(document => document.UploadedAt)
                .Select(document => new KycDocumentView(document.Id, document.DocumentType, document.BlobPath,
                    document.Status, document.UploadedAt))
                .ToArray());
}
