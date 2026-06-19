from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from bot.db import repo
from bot.db.models import Panel
from bot.panel.schemas import ClientInfo, ClientTraffic, InboundOption

logger = logging.getLogger(__name__)


class PanelError(Exception):
    """Raised when the 3X-UI panel returns an unsuccessful response."""


class PanelClient:
    """Async wrapper around the 3X-UI panel REST API (Bearer auth)."""

    def __init__(
        self,
        base_url: str,
        token: str,
        verify_tls: bool = True,
        timeout: float = 25.0,
        use_middle_server: bool = False,
        middle_server_url: str = "",
        middle_server_token: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.verify_tls = verify_tls
        self.use_middle_server = use_middle_server
        self.middle_server_url = middle_server_url.rstrip("/") if middle_server_url else ""
        self.middle_server_token = middle_server_token

        # Circuit breaker states
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

        headers = {
            "Accept": "application/json",
        }
        if self.use_middle_server and self.middle_server_url:
            client_base_url = self.middle_server_url
            headers["X-Relay-Token"] = self.middle_server_token
            headers["X-Relay-Target-URL"] = self.base_url
            headers["X-Relay-Target-Token"] = self.token
            headers["X-Relay-Target-Verify-TLS"] = "true" if self.verify_tls else "false"
        else:
            client_base_url = self.base_url
            headers["Authorization"] = f"Bearer {self.token}"

        self._client = httpx.AsyncClient(
            base_url=client_base_url,
            headers=headers,
            verify=verify_tls,
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "PanelClient":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.aclose()

    # ----------------------- low-level helpers -----------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
        expect_obj: bool = True,
    ) -> Any:
        import asyncio
        import time

        # Circuit breaker check
        if time.time() < self._circuit_open_until:
            remaining_cooldown = int(self._circuit_open_until - time.time())
            raise PanelError(
                f"Circuit breaker is open. Panel is degraded/unreachable. Retry in {remaining_cooldown}s."
            )

        max_retries = 3
        backoff = 0.5
        
        for attempt in range(max_retries):
            try:
                resp = await self._client.request(method, path, json=json, params=params)
            except httpx.HTTPError as exc:
                if attempt < max_retries - 1:
                    logger.warning(
                        "Network error calling %s (attempt %s/%s): %s. Retrying in %s seconds...",
                        path, attempt + 1, max_retries, exc, backoff
                    )
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                # Trip circuit breaker
                self._consecutive_failures += 1
                if self._consecutive_failures >= 5:
                    self._circuit_open_until = time.time() + 60.0
                    logger.error(
                        "Circuit breaker tripped for panel at %s. Service degraded for 60 seconds.",
                        self.base_url
                    )
                raise PanelError(f"Network error calling {path}: {exc}") from exc

            if resp.status_code >= 500:
                if attempt < max_retries - 1:
                    logger.warning(
                        "Panel server error %s on %s (attempt %s/%s). Retrying in %s seconds...",
                        resp.status_code, path, attempt + 1, max_retries, backoff
                    )
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                # Trip circuit breaker
                self._consecutive_failures += 1
                if self._consecutive_failures >= 5:
                    self._circuit_open_until = time.time() + 60.0
                    logger.error(
                        "Circuit breaker tripped for panel at %s. Service degraded for 60 seconds.",
                        self.base_url
                    )
                raise PanelError(f"Panel server error {resp.status_code} on {path}")

            try:
                data = resp.json()
            except ValueError as exc:
                raise PanelError(
                    f"Non-JSON response ({resp.status_code}) from {path}"
                ) from exc

            if not isinstance(data, dict):
                self._consecutive_failures = 0
                return data

            if data.get("success") is False:
                msg = data.get("msg") or ""
                # Check for transient database errors (like deadlocks, lock timeouts, SQLite database locked)
                is_transient = any(
                    err in msg.lower()
                    for err in ("deadlock", "database is locked", "lock wait timeout", "sqlstate 40p01")
                )
                if is_transient and attempt < max_retries - 1:
                    logger.warning(
                        "Transient DB error on %s: %s (attempt %s/%s). Retrying in %s seconds...",
                        path, msg, attempt + 1, max_retries, backoff
                    )
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                raise PanelError(msg or f"Panel rejected {path}")

            # Reset circuit breaker consecutive failures
            self._consecutive_failures = 0
            return data.get("obj") if expect_obj else data

    async def _get(self, path: str, params: Optional[dict] = None) -> Any:
        return await self._request("GET", path, params=params)

    async def _post(
        self, path: str, json: Optional[dict] = None, params: Optional[dict] = None
    ) -> Any:
        return await self._request("POST", path, json=json, params=params)

    # ----------------------------- server -----------------------------

    async def server_status(self) -> Dict[str, Any]:
        obj = await self._get("/panel/api/server/status")
        return obj or {}

    async def get_new_uuid(self) -> Optional[str]:
        return await self._get("/panel/api/server/getNewUUID")

    # ---------------------------- inbounds ----------------------------

    async def inbound_options(self) -> List[InboundOption]:
        obj = await self._get("/panel/api/inbounds/options")
        return [InboundOption.from_api(item) for item in (obj or [])]

    async def get_inbound(self, inbound_id: int) -> Dict[str, Any]:
        obj = await self._get(f"/panel/api/inbounds/get/{inbound_id}")
        return obj or {}

    # ---------------------------- clients -----------------------------

    async def add_client(
        self,
        email: str,
        inbound_ids: List[int],
        total_gb_bytes: int = 0,
        expiry_time_ms: int = 0,
        tg_id: int = 0,
        limit_ip: int = 0,
        enable: bool = True,
        extra: Optional[dict] = None,
    ) -> Any:
        client: Dict[str, Any] = {
            "email": email,
            "totalGB": total_gb_bytes,
            "expiryTime": expiry_time_ms,
            "tgId": tg_id,
            "limitIp": limit_ip,
            "enable": enable,
        }
        if extra:
            client.update(extra)
        payload = {"client": client, "inboundIds": inbound_ids}
        return await self._post("/panel/api/clients/add", json=payload)

    async def get_client(self, email: str) -> Optional[ClientInfo]:
        obj = await self._get(f"/panel/api/clients/get/{email}")
        if not obj:
            return None
        if isinstance(obj, list):
            obj = obj[0] if obj else None
        if not obj:
            return None
        # Panel API returns {client: {...}, inboundIds: [...]} for /get/:email.
        if isinstance(obj, dict) and isinstance(obj.get("client"), dict):
            payload = dict(obj["client"])
            if "inboundIds" not in payload and obj.get("inboundIds") is not None:
                payload["inboundIds"] = obj["inboundIds"]
            return ClientInfo.from_api(payload)
        return ClientInfo.from_api(obj)

    async def update_client(self, email: str, client_payload: dict) -> Any:
        return await self._post(
            f"/panel/api/clients/update/{email}", json=client_payload
        )

    async def delete_client(self, email: str, keep_traffic: bool = False) -> Any:
        return await self._post(
            f"/panel/api/clients/del/{email}",
            params={"keepTraffic": 1 if keep_traffic else 0},
        )

    async def client_traffic(self, email: str) -> Optional[ClientTraffic]:
        obj = await self._get(f"/panel/api/clients/traffic/{email}")
        if not obj:
            return None
        if isinstance(obj, list):
            obj = obj[0] if obj else None
        if not obj:
            return None
        return ClientTraffic.from_api(obj)

    async def client_links(self, email: str) -> List[str]:
        obj = await self._get(f"/panel/api/clients/links/{email}")
        return list(obj or [])

    async def sub_links(self, sub_id: str) -> List[str]:
        obj = await self._get(f"/panel/api/clients/subLinks/{sub_id}")
        return list(obj or [])

    async def clear_ips(self, email: str) -> Any:
        return await self._post(f"/panel/api/clients/clearIps/{email}")

    async def reset_traffic(self, email: str) -> Any:
        return await self._post(f"/panel/api/clients/resetTraffic/{email}")

    async def client_ips(self, email: str) -> Any:
        return await self._post(f"/panel/api/clients/ips/{email}")


_clients: Dict[int, PanelClient] = {}


def _client_from_panel(panel: Panel) -> PanelClient:
    return PanelClient(
        base_url=panel.base_url,
        token=panel.api_token,
        verify_tls=panel.verify_tls,
        use_middle_server=getattr(panel, "use_middle_server", False),
        middle_server_url=getattr(panel, "middle_server_url", ""),
        middle_server_token=getattr(panel, "middle_server_token", ""),
    )


async def get_panel_client(
    panel_id: int,
    *,
    require_active: bool = False,
) -> PanelClient:
    """Return a cached PanelClient for the given panel ID."""
    panel = await repo.get_panel(panel_id)
    if panel is None:
        raise PanelError(f"Panel #{panel_id} not found")
    if require_active and not panel.is_active:
        raise PanelError(f"Panel #{panel_id} ({panel.name}) is inactive")

    cached = _clients.get(panel_id)
    if cached is not None:
        return cached

    client = _client_from_panel(panel)
    _clients[panel_id] = client
    return client


def invalidate_panel_client(panel_id: int) -> None:
    """Drop a cached client so the next call rebuilds it from DB."""
    cached = _clients.pop(panel_id, None)
    if cached is not None:
        # Fire-and-forget close; caller may be sync from repo update path.
        try:
            import asyncio

            loop = asyncio.get_running_loop()
            loop.create_task(cached.aclose())
        except RuntimeError:
            pass


async def close_panel() -> None:
    global _clients
    for client in _clients.values():
        await client.aclose()
    _clients = {}
