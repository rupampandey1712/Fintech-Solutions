"""Generated-report storage: the same Blob/filesystem split as KYC documents.

Spec section 62: "Use Azure Blob Storage abstraction for generated reports." Reports are CSV
bytes written through the configured provider and addressed by the returned path, so a real
deployment keeps them out of the API process and out of the database.
"""

from pathlib import Path

from app.config.settings import get_settings

REPORTS_CONTAINER = "fincore-reports"


async def _save_to_blob(name: str, content: bytes) -> None:
    from azure.core.exceptions import ResourceExistsError
    from azure.storage.blob import ContentSettings
    from azure.storage.blob.aio import BlobServiceClient

    settings = get_settings()
    async with BlobServiceClient.from_connection_string(
        settings.azure_storage_connection_string.get_secret_value(),
    ) as service:
        container_client = service.get_container_client(REPORTS_CONTAINER)
        try:
            await container_client.create_container()
        except ResourceExistsError:
            pass
        await container_client.upload_blob(
            name, content, overwrite=False,
            content_settings=ContentSettings(content_type="text/csv"),
        )


async def save_report(name: str, content: bytes) -> str:
    """Persist report content and return its storage path ("<container>/<name>")."""
    settings = get_settings()
    if settings.storage_provider == "blob":
        await _save_to_blob(name, content)
        return f"{REPORTS_CONTAINER}/{name}"
    storage = Path(settings.report_storage_path)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / name).write_bytes(content)
    return f"reports/{name}"
