"""
댓글 트래킹 모듈

작성한 댓글이 실제로 유지되고 있는지 확인하고,
하이라이트(인기 댓글) 위치를 추적합니다.

기능:
- 댓글 생존 확인 (삭제/숨김 감지)
- 하이라이트 위치 추적 (몇 번째 댓글인지)
- 좋아요 수 모니터링
- 트래킹 히스토리 저장
"""

import os
import json
import time
import re
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from rich.console import Console

console = Console()


class CommentTracker:
    """댓글 생존 확인 및 하이라이트 위치 추적"""

    def __init__(self):
        self.history_file = "config/tracking_history.json"
        self.history = self._load_history()
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def _load_history(self):
        if os.path.exists(self.history_file):
            with open(self.history_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"comments": {}}

    def _save_history(self):
        os.makedirs(os.path.dirname(self.history_file), exist_ok=True)
        with open(self.history_file, "w", encoding="utf-8") as f:
            json.dump(self.history, f, indent=2, ensure_ascii=False)

    def _start_browser(self):
        """트래킹용 브라우저 (비로그인, 시크릿 모드)"""
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        self.context = self.browser.new_context(
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
        )
        self.context.set_default_timeout(20000)
        self.page = self.context.new_page()

    def _close_browser(self):
        try:
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass
        finally:
            self.playwright = None
            self.browser = None
            self.context = None
            self.page = None

    def register_comment(self, comment_url, video_url, account_label, comment_text):
        """
        작성된 댓글을 트래킹 대상으로 등록합니다.

        Args:
            comment_url: 댓글 URL (lc= 파라미터 포함)
            video_url: 영상 URL
            account_label: 작성 계정
            comment_text: 댓글 내용
        """
        comment_id = self._extract_comment_id(comment_url)
        if not comment_id:
            return False

        video_id = self._extract_video_id(video_url)

        self.history["comments"][comment_id] = {
            "comment_url": comment_url,
            "video_url": video_url,
            "video_id": video_id,
            "account_label": account_label,
            "comment_text": comment_text[:200],
            "registered_at": datetime.now().isoformat(),
            "checks": [],
            "status": "active",  # active, hidden, deleted
            "best_position": None,
            "last_position": None,
            "last_likes": 0,
        }

        self._save_history()
        console.print(f"[green]트래킹 등록: {comment_id}[/green]")
        return True

    def check_comment(self, comment_id):
        """
        개별 댓글의 생존 상태와 위치를 확인합니다.

        Returns:
            dict: {alive, position, likes, is_highlighted, status}
        """
        comment_data = self.history["comments"].get(comment_id)
        if not comment_data:
            return {"error": "등록되지 않은 댓글"}

        video_url = comment_data["video_url"]
        comment_text = comment_data["comment_text"]

        try:
            self._start_browser()

            # 영상 페이지 접속
            self.page.goto(video_url, wait_until="domcontentloaded")
            time.sleep(3)

            # 쿠키 동의 팝업 처리
            try:
                accept_btn = self.page.query_selector(
                    'button[aria-label*="Accept"], '
                    'button[aria-label*="동의"], '
                    'tp-yt-paper-button:has-text("동의")'
                )
                if accept_btn:
                    accept_btn.click()
                    time.sleep(1)
            except Exception:
                pass

            # 댓글 섹션 로드를 위해 스크롤
            self.page.evaluate("window.scrollTo(0, 500)")
            time.sleep(3)

            # 댓글 더 로드 (최대 5번 스크롤)
            for _ in range(5):
                self.page.evaluate("window.scrollBy(0, 1000)")
                time.sleep(1.5)

            # 모든 댓글 텍스트 수집
            comment_elements = self.page.query_selector_all(
                "#content-text.ytd-comment-renderer, "
                "ytd-comment-renderer #content-text"
            )

            found = False
            position = -1
            likes = 0
            is_highlighted = False

            for idx, el in enumerate(comment_elements):
                el_text = el.inner_text().strip()

                # 댓글 텍스트의 앞부분으로 매칭 (정확히 일치 또는 80% 포함)
                match_text = comment_text[:80]
                if match_text in el_text or el_text in comment_text:
                    found = True
                    position = idx + 1  # 1-based

                    # 하이라이트 (상위 3개 이내)
                    is_highlighted = position <= 3

                    # 좋아요 수 추출
                    try:
                        parent = el.evaluate_handle(
                            "el => el.closest('ytd-comment-renderer')"
                        )
                        like_el = parent.as_element().query_selector(
                            "#vote-count-middle"
                        )
                        if like_el:
                            like_text = like_el.inner_text().strip()
                            likes = self._parse_like_count(like_text)
                    except Exception:
                        pass

                    break

            # 결과 기록
            check_result = {
                "checked_at": datetime.now().isoformat(),
                "alive": found,
                "position": position if found else -1,
                "likes": likes,
                "is_highlighted": is_highlighted,
            }

            comment_data["checks"].append(check_result)

            if found:
                comment_data["status"] = "active"
                comment_data["last_position"] = position
                comment_data["last_likes"] = likes
                if comment_data["best_position"] is None or position < comment_data["best_position"]:
                    comment_data["best_position"] = position
            else:
                comment_data["status"] = "hidden"

            self._save_history()

            return {
                "alive": found,
                "position": position,
                "likes": likes,
                "is_highlighted": is_highlighted,
                "status": comment_data["status"],
                "total_checks": len(comment_data["checks"]),
            }

        except Exception as e:
            console.print(f"[red]트래킹 오류: {e}[/red]")
            return {"error": str(e)}
        finally:
            self._close_browser()

    def check_all(self):
        """
        등록된 모든 댓글의 상태를 확인합니다.

        Returns:
            dict: {total, active, hidden, results: [...]}
        """
        results = []
        active_comments = {
            cid: data for cid, data in self.history["comments"].items()
            if data["status"] != "deleted"
        }

        total = len(active_comments)
        if total == 0:
            return {"total": 0, "active": 0, "hidden": 0, "results": []}

        console.print(f"[blue]총 {total}개 댓글 트래킹 시작...[/blue]")

        for idx, (comment_id, data) in enumerate(active_comments.items(), 1):
            console.print(f"[dim]확인 중 ({idx}/{total}): {data['account_label']} - {data['comment_text'][:30]}...[/dim]")
            result = self.check_comment(comment_id)
            result["comment_id"] = comment_id
            result["account_label"] = data["account_label"]
            result["comment_text"] = data["comment_text"][:50]
            result["video_id"] = data.get("video_id", "")
            results.append(result)

            # 요청 간 간격 (봇 탐지 방지)
            if idx < total:
                time.sleep(3)

        active_count = sum(1 for r in results if r.get("alive"))
        hidden_count = sum(1 for r in results if not r.get("alive") and "error" not in r)

        return {
            "total": total,
            "active": active_count,
            "hidden": hidden_count,
            "results": results,
        }

    def get_summary(self):
        """등록된 댓글들의 현재 요약 정보를 반환합니다."""
        comments = self.history.get("comments", {})
        summary = []

        for cid, data in comments.items():
            last_check = data["checks"][-1] if data["checks"] else None
            summary.append({
                "comment_id": cid,
                "account_label": data["account_label"],
                "comment_text": data["comment_text"][:50],
                "video_id": data.get("video_id", ""),
                "status": data["status"],
                "position": data.get("last_position"),
                "best_position": data.get("best_position"),
                "likes": data.get("last_likes", 0),
                "registered_at": data["registered_at"],
                "last_checked": last_check["checked_at"] if last_check else None,
                "total_checks": len(data["checks"]),
            })

        return summary

    def remove_comment(self, comment_id):
        """트래킹 대상에서 제거합니다."""
        if comment_id in self.history["comments"]:
            self.history["comments"][comment_id]["status"] = "deleted"
            self._save_history()
            return True
        return False

    def _extract_comment_id(self, comment_url):
        match = re.search(r"lc=([a-zA-Z0-9_-]+)", comment_url)
        return match.group(1) if match else None

    def _extract_video_id(self, url):
        match = re.search(r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})", url)
        return match.group(1) if match else None

    def _parse_like_count(self, text):
        """좋아요 텍스트를 숫자로 변환 (예: '1.2천' → 1200)"""
        if not text or text.strip() == "":
            return 0
        text = text.strip()
        try:
            if "천" in text:
                return int(float(text.replace("천", "").strip()) * 1000)
            elif "만" in text:
                return int(float(text.replace("만", "").strip()) * 10000)
            elif "K" in text.upper():
                return int(float(text.upper().replace("K", "").strip()) * 1000)
            elif "M" in text.upper():
                return int(float(text.upper().replace("M", "").strip()) * 1000000)
            else:
                return int(text.replace(",", ""))
        except (ValueError, AttributeError):
            return 0
