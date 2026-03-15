"""
Lemon Squeezy 결제 연동 클라이언트

구독 플랜 결제 및 웹훅 처리를 담당합니다.
"""

import os
import hmac
import hashlib
import requests
import time

LEMONSQUEEZY_API_BASE = "https://api.lemonsqueezy.com/v1"

# 플랜 이름 → 내부 plan_id 매핑 (Lemon Squeezy 상품명 기준)
PRODUCT_NAME_TO_PLAN = {
    "starter": "starter",
    "business": "business",
    "agency": "agency",
}


class LemonSqueezyClient:
    def __init__(self):
        self.api_key = os.getenv("LEMONSQUEEZY_API_KEY", "")
        self.webhook_secret = os.getenv("LEMONSQUEEZY_WEBHOOK_SECRET", "")
        self.store_id = os.getenv("LEMONSQUEEZY_STORE_ID", "")
        # variant_id → plan 매핑 (앱 시작 시 자동 로드)
        self.variant_map = {}
        # plan → checkout_url 매핑
        self.checkout_urls = {}
        self._initialized = False

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/vnd.api+json",
            "Content-Type": "application/vnd.api+json",
        }

    def _load_direct_checkout_urls(self):
        """환경변수에서 직접 체크아웃 URL 로드 (API 생성 불필요)"""
        direct_urls = {
            "starter": os.getenv("LEMONSQUEEZY_CHECKOUT_STARTER", ""),
            "business": os.getenv("LEMONSQUEEZY_CHECKOUT_BUSINESS", ""),
            "agency": os.getenv("LEMONSQUEEZY_CHECKOUT_AGENCY", ""),
        }
        loaded = 0
        for plan_id, url in direct_urls.items():
            if url:
                self.checkout_urls[plan_id] = url
                loaded += 1
                print(f"[LemonSqueezy] 직접 체크아웃 URL 로드: {plan_id}")

        # 토큰 충전 상품 URL 로드
        token_urls = {
            "token_500": os.getenv("LEMONSQUEEZY_CHECKOUT_TOKEN_500", ""),
            "token_1200": os.getenv("LEMONSQUEEZY_CHECKOUT_TOKEN_1200", ""),
            "token_3000": os.getenv("LEMONSQUEEZY_CHECKOUT_TOKEN_3000", ""),
            "token_7000": os.getenv("LEMONSQUEEZY_CHECKOUT_TOKEN_7000", ""),
        }
        for token_id, url in token_urls.items():
            if url:
                self.checkout_urls[token_id] = url
                loaded += 1
                print(f"[LemonSqueezy] 토큰 충전 URL 로드: {token_id}")

        return loaded

    def initialize(self):
        """앱 시작 시 호출: 스토어/상품/variant 정보 자동 로드"""
        # 1. 먼저 환경변수에서 직접 체크아웃 URL 확인
        direct_count = self._load_direct_checkout_urls()
        if direct_count > 0:
            self._initialized = True
            print(f"[LemonSqueezy] 직접 URL 모드: {direct_count}개 플랜 결제 준비됨")
            # API 키가 있으면 variant_map도 로드 시도 (웹훅 처리용)
            if self.api_key:
                self._load_variants_for_webhook()
            return True

        # 2. 직접 URL이 없으면 API로 자동 로드
        if not self.api_key:
            print("[LemonSqueezy] API 키 미설정, 직접 URL도 없음 - 결제 기능 비활성화")
            return False

        try:
            # 스토어 정보 가져오기
            if not self.store_id:
                resp = requests.get(
                    f"{LEMONSQUEEZY_API_BASE}/stores",
                    headers=self._headers(),
                    timeout=10,
                )
                if resp.status_code == 200:
                    stores = resp.json().get("data", [])
                    if stores:
                        self.store_id = stores[0]["id"]
                        store_name = stores[0]["attributes"]["name"]
                        print(f"[LemonSqueezy] 스토어 감지: {store_name} (ID: {self.store_id})")
                else:
                    print(f"[LemonSqueezy] 스토어 조회 실패: HTTP {resp.status_code}")
                    return False

            # 상품 목록 가져오기
            resp = requests.get(
                f"{LEMONSQUEEZY_API_BASE}/products",
                headers=self._headers(),
                params={"filter[store_id]": self.store_id},
                timeout=10,
            )
            if resp.status_code != 200:
                print(f"[LemonSqueezy] 상품 조회 실패: HTTP {resp.status_code}")
                return False

            products = resp.json().get("data", [])
            print(f"[LemonSqueezy] 상품 {len(products)}개 발견")

            # 각 상품의 variant 가져오기
            for product in products:
                product_id = product["id"]
                product_name = product["attributes"]["name"].lower()

                plan_id = None
                for key in PRODUCT_NAME_TO_PLAN:
                    if key in product_name:
                        plan_id = PRODUCT_NAME_TO_PLAN[key]
                        break

                if not plan_id:
                    print(f"[LemonSqueezy] 알 수 없는 상품: {product['attributes']['name']}")
                    continue

                var_resp = requests.get(
                    f"{LEMONSQUEEZY_API_BASE}/variants",
                    headers=self._headers(),
                    params={"filter[product_id]": product_id},
                    timeout=10,
                )
                if var_resp.status_code == 200:
                    variants = var_resp.json().get("data", [])
                    if variants:
                        variant = variants[0]
                        variant_id = variant["id"]
                        self.variant_map[variant_id] = plan_id
                        print(f"[LemonSqueezy]   {plan_id}: variant_id={variant_id}")

            # 각 variant에 대해 체크아웃 URL 생성
            for variant_id, plan_id in self.variant_map.items():
                checkout_url = self._create_checkout(variant_id)
                if checkout_url:
                    self.checkout_urls[plan_id] = checkout_url

            self._initialized = True
            print(f"[LemonSqueezy] 초기화 완료: {len(self.checkout_urls)}개 플랜 결제 준비됨")
            return True

        except Exception as e:
            print(f"[LemonSqueezy] 초기화 오류: {e}")
            return False

    def _load_variants_for_webhook(self):
        """웹훅 처리를 위한 variant_map 로드 (선택적)"""
        try:
            if not self.store_id:
                resp = requests.get(
                    f"{LEMONSQUEEZY_API_BASE}/stores",
                    headers=self._headers(),
                    timeout=10,
                )
                if resp.status_code == 200:
                    stores = resp.json().get("data", [])
                    if stores:
                        self.store_id = stores[0]["id"]

            resp = requests.get(
                f"{LEMONSQUEEZY_API_BASE}/products",
                headers=self._headers(),
                params={"filter[store_id]": self.store_id},
                timeout=10,
            )
            if resp.status_code != 200:
                return

            for product in resp.json().get("data", []):
                product_id = product["id"]
                product_name = product["attributes"]["name"].lower()
                plan_id = None
                for key in PRODUCT_NAME_TO_PLAN:
                    if key in product_name:
                        plan_id = PRODUCT_NAME_TO_PLAN[key]
                        break
                if not plan_id:
                    continue

                var_resp = requests.get(
                    f"{LEMONSQUEEZY_API_BASE}/variants",
                    headers=self._headers(),
                    params={"filter[product_id]": product_id},
                    timeout=10,
                )
                if var_resp.status_code == 200:
                    variants = var_resp.json().get("data", [])
                    if variants:
                        self.variant_map[variants[0]["id"]] = plan_id

            print(f"[LemonSqueezy] variant_map 로드 완료: {len(self.variant_map)}개")
        except Exception as e:
            print(f"[LemonSqueezy] variant_map 로드 실패 (무시): {e}")

    def _create_checkout(self, variant_id, custom_data=None):
        """Lemon Squeezy Checkout URL 생성"""
        try:
            payload = {
                "data": {
                    "type": "checkouts",
                    "attributes": {
                        "checkout_options": {
                            "embed": True,
                            "media": False,
                            "dark": True,
                        },
                        "checkout_data": {
                            "custom": custom_data or {},
                        },
                        "product_options": {
                            "redirect_url": os.getenv("APP_URL", "http://localhost:5000") + "/?payment_result=success",
                        },
                    },
                    "relationships": {
                        "store": {
                            "data": {
                                "type": "stores",
                                "id": str(self.store_id),
                            }
                        },
                        "variant": {
                            "data": {
                                "type": "variants",
                                "id": str(variant_id),
                            }
                        },
                    },
                }
            }

            resp = requests.post(
                f"{LEMONSQUEEZY_API_BASE}/checkouts",
                headers=self._headers(),
                json=payload,
                timeout=10,
            )

            if resp.status_code in (200, 201):
                checkout_data = resp.json().get("data", {})
                url = checkout_data.get("attributes", {}).get("url", "")
                return url
            else:
                print(f"[LemonSqueezy] 체크아웃 생성 실패: HTTP {resp.status_code} - {resp.text[:200]}")
                return None

        except Exception as e:
            print(f"[LemonSqueezy] 체크아웃 생성 오류: {e}")
            return None

    def get_checkout_url(self, plan_id, user_email=None, license_key=None):
        """플랜별 체크아웃 URL 반환 (커스텀 데이터 포함)"""
        if not self._initialized:
            return None

        # 직접 URL이 있으면 쿼리파라미터로 커스텀 데이터 추가
        direct_url = self.checkout_urls.get(plan_id)

        # variant_id가 있고 API 키가 있으면 커스텀 데이터 포함 체크아웃 시도
        variant_id = None
        for vid, pid in self.variant_map.items():
            if pid == plan_id:
                variant_id = vid
                break

        if variant_id and self.api_key:
            custom_data = {}
            if user_email:
                custom_data["user_email"] = user_email
            if license_key:
                custom_data["license_key"] = license_key

            if custom_data:
                url = self._create_checkout(variant_id, custom_data)
                if url:
                    return url

        # 폴백: 직접 URL에 쿼리파라미터 추가
        if direct_url:
            params = []
            if user_email:
                params.append(f"checkout[email]={user_email}")
            if license_key:
                params.append(f"checkout[custom][license_key]={license_key}")
            if params:
                separator = "&" if "?" in direct_url else "?"
                return direct_url + separator + "&".join(params)
            return direct_url

        return None

    def verify_webhook(self, payload_body, signature):
        """웹훅 서명 검증"""
        if not self.webhook_secret:
            print("[LemonSqueezy] 웹훅 시크릿 미설정 - 서명 검증 스킵")
            return True

        digest = hmac.new(
            self.webhook_secret.encode("utf-8"),
            payload_body,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(digest, signature)

    def get_subscription(self, subscription_id):
        """구독 정보 조회"""
        try:
            resp = requests.get(
                f"{LEMONSQUEEZY_API_BASE}/subscriptions/{subscription_id}",
                headers=self._headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json().get("data", {})
            return None
        except Exception as e:
            print(f"[LemonSqueezy] 구독 조회 오류: {e}")
            return None

    def get_plan_from_variant(self, variant_id):
        """variant_id로 플랜명 반환"""
        return self.variant_map.get(str(variant_id))

    def is_available(self):
        """결제 기능 사용 가능 여부"""
        return self._initialized and bool(self.checkout_urls)

    def get_config(self):
        """프론트엔드에 전달할 설정"""
        return {
            "available": self.is_available(),
            "checkout_urls": self.checkout_urls,
        }
