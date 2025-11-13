"""REST client wrapper for the Upbit API."""

from __future__ import annotations

import logging
import time
import uuid
from hashlib import sha512
from typing import Any
from urllib.parse import urlencode

import requests

from .auth import generate_jwt

LOGGER = logging.getLogger(__name__)


class UpbitAPIError(RuntimeError):
    """Base exception for Upbit API failures."""


class UpbitClient:
    """Lightweight Upbit REST API wrapper."""

    REST_ENDPOINT = "https://api.upbit.com/v1"

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        session: requests.Session | None = None,
        timeout: int = 10,
    ) -> None:
        self.access_key = access_key
        self.secret_key = secret_key
        self.session = session or requests.Session()
        self.timeout = timeout

    def _headers(self, extra_payload: dict[str, Any] | None = None) -> dict[str, str]:
        token = generate_jwt(self.access_key, self.secret_key, payload=extra_payload)
        return {"Authorization": f"Bearer {token}"}

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.REST_ENDPOINT}{path}"
        headers = self._headers()
        response = self.session.request(
            method,
            url,
            headers=headers,
            params=params,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise UpbitAPIError(f"{response.status_code} {response.text}")
        return response.json()

    def get_accounts(self) -> Any:
        return self._request("GET", "/accounts")

    def get_server_time(self) -> Any:
        # Upbit does not expose dedicated server time; simulate via trade-ticks
        trades = self._request("GET", "/trades/ticks", params={"market": "KRW-BTC", "count": 1})
        return trades[0]["timestamp"] if trades else int(time.time() * 1000)

    def get_candles(self, market: str, unit: int = 1, count: int = 200) -> Any:
        return self._request(
            "GET",
            f"/candles/minutes/{unit}",
            params={"market": market, "count": count},
        )

    def get_orderbook(self, market: str) -> Any:
        return self._request("GET", "/orderbook", params={"markets": market})

    def get_ticker(self, market: str) -> Any:
        data = self._request("GET", "/ticker", params={"markets": market})
        return data[0] if data else None
    
    def get_all_markets(self) -> Any:
        """모든 마켓 정보 조회 (인증 불필요)"""
        # Public API 사용
        url = f"{self.REST_ENDPOINT}/market/all"
        response = self.session.request("GET", url, timeout=self.timeout)
        if response.status_code >= 400:
            raise UpbitAPIError(f"{response.status_code} {response.text}")
        return response.json()
    
    def get_krw_markets(self) -> list[str]:
        """KRW 마켓 목록 가져오기"""
        try:
            markets = self.get_all_markets()
            krw_markets = [
                market["market"] 
                for market in markets 
                if market["market"].startswith("KRW-")
            ]
            # 거래 불가능한 코인 제외
            excluded = ["KRW-LUNC", "KRW-APENFT", "KRW-LUNA2", "KRW-DOGE", "KRW-SHIB"]
            return [m for m in krw_markets if m not in excluded]
        except Exception as e:
            LOGGER.error(f"Failed to get KRW markets: {e}")
            # 기본 코인 목록 반환
            return [
                "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-ADA", "KRW-DOT",
                "KRW-LINK", "KRW-LTC", "KRW-BCH", "KRW-EOS", "KRW-TRX"
            ]

    def place_order(
        self,
        market: str,
        side: str,
        volume: str | None = None,
        price: str | None = None,
        ord_type: str = "limit",
        identifier: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {
            "market": market,
            "side": side,
            "ord_type": ord_type,
        }
        if volume:
            params["volume"] = volume
        if price:
            params["price"] = price
        if identifier is None:
            identifier = str(uuid.uuid4())
        params["identifier"] = identifier

        query_string = urlencode(params)
        query_hash = sha512(query_string.encode()).hexdigest()

        headers = self._headers(
            extra_payload={"query_hash": query_hash, "query_hash_alg": "SHA512"},
        )
        response = self.session.request(
            "POST",
            f"{self.REST_ENDPOINT}/orders",
            headers=headers,
            params=params,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise UpbitAPIError(f"{response.status_code} {response.text}")
        return response.json()
