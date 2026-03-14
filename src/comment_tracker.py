"""
댓글 트래킹 모듈

작성한 댓글이 실제로 유지되고 있는지 확인하고,
하이라이트(인기 댓글) 위치를 추적합니다.

트래킹 방식:
1. lc= 파라미터로 댓글 URL 직접 접속 → 해당 댓글이 하이라이트되면 생존 확인
2. 영상 페이지에서 댓글 위치(순서) 확인
3. 비로그인 시크릿 모드로 조회 (IP 부담 없음 - 일반 시청자 행동)

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
import asyncio
import threading
from concurrent.futures import Future
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from rich.console import Console

console = Console()


def _run_in_clean_thread(func, *args, **kwargs):
    """asyncio 루프가 없는 깨끗한 스레드에서 함수를 실행합니다.

    Playwright sync API는 실행 중인 asyncio 루프가 있으면 에러를 발생시킵니다.
    별도 스레드에서 실행하면 부모 스레드의 asyncio 루프와 격리됩니다.
    """
    # 먼저 현재 스레드에 실행 중인 루프가 있는지 확인
    try:
        running = asyncio._get_running_loop()
    except AttributeError:
        running = None

    if running is None:
        # 실행 중인 루프가 없으면 그냥 직접 실행
        return func(*args, **kwargs)

    # 실행 중인 루프가 있으면 별도 깨끗한 스레드에서 실행
    result_future = Future()

    def _worker():
        try:
            r = func(*args, **kwargs)
            result_future.set_result(r)
        except Exception as e:
            result_future.set_exception(e)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=600)  # 최대 10분 대기
    return result_future.result()


def _ensure_clean_event_loop():
    """현재 스레드의 asyncio 이벤트 루프를 깨끗한 상태로 교체합니다."""
    try:
        asyncio.set_event_loop(asyncio.new_event_loop())
    except Exception:
        pass


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
        if self.browser:
            return  # 이미 열려있으면 재사용

        # Flask/threading 환경에서 asyncio 루프 충돌 방지
        _ensure_clean_event_loop()
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

    def _dismiss_consent(self):
        """쿠키 동의 팝업 처리"""
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

        # 이미 등록된 댓글이면 스킵
        if comment_id in self.history["comments"]:
            existing = self.history["comments"][comment_id]
            if existing["status"] != "deleted":
                return True

        video_id = self._extract_video_id(video_url or comment_url)

        # video_url이 없으면 comment_url에서 추출
        if not video_url and video_id:
            video_url = f"https://www.youtube.com/watch?v={video_id}"

        self.history["comments"][comment_id] = {
            "comment_url": comment_url,
            "video_url": video_url or "",
            "video_id": video_id,
            "account_label": account_label or "수동등록",
            "comment_text": comment_text[:200] if comment_text else "",
            "registered_at": datetime.now().isoformat(),
            "checks": [],
            "status": "active",
            "best_position": None,
            "last_position": None,
            "last_likes": 0,
        }

        self._save_history()
        console.print(f"[green]트래킹 등록: {comment_id}[/green]")
        return True

    def check_comment(self, comment_id, reuse_browser=False):
        """
        개별 댓글의 생존 상태와 위치를 확인합니다.
        asyncio 루프 충돌 방지를 위해 필요 시 별도 스레드에서 실행됩니다.
        (reuse_browser=True일 때는 이미 안전한 스레드에서 호출되므로 직접 실행)
        """
        if reuse_browser:
            # check_all 내부에서 호출 - 이미 깨끗한 스레드
            return self._check_comment_impl(comment_id, reuse_browser)
        # 외부에서 직접 호출 - 깨끗한 스레드에서 실행
        return _run_in_clean_thread(self._check_comment_impl, comment_id, reuse_browser)

    def _check_comment_impl(self, comment_id, reuse_browser=False):
        """
        개별 댓글의 생존 상태와 위치를 확인합니다.

        방식: lc= URL로 직접 접속 → 해당 댓글이 페이지에 나타나는지 확인
              + 영상 댓글 목록에서 순서(위치) 확인

        Args:
            reuse_browser: True이면 브라우저를 닫지 않음 (check_all에서 사용)

        Returns:
            dict: {alive, position, likes, is_highlighted, status}
        """
        comment_data = self.history["comments"].get(comment_id)
        if not comment_data:
            return {"error": "등록되지 않은 댓글"}

        comment_url = comment_data["comment_url"]
        video_url = comment_data["video_url"]
        comment_text = comment_data["comment_text"]

        try:
            self._start_browser()

            # ── 1단계: lc= URL로 직접 접속하여 댓글 생존 확인 ──
            self.page.goto(comment_url, wait_until="domcontentloaded")
            time.sleep(3)
            self._dismiss_consent()

            # 댓글 섹션으로 스크롤
            self.page.evaluate("window.scrollTo(0, 500)")
            time.sleep(3)

            # lc= 파라미터로 접속하면 해당 댓글이 하이라이트됨
            # 하이라이트된 댓글 또는 "linked comment" 영역을 찾는다
            alive = False
            likes = 0
            found_text = ""

            # YouTube는 lc= 접속 시 해당 댓글을 상단에 "고정된 댓글"처럼 보여줌
            # 블라인드된 댓글은 lc= 접속 시 하이라이트 영역 없이 일반 댓글만 표시됨
            #
            # 판별 전략:
            # 1) "하이라이트된 댓글" 배너가 있는지 확인 (lc= 정상 동작 증거)
            # 2) 첫 번째 댓글 텍스트가 우리 댓글과 일치하는지 확인
            # → 둘 다 아니면 블라인드 판정

            # 방법 1: "하이라이트된 댓글" 헤더/배지 존재 확인
            # YouTube는 lc= 로 접속 시 해당 댓글에 특별한 속성/배지를 부여함
            # 블라인드 댓글이면 이 요소들이 존재하지 않음
            highlight_header = self.page.query_selector(
                'ytd-comment-thread-renderer[is-showing-linked-comment], '
                '#linked-comment-badge, '
                'yt-formatted-string#linked-comment-badge, '
                '#header-author ytd-comment-renderer[is-linked-comment]'
            )
            console.print(f"[dim]  하이라이트 배지 존재: {bool(highlight_header)}[/dim]")

            highlighted_selectors = [
                "ytd-comment-thread-renderer.ytd-item-section-renderer #content-text",
                "#content-text.ytd-comment-renderer",
            ]

            for selector in highlighted_selectors:
                elements = self.page.query_selector_all(selector)
                if elements:
                    first_text = elements[0].inner_text().strip()
                    if first_text:
                        found_text = first_text

                        # 텍스트 매칭으로 우리 댓글인지 확인
                        is_our_comment = False
                        if comment_text:
                            # 앞부분 40자 이상 일치하면 우리 댓글로 판정
                            match_len = min(40, len(comment_text))
                            our_prefix = comment_text[:match_len].strip()
                            found_prefix = first_text[:match_len].strip()
                            if our_prefix and found_prefix and our_prefix in first_text:
                                is_our_comment = True
                            elif found_prefix and found_prefix in comment_text:
                                is_our_comment = True

                        # 하이라이트 헤더가 있으면 lc= 가 정상 동작한 것
                        if highlight_header:
                            is_our_comment = True

                        if is_our_comment:
                            alive = True

                            # 댓글 텍스트가 비어있었다면 채워넣기 (수동 등록 시)
                            if not comment_text and found_text:
                                comment_data["comment_text"] = found_text[:200]

                            # 좋아요 수 추출
                            try:
                                parent = elements[0].evaluate_handle(
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
                        else:
                            console.print(
                                f"[yellow]텍스트 불일치 → 블라인드 판정[/yellow]\n"
                                f"  기대: {comment_text[:60]}...\n"
                                f"  발견: {first_text[:60]}..."
                            )
                        break

            # ── 2단계: 영상 전체 댓글에서 위치 확인 ──
            position = -1
            is_highlighted = False

            if alive:
                # 영상 페이지로 이동 (인기순 정렬 기본)
                if video_url and video_url != comment_url:
                    self.page.goto(video_url, wait_until="domcontentloaded")
                    time.sleep(3)
                    self._dismiss_consent()
                    self.page.evaluate("window.scrollTo(0, 500)")
                    time.sleep(3)

                # 댓글 더 로드 (최대 5번 스크롤)
                for _ in range(5):
                    self.page.evaluate("window.scrollBy(0, 1000)")
                    time.sleep(1.5)

                # 댓글 목록에서 위치 찾기
                comment_elements = self.page.query_selector_all(
                    "#content-text.ytd-comment-renderer, "
                    "ytd-comment-renderer #content-text"
                )

                search_text = found_text or comment_text
                if search_text:
                    match_text = search_text[:80]
                    for idx, el in enumerate(comment_elements):
                        el_text = el.inner_text().strip()
                        if match_text in el_text or (comment_text and comment_text[:80] in el_text):
                            position = idx + 1  # 1-based
                            is_highlighted = position <= 3
                            break

            # ── 결과 기록 ──
            check_result = {
                "checked_at": datetime.now().isoformat(),
                "alive": alive,
                "position": position if alive else -1,
                "likes": likes,
                "is_highlighted": is_highlighted,
            }

            comment_data["checks"].append(check_result)

            if alive:
                comment_data["status"] = "active"
                comment_data["last_likes"] = likes
                if position > 0:
                    comment_data["last_position"] = position
                    if comment_data["best_position"] is None or position < comment_data["best_position"]:
                        comment_data["best_position"] = position
                console.print(f"[green]✓ 정상노출 확인 (좋아요:{likes}, 위치:{position})[/green]")
            else:
                comment_data["status"] = "hidden"
                console.print(
                    f"[red]✗ 숨김/블라인드 판정: {comment_text[:40]}...[/red]"
                )

            self._save_history()

            return {
                "alive": alive,
                "position": position,
                "likes": likes,
                "is_highlighted": is_highlighted,
                "status": comment_data["status"],
                "total_checks": len(comment_data["checks"]),
                "comment_text": comment_data["comment_text"][:50],
            }

        except Exception as e:
            console.print(f"[red]트래킹 오류: {e}[/red]")
            return {"error": str(e)}
        finally:
            if not reuse_browser:
                self._close_browser()

    def check_all(self):
        """
        등록된 모든 댓글의 상태를 확인합니다.
        asyncio 루프 충돌 방지를 위해 필요 시 별도 스레드에서 실행됩니다.
        """
        return _run_in_clean_thread(self._check_all_impl)

    def _check_all_impl(self):
        """
        등록된 모든 댓글의 상태를 확인합니다.
        같은 영상의 댓글은 한 번의 접속으로 묶어서 확인합니다.

        Returns:
            dict: {total, active, hidden, results: [...]}
        """
        active_comments = {
            cid: data for cid, data in self.history["comments"].items()
            if data["status"] not in ("deleted", "reposted")
        }

        total = len(active_comments)
        if total == 0:
            return {"total": 0, "active": 0, "hidden": 0, "results": []}

        console.print(f"[blue]총 {total}개 댓글 트래킹 시작...[/blue]")

        results = []
        try:
            self._start_browser()

            for idx, (comment_id, data) in enumerate(active_comments.items(), 1):
                console.print(
                    f"[dim]확인 중 ({idx}/{total}): "
                    f"{data['account_label']} - {data['comment_text'][:30]}...[/dim]"
                )
                result = self.check_comment(comment_id, reuse_browser=True)
                result["comment_id"] = comment_id
                result["account_label"] = data["account_label"]
                result["comment_text"] = data["comment_text"][:50]
                result["video_id"] = data.get("video_id", "")
                results.append(result)

                # 요청 간 간격 (rate limit 방지)
                if idx < total:
                    time.sleep(3)

        except Exception as e:
            console.print(f"[red]전체 트래킹 오류: {e}[/red]")
        finally:
            self._close_browser()

        active_count = sum(1 for r in results if r.get("alive"))
        hidden_count = sum(1 for r in results if not r.get("alive") and "error" not in r)

        return {
            "total": total,
            "active": active_count,
            "hidden": hidden_count,
            "results": results,
        }

    def check_selected(self, comment_ids):
        """선택된 댓글만 트래킹합니다."""
        return _run_in_clean_thread(self._check_selected_impl, comment_ids)

    def _check_selected_impl(self, comment_ids):
        """선택된 댓글 ID 목록만 트래킹합니다."""
        targets = {
            cid: self.history["comments"][cid]
            for cid in comment_ids
            if cid in self.history["comments"]
            and self.history["comments"][cid]["status"] not in ("deleted", "reposted")
        }

        total = len(targets)
        if total == 0:
            return {"total": 0, "active": 0, "hidden": 0, "results": []}

        console.print(f"[blue]선택된 {total}개 댓글 트래킹 시작...[/blue]")

        results = []
        try:
            self._start_browser()

            for idx, (comment_id, data) in enumerate(targets.items(), 1):
                console.print(
                    f"[dim]확인 중 ({idx}/{total}): "
                    f"{data['account_label']} - {data['comment_text'][:30]}...[/dim]"
                )
                result = self.check_comment(comment_id, reuse_browser=True)
                result["comment_id"] = comment_id
                result["account_label"] = data["account_label"]
                result["comment_text"] = data["comment_text"][:50]
                result["video_id"] = data.get("video_id", "")
                results.append(result)

                if idx < total:
                    time.sleep(3)

        except Exception as e:
            console.print(f"[red]선택 트래킹 오류: {e}[/red]")
        finally:
            self._close_browser()

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
            if data.get("status") == "deleted":
                continue
            last_check = data["checks"][-1] if data["checks"] else None
            summary.append({
                "comment_id": cid,
                "account_label": data["account_label"],
                "comment_text": data["comment_text"][:50],
                "video_id": data.get("video_id", ""),
                "comment_url": data.get("comment_url", ""),
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
