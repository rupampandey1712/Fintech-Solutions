"""KYC document storage: private Azure Blob Storage, or the local filesystem for tests."""

import base64
from pathlib import Path
from uuid import uuid4

from app.config.settings import get_settings

MAX_DOCUMENT_BYTES = 5 * 1024 * 1024


class DocumentTooLargeError(ValueError):
    pass


def _decode(content_base64: str) -> bytes:
    try:
        content = base64.b64decode(content_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid base64 document content") from exc
    if len(content) > MAX_DOCUMENT_BYTES:
        raise DocumentTooLargeError("document_too_large")
    return content


async def _save_to_blob(container: str, name: str, content: bytes) -> None:
    from azure.core.exceptions import ResourceExistsError
    from azure.storage.blob import ContentSettings
    from azure.storage.blob.aio import BlobServiceClient

    settings = get_settings()
    async with BlobServiceClient.from_connection_string(
        settings.azure_storage_connection_string.get_secret_value(),
    ) as service:
        container_client = service.get_container_client(container)
        try:
            # Containers are created private (no public access level).
            await container_client.create_container()
        except ResourceExistsError:
            pass
        await container_client.upload_blob(
            name, content, overwrite=False,
            content_settings=ContentSettings(content_type="application/octet-stream"),
        )


async def save_document(content_base64: str) -> str:
    """Persist decoded base64 content and return the blob path ("<container>/<name>")."""
    content = _decode(content_base64)
    settings = get_settings()
    name = uuid4().hex
    if settings.storage_provider == "blob":
        await _save_to_blob(settings.kyc_blob_container, name, content)
        return f"{settings.kyc_blob_container}/{name}"
    storage = Path(settings.kyc_storage_path)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / name).write_bytes(content)
    return f"kyc-documents/{name}"
