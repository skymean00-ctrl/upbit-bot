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
    
    def get_order(self, uuid: str | None = None, identifier: str | None = None) -> Any:
        """Get order information."""
        params: dict[str, Any] = {}
        if uuid:
            params["uuid"] = uuid
        if identifier:
            params["identifier"] = identifier
        
        query_string = urlencode(params)
        query_hash = sha512(query_string.encode()).hexdigest()
        
        headers = self._headers(
            extra_payload={"query_hash": query_hash, "query_hash_alg": "SHA512"},
        )
        response = self.session.request(
            "GET",
            f"{self.REST_ENDPOINT}/order",
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
    
    def get_order(self, uuid: str | None = None, identifier: str | None = None) -> Any:
        """Get order information."""
        params: dict[str, Any] = {}
        if uuid:
            params["uuid"] = uuid
        if identifier:
            params["identifier"] = identifier
        
        query_string = urlencode(params)
        query_hash = sha512(query_string.encode()).hexdigest()
        
        headers = self._headers(
            extra_payload={"query_hash": query_hash, "query_hash_alg": "SHA512"},
        )
        response = self.session.request(
            "GET",
            f"{self.REST_ENDPOINT}/order",
            headers=headers,
            params=params,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise UpbitAPIError(f"{response.status_code} {response.text}")
        return response.json()
    
    def get_orders(
        self,
        state: str = "done",
        market: str | None = None,
        page: int = 1,
        limit: int = 100,
        order_by: str = "desc",
    ) -> Any:
        """
        주문 내역 조회 (사용자가 직접 거래한 내용 포함).
        
        Args:
            state: 주문 상태 ('wait', 'watch', 'done', 'cancel')
            market: 마켓 ID (예: 'KRW-BTC', None이면 전체)
            page: 페이지 번호
            limit: 요청 개수 (기본값: 100, 최대: 100)
            order_by: 정렬 방식 ('asc' 또는 'desc')
        
        Returns:
            주문 내역 리스트
        """
        params: dict[str, Any] = {
            "state": state,
            "page": str(page),
            "limit": str(min(limit, 100)),  # 최대 100개
            "order_by": order_by,
        }
        if market:
            params["market"] = market
        
        query_string = urlencode(params)
        query_hash = sha512(query_string.encode()).hexdigest()
        
        headers = self._headers(
            extra_payload={"query_hash": query_hash, "query_hash_alg": "SHA512"},
        )
        response = self.session.request(
            "GET",
            f"{self.REST_ENDPOINT}/orders",
            headers=headers,
            params=params,
            timeout=self.timeout,
        )
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
    
    def get_top_volume_markets(self, limit: int = 10) -> list[str]:
        """거래량 상위 N개 KRW 마켓 가져오기"""
        try:
            # 모든 KRW 마켓 가져오기
            all_markets = self.get_krw_markets()
            
            # 거래량 정보를 가져오기 위해 티커 조회 (한 번에 최대 100개까지 가능)
            # 여러 번 나눠서 조회
            market_volumes: list[tuple[str, float]] = []
            
            # 티커를 여러 번 나눠서 조회 (한 번에 최대 100개)
            batch_size = 100
            for i in range(0, len(all_markets), batch_size):
                batch = all_markets[i:i + batch_size]
                try:
                    # 티커 조회 (인증 불필요)
                    url = f"{self.REST_ENDPOINT}/ticker"
                    params = {"markets": ",".join(batch)}
                    response = self.session.request("GET", url, params=params, timeout=self.timeout)
                    if response.status_code == 200:
                        tickers = response.json()
                        for ticker in tickers:
                            market = ticker.get("market")
                            # 24시간 누적 거래량 (acc_trade_volume_24h)
                            volume = float(ticker.get("acc_trade_volume_24h", 0))
                            market_volumes.append((market, volume))
                    else:
                        LOGGER.warning(f"Failed to get tickers for batch {i//batch_size + 1}: {response.status_code}")
                except Exception as e:
                    LOGGER.warning(f"Error fetching tickers for batch {i//batch_size + 1}: {e}")
                    continue
            
            # 거래량 기준으로 정렬 (내림차순)
            market_volumes.sort(key=lambda x: x[1], reverse=True)
            
            # 상위 N개만 반환
            top_markets = [market for market, volume in market_volumes[:limit]]
            
            LOGGER.info(f"Selected top {len(top_markets)} markets by volume: {top_markets}")
            return top_markets
            
        except Exception as e:
            LOGGER.error(f"Failed to get top volume markets: {e}")
            # 기본 코인 목록 반환
            return [
                "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-ADA", "KRW-DOT",
                "KRW-LINK", "KRW-LTC", "KRW-BCH", "KRW-EOS", "KRW-TRX"
            ][:limit]

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
    
    def get_order(self, uuid: str | None = None, identifier: str | None = None) -> Any:
        """Get order information."""
        params: dict[str, Any] = {}
        if uuid:
            params["uuid"] = uuid
        if identifier:
            params["identifier"] = identifier
        
        query_string = urlencode(params)
        query_hash = sha512(query_string.encode()).hexdigest()
        
        headers = self._headers(
            extra_payload={"query_hash": query_hash, "query_hash_alg": "SHA512"},
        )
        response = self.session.request(
            "GET",
            f"{self.REST_ENDPOINT}/order",
            headers=headers,
            params=params,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise UpbitAPIError(f"{response.status_code} {response.text}")
        return response.json()
