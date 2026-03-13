"""
YouTube 브라우저 자동화 모듈
- 시크릿 모드로 브라우저 실행
- 안티디텍트 브라우저 지문 적용
- YouTube 로그인
- 댓글 작성
- 댓글 URL 추출

IP 가이드라인 + 유튜브 바이럴 가이드라인 반영:
- 시크릿 모드 필수 사용
- 계정 전환 시 브라우저 완전 종료 후 재시작
- 프록시를 통한 IP 분리
- 안티디텍트 브라우저 지문으로 계정 간 연결 차단
"""

import os
import json
import time
import re
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from rich.console import Console

console = Console()


class YouTubeBot:
    def __init__(self, proxy_config=None, fingerprint_manager=None, account_label=None):
        self.headless = os.getenv("HEADLESS", "false").lower() == "true"
        self.page_timeout = int(os.getenv("PAGE_LOAD_TIMEOUT", "30")) * 1000
        self.delay_after_comment = int(os.getenv("DELAY_AFTER_COMMENT", "5"))
        self.proxy_config = proxy_config
        self.fingerprint_manager = fingerprint_manager
        self.account_label = account_label
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def start_browser(self):
        """안티디텍트 지문이 적용된 시크릿 모드 브라우저를 시작합니다."""
        self.playwright = sync_playwright().start()

        launch_args = {
            "headless": self.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--disable-extensions",
            ],
        }

        # 프록시 설정
        if self.proxy_config:
            launch_args["proxy"] = self.proxy_config
            console.print(f"[blue]프록시 연결: {self.proxy_config.get('server', 'N/A')}[/blue]")

        self.browser = self.playwright.chromium.launch(**launch_args)

        # 안티디텍트 지문 기반 컨텍스트 설정
        if self.fingerprint_manager and self.account_label:
            context_args = self.fingerprint_manager.get_playwright_context_args(
                self.account_label
            )
        else:
            context_args = {
                "locale": "ko-KR",
                "timezone_id": "Asia/Seoul",
                "viewport": {"width": 1280, "height": 800},
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0.0.0 Safari/537.36"
                ),
            }

        self.context = self.browser.new_context(**context_args)
        self.context.set_default_timeout(self.page_timeout)

        # 안티디텍트 스크립트 주입 (모든 페이지에 적용)
        if self.fingerprint_manager and self.account_label:
            antidetect_script = self.fingerprint_manager.get_antidetect_scripts(
                self.account_label
            )
            self.context.add_init_script(antidetect_script)
            console.print(f"[green]안티디텍트 지문 적용됨: {self.account_label}[/green]")

        self.page = self.context.new_page()
        console.print("[green]시크릿 모드 브라우저 시작됨[/green]")

    def close_browser(self):
        """
        브라우저를 완전히 종료합니다.

        IP 가이드라인: 계정 전환 시 모든 창을 완전히 닫아야 함
        - 시크릿 모드 창 전부 닫기
        - 비행기모드 ON/OFF (= 프록시 변경)
        - 새 시크릿 모드로 재시작
        """
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

        console.print("[yellow]브라우저 완전 종료됨 (세션 초기화)[/yellow]")

    def _get_cookie_path(self, account_label=None):
        """계정별 쿠키 파일 경로를 반환합니다."""
        label = account_label or self.account_label or "default"
        # 파일명에 사용할 수 없는 문자 제거
        safe_label = re.sub(r'[^\w\-]', '_', label)
        cookie_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "sessions")
        os.makedirs(cookie_dir, exist_ok=True)
        return os.path.join(cookie_dir, f"{safe_label}.json")

    def save_cookies(self):
        """현재 브라우저 쿠키를 파일에 저장합니다."""
        if not self.context:
            return False
        try:
            cookies = self.context.cookies()
            cookie_path = self._get_cookie_path()
            with open(cookie_path, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False)
            console.print(f"[green]쿠키 저장 완료: {cookie_path}[/green]")
            return True
        except Exception as e:
            console.print(f"[red]쿠키 저장 실패: {e}[/red]")
            return False

    def load_cookies(self):
        """저장된 쿠키를 브라우저에 로드합니다."""
        cookie_path = self._get_cookie_path()
        if not os.path.exists(cookie_path):
            return False
        try:
            with open(cookie_path, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            if not cookies:
                return False
            self.context.add_cookies(cookies)
            console.print(f"[green]저장된 쿠키 로드 완료 ({len(cookies)}개)[/green]")
            return True
        except Exception as e:
            console.print(f"[yellow]쿠키 로드 실패: {e}[/yellow]")
            return False

    def has_saved_cookies(self):
        """저장된 쿠키 파일이 있는지 확인합니다."""
        cookie_path = self._get_cookie_path()
        return os.path.exists(cookie_path)

    def check_login_status(self):
        """현재 YouTube 로그인 상태를 확인합니다."""
        try:
            self.page.goto("https://www.youtube.com", wait_until="domcontentloaded")
            time.sleep(3)
            # 아바타 버튼이 있으면 로그인된 상태
            avatar = self.page.query_selector(
                "button#avatar-btn, "
                "ytd-topbar-menu-button-renderer img.yt-img-shadow"
            )
            return avatar is not None
        except Exception:
            return False

    def manual_login(self, email=None, timeout=300):
        """
        수동 로그인 - 브라우저 화면에서 사용자가 직접 로그인합니다.
        로그인 완료 후 쿠키를 저장합니다.

        Args:
            email: 이메일 (자동 입력용, 선택)
            timeout: 최대 대기 시간 (초)

        Returns:
            bool: 로그인 성공 여부
        """
        console.print("[blue]수동 로그인 모드: 브라우저에서 직접 로그인해주세요[/blue]")

        try:
            self.page.goto("https://accounts.google.com/signin")
            time.sleep(2)

            # 이메일 자동 입력 (선택)
            if email:
                try:
                    email_input = self.page.wait_for_selector('input[type="email"]', timeout=5000)
                    email_input.fill(email)
                    self.page.click("#identifierNext")
                    console.print(f"[blue]이메일 자동 입력됨: {email[:5]}***[/blue]")
                except Exception:
                    pass

            # 사용자가 로그인 완료할 때까지 대기
            console.print(f"[yellow]{timeout}초 내에 로그인을 완료해주세요...[/yellow]")
            for i in range(timeout // 3):
                time.sleep(3)
                try:
                    current_url = self.page.url
                except Exception:
                    continue
                console.print(f"[dim]현재 URL: {current_url[:80]}[/dim]")

                login_detected = (
                    "myaccount.google.com" in current_url
                    or ("youtube.com" in current_url and "signin" not in current_url)
                    or "accounts.google.com/SignOutOptions" in current_url
                )

                if login_detected:
                    console.print("[green]로그인 감지! 쿠키 저장 중...[/green]")
                    # YouTube로 이동하여 YouTube 쿠키도 저장 (실패해도 OK)
                    try:
                        self.page.goto(
                            "https://www.youtube.com",
                            wait_until="domcontentloaded",
                            timeout=15000,
                        )
                        time.sleep(3)
                    except Exception as e:
                        console.print(f"[yellow]YouTube 이동 실패 (무시): {e}[/yellow]")
                    self.save_cookies()
                    return True

            console.print("[red]로그인 시간 초과[/red]")
            return False

        except Exception as e:
            console.print(f"[red]수동 로그인 오류: {e}[/red]")
            return False

    def login_youtube(self, email, password):
        """YouTube(Google) 계정에 로그인합니다. 저장된 쿠키가 있으면 먼저 시도합니다."""
        console.print(f"[blue]YouTube 로그인 시도: {email[:5]}***[/blue]")

        # 저장된 쿠키로 먼저 시도
        if self.has_saved_cookies():
            console.print("[blue]저장된 쿠키로 로그인 시도...[/blue]")
            if self.load_cookies() and self.check_login_status():
                console.print("[green]쿠키 로그인 성공![/green]")
                return True
            console.print("[yellow]쿠키 로그인 실패, 일반 로그인 시도...[/yellow]")

        try:
            self.page.goto("https://accounts.google.com/signin")
            time.sleep(2)

            # 이메일 입력
            email_input = self.page.wait_for_selector('input[type="email"]', timeout=10000)
            email_input.fill(email)
            self.page.click("#identifierNext")
            time.sleep(3)

            # 비밀번호 입력
            password_input = self.page.wait_for_selector(
                'input[type="password"]', timeout=10000
            )
            password_input.fill(password)
            self.page.click("#passwordNext")
            time.sleep(5)

            # 로그인 성공 확인
            current_url = self.page.url
            if "myaccount.google.com" in current_url or "youtube.com" in current_url:
                console.print("[green]YouTube 로그인 성공![/green]")
                self.save_cookies()
                return True

            # 추가 인증이 필요한 경우 (2FA 등)
            if "challenge" in current_url or "signin" in current_url:
                console.print(
                    "[yellow]추가 인증이 필요합니다. "
                    "브라우저에서 직접 완료해주세요.[/yellow]"
                )
                if not self.headless:
                    console.print("[yellow]120초 내에 인증을 완료해주세요...[/yellow]")
                    for i in range(24):
                        time.sleep(5)
                        current_url = self.page.url
                        if "myaccount.google.com" in current_url or "youtube.com" in current_url:
                            console.print("[green]인증 완료! 로그인 성공![/green]")
                            self.save_cookies()
                            return True
                    console.print("[red]인증 시간 초과[/red]")
                    return False

            console.print("[green]로그인 진행됨[/green]")
            return True

        except PlaywrightTimeout:
            console.print("[red]로그인 시간 초과[/red]")
            return False
        except Exception as e:
            console.print(f"[red]로그인 실패: {e}[/red]")
            return False

    def post_comment(self, youtube_url, comment_text):
        """
        유튜브 영상에 댓글을 작성하고 댓글 URL을 반환합니다.

        Returns:
            str: 댓글 URL 또는 None
        """
        console.print(f"[blue]영상 접속: {youtube_url}[/blue]")

        try:
            self.page.goto(youtube_url)
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

            # 페이지 아래로 스크롤하여 댓글 섹션 로드
            self.page.evaluate("window.scrollTo(0, 500)")
            time.sleep(3)

            # 댓글 입력란 클릭 (활성화)
            comment_placeholder = self.page.wait_for_selector(
                "#simplebox-placeholder, ytd-comment-simplebox-renderer #placeholder-area",
                timeout=15000,
            )
            comment_placeholder.click()
            time.sleep(1)

            # 댓글 텍스트 입력 - type()으로 사람처럼 입력
            comment_input = self.page.wait_for_selector(
                "#contenteditable-root, "
                "ytd-comment-simplebox-renderer #contenteditable-textarea, "
                'div[contenteditable="true"]',
                timeout=10000,
            )
            # 사람처럼 타이핑 (봇 탐지 우회)
            comment_input.click()
            self.page.keyboard.type(comment_text, delay=50)
            time.sleep(1)

            # 댓글 게시 버튼 클릭
            submit_button = self.page.wait_for_selector(
                "#submit-button ytd-button-renderer, "
                "ytd-comment-simplebox-renderer #submit-button",
                timeout=5000,
            )
            submit_button.click()

            console.print("[green]댓글 작성 요청 전송됨[/green]")

            # 댓글이 게시될 때까지 대기
            time.sleep(self.delay_after_comment)

            # 댓글 URL 추출
            comment_url = self._extract_comment_url(youtube_url)

            if comment_url:
                console.print(f"[green]댓글 URL: {comment_url}[/green]")
            else:
                console.print("[yellow]댓글은 작성되었으나 URL을 추출하지 못했습니다.[/yellow]")
                comment_url = self._build_fallback_url(youtube_url)

            return comment_url

        except PlaywrightTimeout:
            console.print("[red]댓글 작성 시간 초과 - 댓글 섹션을 찾을 수 없습니다[/red]")
            return None
        except Exception as e:
            console.print(f"[red]댓글 작성 실패: {e}[/red]")
            return None

    def _extract_comment_url(self, video_url):
        """
        작성된 댓글의 URL을 추출합니다.

        YouTube 댓글 URL 형식:
        https://www.youtube.com/watch?v=VIDEO_ID&lc=COMMENT_ID
        """
        try:
            self.page.evaluate("window.scrollTo(0, 500)")
            time.sleep(2)

            # 최신 댓글의 타임스탬프 링크에서 comment ID 추출
            comment_links = self.page.query_selector_all(
                "ytd-comment-thread-renderer a.yt-simple-endpoint"
            )

            for link in comment_links[:5]:
                href = link.get_attribute("href")
                if href and "&lc=" in href:
                    if href.startswith("/"):
                        return f"https://www.youtube.com{href}"
                    return href

            return None

        except Exception as e:
            console.print(f"[yellow]댓글 URL 추출 중 오류: {e}[/yellow]")
            return None

    def _build_fallback_url(self, video_url):
        """영상 URL을 기반으로 fallback URL을 생성합니다."""
        video_id_match = re.search(
            r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})", video_url
        )
        if video_id_match:
            video_id = video_id_match.group(1)
            return f"https://www.youtube.com/watch?v={video_id}"
        return video_url
