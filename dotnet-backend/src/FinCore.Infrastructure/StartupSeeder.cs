using FinCore.Application;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Serilog;

namespace FinCore.Infrastructure;

public static class StartupSeeder
{
    public static readonly (string Code, string Description, long ThresholdMinor, int Weight)[] DefaultFraudRules =
    [
        ("large_amount", "Payment amount above configured threshold", 5_000_000, 30),
        ("velocity_burst", "Too many payments within a short window", 3, 25),
        ("new_beneficiary_high_amount", "Large transfer to a newly added beneficiary", 2_000_000, 20),
        ("young_account_large_transfer", "Large transfer from a young account", 1_000_000, 25),
        ("failed_attempts", "Too many failed payment attempts", 3, 20)
    ];

    // Seeding must never prevent startup: the database may not be migrated yet.
    public static async Task SeedAsync(IConfiguration configuration, FinCoreDbContext db, IPasswordHasher hasher)
    {
        try
        {
            await SeedFraudRulesAsync(db);
            await SeedAdminAsync(configuration, db, hasher);
        }
        catch (Exception exception)
        {
            Log.Warning(exception, "Startup seeding skipped because the database is unavailable");
        }
    }

    private static async Task SeedFraudRulesAsync(FinCoreDbContext db)
    {
        var existing = await db.FraudRules.Select(rule => rule.Code).ToListAsync();
        if (existing.Count > 0) return;
        foreach (var (code, description, thresholdMinor, weight) in DefaultFraudRules)
        {
            db.FraudRules.Add(new FraudRuleRecord
            {
                Code = code,
                Description = description,
                ThresholdMinor = thresholdMinor,
                Weight = weight,
                Enabled = true
            });
        }
        await db.SaveChangesAsync();
    }

    private static async Task SeedAdminAsync(IConfiguration configuration, FinCoreDbContext db, IPasswordHasher hasher)
    {
        var adminEmail = configuration["ADMIN_EMAIL"];
        var adminPassword = configuration["ADMIN_PASSWORD"];
        if (string.IsNullOrWhiteSpace(adminEmail) || string.IsNullOrWhiteSpace(adminPassword)) return;

        var normalized = adminEmail.Trim().ToLowerInvariant();
        if (await db.Customers.AnyAsync(customer => customer.EmailNormalized == normalized)) return;

        db.Customers.Add(new CustomerRecord
        {
            EmailNormalized = normalized,
            PasswordHash = hasher.Hash(normalized, adminPassword),
            Status = "active",
            Roles = "[\"admin\",\"fraud_analyst\",\"finance_user\"]"
        });
        await db.SaveChangesAsync();
        Log.Information("Seeded admin customer {Email}", normalized);
    }
}
