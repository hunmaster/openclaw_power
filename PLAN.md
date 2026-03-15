# 구조 전환 계획: 클라우드 SaaS → 로컬 앱 + 클라우드 서비스

## 현재 문제
- 메인 앱(대시보드+자동화)이 Fly.io에 배포되어 있음
- ADB/브라우저 GUI가 필수라 클라우드에서는 제대로 동작 불가
- 모든 사용자가 하나의 서버를 공유하는 구조 → 데이터 초기화 문제 발생

## 목표 구조

```
commentboost.cloud (Fly.io)
├── 랜딩 페이지 (소개, 가격, 다운로드, 시작 가이드)
└── 라이선스 서버 (api.commentboost.cloud - 이미 배포됨)

고객 PC (각자 로컬)
└── CommentBoost 앱 (설치형)
    ├── 대시보드 (localhost:5000)
    ├── 유튜브 자동화 (Playwright + ADB)
    └── 라이선스 검증 → api.commentboost.cloud
```

## 작업 목록

### Phase 1: 랜딩 페이지 생성
- `landing/` 디렉토리에 별도 Flask 앱 생성 (또는 정적 HTML)
- 페이지 구성:
  1. **히어로 섹션**: 서비스 소개, CTA (다운로드)
  2. **기능 소개**: 핵심 기능 3~4개
  3. **가격 플랜**: Free / Starter / Business / Agency / Enterprise
  4. **다운로드 섹션**: Windows 설치 가이드 (ZIP 다운로드)
  5. **시작 가이드**: 설치 → 라이선스 등록 → Notion 연결 → 계정 추가 → 실행
  6. **FAQ**
- `landing/Dockerfile` + `landing/fly.toml` (commentboost.cloud용)

### Phase 2: 고객용 배포 패키지 구성
- `install/` 디렉토리에 Windows 설치 스크립트 생성
  - `install.bat`: Python 환경 설정 + 의존성 설치 + Playwright 설치
  - `start.bat`: 앱 실행 (python app.py → localhost:5000 자동 열림)
  - `update.bat`: 최신 버전 업데이트
- `.env.example` 정리 (고객용 최소 설정)
- 불필요한 클라우드 배포 파일 정리

### Phase 3: 메인 앱 fly.toml → 랜딩 페이지용으로 교체
- 기존 `fly.toml`(메인 앱용) 제거 또는 랜딩 페이지용으로 교체
- `Dockerfile`은 로컬 실행용으로 유지 (docker-compose로 로컬 사용 가능)

### Phase 4: 고객 여정 (User Journey) 문서화
사용자가 보게 될 과정:
1. commentboost.cloud 접속 → 서비스 소개 확인
2. 다운로드 ZIP 받기 (GitHub Release 또는 직접 다운로드)
3. 압축 해제 → `install.bat` 실행 (Python + 의존성 자동 설치)
4. `start.bat` 실행 → 브라우저에서 localhost:5000 자동 열림
5. 회원가입 → 라이선스 키 입력 → 셋업 위자드 진행
6. Notion 연결 → 유튜브 계정 추가 → 자동화 시작
