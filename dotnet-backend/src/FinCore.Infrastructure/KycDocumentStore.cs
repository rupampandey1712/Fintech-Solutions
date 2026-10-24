using Azure;
using Azure.Storage.Blobs;
using Azure.Storage.Blobs.Models;
using FinCore.Application;
using Microsoft.Extensions.Configuration;

namespace FinCore.Infrastructure;

/// <summary>
/// Stores KYC document bytes in private Azure Blob Storage (Azurite locally) when
/// STORAGE_PROVIDER=blob, otherwise under KYC_STORAGE_PATH for tests and Docker-free runs.
/// </summary>
public sealed class KycDocumentStore
{
    public const long MaxContentBytes = 5 * 1024 * 1024;

    private readonly IConfiguration _configuration;
    private readonly BlobContainerClient? _container;
    private volatile bool _containerReady;

    public KycDocumentStore(IConfiguration configuration)
    {
        _configuration = configuration;
        if (!string.Equals(configuration["STORAGE_PROVIDER"], "blob", StringComparison.OrdinalIgnoreCase))
            return;
        var connectionString = configuration["AZURE_STORAGE_CONNECTION_STRING"];
        if (string.IsNullOrWhiteSpace(connectionString))
            throw new InvalidOperationException("AZURE_STORAGE_CONNECTION_STRING is required when STORAGE_PROVIDER=blob.");
        _container = new BlobContainerClient(connectionString, configuration["KYC_BLOB_CONTAINER"] ?? "kyc-documents");
    }

    public async Task<string> SaveAsync(string contentBase64, CancellationToken cancellationToken)
    {
        byte[] content;
        try
        {
            content = Convert.FromBase64String(contentBase64);
        }
        catch (FormatException exception)
        {
            throw new KycException("invalid_base64", "Document content must be valid base64.", 422, exception);
        }
        if (content.Length > MaxContentBytes)
            throw new KycException("document_too_large", "Document exceeds the 5 MB limit.", 422);

        var fileName = $"{Guid.NewGuid():N}.bin";
        if (_container is not null)
        {
            try
            {
                if (!_containerReady)
                {
                    // Concurrent first uploads may both get here; CreateIfNotExists is idempotent.
                    await _container.CreateIfNotExistsAsync(PublicAccessType.None, cancellationToken: cancellationToken);
                    _containerReady = true;
                }
                await _container.GetBlobClient(fileName).UploadAsync(BinaryData.FromBytes(content), overwrite: false,
                    cancellationToken);
            }
            catch (RequestFailedException exception)
            {
                throw new KycException("storage_unavailable", "Document storage is unavailable.", 503, exception);
            }
            return $"{_container.Name}/{fileName}";
        }

        var directory = _configuration["KYC_STORAGE_PATH"] ?? "data/kyc-documents";
        Directory.CreateDirectory(directory);
        await File.WriteAllBytesAsync(Path.Combine(directory, fileName), content, cancellationToken);
        return $"kyc-documents/{fileName}";
    }
}
