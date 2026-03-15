"""
라이선스 클라이언트 - 프로그램에서 라이선스 서버와 통신
프로그램 시작 시 검증, 작업 수행 시 토큰 소모

LICENSE_MODE 환경변수:
  - "owner": 개발자 모드 (모든 기능 무제한, 라이선스 검증 스킵)
  - "client": 고객 모드 (기본값, 라이선스 검증 필수)
"""

import os
import platform
import hashlib
import json
import threading
import time
import requests

# 토큰 소모 기준
TOKEN_COSTS = {
    "comment_post": 10,      # 댓글 작성
    "comment_repost": 10,    # 리포스팅
    "exposure_check": 2,     # 노출 확인
    "rank_check": 5,         # 순위 체크
    "duplicate_scan": 3,     # 중복 스캔
    "notion_sync": 1,        # 노션 동기화
}

# 좋아요 대행 가격 (원/개) - 3개 품질 티어
# SMM King 원가: 약 0.59원/개 → 전부 마진
LIKE_TIERS = {
    "basic": {
        "name": "베이직",
        "description": "일반 좋아요 (속도 느림, 이탈률 있음)",
        "price_per_unit": 10,   # 원/개
        "min_quantity": 10,
        "smm_service_env": "SMM_LIKE_SERVICE_ID_BASIC",
        "badge_color": "#888",
    },
    "standard": {
        "name": "스탠다드",
        "description": "고품질 좋아요 (빠른 처리, 안정적)",
        "price_per_unit": 15,   # 원/개
        "min_quantity": 10,
        "smm_service_env": "SMM_LIKE_SERVICE_ID_STANDARD",
        "badge_color": "#3b82f6",
        "recommended": True,
    },
    "premium": {
        "name": "프리미엄",
        "description": "최고품질 좋아요 (실제 활성 계정, 이탈 없음)",
        "price_per_unit": 20,   # 원/개
        "min_quantity": 10,
        "smm_service_env": "SMM_LIKE_SERVICE_ID_PREMIUM",
        "badge_color": "#f59e0b",
    },
}

# 플랜별 기능 잠금
PLAN_FEATURES = {
    "starter": {
        "comment_post": True,
        "exposure_check_manual": True,
        "notion_sync": True,
        "auto_repost": False,          # Business부터
        "rank_check": False,           # Business부터
        "duplicate_scan": False,       # Business부터
        "auto_exposure_schedule": False,  # Agency부터
        "multi_account_parallel": False,  # Agency부터
        "task_scheduling": False,      # Agency부터
        "like_boost": False,           # Business부터
        "tracking_unlimited": False,   # Agency부터 (Starter/Business는 월 3회 무료)
        "api_access": False,           # Enterprise만
    },
    "business": {
        "comment_post": True,
        "exposure_check_manual": True,
        "notion_sync": True,
        "auto_repost": True,
        "rank_check": True,
        "duplicate_scan": True,
        "auto_exposure_schedule": False,
        "multi_account_parallel": True,   # 2계정
        "task_scheduling": False,
        "like_boost": True,
        "tracking_unlimited": False,   # Business도 월 3회 무료
        "api_access": False,
    },
    "agency": {
        "comment_post": True,
        "exposure_check_manual": True,
        "notion_sync": True,
        "auto_repost": True,
        "rank_check": True,
        "duplicate_scan": True,
        "auto_exposure_schedule": True,
        "multi_account_parallel": True,   # 5계정
        "task_scheduling": True,
        "like_boost": True,
        "tracking_unlimited": True,    # Agency부터 무제한
        "api_access": False,
    },
    "enterprise": {
        "comment_post": True,
        "exposure_check_manual": True,
        "notion_sync": True,
        "auto_repost": True,
        "rank_check": True,
        "duplicate_scan": True,
        "auto_exposure_schedule": True,
        "multi_account_parallel": True,
        "task_scheduling": True,
        "like_boost": True,
        "tracking_unlimited": True,    # 무제한
        "api_access": True,
    },
}

# 트래킹 무료 횟수 제한 (Starter/Business)
TRACKING_FREE_LIMIT = 3

LICENSE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", ".license")


def is_owner_mode():
    """개발자(owner) 모드인지 확인"""
    return os.environ.get("LICENSE_MODE", "client").lower() == "owner"


class LicenseClient:
    def __init__(self, server_url=None):
        self.server_url = server_url or os.environ.get(
            "LICENSE_SERVER_URL", "http://localhost:5100"
        )
        self.license_key = None
        self.hardware_id = self._generate_hardware_id()
        self.license_info = None
        self.token_balance = 0
        self._heartbeat_thread = None
        self._running = False
        self.owner_mode = is_owner_mode()

        if self.owner_mode:
            # Owner 모드: 무제한 설정
            self.license_info = {
                "plan": "Owner",
                "plan_name": "owner",
                "is_permanent": True,
                "max_accounts": 9999,
                "max_devices": 9999,
            }
            self.token_balance = 999999999

    @staticmethod
    def _generate_hardware_id():
        """이 PC의 고유 하드웨어 ID 생성"""
        try:
            info_parts = [
                platform.node(),
                platform.machine(),
                platform.system(),
            ]
            # MAC 주소 추가 (가능한 경우)
            try:
                import uuid as _uuid
                mac = _uuid.getnode()
                info_parts.append(str(mac))
            except Exception:
                pass
            raw = "-".join(info_parts)
            return hashlib.sha256(raw.encode()).hexdigest()[:32]
        except Exception:
            return hashlib.sha256(platform.node().encode()).hexdigest()[:32]

    def _save_key(self, key):
        """라이선스 키를 로컬 파일에 저장"""
        os.makedirs(os.path.dirname(LICENSE_FILE), exist_ok=True)
        with open(LICENSE_FILE, "w") as f:
            json.dump({"license_key": key}, f)

    def _load_key(self):
        """저장된 라이선스 키 로드"""
        try:
            with open(LICENSE_FILE, "r") as f:
                data = json.load(f)
                return data.get("license_key", "")
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def activate(self, license_key):
        """라이선스 키로 활성화"""
        if self.owner_mode:
            return {"valid": True, "message": "Owner 모드"}
        self.license_key = license_key.strip()
        result = self.verify()
        if result.get("valid"):
            self._save_key(self.license_key)
            self._start_heartbeat()
        return result

    def verify(self):
        """라이선스 서버에 검증 요청"""
        if self.owner_mode:
            return {"valid": True, "message": "Owner 모드"}
        if not self.license_key:
            return {"valid": False, "error": "라이선스 키가 설정되지 않았습니다."}

        try:
            resp = requests.post(
                f"{self.server_url}/api/license/verify",
                json={
                    "license_key": self.license_key,
                    "hardware_id": self.hardware_id,
                    "hostname": platform.node(),
                },
                timeout=10,
            )
            data = resp.json()

            if data.get("valid"):
                self.license_info = data.get("license", {})
                self.token_balance = data.get("tokens", {}).get("balance", 0)

            return data
        except requests.exceptions.ConnectionError:
            return {"valid": False, "error": "라이선스 서버에 연결할 수 없습니다."}
        except Exception as e:
            return {"valid": False, "error": f"검증 오류: {str(e)}"}

    def auto_verify(self):
        """저장된 키로 자동 검증 시도"""
        if self.owner_mode:
            return {"valid": True, "message": "Owner 모드"}
        saved_key = self._load_key()
        if saved_key:
            self.license_key = saved_key
            result = self.verify()
            if result.get("valid"):
                self._start_heartbeat()
            return result
        return {"valid": False, "error": "저장된 라이선스 키가 없습니다."}

    def can_use_feature(self, feature_name):
        """현재 플랜에서 해당 기능을 사용할 수 있는지 확인"""
        if self.owner_mode:
            return True
        if not self.license_info:
            return False
        plan_name = self.license_info.get("plan_name", "starter").lower()
        features = PLAN_FEATURES.get(plan_name, PLAN_FEATURES["starter"])
        return features.get(feature_name, False)

    def get_upgrade_message(self, feature_name):
        """기능 잠금 시 업그레이드 안내 메시지 반환"""
        required_plan = "Business"
        for plan in ["business", "agency", "enterprise"]:
            if PLAN_FEATURES[plan].get(feature_name):
                required_plan = plan.capitalize()
                break
        return f"이 기능은 {required_plan} 플랜부터 사용 가능합니다. 업그레이드해주세요."

    def get_like_cost(self, quantity, tier="standard"):
        """좋아요 비용 계산 (원 단위)"""
        if self.owner_mode:
            return 0
        tier_info = LIKE_TIERS.get(tier, LIKE_TIERS["standard"])
        return quantity * tier_info["price_per_unit"]

    def get_like_tiers(self):
        """사용 가능한 좋아요 티어 목록 반환"""
        return LIKE_TIERS

    def use_tokens(self, action, description=None):
        """토큰 소모. 성공 시 잔액 반환, 실패 시 None"""
        if self.owner_mode:
            return 999999999

        tokens = TOKEN_COSTS.get(action, 0)
        if tokens == 0:
            return self.token_balance  # 무료 작업

        if not self.license_key:
            return None

        # 영구 라이선스는 로컬 체크만
        if self.license_info and self.license_info.get("is_permanent"):
            return 999999999

        try:
            resp = requests.post(
                f"{self.server_url}/api/license/tokens/use",
                json={
                    "license_key": self.license_key,
                    "action": action,
                    "tokens": tokens,
                    "description": description or "",
                },
                timeout=10,
            )
            data = resp.json()

            if resp.status_code == 200 and data.get("success"):
                self.token_balance = data.get("remaining", 0)
                return self.token_balance
            else:
                return None
        except Exception:
            # 네트워크 오류 시 허용 (오프라인 허용 정책)
            return self.token_balance

    def get_balance(self):
        """토큰 잔액 조회"""
        if self.owner_mode:
            return 999999999
        if not self.license_key:
            return 0
        try:
            resp = requests.post(
                f"{self.server_url}/api/license/tokens/balance",
                json={"license_key": self.license_key},
                timeout=10,
            )
            data = resp.json()
            self.token_balance = data.get("balance", 0)
            return self.token_balance
        except Exception:
            return self.token_balance

    def is_active(self):
        """라이선스가 활성 상태인지"""
        return self.license_info is not None

    def get_plan_name(self):
        """현재 플랜 이름"""
        if self.license_info:
            return self.license_info.get("plan", "Unknown")
        return None

    def get_max_accounts(self):
        """최대 계정 수"""
        if self.license_info:
            return self.license_info.get("max_accounts", 0)
        return 0

    # ─── 하트비트 ───

    def _start_heartbeat(self):
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return
        self._running = True
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def _heartbeat_loop(self):
        while self._running:
            time.sleep(1800)  # 30분마다
            if not self._running:
                break
            try:
                resp = requests.post(
                    f"{self.server_url}/api/license/heartbeat",
                    json={
                        "license_key": self.license_key,
                        "hardware_id": self.hardware_id,
                    },
                    timeout=10,
                )
                data = resp.json()
                if not data.get("valid"):
                    self.license_info = None
                else:
                    self.token_balance = data.get("tokens_remaining", self.token_balance)
            except Exception:
                pass

    def stop(self):
        self._running = False


# 글로벌 인스턴스
license_client = LicenseClient()
