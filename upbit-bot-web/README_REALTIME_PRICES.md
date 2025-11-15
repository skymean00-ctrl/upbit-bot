# 실시간 보유 코인 시세 페이지

## 개요

이 페이지는 Upbit 자동 매매 봇에서 보유 중인 코인들의 실시간 시세를 1초마다 자동으로 업데이트하여 표시합니다.

## 기능

- ✅ 서버 API에서 보유 코인 목록 자동 조회
- ✅ 업비트 공개 API로 실시간 시세 정보 가져오기
- ✅ 1초마다 자동 업데이트 (페이지 새로고침 없이)
- ✅ 등락률에 따른 색상 구분 (상승: 빨강, 하락: 파랑)
- ✅ 반응형 디자인 (모바일 대응)
- ✅ 로딩 상태 및 에러 처리
- ✅ 마지막 업데이트 시간 표시

## 접속 방법

웹 서버가 실행 중인 상태에서 다음 URL로 접속:

```
http://서버주소:8080/real-time-prices
```

예시:
- 로컬: `http://localhost:8080/real-time-prices`
- 원격: `http://100.98.189.30:8080/real-time-prices`

## API 엔드포인트

### 1. 보유 코인 목록 조회
- **URL**: `/api/holdings`
- **Method**: `GET`
- **응답 형식**:
  ```json
  {
    "coins": ["BTC", "ETH", "XRP"]
  }
  ```

### 2. 실시간 시세 페이지
- **URL**: `/real-time-prices`
- **Method**: `GET`
- **응답**: HTML 페이지

## 표시 정보

테이블에 다음 정보가 표시됩니다:

1. **코인명**: 한글 이름 (예: 비트코인)
2. **심볼**: 코인 심볼 (예: BTC)
3. **현재가**: 원화 가격 (쉼표 포함)
4. **등락률**: 전일 대비 등락률 (%)
5. **거래대금 (24h)**: 24시간 거래대금 (억원 단위)
6. **고가 (24h)**: 24시간 최고가
7. **저가 (24h)**: 24시간 최저가

## 기술 사양

- **프레임워크**: 순수 JavaScript (Vanilla JS)
- **API 통신**: Fetch API (async/await)
- **업데이트 주기**: 1초 (1000ms)
- **외부 API**: 
  - 업비트 공개 API: `https://api.upbit.com/v1/ticker`

## 파일 구조

```
projects/upbit-bot/
├── upbit_bot/
│   └── web/
│       └── app.py                    # [수정됨] API 엔드포인트 추가
└── upbit-bot-web/
    └── real_time_prices.html         # [새로 추가됨] 실시간 시세 페이지
```

## 주요 코드 설명

### 1. 보유 코인 목록 가져오기
```javascript
async function getHoldingCoins() {
    const response = await fetch('/api/holdings');
    const data = await response.json();
    return data.coins || [];
}
```

### 2. 업비트 시세 조회
```javascript
async function fetchPrices(coins) {
    const markets = coins.map(coin => 'KRW-' + coin).join(',');
    const response = await fetch(`https://api.upbit.com/v1/ticker?markets=${markets}`);
    return await response.json();
}
```

### 3. 1초마다 자동 업데이트
```javascript
setInterval(updateAll, 1000);
```

## 서버 API 경로 수정 방법

만약 서버 API 경로가 다르다면 `real_time_prices.html` 파일 내의 다음 부분을 수정하세요:

```javascript
// [서버 API 경로 수정 위치]
const HOLDINGS_API = '/api/holdings';  // 여기를 수정하세요
```

## 에러 처리

다음 상황에서 에러 메시지가 표시됩니다:

1. 서버 API 호출 실패
2. 업비트 API 호출 실패
3. 네트워크 오류
4. 데이터 형식 오류

## 성능 최적화

- 중복 업데이트 방지 (이전 업데이트가 완료되기 전에는 새로운 업데이트를 시작하지 않음)
- 에러 발생 시에도 기존 데이터 유지
- 모바일 환경에서는 일부 컬럼 자동 숨김 처리

## 주의사항

1. 업비트 공개 API는 초당 요청 제한이 있습니다 (최대 10회/초)
2. 보유 코인이 많을 경우 업데이트 시간이 다소 걸릴 수 있습니다
3. 네트워크 상태에 따라 업데이트가 지연될 수 있습니다

## 문제 해결

### 페이지가 로드되지 않는 경우
1. 서버가 실행 중인지 확인
2. `/api/holdings` 엔드포인트가 정상 동작하는지 확인
3. 브라우저 콘솔에서 에러 메시지 확인

### 시세가 업데이트되지 않는 경우
1. 브라우저 콘솔에서 API 호출 로그 확인
2. 네트워크 탭에서 API 응답 확인
3. 업비트 API 상태 확인

## 변경 이력

- 2024-12-XX: 초기 버전 생성
  - 실시간 시세 조회 기능 추가
  - 자동 업데이트 기능 구현
  - 반응형 디자인 적용
