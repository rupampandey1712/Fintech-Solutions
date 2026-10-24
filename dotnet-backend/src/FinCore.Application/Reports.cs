namespace FinCore.Application;

public sealed record DashboardTotals(int Payments, int Completed, int Failed, int Blocked);

public sealed record CategoryCount(int Open, int Total);

public sealed record MonthVolume(string Month, string Currency, int Count, long VolumeMinor);

public sealed record DashboardView(DashboardTotals Totals, double? SuccessRate, CategoryCount FraudCases,
    CategoryCount AmlAlerts, IReadOnlyList<MonthVolume> MonthlyVolume);

public sealed record DayVolume(string Day, string Currency, int Count, long VolumeMinor);

public sealed record TransactionsReport(IReadOnlyList<DayVolume> Series);
