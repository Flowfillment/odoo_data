"""A minimal JSON-RPC client for the Odoo (Online 17) external API.

Odoo exposes an external API over JSON-RPC at ``{url}/jsonrpc``. Every call is a
POST with the envelope::

    {"jsonrpc": "2.0", "method": "call", "params": {...}, "id": <n>}

Authentication uses the ``common`` service; model access uses the ``object``
service's ``execute_kw`` method. On Odoo Online, an API key is used in place of
the account password.
"""

from __future__ import annotations

import itertools
from typing import Any, Iterator

import requests


class OdooError(RuntimeError):
    """Raised when Odoo returns a JSON-RPC error or authentication fails."""


class OdooClient:
    """Thin JSON-RPC wrapper around the Odoo external API."""

    def __init__(
        self,
        url: str,
        db: str,
        username: str,
        api_key: str,
        timeout: int = 120,
    ) -> None:
        self.url = url.rstrip("/")
        self.db = db
        self.username = username
        self.api_key = api_key
        self.timeout = timeout
        self.uid: int | None = None

        self._session = requests.Session()
        self._id_counter = itertools.count(1)

    # -- transport ---------------------------------------------------------

    def _jsonrpc(self, service: str, method: str, args: list[Any]) -> Any:
        """POST a single JSON-RPC call and return its ``result``.

        Raises OdooError on a JSON-RPC-level error and re-raises transport
        errors from ``requests`` (HTTP status, connection, timeout).
        """
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {"service": service, "method": method, "args": args},
            "id": next(self._id_counter),
        }

        response = self._session.post(
            f"{self.url}/jsonrpc",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()

        if "error" in data:
            error = data["error"]
            message = error.get("message", "Unknown error")
            details = error.get("data", {})
            debug = details.get("debug") or details.get("message") or ""
            raise OdooError(f"Odoo JSON-RPC error: {message}\n{debug}".strip())

        return data.get("result")

    # -- authentication ----------------------------------------------------

    def authenticate(self) -> int:
        """Log in and cache the user id. Raises OdooError on bad credentials."""
        uid = self._jsonrpc("common", "login", [self.db, self.username, self.api_key])
        if not uid:
            raise OdooError(
                "Authentication failed: Odoo returned no user id. "
                "Check ODOO_DB, ODOO_USERNAME, and ODOO_API_KEY."
            )
        self.uid = uid
        return uid

    def _ensure_authenticated(self) -> int:
        if self.uid is None:
            return self.authenticate()
        return self.uid

    # -- model access ------------------------------------------------------

    def execute_kw(
        self,
        model: str,
        method: str,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> Any:
        """Call ``model.method(*args, **kwargs)`` via ``object.execute_kw``."""
        uid = self._ensure_authenticated()
        return self._jsonrpc(
            "object",
            "execute_kw",
            [self.db, uid, self.api_key, model, method, args or [], kwargs or {}],
        )

    def search_read(
        self,
        model: str,
        domain: list[Any] | None = None,
        fields: list[str] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Search + read records of ``model`` in a single round trip."""
        kwargs: dict[str, Any] = {"offset": offset}
        if fields is not None:
            kwargs["fields"] = fields
        if limit is not None:
            kwargs["limit"] = limit
        return self.execute_kw(model, "search_read", [domain or []], kwargs)

    def search_read_all(
        self,
        model: str,
        domain: list[Any] | None = None,
        fields: list[str] | None = None,
        batch_size: int = 200,
    ) -> Iterator[dict[str, Any]]:
        """Yield every matching record, paging in batches of ``batch_size``."""
        offset = 0
        while True:
            batch = self.search_read(
                model,
                domain=domain,
                fields=fields,
                limit=batch_size,
                offset=offset,
            )
            if not batch:
                break
            yield from batch
            if len(batch) < batch_size:
                break
            offset += batch_size

    def version(self) -> dict[str, Any]:
        """Return server version info (also handy as an unauthenticated ping)."""
        return self._jsonrpc("common", "version", [])
