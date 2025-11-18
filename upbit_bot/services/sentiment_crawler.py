"""감정 지표 크롤러 (AI 없이 키워드/이모지 기반 분석)."""

from __future__ import annotations

import logging
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, UTC
from typing import Any

import requests

LOGGER = logging.getLogger(__name__)

# 감정 단어 사전 (AI 없이 사용)
POSITIVE_WORDS = {
    "상승", "급등", "폭등", "신고가", "골든크로스", "돌파", "강세", "호재",
    "상장", "상승세", "매수", "롱", "🚀", "📈", "💎", "🔥", "⭐", "💪",
    "good", "bullish", "pump", "moon", "lambo", "hodl", "buy", "long",
    "rally", "breakout", "support", "resistance", "bull", "green"
}

NEGATIVE_WORDS = {
    "하락", "급락", "폭락", "신저가", "데드크로스", "침체", "약세", "악재",
    "상장폐지", "하락세", "매도", "숏", "😱", "📉", "💀", "⚠️", "🚨", "💔",
    "bad", "bearish", "dump", "crash", "rug", "scam", "sell", "short",
    "fall", "breakdown", "rejection", "bear", "red"
}


class SentimentCrawler:
    """감정 지표 크롤러 (AI 없이 키워드/이모지 기반 분석)."""

    def __init__(self, timeout: int = 5, cache_ttl: int = 1800):
        """
        Args:
            timeout: 요청 타임아웃 (초, 기본값: 5초, 빠른 실패 감지)
            cache_ttl: 캐시 유지 시간 (초, 기본값: 1800초 = 30분)
        """
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        
        # 캐시 저장소 (메모리 기반)
        self._cache: dict[str, tuple[dict[str, Any], datetime]] = {}
        self._cache_lock = threading.Lock()

    def crawl_reddit_sentiment(
        self, coin_symbol: str, limit: int = 30, subreddit: str | None = None, use_cache: bool = True
    ) -> dict[str, Any]:
        """
        Reddit에서 코인 감정 지표 크롤링.
        
        Args:
            coin_symbol: 코인 심볼 (예: "BTC", "ETH")
            limit: 최대 게시물 수 (기본값: 30)
            subreddit: 서브레딧 이름 (None이면 coin_symbol 사용)
            use_cache: 캐시 사용 여부 (기본값: True)
        
        Returns:
            감정 지표 딕셔너리
        """
        # 캐시 확인
        if use_cache:
            with self._cache_lock:
                if coin_symbol in self._cache:
                    cached_result, cached_time = self._cache[coin_symbol]
                    age = (datetime.now(UTC) - cached_time).total_seconds()
                    if age < self.cache_ttl:
                        LOGGER.debug(f"Reddit 캐시 사용 ({coin_symbol}): {age:.0f}초 전 결과")
                        return cached_result
        
        subreddit_name = subreddit or coin_symbol
        try:
            # Reddit JSON API 사용 (공개, API 키 불필요)
            url = f"https://www.reddit.com/r/{subreddit_name}/hot.json"
            params = {"limit": min(limit, 100)}
            
            response = self.session.get(url, params=params, timeout=self.timeout)
            
            if response.status_code == 404:
                # 서브레딧이 없으면 검색 시도
                LOGGER.debug(f"서브레딧 r/{subreddit_name} 없음, 검색 시도")
                return self._crawl_reddit_search(coin_symbol, limit)
            
            if response.status_code != 200:
                LOGGER.warning(f"Reddit 크롤링 실패: HTTP {response.status_code}")
                return {"sentiment": 0.5, "source": "reddit", "error": f"HTTP {response.status_code}"}
            
            data = response.json()
            posts = data.get("data", {}).get("children", [])
            
            if not posts:
                LOGGER.debug(f"Reddit 게시물 없음: r/{subreddit_name}")
                return {"sentiment": 0.5, "source": "reddit", "post_count": 0}
            
            # 감정 분석
            total_sentiment = 0.0
            post_count = 0
            
            for post_data in posts:
                post = post_data.get("data", {})
                title = post.get("title", "").lower()
                selftext = post.get("selftext", "").lower()
                text = f"{title} {selftext}"
                
                # 키워드 기반 감정 점수 계산
                sentiment = self._calculate_keyword_sentiment(text)
                
                # 업보트 비율 반영
                ups = post.get("ups", 0)
                downs = max(post.get("downs", 0), 0)  # 다운보트는 항상 0 (Reddit API)
                total_votes = ups + downs
                if total_votes > 0:
                    upvote_ratio = ups / total_votes
                    # 업보트 비율이 높으면 감정 점수 상향
                    sentiment = (sentiment * 0.7) + (upvote_ratio * 0.3)
                
                # 코멘트 비율 반영 (댓글이 많으면 관심도 높음)
                num_comments = post.get("num_comments", 0)
                if num_comments > 0:
                    # 댓글 수가 많을수록 약간 상향 (최대 0.1 포인트)
                    comment_bonus = min(num_comments / 100.0, 0.1)
                    sentiment = min(sentiment + comment_bonus, 1.0)
                
                total_sentiment += sentiment
                post_count += 1
            
            avg_sentiment = total_sentiment / post_count if post_count > 0 else 0.5
            
            LOGGER.debug(
                f"Reddit 감정 분석 ({coin_symbol}): {avg_sentiment:.2f} "
                f"(게시물 {post_count}개)"
            )
            
            result = {
                "sentiment": avg_sentiment,  # 0.0 (부정) ~ 1.0 (긍정)
                "source": "reddit",
                "post_count": post_count,
                "subreddit": subreddit_name,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            
            # 캐시 저장
            if use_cache and "error" not in result:
                with self._cache_lock:
                    self._cache[coin_symbol] = (result, datetime.now(UTC))
            
            return result
            
        except requests.exceptions.Timeout:
            LOGGER.debug(f"Reddit 크롤링 타임아웃 ({coin_symbol})")
            # 타임아웃 시 기본값 반환 (캐시 저장 안함)
            return {"sentiment": 0.5, "source": "reddit", "error": "timeout"}
        except Exception as e:
            LOGGER.debug(f"Reddit 크롤링 오류 ({coin_symbol}): {e}")
            # 오류 시 기본값 반환 (캐시 저장 안함)
            return {"sentiment": 0.5, "source": "reddit", "error": str(e)[:50]}

    def _crawl_reddit_search(self, coin_symbol: str, limit: int = 30) -> dict[str, Any]:
        """
        Reddit 검색으로 코인 감정 지표 크롤링.
        
        Args:
            coin_symbol: 코인 심볼
            limit: 최대 게시물 수
        
        Returns:
            감정 지표 딕셔너리
        """
        try:
            # Reddit 검색 API 사용
            url = "https://www.reddit.com/search.json"
            params = {
                "q": coin_symbol,
                "sort": "hot",
                "limit": min(limit, 25),  # 검색은 최대 25개
                "t": "day",  # 최근 1일
            }
            
            response = self.session.get(url, params=params, timeout=self.timeout)
            
            if response.status_code != 200:
                return {"sentiment": 0.5, "source": "reddit_search", "error": f"HTTP {response.status_code}"}
            
            data = response.json()
            posts = data.get("data", {}).get("children", [])
            
            if not posts:
                return {"sentiment": 0.5, "source": "reddit_search", "post_count": 0}
            
            # 감정 분석 (서브레딧과 동일한 방식)
            total_sentiment = 0.0
            post_count = 0
            
            for post_data in posts:
                post = post_data.get("data", {})
                title = post.get("title", "").lower()
                selftext = post.get("selftext", "").lower()
                text = f"{title} {selftext}"
                
                # 코인 심볼이 포함된 경우만 분석
                if coin_symbol.lower() not in text:
                    continue
                
                sentiment = self._calculate_keyword_sentiment(text)
                
                ups = post.get("ups", 0)
                total_votes = ups
                if total_votes > 0:
                    upvote_ratio = ups / (total_votes + 10)  # 다운보트 추정
                    sentiment = (sentiment * 0.7) + (upvote_ratio * 0.3)
                
                total_sentiment += sentiment
                post_count += 1
            
            avg_sentiment = total_sentiment / post_count if post_count > 0 else 0.5
            
            return {
                "sentiment": avg_sentiment,
                "source": "reddit_search",
                "post_count": post_count,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            
        except Exception as e:
            LOGGER.warning(f"Reddit 검색 크롤링 오류 ({coin_symbol}): {e}")
            return {"sentiment": 0.5, "source": "reddit_search", "error": str(e)[:50]}

    def _calculate_keyword_sentiment(self, text: str) -> float:
        """
        키워드 기반 감정 점수 계산 (0.0 ~ 1.0).
        
        Args:
            text: 분석할 텍스트
        
        Returns:
            감정 점수 (0.0: 부정, 1.0: 긍정, 0.5: 중립)
        """
        text_lower = text.lower()
        
        # 긍정/부정 키워드 개수 계산
        positive_count = sum(1 for word in POSITIVE_WORDS if word.lower() in text_lower)
        negative_count = sum(1 for word in NEGATIVE_WORDS if word.lower() in text_lower)
        
        # 이모지 분석
        emoji_positive = len(re.findall(r'[🚀📈💎🔥⭐💪💚🟢]', text))
        emoji_negative = len(re.findall(r'[😱📉💀⚠️🚨💔🔴]', text))
        
        positive_total = positive_count + (emoji_positive * 2)  # 이모지는 가중치 2배
        negative_total = negative_count + (emoji_negative * 2)
        
        # 감정 점수 계산
        total = positive_total + negative_total
        if total == 0:
            return 0.5  # 중립
        
        sentiment = positive_total / total
        
        # 0.3 ~ 0.7 범위로 정규화 (극단적인 값 방지)
        normalized_sentiment = 0.3 + (sentiment * 0.4)
        
        return normalized_sentiment

    def crawl_multiple_coins(
        self,
        coin_symbols: list[str],
        max_workers: int = 3,  # Reddit rate limit 고려 (3개로 제한)
        limit_per_coin: int = 20,  # 게시물 수 감소 (20개로 제한)
        top_n_only: int | None = None,  # 상위 N개만 크롤링 (None이면 전체)
    ) -> dict[str, dict[str, Any]]:
        """
        여러 코인의 Reddit 감정 지표를 병렬로 크롤링.
        
        Args:
            coin_symbols: 코인 심볼 리스트
            max_workers: 최대 동시 처리 수 (기본값: 3, Reddit rate limit 고려)
            limit_per_coin: 코인당 최대 게시물 수 (기본값: 20, 속도 향상)
            top_n_only: 상위 N개만 크롤링 (None이면 전체, 예: 10)
        
        Returns:
            {coin_symbol: sentiment_data} 딕셔너리
        """
        # 상위 N개만 크롤링 (검토 시간 단축)
        if top_n_only and top_n_only < len(coin_symbols):
            coin_symbols = coin_symbols[:top_n_only]
            LOGGER.info(f"Reddit 크롤링: 상위 {top_n_only}개 코인만 크롤링 (검토 시간 단축)")
        
        results: dict[str, dict[str, Any]] = {}
        results_lock = threading.Lock()
        
        def crawl_one(coin_symbol: str) -> tuple[str, dict[str, Any]]:
            """단일 코인 크롤링 래퍼."""
            try:
                # 요청 간 짧은 딜레이 (rate limit 방지)
                time.sleep(0.5)
                result = self.crawl_reddit_sentiment(
                    coin_symbol, 
                    limit=limit_per_coin,
                    use_cache=True  # 캐시 사용으로 빠른 반환
                )
                return coin_symbol, result
            except Exception as e:
                LOGGER.debug(f"Reddit 크롤링 실패 ({coin_symbol}): {e}")
                return coin_symbol, {"sentiment": 0.5, "source": "reddit", "error": str(e)[:50]}
        
        LOGGER.info(
            f"Reddit 감정 지표 크롤링 시작: {len(coin_symbols)}개 코인 "
            f"(병렬: {max_workers}개, 타임아웃: {self.timeout}초)"
        )
        
        # 병렬 처리 실행 (빠른 타임아웃으로 전체 검토 시간 단축)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(crawl_one, coin_symbol): coin_symbol
                for coin_symbol in coin_symbols
            }
            
            completed = 0
            failed = 0
            try:
                # 전체 타임아웃 단축 (30개 * 5초 / 3 workers ≈ 50초, 최대 60초로 설정)
                total_timeout = min(60, len(coin_symbols) * self.timeout / max_workers + 10)
                
                for future in as_completed(futures, timeout=total_timeout):
                    completed += 1
                    try:
                        coin_symbol, result = future.result(timeout=1)  # 개별 결과 타임아웃 1초
                        with results_lock:
                            results[coin_symbol] = result
                        
                        if completed % 10 == 0 or completed == len(coin_symbols):
                            LOGGER.info(
                                f"Reddit 크롤링 진행: {completed}/{len(coin_symbols)} 완료 "
                                f"({len(results)}개 성공, {failed}개 실패)"
                            )
                    except Exception as e:
                        coin_symbol = futures.get(future, "unknown")
                        failed += 1
                        LOGGER.debug(f"Reddit 크롤링 처리 오류 ({coin_symbol}): {e}")
                        # 기본값 저장
                        with results_lock:
                            results[coin_symbol] = {"sentiment": 0.5, "source": "reddit", "error": "timeout"}
            except Exception as e:
                LOGGER.warning(f"Reddit 크롤링 타임아웃: {e}, 완료된 {len(results)}개 결과 반환")
                # 타임아웃된 코인들 기본값으로 채우기
                for coin_symbol in coin_symbols:
                    if coin_symbol not in results:
                        results[coin_symbol] = {"sentiment": 0.5, "source": "reddit", "error": "timeout"}
        
        LOGGER.info(
            f"Reddit 감정 지표 크롤링 완료: {len(results)}개 코인 분석됨 "
            f"(성공률: {sum(1 for r in results.values() if 'error' not in r) / len(results) * 100:.1f}%)"
        )
        
        return results

