"""Azure Blob Storage wrapper for LinguaProctor.

Provides async operations for uploading, downloading, listing, and
deleting blobs, as well as SAS URL generation for time-limited access.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from azure.storage.blob import BlobSasPermissions, generate_blob_sas
from azure.storage.blob.aio import BlobServiceClient, ContainerClient

from src.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BlobInfo:
    """Lightweight metadata for a blob in a container."""

    name: str
    size_bytes: int
    content_type: str | None
    last_modified: datetime | None
    etag: str | None


class BlobStorageError(Exception):
    """Raised when a Blob Storage operation fails."""


class BlobStorageClient:
    """Async wrapper around Azure Blob Storage.

    The :class:`BlobServiceClient` is initialised lazily on first call.

    Usage::

        async with BlobStorageClient() as blobs:
            url = await blobs.upload_blob("exams", "q1.wav", audio_bytes, "audio/wav")
    """

    def __init__(self) -> None:
        self._service_client: BlobServiceClient | None = None

    # ── Lazy init ──────────────────────────────────────────────────────

    def _get_service_client(self) -> BlobServiceClient:
        """Return or create the async :class:`BlobServiceClient`."""
        if self._service_client is None:
            settings = get_settings().blob_storage
            self._service_client = BlobServiceClient.from_connection_string(
                settings.connection_string.get_secret_value(),
            )
            logger.info(
                "Initialized Blob Storage client — account=%s",
                settings.account_name,
            )
        return self._service_client

    def _get_container_client(self, container: str) -> ContainerClient:
        """Return a :class:`ContainerClient` for the given container name."""
        return self._get_service_client().get_container_client(container)

    # ── Upload ─────────────────────────────────────────────────────────

    async def upload_blob(
        self,
        container: str,
        blob_name: str,
        data: bytes | str,
        content_type: str = "application/octet-stream",
        *,
        overwrite: bool = True,
    ) -> str:
        """Upload data to a blob and return its URL.

        Args:
            container: Target container name.
            blob_name: The blob path/name inside the container.
            data: Raw bytes or string content.
            content_type: MIME type for the blob.
            overwrite: Whether to overwrite an existing blob.

        Returns:
            The full URL to the uploaded blob.

        Raises:
            BlobStorageError: On upload failure.
        """
        try:
            container_client = self._get_container_client(container)
            blob_client = container_client.get_blob_client(blob_name)
            await blob_client.upload_blob(
                data,
                overwrite=overwrite,
                content_settings=_content_settings(content_type),
            )
            url = blob_client.url
            logger.info("Uploaded blob — container=%s name=%s size=%d", container, blob_name, len(data) if isinstance(data, (bytes, str)) else 0)
            return url
        except Exception as exc:
            raise BlobStorageError(
                f"Failed to upload blob '{blob_name}' to container '{container}': {exc}"
            ) from exc

    # ── Download ───────────────────────────────────────────────────────

    async def download_blob(self, container: str, blob_name: str) -> bytes:
        """Download a blob's contents as bytes.

        Args:
            container: Source container name.
            blob_name: The blob path/name.

        Returns:
            The raw bytes of the blob.

        Raises:
            BlobStorageError: On download failure.
        """
        try:
            blob_client = self._get_container_client(container).get_blob_client(blob_name)
            stream = await blob_client.download_blob()
            data = await stream.readall()
            logger.debug("Downloaded blob — container=%s name=%s size=%d", container, blob_name, len(data))
            return data
        except Exception as exc:
            raise BlobStorageError(
                f"Failed to download blob '{blob_name}' from container '{container}': {exc}"
            ) from exc

    # ── List ───────────────────────────────────────────────────────────

    async def list_blobs(
        self,
        container: str,
        prefix: str | None = None,
    ) -> list[BlobInfo]:
        """List blobs in a container, optionally filtered by prefix.

        Args:
            container: Container name.
            prefix: Optional path prefix filter.

        Returns:
            A list of :class:`BlobInfo` objects.

        Raises:
            BlobStorageError: On list failure.
        """
        try:
            container_client = self._get_container_client(container)
            results: list[BlobInfo] = []
            async for blob in container_client.list_blobs(name_starts_with=prefix):
                results.append(
                    BlobInfo(
                        name=blob.name,
                        size_bytes=blob.size or 0,
                        content_type=blob.content_settings.content_type if blob.content_settings else None,
                        last_modified=blob.last_modified,
                        etag=blob.etag,
                    )
                )
            logger.debug("Listed %d blobs — container=%s prefix=%s", len(results), container, prefix)
            return results
        except Exception as exc:
            raise BlobStorageError(
                f"Failed to list blobs in container '{container}': {exc}"
            ) from exc

    # ── Delete ─────────────────────────────────────────────────────────

    async def delete_blob(self, container: str, blob_name: str) -> None:
        """Delete a single blob.

        Args:
            container: Container name.
            blob_name: The blob path/name to delete.

        Raises:
            BlobStorageError: On deletion failure.
        """
        try:
            blob_client = self._get_container_client(container).get_blob_client(blob_name)
            await blob_client.delete_blob()
            logger.info("Deleted blob — container=%s name=%s", container, blob_name)
        except Exception as exc:
            raise BlobStorageError(
                f"Failed to delete blob '{blob_name}' from container '{container}': {exc}"
            ) from exc

    # ── SAS URL ────────────────────────────────────────────────────────

    def generate_sas_url(
        self,
        container: str,
        blob_name: str,
        expiry_hours: int = 1,
    ) -> str:
        """Generate a time-limited SAS URL for a blob.

        This is a synchronous method because it only performs local
        cryptographic signing—no network call is made.

        Args:
            container: Container name.
            blob_name: The blob path/name.
            expiry_hours: Number of hours until the SAS token expires.

        Returns:
            A full URL including the SAS query string.
        """
        settings = get_settings().blob_storage
        sas_token = generate_blob_sas(
            account_name=settings.account_name,
            container_name=container,
            blob_name=blob_name,
            account_key=settings.account_key.get_secret_value(),
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(UTC) + timedelta(hours=expiry_hours),
        )
        url = (
            f"https://{settings.account_name}.blob.core.windows.net/"
            f"{container}/{blob_name}?{sas_token}"
        )
        logger.debug("Generated SAS URL — container=%s name=%s expiry=%dh", container, blob_name, expiry_hours)
        return url

    # ── Cleanup ────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        if self._service_client is not None:
            await self._service_client.close()
            self._service_client = None
            logger.info("Closed Blob Storage client")

    async def __aenter__(self) -> BlobStorageClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()


# ── Helpers ────────────────────────────────────────────────────────────


def _content_settings(content_type: str):
    """Build a :class:`ContentSettings` from a MIME type string."""
    from azure.storage.blob import ContentSettings

    return ContentSettings(content_type=content_type)
