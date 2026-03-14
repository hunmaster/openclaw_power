# 라이선스 서버 배포 가이드

## 추천: Oracle Cloud Free Tier (무료)

### 1. 서버 생성
1. [Oracle Cloud](https://cloud.oracle.com) 가입 (신용카드 필요, 과금 없음)
2. Compute > Instances > Create Instance
3. Shape: VM.Standard.E2.1.Micro (Always Free)
4. OS: Ubuntu 22.04
5. SSH 키 등록 후 생성

### 2. 서버 접속 및 Docker 설치
```bash
ssh ubuntu@<서버IP>

# Docker 설치
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker ubuntu
# 재접속
exit
ssh ubuntu@<서버IP>
```

### 3. 라이선스 서버 배포
```bash
# 파일 업로드 (로컬에서)
scp -r license_server/ ubuntu@<서버IP>:~/license-server/

# 서버에서 실행
ssh ubuntu@<서버IP>
cd ~/license-server

# 관리자 키 설정 (반드시 변경!)
export LICENSE_ADMIN_KEY="여기에-안전한-키-입력"

# 실행
docker compose up -d --build

# 확인
curl http://localhost:5100/api/health
```

### 4. 도메인 연결 (선택)
```bash
# Nginx 리버스 프록시 설치
sudo apt install -y nginx certbot python3-certbot-nginx

# Nginx 설정
sudo tee /etc/nginx/sites-available/license << 'NGINX'
server {
    server_name license.yourdomain.com;
    location / {
        proxy_pass http://localhost:5100;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
NGINX

sudo ln -s /etc/nginx/sites-available/license /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# SSL 인증서 (HTTPS)
sudo certbot --nginx -d license.yourdomain.com
```

### 5. 방화벽 설정
Oracle Cloud 콘솔에서:
- Networking > Virtual Cloud Networks > Security Lists
- Ingress Rule 추가: TCP 80, 443 허용

### 6. 관리자 대시보드 접속
```
https://license.yourdomain.com/admin
```
접속 시 관리자 API 키 입력 요구됨.

## 고객 프로그램에 서버 URL 반영

고객 docker-compose.yml의 LICENSE_SERVER_URL을 실제 도메인으로 변경:
```yaml
environment:
  - LICENSE_SERVER_URL=https://license.yourdomain.com
```

## 백업
```bash
# DB 백업 (주기적)
docker cp license-server:/data/license.db ./backup_$(date +%Y%m%d).db
```
