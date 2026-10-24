using FinCore.Application;
using FinCore.Infrastructure;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace FinCore.Api.Controllers;

[Route("api/v1/transactions")]
[Authorize]
[Tags("transactions")]
public sealed class TransactionsController(FinCoreDbContext db) : FinCoreControllerBase
{
    private const string PaymentPrefix = "payment:";

    [HttpGet]
    public async Task<IActionResult> List([FromQuery] Guid? accountId, [FromQuery] int? limit,
        [FromQuery] string? cursor, CancellationToken cancellationToken)
    {
        var pageSize = Math.Clamp(limit ?? 20, 1, 200);
        DateTimeOffset? cursorAt = null;
        Guid? cursorId = null;
        if (cursor is not null)
        {
            if (!CursorCodec.TryDecode(cursor, out var at, out var id))
                return ProblemResult(422, "invalid_cursor", "Request validation failed", "Cursor is invalid.");
            cursorAt = at;
            cursorId = id;
        }

        var query = from line in db.JournalLines.AsNoTracking()
                    join entry in db.JournalEntries.AsNoTracking() on line.JournalEntryId equals entry.Id
                    join ledger in db.LedgerAccounts.AsNoTracking() on line.LedgerAccountId equals ledger.Id
                    join account in db.Accounts.AsNoTracking() on ledger.AccountId equals account.Id
                    where account.CustomerId == CustomerId && entry.BusinessReference.StartsWith(PaymentPrefix)
                    select new { Line = line, Entry = entry, Account = account };
        if (accountId is not null)
            query = query.Where(row => row.Account.Id == accountId);
        if (cursorAt is not null)
            query = query.Where(row => row.Entry.PostedAt < cursorAt
                || (row.Entry.PostedAt == cursorAt && row.Line.Id < cursorId));

        var rows = await query
            .OrderByDescending(row => row.Entry.PostedAt).ThenByDescending(row => row.Line.Id)
            .Take(pageSize + 1).ToListAsync(cancellationToken);

        string? nextCursor = null;
        if (rows.Count > pageSize)
        {
            rows = rows.Take(pageSize).ToList();
            var last = rows[^1];
            nextCursor = CursorCodec.Encode(last.Entry.PostedAt, last.Line.Id);
        }

        var items = await BuildViewsAsync(db, rows.Select(row => (row.Line, row.Entry, row.Account)).ToList(),
            cancellationToken);
        return Ok(new { items, nextCursor });
    }

    [HttpGet("{lineId:guid}")]
    [ProducesResponseType<TransactionView>(StatusCodes.Status200OK)]
    public async Task<IActionResult> Get(Guid lineId, CancellationToken cancellationToken)
    {
        var rows = await (from line in db.JournalLines.AsNoTracking()
                          join entry in db.JournalEntries.AsNoTracking() on line.JournalEntryId equals entry.Id
                          join ledger in db.LedgerAccounts.AsNoTracking() on line.LedgerAccountId equals ledger.Id
                          join account in db.Accounts.AsNoTracking() on ledger.AccountId equals account.Id
                          where account.CustomerId == CustomerId && line.Id == lineId
                                && entry.BusinessReference.StartsWith(PaymentPrefix)
                          select new { Line = line, Entry = entry, Account = account })
            .Take(1).ToListAsync(cancellationToken);
        if (rows.Count == 0)
            return ProblemResult(404, "not_found", "Resource not found", "Transaction not found");
        var items = await BuildViewsAsync(db, rows.Select(row => (row.Line, row.Entry, row.Account)).ToList(),
            cancellationToken);
        return Ok(items[0]);
    }

    private async Task<IReadOnlyList<TransactionView>> BuildViewsAsync(FinCoreDbContext context,
        List<(JournalLineRecord Line, JournalEntryRecord Entry, CustomerAccountRecord Account)> rows,
        CancellationToken cancellationToken)
    {
        if (rows.Count == 0) return [];
        var paymentIds = rows
            .Select(row => row.Entry.BusinessReference[PaymentPrefix.Length..])
            .Where(value => Guid.TryParse(value, out _))
            .Select(Guid.Parse).Distinct().ToArray();
        var payments = paymentIds.Length == 0
            ? new Dictionary<Guid, PaymentRecord>()
            : await context.Payments.AsNoTracking().Where(payment => paymentIds.Contains(payment.Id))
                .ToDictionaryAsync(payment => payment.Id, cancellationToken);

        var journalIds = rows.Select(row => row.Entry.Id).Distinct().ToArray();
        var journalLines = await (from line in context.JournalLines.AsNoTracking()
                                  join ledger in context.LedgerAccounts.AsNoTracking() on line.LedgerAccountId equals ledger.Id
                                  where journalIds.Contains(line.JournalEntryId) && ledger.AccountId != null
                                  select new { line.JournalEntryId, line.LedgerAccountId, AccountId = ledger.AccountId!.Value })
            .ToListAsync(cancellationToken);

        return rows.Select(row =>
        {
            var paymentId = Guid.Parse(row.Entry.BusinessReference[PaymentPrefix.Length..]);
            payments.TryGetValue(paymentId, out var payment);
            var counterparty = journalLines
                .Where(line => line.JournalEntryId == row.Entry.Id && line.LedgerAccountId != row.Line.LedgerAccountId)
                .Select(line => (Guid?)line.AccountId)
                .FirstOrDefault();
            return new TransactionView(row.Line.Id, paymentId, row.Account.Id, row.Line.Side, counterparty,
                row.Line.AmountMinor, row.Entry.Currency, row.Entry.PostedAt,
                payment?.Status.ToString().ToLowerInvariant() ?? "completed", payment?.Reference);
        }).ToArray();
    }
}

[Route("api/v1/ledger")]
[Authorize]
[Tags("ledger")]
public sealed class LedgerController(FinCoreDbContext db) : FinCoreControllerBase
{
    [HttpGet("accounts/{accountId:guid}")]
    [ProducesResponseType<LedgerAccountStatement>(StatusCodes.Status200OK)]
    public async Task<IActionResult> Statement(Guid accountId, CancellationToken cancellationToken)
    {
        var account = await db.Accounts.AsNoTracking().SingleOrDefaultAsync(
            item => item.Id == accountId && item.CustomerId == CustomerId, cancellationToken);
        if (account is null)
            return ProblemResult(404, "not_found", "Resource not found", "Account not found");
        var ledger = await db.LedgerAccounts.AsNoTracking().SingleOrDefaultAsync(
            item => item.AccountId == accountId, cancellationToken);
        if (ledger is null)
            return ProblemResult(409, "ledger_not_configured", "Conflict", "Account ledger is not configured.");

        var entries = await (from line in db.JournalLines.AsNoTracking()
                             join entry in db.JournalEntries.AsNoTracking() on line.JournalEntryId equals entry.Id
                             where line.LedgerAccountId == ledger.Id
                             orderby entry.PostedAt descending, line.Id descending
                             select new LedgerEntryView(entry.Id, entry.PostedAt, entry.BusinessReference,
                                 line.Side, line.AmountMinor))
            .Take(200).ToListAsync(cancellationToken);
        var balance = await db.JournalLines.AsNoTracking()
            .Where(line => line.LedgerAccountId == ledger.Id)
            .SumAsync(line => line.Side == "credit" ? line.AmountMinor : -line.AmountMinor, cancellationToken);
        return Ok(new LedgerAccountStatement(account.Id, account.Currency, balance, entries));
    }
}

[Route("api/v1/notifications")]
[Authorize]
[Tags("notifications")]
public sealed class NotificationsController(FinCoreDbContext db) : FinCoreControllerBase
{
    [HttpGet]
    public async Task<IActionResult> List([FromQuery] int? limit, CancellationToken cancellationToken)
    {
        var pageSize = Math.Clamp(limit ?? 50, 1, 200);
        var items = await db.Notifications.AsNoTracking()
            .Where(item => item.CustomerId == CustomerId)
            .OrderByDescending(item => item.CreatedAt).ThenByDescending(item => item.Id)
            .Take(pageSize)
            .Select(item => new NotificationView(item.Id, item.Channel, item.EventType, item.Subject, item.Body,
                item.Status, item.SentAt, item.CreatedAt))
            .ToListAsync(cancellationToken);
        return Ok(new { items, nextCursor = (string?)null });
    }
}
