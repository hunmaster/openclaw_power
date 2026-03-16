"""
CommentBoost 데스크탑 앱 (PyWebView + Flask)

PyWebView로 Flask 대시보드를 네이티브 윈도우 창에서 실행합니다.
브라우저 URL바 없이 앱처럼 동작하며, HTTPS 없이 내부 통신합니다.
"""

import os
import sys
import time
import threading
import socket
import logging

# 프로젝트 루트를 경로에 추가
_APP_ROOT = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, 'frozen', False):
    _APP_ROOT = os.path.dirname(sys.executable)
sys.path.insert(0, _APP_ROOT)

# 파일 로깅 설정 (--noconsole에서도 디버깅 가능)
_log_dir = os.path.join(_APP_ROOT, "data")
os.makedirs(_log_dir, exist_ok=True)
logging.basicConfig(
    filename=os.path.join(_log_dir, "desktop.log"),
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_log = logging.getLogger("desktop")

# Flask 앱 임포트 전에 환경 설정
os.environ["DESKTOP_MODE"] = "1"


def find_free_port(start=5000, end=5100):
    """사용 가능한 포트 찾기"""
    for port in range(start, end):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    return start


def start_flask(port):
    """Flask 서버를 별도 스레드에서 실행"""
    from app import app, _start_scheduler

    _start_scheduler()

    # 데스크탑 모드: HTTP로 실행 (PyWebView 내부 통신)
    app.run(
        host="127.0.0.1",
        port=port,
        debug=False,
        use_reloader=False,
        threaded=True,
    )


def wait_for_server(port, timeout=15):
    """Flask 서버가 준비될 때까지 대기"""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect(("127.0.0.1", port))
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.3)
    return False


# ─── 업데이트 체크 & 팝업 ───

def check_and_show_update():
    """
    앱 시작 전 업데이트 확인.
    업데이트가 있으면 tkinter 팝업을 표시하고, 사용자가 승인하면 업데이트 진행.
    Returns: True면 업데이트 완료 후 재시작 필요, False면 그냥 앱 실행
    """
    try:
        from src.updater import check_for_updates, get_current_version
    except ImportError as e:
        _log.error(f"updater 모듈 로드 실패: {e}")
        return False

    _log.info("업데이트 확인 중...")
    update_info = check_for_updates()
    _log.info(f"서버 응답: {update_info}")

    if not update_info.get("needs_update"):
        if update_info.get("error"):
            _log.warning(f"확인 실패: {update_info['error']}")
        else:
            _log.info("최신 버전입니다.")
        return False

    current = get_current_version()
    current_ver = current.get("version", "0.0.0")
    latest_ver = update_info.get("latest_version", "?")
    changelog = update_info.get("changelog", "")

    _log.info(f"새 버전 발견: v{current_ver} → v{latest_ver}")

    # tkinter 팝업 표시
    return _show_update_popup(current_ver, latest_ver, changelog, update_info)


def _show_update_popup(current_ver, latest_ver, changelog, update_info):
    """tkinter 업데이트 팝업 표시. 전체화면 어두운 배경 + 중앙 카드."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        _log.error("tkinter 사용 불가, 업데이트 건너뜀")
        return False

    result = {"do_update": False}

    # 전체화면 단일 창 (어두운 배경 + 중앙 팝업 카드)
    root = tk.Tk()
    root.title("CommentBoost 업데이트")
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)
    root.configure(bg="#0a0a0a")
    root.overrideredirect(True)

    # 아이콘
    icon_path = os.path.join(_APP_ROOT, "app_icon.ico")
    if os.path.exists(icon_path):
        try:
            root.iconbitmap(icon_path)
        except Exception:
            pass

    # 스타일
    bg = "#1a1a2e"
    card_bg = "#16213e"
    text_color = "#e0e0e0"
    accent = "#7c3aed"
    green = "#10b981"
    dim = "#888888"

    # 중앙 카드 프레임
    card = tk.Frame(root, bg=bg, padx=40, pady=30,
                    highlightbackground="#333355", highlightthickness=2)
    card.place(relx=0.5, rely=0.5, anchor="center")

    # 제목
    tk.Label(
        card, text="신규 업데이트가 있습니다!",
        font=("맑은 고딕", 18, "bold"), fg="#ffffff", bg=bg,
    ).pack(pady=(10, 20))

    # 버전 정보 카드
    ver_frame = tk.Frame(card, bg=card_bg, padx=24, pady=16,
                         highlightbackground="#333355", highlightthickness=1)
    ver_frame.pack(fill="x", pady=(0, 16))

    tk.Label(
        ver_frame, text=f"현재 버전:  v{current_ver}",
        font=("맑은 고딕", 12), fg=dim, bg=card_bg, anchor="w",
    ).pack(fill="x")
    tk.Label(
        ver_frame, text=f"최신 버전:  v{latest_ver}",
        font=("맑은 고딕", 12, "bold"), fg=green, bg=card_bg, anchor="w",
    ).pack(fill="x", pady=(6, 0))

    # 변경 사항
    if changelog:
        tk.Label(
            card, text="변경 사항",
            font=("맑은 고딕", 11, "bold"), fg=text_color, bg=bg, anchor="w",
        ).pack(fill="x", pady=(4, 6))

        log_frame = tk.Frame(card, bg=card_bg, padx=14, pady=12,
                             highlightbackground="#333355", highlightthickness=1)
        log_frame.pack(fill="x", pady=(0, 16))

        log_text = tk.Text(
            log_frame, height=5, wrap="word", bg=card_bg, fg=text_color,
            font=("맑은 고딕", 10), relief="flat", borderwidth=0, width=50,
        )
        log_text.insert("1.0", changelog)
        log_text.config(state="disabled")
        log_text.pack(fill="x")

    # 프로그레스 바 (처음엔 숨김)
    progress_frame = tk.Frame(card, bg=bg)
    progress_label = tk.Label(progress_frame, text="", font=("맑은 고딕", 10), fg=text_color, bg=bg)
    progress_label.pack(fill="x", pady=(0, 6))
    progress_bar = ttk.Progressbar(progress_frame, length=420, mode="determinate")
    progress_bar.pack(fill="x")

    # 버튼 프레임
    btn_frame = tk.Frame(card, bg=bg)
    btn_frame.pack(fill="x", pady=(10, 0))

    def on_update():
        result["do_update"] = True
        update_btn.config(state="disabled", text="업데이트 중...")
        btn_frame.pack_forget()
        progress_frame.pack(fill="x", pady=(10, 0))
        threading.Thread(target=_run_update, daemon=True).start()

    def _run_update():
        """업데이트 실행 (공통 로직 사용)"""
        try:
            from src.updater import download_and_apply

            download_and_apply(update_info, progress_callback=_update_label)

            _update_label("앱을 재시작합니다...", 100)
            time.sleep(1.5)

            root.after(0, root.destroy)
            _restart_after_update()

        except Exception as e:
            _update_label(f"업데이트 오류: {str(e)}", 0)
            _show_error_and_continue()

    def _update_label(text, pct):
        """스레드 안전하게 UI 업데이트"""
        def _do():
            progress_label.config(text=text)
            progress_bar["value"] = pct
        root.after(0, _do)

    def _show_error_and_continue():
        """에러 발생 시 재시도 버튼 표시"""
        def _do():
            progress_label.config(fg="#ef4444")
            retry_btn = tk.Button(
                progress_frame, text="재시도", font=("맑은 고딕", 11, "bold"),
                bg=accent, fg="#ffffff", relief="flat", padx=20, pady=6,
                cursor="hand2", command=lambda: [retry_btn.destroy(), on_update()],
            )
            retry_btn.pack(pady=(10, 0))
        root.after(0, _do)

    # 업데이트 버튼
    update_btn = tk.Button(
        btn_frame, text="업데이트 하기", font=("맑은 고딕", 13, "bold"),
        bg=accent, fg="#ffffff", activebackground="#6d28d9", activeforeground="#ffffff",
        relief="flat", padx=30, pady=10, cursor="hand2", command=on_update,
    )
    update_btn.pack(expand=True)

    # 안내 문구
    tk.Label(
        card, text="※ 업데이트를 완료해야 프로그램을 사용할 수 있습니다.",
        font=("맑은 고딕", 9), fg="#ef4444", bg=bg,
    ).pack(pady=(12, 0))

    # ESC/X 버튼 → 프로그램 종료
    def on_close():
        root.destroy()
        sys.exit(0)

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.bind("<Escape>", lambda e: on_close())

    # 포커스
    root.focus_force()

    root.mainloop()

    return result["do_update"]


def _restart_after_update():
    """업데이트 완료 후 앱 재시작"""
    import subprocess

    app_root = os.path.dirname(os.path.abspath(__file__))

    if sys.platform == "win32":
        # EXE로 실행 중인 경우
        exe_path = sys.executable
        if exe_path.lower().endswith((".exe",)) and "python" not in exe_path.lower():
            subprocess.Popen([exe_path], cwd=app_root,
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            # python desktop.py로 실행 중
            start_bat = os.path.join(app_root, "start.bat")
            if os.path.exists(start_bat):
                subprocess.Popen(["cmd", "/c", "start", "", start_bat],
                                 cwd=app_root, creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:
                subprocess.Popen([sys.executable, __file__],
                                 cwd=app_root, creationflags=subprocess.CREATE_NEW_CONSOLE)
    else:
        subprocess.Popen([sys.executable, __file__], cwd=app_root)

    time.sleep(0.5)
    os._exit(0)


def main():
    # ── 1단계: 업데이트 체크 ──
    _log.info(f"앱 시작 (APP_ROOT: {_APP_ROOT})")
    try:
        updated = check_and_show_update()
        if updated:
            return  # 업데이트 후 재시작됨
    except Exception as e:
        _log.error(f"업데이트 체크 오류: {e}", exc_info=True)

    # ── 2단계: 앱 실행 ──
    try:
        import webview
    except ImportError:
        print("[오류] pywebview가 설치되지 않았습니다.")
        print("  설치: pip install pywebview")
        input("Enter를 눌러 종료...")
        sys.exit(1)

    port = find_free_port()
    url = f"http://127.0.0.1:{port}"

    print(f"[CommentBoost] 데스크탑 앱 시작 중... (포트: {port})")

    # Flask 서버를 데몬 스레드로 실행
    flask_thread = threading.Thread(target=start_flask, args=(port,), daemon=True)
    flask_thread.start()

    # 서버 준비 대기
    if not wait_for_server(port):
        print("[오류] Flask 서버 시작 실패")
        sys.exit(1)

    print(f"[CommentBoost] 서버 준비 완료: {url}")

    # PyWebView 네이티브 창 생성
    window = webview.create_window(
        title="CommentBoost - 댓글 부스터",
        url=url,
        width=1400,
        height=900,
        min_size=(1024, 680),
        resizable=True,
        confirm_close=True,
        text_select=True,
    )

    # 결제 시 외부 브라우저로 열기 위한 JS API
    def open_external(url):
        """외부 브라우저에서 URL 열기 (결제 페이지 등)"""
        import webbrowser
        webbrowser.open(url)

    window.expose(open_external)

    print("[CommentBoost] 앱 창 열기...")
    webview.start(debug=False)
    print("[CommentBoost] 앱 종료")


if __name__ == "__main__":
    main()
