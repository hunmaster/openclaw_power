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
        self._log_callback = None  # 외부 로그 콜백 (app.py add_log 연결용)

    def set_log_callback(self, callback):
        """대시보드 실행로그에 연결할 콜백을 설정합니다."""
        self._log_callback = callback

    def _log(self, message, level="info"):
        """콘솔 + 대시보드 실행로그에 동시 출력"""
        color_map = {"info": "blue", "warning": "yellow", "error": "red", "debug": "dim"}
        color = color_map.get(level, "white")
        console.print(f"[{color}]{message}[/{color}]")
        if self._log_callback:
            self._log_callback(f"[트래킹] {message}", level)

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
            self._log(f"1단계 시작: lc= URL 접속 → {comment_url[:80]}...", "debug")
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

            # 판별 전략: 텍스트 매칭만 신뢰
            # YouTube 배지(linked-comment-badge)는 블라인드 댓글에서도 나타날 수 있어 신뢰 불가
            # → 페이지에서 찾은 댓글 텍스트가 우리 댓글과 일치하는지만으로 판정

            highlighted_selectors = [
                "ytd-comment-thread-renderer.ytd-item-section-renderer #content-text",
                "#content-text.ytd-comment-renderer",
            ]

            for selector in highlighted_selectors:
                elements = self.page.query_selector_all(selector)
                self._log(f"1단계 셀렉터 '{selector}' → {len(elements)}개 요소 발견", "debug")
                if elements:
                    # 모든 댓글을 순회하며 우리 댓글 텍스트와 매칭
                    for el in elements:
                        el_text = el.inner_text().strip()
                        if not el_text:
                            continue

                        is_our_comment = False
                        if comment_text:
                            match_len = min(40, len(comment_text))
                            our_prefix = comment_text[:match_len].strip()
                            if our_prefix and our_prefix in el_text:
                                is_our_comment = True
                            elif el_text[:match_len].strip() in comment_text and len(el_text) > 10:
                                is_our_comment = True

                        if is_our_comment:
                            alive = True
                            found_text = el_text

                            # 댓글 텍스트가 비어있었다면 채워넣기 (수동 등록 시)
                            if not comment_text and found_text:
                                comment_data["comment_text"] = found_text[:200]

                            # 좋아요 수 추출
                            try:
                                parent = el.evaluate_handle(
                                    "el => el.closest('ytd-comment-renderer')"
                                )
                                parent_el = parent.as_element()
                                # YouTube 좋아요 셀렉터 (버전별 대응)
                                like_selectors = [
                                    "#vote-count-middle",
                                    "span#vote-count-middle",
                                    "#toolbar yt-formatted-string.count-text",
                                    "#toolbar span[aria-label*='좋아요']",
                                    "#toolbar span[aria-label*='like']",
                                ]
                                for like_sel in like_selectors:
                                    like_el = parent_el.query_selector(like_sel)
                                    if like_el:
                                        like_text = like_el.inner_text().strip()
                                        if like_text:
                                            likes = self._parse_like_count(like_text)
                                            break
                            except Exception:
                                pass
                            break

                    if alive:
                        self._log(f"1단계 결과: 텍스트 매칭 성공, 좋아요={likes}, found_text={found_text[:60]}", "info")
                    else:
                        # 첫 번째 댓글 텍스트를 로그로 남김 (디버그용)
                        sample = elements[0].inner_text().strip()[:60] if elements else "없음"
                        self._log(
                            f"1단계 결과: 텍스트 불일치 → 블라인드 판정\n"
                            f"    기대: {comment_text[:60]}...\n"
                            f"    페이지 첫댓글: {sample}...", "warning"
                        )
                    break

            # ── 2단계: 영상 전체 댓글에서 위치 확인 ──
            position = -1
            is_highlighted = False

            if alive:
                # 영상 페이지로 이동 (인기순 정렬 기본)
                if video_url and video_url != comment_url:
                    self._log(f"2단계 시작: 영상 페이지 이동 → {video_url}", "debug")
                    self.page.goto(video_url, wait_until="domcontentloaded")
                    time.sleep(3)
                    self._dismiss_consent()
                else:
                    self._log(f"2단계: video_url='{video_url}', comment_url과 동일하여 페이지 이동 안함", "warning")

                # 현재 페이지 URL 확인
                current_url = self.page.url
                self._log(f"2단계 현재 페이지: {current_url}", "debug")

                # 댓글 섹션 요소 존재 여부 확인
                has_comments_section = self.page.evaluate("""
                    (() => {
                        const el = document.querySelector('ytd-comments#comments, #comments');
                        return el ? el.tagName + ' found' : 'NOT found';
                    })()
                """)
                self._log(f"2단계 댓글 섹션 요소: {has_comments_section}", "debug")

                # 댓글 섹션까지 스크롤하여 로드 트리거
                # YouTube 댓글은 뷰포트에 들어와야 lazy-load 됨
                # 1) 댓글 섹션 요소로 직접 스크롤 시도
                scroll_result = self.page.evaluate("""
                    (() => {
                        const comments = document.querySelector('ytd-comments#comments, #comments');
                        if (comments) {
                            comments.scrollIntoView({behavior: 'instant', block: 'start'});
                            return 'scrollIntoView 실행됨';
                        } else {
                            window.scrollTo(0, 800);
                            return 'comments 요소 없음 → fallback scrollTo(0, 800)';
                        }
                    })()
                """)
                self._log(f"2단계 스크롤: {scroll_result}", "debug")
                time.sleep(3)

                # 2) 댓글이 아직 안 보이면 점진적으로 더 스크롤
                comments_loaded = False
                for scroll_try in range(3):
                    try:
                        self.page.wait_for_selector(
                            "ytd-comment-renderer #content-text",
                            timeout=5000
                        )
                        comments_loaded = True
                        self._log(f"2단계 댓글 로드 성공 (시도 {scroll_try + 1}회)", "debug")
                        break
                    except PlaywrightTimeout:
                        scroll_amount = 800 + scroll_try * 500
                        self._log(f"2단계 댓글 미로드 → 추가 스크롤 {scroll_amount}px (시도 {scroll_try + 1}/3)", "debug")
                        self.page.evaluate(f"window.scrollBy(0, {scroll_amount})")
                        time.sleep(2)

                if not comments_loaded:
                    self._log("2단계 실패: 댓글 로드 타임아웃 (3회 스크롤 시도 후에도 댓글 없음)", "warning")
                    # 페이지 HTML 일부를 로그에 남김
                    page_info = self.page.evaluate("""
                        (() => {
                            const body = document.body;
                            const title = document.title;
                            const commentSections = document.querySelectorAll('[id*="comment"], [class*="comment"]');
                            const sectionInfo = Array.from(commentSections).slice(0, 5).map(
                                el => el.tagName + '#' + el.id + '.' + el.className.split(' ')[0]
                            );
                            return {
                                title: title,
                                url: location.href,
                                bodyLen: body.innerHTML.length,
                                commentSections: sectionInfo
                            };
                        })()
                    """)
                    self._log(f"2단계 페이지 진단: title={page_info.get('title', '?')[:50]}, "
                              f"bodyLen={page_info.get('bodyLen', 0)}, "
                              f"comment요소={page_info.get('commentSections', [])}", "warning")

                # 댓글 더 로드 (최대 10번 스크롤, 충분히 로드)
                prev_count = 0
                for scroll_i in range(10):
                    self.page.evaluate("window.scrollBy(0, 1500)")
                    time.sleep(1.5)
                    cur_count = self.page.evaluate(
                        "document.querySelectorAll('ytd-comment-renderer #content-text').length"
                    )
                    if cur_count == prev_count and cur_count > 0:
                        break  # 더 이상 새 댓글 로드 안 됨
                    prev_count = cur_count

                self._log(f"2단계 댓글 {prev_count}개 로드됨", "debug")

                # 댓글 목록에서 위치 찾기
                comment_elements = self.page.query_selector_all(
                    "ytd-comment-renderer #content-text"
                )

                search_text = found_text or comment_text
                self._log(f"2단계 텍스트 매칭: 검색어='{search_text[:50] if search_text else '(없음)'}', "
                          f"댓글요소={len(comment_elements)}개", "debug")

                if search_text:
                    match_len = min(40, len(search_text))
                    match_prefix = search_text[:match_len].strip()

                    # 처음 5개 댓글 텍스트 로그 (디버그용)
                    for dbg_idx, dbg_el in enumerate(comment_elements[:5]):
                        dbg_text = dbg_el.inner_text().strip()[:60]
                        self._log(f"  댓글[{dbg_idx}]: {dbg_text}", "debug")

                    for idx, el in enumerate(comment_elements):
                        el_text = el.inner_text().strip()
                        if match_prefix and match_prefix in el_text:
                            position = idx + 1  # 1-based
                            is_highlighted = position <= 3

                            # 위치 찾은 김에 좋아요도 재확인
                            try:
                                parent = el.evaluate_handle(
                                    "el => el.closest('ytd-comment-renderer')"
                                )
                                parent_el = parent.as_element()
                                like_selectors = [
                                    "#vote-count-middle",
                                    "span#vote-count-middle",
                                    "#toolbar yt-formatted-string.count-text",
                                    "#toolbar span[aria-label*='좋아요']",
                                    "#toolbar span[aria-label*='like']",
                                ]
                                for like_sel in like_selectors:
                                    like_el = parent_el.query_selector(like_sel)
                                    if like_el:
                                        like_text = like_el.inner_text().strip()
                                        self._log(f"2단계 좋아요 셀렉터 '{like_sel}' → '{like_text}'", "debug")
                                        if like_text:
                                            parsed = self._parse_like_count(like_text)
                                            if parsed > likes:
                                                likes = parsed
                                            break
                            except Exception as e:
                                self._log(f"2단계 좋아요 추출 오류: {e}", "warning")
                            break

                    if position == -1:
                        self._log(f"2단계 실패: '{match_prefix}' 텍스트가 {len(comment_elements)}개 댓글에서 미발견", "warning")

                self._log(f"2단계 최종 결과: 위치={position}, 좋아요={likes}", "info")

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
                self._log(f"✓ 정상노출 확인 (좋아요:{likes}, 위치:{position})", "info")
            else:
                comment_data["status"] = "hidden"
                self._log(f"✗ 숨김/블라인드 판정: {comment_text[:40]}...", "warning")

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
