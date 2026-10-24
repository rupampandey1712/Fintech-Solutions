namespace FinCore.Infrastructure;

/// <summary>
/// Generated-report storage (architecture spec, section 62): the same Blob/filesystem split as
/// KYC documents, so reports are kept out of the API process and out of the database.
/// </summary>
public static class ReportStore
{
    public const string ReportsContainer = "fincore-reports";

    public static async Task<string> SaveReportAsync(string name, byte[] content)
    {
        var provider = Environment.GetEnvironmentVariable("STORAGE_PROVIDER") ?? "filesystem";
        if (provider.Equals("blob", StringComparison.OrdinalIgnoreCase))
        {
            var connectionString = Environment.GetEnvironmentVariable("AZURE_STORAGE_CONNECTION_STRING")
                ?? throw new InvalidOperationException(
                    "AZURE_STORAGE_CONNECTION_STRING is required when STORAGE_PROVIDER=blob");
            var service = new Azure.Storage.Blobs.BlobServiceClient(connectionString);
            var container = service.GetBlobContainerClient(ReportsContainer);
            await container.CreateIfNotExistsAsync();
            var blob = container.GetBlobClient(name);
            await blob.UploadAsync(new MemoryStream(content), overwrite: true);
            return $"{ReportsContainer}/{name}";
        }
        var path = Path.Combine(
            Environment.GetEnvironmentVariable("REPORT_STORAGE_PATH") ?? "data/reports", name);
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        await File.WriteAllBytesAsync(path, content);
        return $"reports/{name}";
    }
}
