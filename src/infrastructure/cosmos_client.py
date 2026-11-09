"""Azure Cosmos DB async wrapper for LinguaProctor.

Provides CRUD and query operations with lazy container initialisation
and proper async resource management.
"""

from __future__ import annotations

import logging
from typing import Any

from azure.cosmos.aio import ContainerProxy, CosmosClient, DatabaseProxy
from azure.cosmos.exceptions import CosmosResourceNotFoundError

from src.config import get_settings

logger = logging.getLogger(__name__)


class CosmosDBError(Exception):
    """Raised when a Cosmos DB operation fails."""


class CosmosDBClient:
    """Async wrapper around the Azure Cosmos DB Python SDK.

    Containers are resolved lazily and cached for the lifetime of the
    client instance.

    Usage::

        async with CosmosDBClient() as db:
            item = await db.create_item("exams", {"id": "e1", "title": "Demo"})
    """

    def __init__(self) -> None:
        self._client: CosmosClient | None = None
        self._database: DatabaseProxy | None = None
        self._containers: dict[str, ContainerProxy] = {}

    # ── Lazy init ──────────────────────────────────────────────────────

    def _get_client(self) -> CosmosClient:
        """Return or create the async :class:`CosmosClient`."""
        if self._client is None:
            settings = get_settings().cosmos
            self._client = CosmosClient(
                url=settings.endpoint,
                credential=settings.key.get_secret_value(),
            )
            logger.info("Initialized Cosmos DB client — endpoint=%s", settings.endpoint)
        return self._client

    def _get_database(self) -> DatabaseProxy:
        """Return or create the :class:`DatabaseProxy`."""
        if self._database is None:
            settings = get_settings().cosmos
            self._database = self._get_client().get_database_client(settings.database_name)
            logger.debug("Using Cosmos database '%s'", settings.database_name)
        return self._database

    def _get_container(self, container: str) -> ContainerProxy:
        """Return a cached :class:`ContainerProxy` for *container*.

        Args:
            container: The Cosmos container name.

        Returns:
            A :class:`ContainerProxy` for the specified container.
        """
        if container not in self._containers:
            self._containers[container] = self._get_database().get_container_client(container)
            logger.debug("Resolved Cosmos container '%s'", container)
        return self._containers[container]

    # ── CRUD operations ────────────────────────────────────────────────

    async def create_item(self, container: str, item: dict[str, Any]) -> dict[str, Any]:
        """Create a new item in the specified container.

        Args:
            container: Target container name.
            item: The document to create.  Must include ``"id"``.

        Returns:
            The created document (with Cosmos system properties).

        Raises:
            CosmosDBError: On creation failure (e.g. duplicate id).
        """
        try:
            proxy = self._get_container(container)
            result = await proxy.create_item(body=item)
            logger.info("Created item — container=%s id=%s", container, item.get("id"))
            return result
        except Exception as exc:
            raise CosmosDBError(
                f"Failed to create item in '{container}': {exc}"
            ) from exc

    async def read_item(
        self,
        container: str,
        item_id: str,
        partition_key: str,
    ) -> dict[str, Any] | None:
        """Read a single item by id and partition key.

        Args:
            container: Source container name.
            item_id: The document ``id``.
            partition_key: The partition key value.

        Returns:
            The document dict, or ``None`` if not found.

        Raises:
            CosmosDBError: On read failure (other than 404).
        """
        try:
            proxy = self._get_container(container)
            result = await proxy.read_item(item=item_id, partition_key=partition_key)
            return result
        except CosmosResourceNotFoundError:
            logger.debug("Item not found — container=%s id=%s", container, item_id)
            return None
        except Exception as exc:
            raise CosmosDBError(
                f"Failed to read item '{item_id}' from '{container}': {exc}"
            ) from exc

    async def query_items(
        self,
        container: str,
        query: str,
        parameters: list[dict[str, Any]] | None = None,
        *,
        partition_key: str | None = None,
        max_item_count: int = 100,
    ) -> list[dict[str, Any]]:
        """Execute a SQL query against a container.

        Args:
            container: Container to query.
            query: Cosmos SQL query string (e.g.
                ``"SELECT * FROM c WHERE c.status = @status"``).
            parameters: Optional list of parameter dicts, e.g.
                ``[{"name": "@status", "value": "active"}]``.
            partition_key: Optional partition key to scope the query.
            max_item_count: Maximum number of items per page.

        Returns:
            A list of matching documents.

        Raises:
            CosmosDBError: On query failure.
        """
        try:
            proxy = self._get_container(container)
            kwargs: dict[str, Any] = {
                "query": query,
                "max_item_count": max_item_count,
            }
            if parameters:
                kwargs["parameters"] = parameters
            if partition_key is not None:
                kwargs["partition_key"] = partition_key

            items: list[dict[str, Any]] = []
            async for item in proxy.query_items(**kwargs):
                items.append(item)

            logger.debug(
                "Query returned %d items — container=%s query=%s",
                len(items),
                container,
                query[:80],
            )
            return items
        except Exception as exc:
            raise CosmosDBError(
                f"Query failed on '{container}': {exc}"
            ) from exc

    async def upsert_item(self, container: str, item: dict[str, Any]) -> dict[str, Any]:
        """Create or replace an item (upsert semantics).

        Args:
            container: Target container name.
            item: The document to upsert.  Must include ``"id"``.

        Returns:
            The upserted document.

        Raises:
            CosmosDBError: On upsert failure.
        """
        try:
            proxy = self._get_container(container)
            result = await proxy.upsert_item(body=item)
            logger.info("Upserted item — container=%s id=%s", container, item.get("id"))
            return result
        except Exception as exc:
            raise CosmosDBError(
                f"Failed to upsert item in '{container}': {exc}"
            ) from exc

    async def delete_item(
        self,
        container: str,
        item_id: str,
        partition_key: str,
    ) -> None:
        """Delete a single item by id and partition key.

        Args:
            container: Container name.
            item_id: The document ``id``.
            partition_key: The partition key value.

        Raises:
            CosmosDBError: On deletion failure.
        """
        try:
            proxy = self._get_container(container)
            await proxy.delete_item(item=item_id, partition_key=partition_key)
            logger.info("Deleted item — container=%s id=%s", container, item_id)
        except CosmosResourceNotFoundError:
            logger.warning("Item not found for deletion — container=%s id=%s", container, item_id)
        except Exception as exc:
            raise CosmosDBError(
                f"Failed to delete item '{item_id}' from '{container}': {exc}"
            ) from exc

    # ── Cleanup ────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Close the underlying Cosmos client and release connections."""
        if self._client is not None:
            await self._client.close()
            self._client = None
            self._database = None
            self._containers.clear()
            logger.info("Closed Cosmos DB client")

    async def __aenter__(self) -> CosmosDBClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
