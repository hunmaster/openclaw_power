FROM python:3.11-slim

# 시스템 의존성 (Playwright 브라우저용)
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget curl gnupg ca-certificates fonts-noto-cjk \
    libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    libxshmfence1 libx11-xcb1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright 브라우저 설치
RUN playwright install chromium
RUN playwright install-deps chromium

# 소스 코드 복사
COPY . .

# config 디렉토리 생성
RUN mkdir -p /app/config

# 기본 환경변수 (고객 모드)
ENV LICENSE_MODE=client
ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1

# 포트 노출
EXPOSE 5000

# 실행
CMD ["python", "app.py"]
