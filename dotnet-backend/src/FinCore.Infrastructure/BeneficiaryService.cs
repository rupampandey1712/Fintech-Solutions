using FinCore.Application;
using Microsoft.EntityFrameworkCore;

namespace FinCore.Infrastructure;

public sealed class BeneficiaryService(FinCoreDbContext db, TimeSpan coolingPeriod) : IBeneficiaryService
{
    public async Task<BeneficiaryView> AddAsync(Guid customerId, Guid destinationAccountId, string label,
        CancellationToken cancellationToken)
    {
        var destination = await db.Accounts.AsNoTracking()
            .SingleOrDefaultAsync(account => account.Id == destinationAccountId && account.Status == "active", cancellationToken);
        if (destination is null)
            throw new BeneficiaryException("destination_not_found", "Destination account not found.", 404);
        if (destination.CustomerId == customerId)
            throw new BeneficiaryException("self_beneficiary", "A beneficiary must belong to another customer.", 422);
        if (string.IsNullOrWhiteSpace(label) || label.Trim().Length > 80)
            throw new BeneficiaryException("invalid_label", "Beneficiary label must be between 1 and 80 characters.", 422);

        var beneficiary = new BeneficiaryRecord
        {
            CustomerId = customerId,
            DestinationAccountId = destinationAccountId,
            Label = label.Trim()
        };
        db.Beneficiaries.Add(beneficiary);
        await db.SaveChangesAsync(cancellationToken);
        return Map(beneficiary);
    }

    public async Task<IReadOnlyList<BeneficiaryView>> ListAsync(Guid customerId, CancellationToken cancellationToken)
    {
        var rows = await db.Beneficiaries.AsNoTracking()
            .Where(item => item.CustomerId == customerId)
            .OrderByDescending(item => item.CreatedAt).Take(100).ToListAsync(cancellationToken);
        return rows.Select(Map).ToArray();
    }

    public Task<BeneficiaryView> VerifyAsync(Guid customerId, Guid beneficiaryId, CancellationToken cancellationToken) =>
        ChangeAsync(customerId, beneficiaryId, "verify", cancellationToken);

    public Task<BeneficiaryView> ActivateAsync(Guid customerId, Guid beneficiaryId, CancellationToken cancellationToken) =>
        ChangeAsync(customerId, beneficiaryId, "activate", cancellationToken);

    public Task<BeneficiaryView> DisableAsync(Guid customerId, Guid beneficiaryId, CancellationToken cancellationToken) =>
        ChangeAsync(customerId, beneficiaryId, "disable", cancellationToken);

    private async Task<BeneficiaryView> ChangeAsync(Guid customerId, Guid beneficiaryId, string action,
        CancellationToken cancellationToken)
    {
        var beneficiary = await db.Beneficiaries.SingleOrDefaultAsync(
            item => item.Id == beneficiaryId && item.CustomerId == customerId, cancellationToken);
        if (beneficiary is null)
            throw new BeneficiaryException("not_found", "Beneficiary not found.", 404);
        var now = DateTimeOffset.UtcNow;
        if (action == "verify")
        {
            if (beneficiary.Status != "pending_verification")
                throw new BeneficiaryException("invalid_state", "Beneficiary is not awaiting verification.", 409);
            beneficiary.VerifiedAt = now;
            beneficiary.CoolingUntil = now + coolingPeriod;
            beneficiary.Status = coolingPeriod <= TimeSpan.Zero ? "active" : "cooling";
        }
        else if (action == "activate")
        {
            if (beneficiary.Status == "active") return Map(beneficiary);
            if (beneficiary.Status != "cooling" || beneficiary.CoolingUntil is null)
                throw new BeneficiaryException("invalid_state", "Beneficiary must be verified before activation.", 409);
            if (beneficiary.CoolingUntil > now)
                throw new BeneficiaryException("cooling_period_active", "Beneficiary cooling period has not elapsed.", 409);
            beneficiary.Status = "active";
        }
        else
        {
            beneficiary.Status = "disabled";
        }
        await db.SaveChangesAsync(cancellationToken);
        return Map(beneficiary);
    }

    private static BeneficiaryView Map(BeneficiaryRecord item) =>
        new(item.Id, item.DestinationAccountId, item.Label, item.Status,
            item.VerifiedAt, item.CoolingUntil, item.CreatedAt);
}
