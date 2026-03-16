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

# Windows 한국어 환경 (cp949) 인코딩 문제 해결
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for _stream in (sys.stdout, sys.stderr):
        if _stream and hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

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


def _load_updater():
    """
    src/updater.py를 파일시스템에서 직접 로드.
    PyInstaller 번들이 옛날 코드를 캐시하는 문제를 우회.
    """
    import importlib.util
    updater_path = os.path.join(_APP_ROOT, "src", "updater.py")
    if not os.path.exists(updater_path):
        _log.error(f"updater.py 없음: {updater_path}")
        return None
    spec = importlib.util.spec_from_file_location("src.updater", updater_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


def _force_load_from_filesystem():
    """
    PyInstaller EXE에서 번들 캐시를 우회하고 디스크의 최신 코드를 로드.
    업데이트 후 새 코드가 반영되려면 이 과정이 필수.
    """
    if not getattr(sys, 'frozen', False):
        return  # 개발 환경에서는 불필요

    import importlib.util

    src_dir = os.path.join(_APP_ROOT, "src")
    app_path = os.path.join(_APP_ROOT, "app.py")

    if not os.path.exists(app_path):
        _log.warning(f"app.py가 디스크에 없음: {app_path}")
        return

    _log.info("PyInstaller 번들 우회 - 디스크에서 최신 코드 로드")

    # 1. 기존 번들 모듈 제거
    for mod_name in list(sys.modules.keys()):
        if mod_name == "app" or mod_name == "src" or mod_name.startswith("src."):
            del sys.modules[mod_name]

    # 2. src 패키지를 파일시스템에서 로드
    src_init = os.path.join(src_dir, "__init__.py")
    if os.path.exists(src_init):
        spec = importlib.util.spec_from_file_location(
            "src", src_init,
            submodule_search_locations=[src_dir],
        )
        src_mod = importlib.util.module_from_spec(spec)
        sys.modules["src"] = src_mod
        spec.loader.exec_module(src_mod)

    # 3. src 하위 모듈을 파일시스템에서 로드
    if os.path.isdir(src_dir):
        for fname in sorted(os.listdir(src_dir)):
            if fname.endswith(".py") and fname != "__init__.py":
                mod_name = f"src.{fname[:-3]}"
                fpath = os.path.join(src_dir, fname)
                try:
                    spec = importlib.util.spec_from_file_location(mod_name, fpath)
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[mod_name] = mod
                    spec.loader.exec_module(mod)
                except Exception as e:
                    _log.warning(f"모듈 로드 실패 {mod_name}: {e}")

    # 4. app.py를 파일시스템에서 로드
    try:
        spec = importlib.util.spec_from_file_location("app", app_path)
        app_mod = importlib.util.module_from_spec(spec)
        sys.modules["app"] = app_mod
        spec.loader.exec_module(app_mod)
        _log.info("app.py 파일시스템 로드 완료")
    except Exception as e:
        _log.error(f"app.py 로드 실패: {e}", exc_info=True)
        raise


def start_flask(port):
    """Flask 서버를 별도 스레드에서 실행"""
    # PyInstaller EXE에서는 번들된 코드 대신 디스크의 최신 코드 사용
    _force_load_from_filesystem()

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
    updater = _load_updater()
    if not updater:
        return False

    _log.info("업데이트 확인 중...")
    update_info = updater.check_for_updates()
    _log.info(f"서버 응답: {update_info}")

    if not update_info.get("needs_update"):
        if update_info.get("error"):
            _log.warning(f"확인 실패: {update_info['error']}")
        else:
            _log.info("최신 버전입니다.")
        return False

    current = updater.get_current_version()
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

    # 일반 윈도우 (타이틀바 + 닫기 버튼 있음)
    root = tk.Tk()
    root.title("CommentBoost 업데이트")
    root.resizable(False, False)
    root.configure(bg="#1a1a2e")

    # 창 너비 고정, 높이는 콘텐츠에 맞게 자동 조절
    win_w = 500
    root.update_idletasks()
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()

    def _center_window():
        root.update_idletasks()
        win_h = root.winfo_reqheight()
        x = (screen_w - win_w) // 2
        y = (screen_h - win_h) // 2
        root.geometry(f"{win_w}x{win_h}+{x}+{y}")

    root.after(50, _center_window)

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

    # 메인 프레임
    main_frame = tk.Frame(root, bg=bg, padx=30, pady=20)
    main_frame.pack(fill="both", expand=True)

    # 제목
    tk.Label(
        main_frame, text="신규 업데이트가 있습니다!",
        font=("맑은 고딕", 16, "bold"), fg="#ffffff", bg=bg,
    ).pack(pady=(10, 16))

    # 버전 정보 카드
    ver_frame = tk.Frame(main_frame, bg=card_bg, padx=20, pady=14,
                         highlightbackground="#333355", highlightthickness=1)
    ver_frame.pack(fill="x", pady=(0, 12))

    tk.Label(
        ver_frame, text=f"현재 버전:  v{current_ver}",
        font=("맑은 고딕", 11), fg=dim, bg=card_bg, anchor="w",
    ).pack(fill="x")
    tk.Label(
        ver_frame, text=f"최신 버전:  v{latest_ver}",
        font=("맑은 고딕", 11, "bold"), fg=green, bg=card_bg, anchor="w",
    ).pack(fill="x", pady=(4, 0))

    # 변경 사항
    if changelog:
        tk.Label(
            main_frame, text="변경 사항",
            font=("맑은 고딕", 10, "bold"), fg=text_color, bg=bg, anchor="w",
        ).pack(fill="x", pady=(4, 4))

        log_frame = tk.Frame(main_frame, bg=card_bg, padx=12, pady=10,
                             highlightbackground="#333355", highlightthickness=1)
        log_frame.pack(fill="x", pady=(0, 12))

        log_text = tk.Text(
            log_frame, height=5, wrap="word", bg=card_bg, fg=text_color,
            font=("맑은 고딕", 10), relief="flat", borderwidth=0,
        )
        log_text.insert("1.0", changelog)
        log_text.config(state="disabled")
        log_text.pack(fill="x")

    # 프로그레스 바 (처음엔 숨김)
    progress_frame = tk.Frame(main_frame, bg=bg)
    progress_label = tk.Label(progress_frame, text="", font=("맑은 고딕", 10), fg=text_color, bg=bg)
    progress_label.pack(fill="x", pady=(0, 4))
    progress_bar = ttk.Progressbar(progress_frame, length=400, mode="determinate")
    progress_bar.pack(fill="x")

    # 버튼 프레임
    btn_frame = tk.Frame(main_frame, bg=bg)
    btn_frame.pack(fill="x", pady=(8, 0))

    def on_update():
        result["do_update"] = True
        update_btn.config(state="disabled", text="업데이트 중...")
        btn_frame.pack_forget()
        progress_frame.pack(fill="x", pady=(8, 0))
        threading.Thread(target=_run_update, daemon=True).start()

    def _run_update():
        """업데이트 다운로드 → 적용 → 재시작 (외부 모듈 의존 없이 직접 실행)"""
        try:
            import requests
            import tempfile
            import zipfile
            import shutil

            # 서버 URL
            server_url = os.environ.get(
                "UPDATE_SERVER_URL", "https://commentboost-app.fly.dev")
            download_filename = update_info.get(
                "download_url", "commentboost-latest.zip")
            download_url = f"{server_url}/download/{download_filename}"

            # 1. ZIP 다운로드 (최대 3회 재시도)
            _update_label("업데이트 파일 다운로드 중...", 10)
            tmp_dir = tempfile.mkdtemp(prefix="commentboost_update_")
            zip_path = os.path.join(tmp_dir, "update.zip")

            for attempt in range(3):
                try:
                    resp = requests.get(download_url, stream=True, timeout=180)
                    if resp.status_code != 200:
                        raise RuntimeError(f"HTTP {resp.status_code}")

                    total_size = int(resp.headers.get("content-length", 0))
                    downloaded = 0
                    with open(zip_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=32768):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total_size > 0:
                                pct = int(10 + (downloaded / total_size) * 40)
                                _update_label(
                                    f"다운로드 중... {downloaded // 1024}KB / {total_size // 1024}KB",
                                    min(pct, 50))

                    # 다운로드 완료 검증
                    if total_size > 0 and downloaded < total_size:
                        raise RuntimeError(f"불완전한 다운로드 ({downloaded}/{total_size})")
                    break  # 성공

                except Exception as dl_err:
                    if attempt < 2:
                        wait = (attempt + 1) * 3
                        _update_label(f"다운로드 재시도 {attempt + 2}/3... ({wait}초 대기)", 10)
                        time.sleep(wait)
                    else:
                        shutil.rmtree(tmp_dir, ignore_errors=True)
                        raise RuntimeError(f"다운로드 실패 (3회 시도): {dl_err}")

            # 2. 압축 해제
            _update_label("압축 해제 중...", 55)
            extract_dir = os.path.join(tmp_dir, "extracted")
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)

            entries = os.listdir(extract_dir)
            if len(entries) == 1 and os.path.isdir(
                    os.path.join(extract_dir, entries[0])):
                source_dir = os.path.join(extract_dir, entries[0])
            else:
                source_dir = extract_dir

            # 3. 파일 적용 (보존 대상 제외)
            _update_label("파일 업데이트 중...", 65)
            preserve = {".env", ".env.local", ".license", "config", "data",
                        "cert.pem", "key.pem", "desktop.ini"}
            preserve_ext = {".db", ".sqlite", ".sqlite3"}

            for root_d, dirs, files in os.walk(source_dir):
                rel_root = os.path.relpath(root_d, source_dir)
                if rel_root == ".":
                    rel_root = ""
                top = rel_root.split(os.sep)[0] if rel_root else ""
                if top in preserve:
                    continue
                target_root = os.path.join(
                    _APP_ROOT, rel_root) if rel_root else _APP_ROOT
                os.makedirs(target_root, exist_ok=True)
                for fn in files:
                    rel_file = os.path.join(rel_root, fn) if rel_root else fn
                    if rel_file in preserve or fn in preserve:
                        continue
                    _, ext = os.path.splitext(fn)
                    if ext.lower() in preserve_ext:
                        continue
                    try:
                        shutil.copy2(
                            os.path.join(root_d, fn),
                            os.path.join(target_root, fn))
                    except (PermissionError, OSError):
                        pass

            # 4. version.json을 서버 최신 버전으로 갱신 (무한 루프 방지)
            local_ver_path = os.path.join(_APP_ROOT, "version.json")
            try:
                import json as _json
                new_ver = {
                    "version": update_info.get("latest_version", "1.0.0"),
                    "build": update_info.get("latest_build", 0),
                    "release_date": update_info.get("release_date", ""),
                }
                with open(local_ver_path, "w", encoding="utf-8") as vf:
                    _json.dump(new_ver, vf, ensure_ascii=False, indent=4)
                _log.info(f"version.json 갱신: v{new_ver['version']}")
            except Exception as ve:
                _log.warning(f"version.json 갱신 실패: {ve}")

            # 5. 정리
            _update_label("정리 중...", 90)
            shutil.rmtree(tmp_dir, ignore_errors=True)

            _update_label("앱을 재시작합니다...", 100)
            time.sleep(1.5)

            root.after(0, root.destroy)
            _restart_after_update()

        except Exception as e:
            import traceback
            err_detail = traceback.format_exc()
            _log.error(f"업데이트 실행 오류: {e}", exc_info=True)
            _update_label(f"업데이트 오류: {str(e)}", 0)
            _show_error_and_continue(err_detail)

    def _update_label(text, pct):
        """스레드 안전하게 UI 업데이트"""
        def _do():
            progress_label.config(text=text)
            progress_bar["value"] = pct
        root.after(0, _do)

    def _show_error_and_continue(err_detail=""):
        """에러 발생 시 전체 에러 로그 + 재시도/종료 버튼 표시"""
        def _do():
            progress_label.config(fg="#ef4444")

            # 에러 상세 로그 (복사 가능한 Text 위젯)
            if err_detail:
                err_text = tk.Text(
                    progress_frame, height=6, wrap="word",
                    bg="#1a0a0a", fg="#ff6b6b", font=("Consolas", 9),
                    relief="flat", borderwidth=1, highlightbackground="#ef4444",
                    highlightthickness=1,
                )
                err_text.insert("1.0", err_detail)
                err_text.config(state="normal")  # 복사 가능하도록 유지
                err_text.pack(fill="x", pady=(8, 0))

                # 창 너비를 에러 메시지에 맞게 확장
                root.update_idletasks()
                needed_w = max(600, root.winfo_reqwidth())
                root.geometry(f"{needed_w}x{root.winfo_reqheight()}")
                _center_window()

            err_btn_frame = tk.Frame(progress_frame, bg=bg)
            err_btn_frame.pack(pady=(10, 0))
            tk.Button(
                err_btn_frame, text="재시도", font=("맑은 고딕", 11, "bold"),
                bg=accent, fg="#ffffff", relief="flat", padx=20, pady=6,
                cursor="hand2",
                command=lambda: [err_btn_frame.destroy(), on_update()],
            ).pack(side="left", padx=(0, 10))
            tk.Button(
                err_btn_frame, text="종료", font=("맑은 고딕", 11),
                bg="#333355", fg="#ffffff", relief="flat", padx=20, pady=6,
                cursor="hand2", command=on_close,
            ).pack(side="left")
        root.after(0, _do)

    # 업데이트 버튼
    update_btn = tk.Button(
        btn_frame, text="업데이트 하기", font=("맑은 고딕", 12, "bold"),
        bg=accent, fg="#ffffff", activebackground="#6d28d9", activeforeground="#ffffff",
        relief="flat", padx=24, pady=8, cursor="hand2", command=on_update,
    )
    update_btn.pack(expand=True)

    # 안내 문구
    tk.Label(
        main_frame, text="※ 업데이트를 완료해야 프로그램을 사용할 수 있습니다.",
        font=("맑은 고딕", 9), fg="#ef4444", bg=bg,
    ).pack(pady=(8, 0))

    # X 버튼 → 프로그램 종료
    def on_close():
        root.destroy()
        sys.exit(0)

    root.protocol("WM_DELETE_WINDOW", on_close)

    # 포커스 & 항상 위
    root.attributes("-topmost", True)
    root.after(200, lambda: root.attributes("-topmost", False))
    root.lift()
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
