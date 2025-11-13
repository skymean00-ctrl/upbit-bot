"""Utilities for interacting with the official Upbit REST/WebSocket APIs.

The implementation only relies on the documented endpoints:
- https://docs.upbit.com/reference/rest-public-ticker
- https://docs.upbit.com/reference/websocket-ticker
- https://docs.upbit.com/reference/private-order

The module keeps the interface lightweight so it can be mocked in unit tests and
plugged into higher level services through dependency injection.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Dict, Iterable, List, MutableMapping, Optional
from uuid import uuid4
from urllib.parse import urlencode

import requests

try:  # The websockets package is not part of the stdlib so we load it lazily.
    import websockets
except ImportError:  # pragma: no cover - handled gracefully when dependency is missing.
    websockets = None  # type: ignore


LOGGER = logging.getLogger(__name__)


@dataclass
class OrderRequest:
    """Strongly typed container for trade requests."""

    market: str
    side: str  # "bid" or "ask"
    volume: Optional[str] = None
    price: Optional[str] = None
    ord_type: str = "limit"  # "limit", "price" (market buy) or "market" (market sell)

    def as_payload(self) -> Dict[str, str]:
        data: Dict[str, str] = {"market": self.market, "side": self.side, "ord_type": self.ord_type}
        if self.volume:
            data["volume"] = self.volume
        if self.price:
            data["price"] = self.price
        return data


class UpbitClient:
    """Minimal REST/WebSocket client for Upbit.

    The class purposefully stays synchronous for REST calls because the official
    API rate limits are low and HTTP requests can easily be wrapped in an executor
    when needed. WebSocket streaming, however, exposes an asyncio friendly
    generator for real-time use cases.
    """

    REST_URL = "https://api.upbit.com/v1"
    WS_URL = "wss://api.upbit.com/websocket/v1"

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.access_key = access_key
        self.secret_key = secret_key
        self._session = session or requests.Session()

    # ------------------------------------------------------------------
    # REST helpers
    # ------------------------------------------------------------------
    def fetch_ticker(self, market: str) -> Dict[str, Any]:
        response = self._request("GET", "/ticker", params={"markets": market})
        return response[0]

    def fetch_orderbook(self, market: str) -> Dict[str, Any]:
        response = self._request("GET", "/orderbook", params={"markets": market})
        return response[0]

    def fetch_candles(self, interval: str, market: str, count: int = 200) -> List[Dict[str, Any]]:
        endpoint = f"/candles/{interval}"
        params = {"market": market, "count": count}
        return self._request("GET", endpoint, params=params)

    def place_order(self, order: OrderRequest) -> Dict[str, Any]:
        payload = order.as_payload()
        return self._request("POST", "/orders", data=payload, auth=True)

    def cancel_order(self, uuid: str) -> Dict[str, Any]:
        return self._request("DELETE", "/order", params={"uuid": uuid}, auth=True)

    # ------------------------------------------------------------------
    # WebSocket helpers
    # ------------------------------------------------------------------
    async def ticker_stream(self, markets: Iterable[str]) -> AsyncGenerator[Dict[str, Any], None]:
        """Yield ticker snapshots using the official WebSocket endpoint."""

        if not websockets:
            raise RuntimeError(
                "websockets dependency is required for real-time streaming but is not installed"
            )

        subscribe_request = [
            {"ticket": f"upbit-bot-{uuid4()}"},
            {"type": "ticker", "codes": list(markets)},
        ]

        async with websockets.connect(self.WS_URL) as ws:  # type: ignore[attr-defined]
            await ws.send(json.dumps(subscribe_request))
            LOGGER.info("Subscribed to ticker stream: %s", markets)
            while True:
                raw = await ws.recv()
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                yield json.loads(raw)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        params: Optional[MutableMapping[str, Any]] = None,
        data: Optional[MutableMapping[str, Any]] = None,
        auth: bool = False,
    ) -> Any:
        url = f"{self.REST_URL}{path}"
        headers: Dict[str, str] = {"User-Agent": "upbit-bot/1.0"}
        if auth:
            headers.update(self._auth_headers(params or data))

        LOGGER.debug("HTTP %s %s params=%s data=%s", method, url, params, data)
        response = self._session.request(method, url, params=params, data=data, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()

    def _auth_headers(self, payload: Optional[MutableMapping[str, Any]]) -> Dict[str, str]:
        jwt_token = self._create_jwt(payload)
        return {"Authorization": f"Bearer {jwt_token}"}

    def _create_jwt(self, payload: Optional[MutableMapping[str, Any]]) -> str:
        body: Dict[str, Any] = {"access_key": self.access_key, "nonce": str(uuid4())}
        if payload:
            # Upbit requires a SHA512 hash of the query/body string.
            query_string = urlencode(sorted(payload.items()), doseq=True)
            query_hash = hashlib.sha512(query_string.encode("utf-8")).hexdigest()
            body.update({"query_hash": query_hash, "query_hash_alg": "SHA512"})
        return self._encode_jwt(body)

    def _encode_jwt(self, payload: Dict[str, Any]) -> str:
        header = {"alg": "HS256", "typ": "JWT"}
        segments = [self._b64(header), self._b64(payload)]
        signing_input = b".".join(segments)
        signature = hmac.new(self.secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
        segments.append(base64.urlsafe_b64encode(signature).rstrip(b"="))
        return b".".join(segments).decode("utf-8")

    @staticmethod
    def _b64(data: Dict[str, Any]) -> bytes:
        json_bytes = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return base64.urlsafe_b64encode(json_bytes).rstrip(b"=")


__all__ = ["OrderRequest", "UpbitClient"]
