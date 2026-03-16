# CommentBoost 개발 참고 자료

## 프로젝트 개요
- **이름**: CommentBoost (댓글 부스터)
- **스택**: Flask + SQLAlchemy + PyWebView + Playwright + PyInstaller
- **구조**: 데스크탑 앱 (EXE) + 랜딩 서버 (Fly.io)
- **결제**: Lemon Squeezy 구독/크레딧
- **현재 버전**: v1.2.0

## 디렉토리 구조
```
app.py          - Flask 메인 앱 (API + 라우팅)
desktop.py      - PyWebView 래퍼 (EXE 진입점, 자동 업데이트)
build.py        - PyInstaller 빌드 스크립트
src/            - 핵심 모듈 (youtube_bot, updater, models 등)
templates/      - HTML 템플릿 (dashboard.html 등)
landing/        - 랜딩 페이지 서버 (Fly.io 배포)
config/         - 사용자 설정 (accounts, proxies, sessions)
data/           - 사용자 DB (users.db)
```

## 업데이트 시 보존 대상
절대 덮어쓰면 안 되는 파일:
- `.env`, `.env.local`, `.license`
- `config/` (계정, 프록시, 쿠키 세션)
- `data/` (users.db, 백업)
- `cert.pem`, `key.pem`
- `.db`, `.sqlite` 확장자 전체

---

# SaaS 벤치마킹 참고 자료

## 1. GitHub 오픈소스 SaaS 보일러플레이트

### flaskSaaS (★ 추천 - 동일 스택)
- **URL**: https://github.com/alectrocute/flaskSaaS
- **스택**: Flask, Flask-Login, Flask-SQLAlchemy, Stripe
- **핵심 기능**: 회원가입/로그인(이메일 인증), 관리자 패널, Stripe 구독 결제
- **참고 포인트**: 구독 빌링 흐름, 사용자 인증 패턴

### Ignite
- **URL**: https://github.com/Sumukh/Ignite
- **스택**: Flask + Stripe
- **핵심 기능**: OAuth, 팀 관리, 빌링, 이메일
- **참고 포인트**: 팀/조직 기능, OAuth 소셜 로그인

### SaaS Forge (Open Source SaaS Boilerplate)
- **URL**: https://github.com/saasforge/open-source-saas-boilerpate
- **스택**: Flask + React + PostgreSQL + Webpack
- **핵심 기능**: Blueprint 구조, 대시보드, 인증 레벨 분리
- **참고 포인트**: Blueprint 기반 모듈 분리, API 자동 임포트

### Apptension SaaS Boilerplate (⭐ 4.7k)
- **URL**: https://github.com/apptension/saas-boilerplate
- **스택**: React + Django + AWS
- **핵심 기능**: 프론트엔드, 백엔드 API, 관리자 패널, 워커
- **참고 포인트**: 확장 가능 아키텍처, CI/CD, 멀티 환경

### awesome-opensource-boilerplates (종합 목록)
- **URL**: https://github.com/EinGuterWaran/awesome-opensource-boilerplates
- **설명**: 모든 오픈소스 SaaS 보일러플레이트 큐레이션 리스트

## 2. YouTube 자동화 관련 GitHub

### y-t-bot/bot-subscribers-for-youtube
- **URL**: https://github.com/y-t-bot/bot-subscribers-for-youtube
- **특징**: Playwright/Selenium 어댑터, 프록시 로테이션, 속도 제한, REST API
- **참고 포인트**: 인간 행동 모방 (랜덤 대기, 스크롤, 체류), 동시성 제어

### BitTheByte/YouTubeShop
- **URL**: https://github.com/BitTheByte/YouTubeShop
- **특징**: YouTube 자동 좋아요/구독, Python + Selenium
- **참고 포인트**: botguard 토큰 우회 로직

### vtorres/youcheater
- **URL**: https://github.com/vtorres/youcheater
- **특징**: YouTube API 기반 자동 댓글/좋아요
- **참고 포인트**: 최신 영상 자동 탐지 → 댓글/좋아요

### yashu1wwww/Youtube-Auto-Likes-And-Subscribe
- **URL**: https://github.com/yashu1wwww/Youtube-Auto-Likes-And-Subscribe
- **특징**: undetected_chromedriver 사용, 자동 좋아요/구독
- **참고 포인트**: 탐지 회피 기법

### flaskwebgui (Flask → 데스크탑 앱)
- **URL**: https://github.com/ClimenteA/flaskwebgui
- **특징**: Flask/Django/FastAPI를 데스크탑 앱으로 변환
- **참고 포인트**: PyInstaller 배포, webview 통합

---

# 씨그로(Cigro) 벤치마킹 분석

## 서비스 개요
- **회사**: (주)씨그로
- **서비스**: 이커머스 매출/광고비 통합 관리 분석 대시보드 SaaS
- **URL**: https://www.cigro.io
- **고객**: 3,500+ 기업 (디아르망, 링티, 소소바람 등)
- **매출**: 2023년 약 7.8억원 (전년대비 1,014% 성장)
- **투자**: 시드 투자 유치 (스프링캠프, 슈미트, 탭엔젤파트너스 등)

## 핵심 기능
1. **다채널 데이터 통합**: 50여개 판매/광고 채널 자동 연동
   - 판매채널 35개+, 광고채널 15개+
   - 홈택스, 은행, 스프레드시트, 회사 DB 등 추가 연동 가능
2. **자동 데이터 수집**: 채널 연동 한 번이면 엑셀 입력 없이 그래프 자동 생성
3. **브랜드별 대시보드**: 판매/광고 데이터를 브랜드별로 분류, 대시보드 자동 구성
4. **월 목표 관리**: 이번달/지난달 실적 vs 목표 비교 차트
5. **공헌이익 산출**: 광고비/수수료/비용을 채널별/브랜드별 자동 배분
6. **커스텀 대시보드**: 기업 맞춤형 대시보드 제작 (350만~1,000만원)

## 구독 모델 구조
- **무료 체험**: 21일
- **월 구독 / 연 구독** 2가지 플랜
- **커스텀 가격**: 기업 규모에 따라 맞춤 견적
- **클라우드/데이터 바우처**: 정부 지원 75~80% 할인 가능
- **환불 정책**: 7일 이내 미사용 시 전액 환불, 연구독은 월할 정산 - 위약금 10%

## CommentBoost에 적용 가능한 인사이트

### 구독 모델 참고
- **무료 체험 기간** (21일 → CommentBoost에도 적용 가능)
- **월/연 구독 분리** (연 구독 시 할인으로 장기 고객 확보)
- **클라우드 바우처 연계** (정부 지원 사업 참여로 고객 확보)

### UX/UI 참고
- **채널 연동 한번이면 끝** → 최초 설정 간편화 중요
- **아침마다 자동 수집** → 자동화 가치 강조하는 카피
- **브랜드별 대시보드** → CommentBoost도 계정/채널별 대시보드 제공 가능
- **월 목표 관리 차트** → 댓글 목표 대비 실적 시각화

### 성장 전략 참고
- 중소형 → 대형 기업으로 타겟 확대 (씨그로 1.0 → 2.0)
- MRR 기반 성장 관리 (월간반복매출 2.7배 증가)
- 연동 채널 수 확대가 고객 확보의 핵심 동력

### 마케팅 카피 참고
- "매일 아침 엑셀 시간 낭비 → 채널 연동 한 번이면 끝"
- "데이터 기반 의사결정으로 매출 증대"
- CommentBoost 버전: "대표님 잘 시간에 댓글 부스터가 실수없이 달아줍니다. 매출은 '효율'의 차이입니다"

---

# SaaS 구독형 서비스 설계 원칙 (벤치마킹 종합)

## 구독 플랜 설계
1. **Free Trial**: 7~21일 무료 체험 (결제 정보 없이)
2. **Tiered Pricing**: 3~4단계 구독 플랜 (Starter / Pro / Business / Enterprise)
3. **Annual Discount**: 연 결제 시 20~30% 할인
4. **Usage-based**: 크레딧/토큰 기반 종량제 (좋아요 크레딧 등)

## 필수 기능 체크리스트
- [ ] 회원가입 / 로그인 (이메일 + 소셜 OAuth)
- [ ] 구독 플랜 관리 (업그레이드/다운그레이드/취소)
- [ ] 결제 통합 (Stripe / Lemon Squeezy / 토스)
- [ ] 사용량 대시보드 (남은 크레딧, 사용 내역)
- [ ] 관리자 패널 (유저 관리, 매출 모니터링)
- [ ] 자동 업데이트 시스템
- [ ] 사용자 데이터 백업/보존
- [ ] 온보딩 위자드 (최초 설정 가이드)

## 성장 지표
- **MRR** (Monthly Recurring Revenue): 월간반복매출
- **Churn Rate**: 구독 해지율
- **LTV**: 고객 생애 가치
- **CAC**: 고객 획득 비용
- **NPS**: 순추천지수
