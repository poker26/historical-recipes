"""Temporal client factory.

A single helper used by both the API (to start/query workflows) and the worker
(to connect and poll the task queue).  The shared cluster has no TLS and no
auth, so the connection is a plain ``Client.connect``.
"""

from temporalio.client import Client

from app.config import settings

_client: Client | None = None


async def get_temporal_client() -> Client:
    """Return a process-cached Temporal client connected to the shared cluster."""
    global _client
    if _client is None:
        _client = await Client.connect(
            settings.temporal_address,
            namespace=settings.temporal_namespace,
        )
    return _client
