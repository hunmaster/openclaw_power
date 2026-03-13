"""
SMM Kings API 연동 모듈
- 댓글 좋아요 자동 구매
- 주문 상태 조회
- 잔액 확인

API: https://smmkings.com/api/v2
"""

import os
import time
import requests
from dotenv import load_dotenv
from rich.console import Console

console = Console()

API_URL = "https://smmkings.com/api/v2"


class SMMClient:
    def __init__(self):
        load_dotenv(override=True)
        self.api_key = os.getenv("SMM_API_KEY", "")
        self.service_id = os.getenv("SMM_LIKE_SERVICE_ID", "")
        self.like_quantity = int(os.getenv("SMM_LIKE_QUANTITY", "10"))
        self.enabled = os.getenv("SMM_ENABLED", "false").lower() == "true"

        if self.enabled and not self.api_key:
            console.print("[red]SMM_API_KEY가 설정되지 않았습니다.[/red]")
            self.enabled = False

        if self.enabled and not self.service_id:
            console.print(
                "[yellow]SMM_LIKE_SERVICE_ID가 설정되지 않았습니다. "
                "서비스 목록을 조회하여 유튜브 댓글 좋아요 서비스 ID를 확인하세요.[/yellow]"
            )

    def _request(self, params):
        """SMM Kings API에 요청을 보냅니다."""
        params["key"] = self.api_key
        try:
            response = requests.post(API_URL, data=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            console.print(f"[red]SMM API 요청 실패: {e}[/red]")
            return None

    def get_balance(self):
        """잔액을 조회합니다."""
        result = self._request({"action": "balance"})
        if result and "balance" in result:
            balance = result["balance"]
            currency = result.get("currency", "USD")
            console.print(f"[green]SMM 잔액: ${balance} {currency}[/green]")
            return float(balance)
        return None

    def get_services(self):
        """사용 가능한 서비스 목록을 조회합니다."""
        result = self._request({"action": "services"})
        if result and isinstance(result, list):
            console.print(f"[green]총 {len(result)}개 서비스 조회됨[/green]")
            return result
        return []

    def find_youtube_like_services(self):
        """유튜브 댓글 좋아요 관련 서비스를 검색합니다."""
        services = self.get_services()
        youtube_like_services = []

        for svc in services:
            name = svc.get("name", "").lower()
            category = svc.get("category", "").lower()
            # YouTube 댓글 좋아요 관련 서비스 필터링
            if ("youtube" in name or "youtube" in category) and (
                "like" in name or "comment" in name
            ):
                youtube_like_services.append(svc)

        if youtube_like_services:
            console.print(f"\n[bold]유튜브 관련 서비스 {len(youtube_like_services)}개 발견:[/bold]")
            for svc in youtube_like_services:
                console.print(
                    f"  ID: {svc['service']} | {svc['name']} | "
                    f"${svc['rate']}/1000 | 최소: {svc['min']} ~ 최대: {svc['max']}"
                )
        else:
            console.print("[yellow]유튜브 댓글 좋아요 서비스를 찾지 못했습니다.[/yellow]")
            console.print("[yellow]전체 서비스 목록에서 직접 확인해주세요:[/yellow]")
            for svc in services[:20]:
                console.print(
                    f"  ID: {svc['service']} | {svc['name']} | "
                    f"카테고리: {svc.get('category', 'N/A')}"
                )

        return youtube_like_services

    def order_likes(self, comment_url, quantity=None):
        """
        댓글 좋아요를 주문합니다.

        Args:
            comment_url: 좋아요를 달 댓글 URL
            quantity: 좋아요 수 (None이면 기본값 사용)

        Returns:
            dict: {"order_id": int, "success": bool, "error": str}
        """
        if not self.enabled:
            return {"success": False, "error": "SMM 서비스 비활성화"}

        if not self.service_id:
            return {"success": False, "error": "SMM_LIKE_SERVICE_ID 미설정"}

        if not comment_url:
            return {"success": False, "error": "댓글 URL이 없습니다"}

        qty = quantity or self.like_quantity

        console.print(
            f"[blue]좋아요 주문: {comment_url[:60]}... | "
            f"수량: {qty}개[/blue]"
        )

        result = self._request({
            "action": "add",
            "service": self.service_id,
            "link": comment_url,
            "quantity": qty,
        })

        if result and "order" in result:
            order_id = result["order"]
            console.print(f"[green]좋아요 주문 완료! 주문 ID: {order_id}[/green]")
            return {"success": True, "order_id": order_id}
        elif result and "error" in result:
            console.print(f"[red]좋아요 주문 실패: {result['error']}[/red]")
            return {"success": False, "error": result["error"]}
        else:
            return {"success": False, "error": "알 수 없는 오류"}

    def order_mass_likes(self, comment_urls, quantity=None):
        """
        대량 주문으로 여러 댓글에 좋아요를 한 번에 주문합니다.

        Args:
            comment_urls: 좋아요를 달 댓글 URL 리스트
            quantity: 좋아요 수 (None이면 기본값 사용)

        Returns:
            dict: {"success": bool, "order_ids": list, "errors": list}
        """
        if not self.enabled:
            return {"success": False, "order_ids": [], "errors": ["SMM 서비스 비활성화"]}

        if not self.service_id:
            return {"success": False, "order_ids": [], "errors": ["SMM_LIKE_SERVICE_ID 미설정"]}

        if not comment_urls:
            return {"success": False, "order_ids": [], "errors": ["주문할 URL이 없습니다"]}

        qty = quantity or self.like_quantity

        # 대량주문 형식: "서비스ID | 링크 | 수량" (한 줄에 하나씩)
        orders_text = "\n".join(
            f"{self.service_id} | {url} | {qty}" for url in comment_urls
        )

        console.print(
            f"[blue]대량 좋아요 주문: {len(comment_urls)}개 댓글 | "
            f"수량: 각 {qty}개[/blue]"
        )

        result = self._request({
            "action": "mass",
            "orders": orders_text,
        })

        if result and isinstance(result, list):
            order_ids = []
            errors = []
            for item in result:
                if "order" in item:
                    order_ids.append(item["order"])
                elif "error" in item:
                    errors.append(item["error"])
            console.print(
                f"[green]대량 주문 완료! 성공: {len(order_ids)}건, "
                f"실패: {len(errors)}건[/green]"
            )
            return {"success": len(order_ids) > 0, "order_ids": order_ids, "errors": errors}
        elif result and "error" in result:
            console.print(f"[red]대량 주문 실패: {result['error']}[/red]")
            return {"success": False, "order_ids": [], "errors": [result["error"]]}
        else:
            return {"success": False, "order_ids": [], "errors": ["알 수 없는 오류"]}

    def check_order_status(self, order_id):
        """주문 상태를 확인합니다."""
        result = self._request({
            "action": "status",
            "order": order_id,
        })

        if result and "status" in result:
            status = result["status"]
            console.print(
                f"[blue]주문 {order_id} 상태: {status} | "
                f"남은 수량: {result.get('remains', 'N/A')}[/blue]"
            )
            return result
        return None

    def check_multiple_orders(self, order_ids):
        """여러 주문 상태를 한번에 확인합니다."""
        if not order_ids:
            return {}

        orders_str = ",".join(str(oid) for oid in order_ids)
        result = self._request({
            "action": "status",
            "orders": orders_str,
        })

        if result:
            for oid, status_data in result.items():
                if isinstance(status_data, dict) and "status" in status_data:
                    console.print(
                        f"  주문 {oid}: {status_data['status']} "
                        f"(남은: {status_data.get('remains', 'N/A')})"
                    )

        return result or {}
