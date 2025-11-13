# Upbit Bot 웹 대시보드 디자인 업데이트

## ✅ 완료된 업데이트

### 🎨 모던 UI 디자인 적용
- **Tailwind CSS** 통합: 최신 유틸리티 우선 CSS 프레임워크 사용
- **반응형 디자인**: 모바일, 태블릿, 데스크톱 모든 화면 크기 지원
- **다크 모드 지원**: 사용자 선호도에 따라 다크 모드 전환 가능
- **카드 기반 레이아웃**: 모던한 카드 디자인으로 정보를 직관적으로 표시

### 📊 주요 개선 사항

#### 1. **헤더 섹션**
- 상태 인디케이터 (실시간 애니메이션)
- 실행 상태 표시 (RUNNING/STOPPED)
- 모드 표시 (DRY-RUN/LIVE)

#### 2. **잔액 카드**
- KRW Balance: 원화 잔액
- Crypto Value: 암호화폐 평가액
- Total Balance: 총 자산

#### 3. **상태 및 제어**
- 상태 정보 카드 (Market, Strategy, Last Signal 등)
- 제어 패널 (Start/Stop 버튼, 모드 선택)
- 실시간 업데이트 기능

#### 4. **계좌 및 주문**
- 계좌 스냅샷 테이블 (반응형)
- 최신 주문 정보
- 오류 메시지 표시

### 🚀 실시간 기능
- **자동 새로고침**: 15초마다 상태 업데이트
- **실시간 상태 업데이트**: 5초마다 API 호출로 상태 동기화
- **카운터 표시**: 다음 새로고침까지 남은 시간 표시

### 🎯 디자인 특징
- **그라디언트 및 그림자**: 카드 호버 효과
- **아이콘 사용**: SVG 아이콘으로 시각적 표현 향상
- **색상 코딩**: 상태에 따른 색상 구분 (녹색: 실행, 빨강: 중지)
- **타이포그래피**: 명확한 계층 구조와 가독성

## 📱 접속 정보

- **로컬**: http://localhost:8080
- **Tailscale**: http://100.81.173.120:8080

## 🔧 기술 스택

- **Frontend**: Tailwind CSS (CDN)
- **Backend**: FastAPI
- **JavaScript**: 실시간 업데이트 및 상태 동기화
- **반응형**: Grid Layout 및 Flexbox

## 🎨 색상 팔레트

- **Primary**: Blue (#3b82f6)
- **Success**: Green (#10b981)
- **Danger**: Red (#ef4444)
- **Warning**: Orange (#f59e0b)
- **Dark Mode**: Gray-900 배경, Gray-800 카드

## 📝 다음 단계 (선택사항)

1. **차트 추가**: 거래 내역 및 수익률 차트
2. **웹소켓**: 실시간 데이터 스트리밍
3. **알림 시스템**: 브라우저 알림 기능
4. **테마 전환**: 라이트/다크 모드 토글 버튼
5. **데이터 내보내기**: CSV/PDF 내보내기 기능

## 🐛 알려진 이슈

현재 없음

## 📚 참고 자료

- [Tailwind CSS Documentation](https://tailwindcss.com/docs)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Heroicons](https://heroicons.com/) (아이콘 참고)

