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

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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


def main():
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
    class DesktopAPI:
        def open_external(self, url):
            """외부 브라우저에서 URL 열기 (결제 페이지 등)"""
            import webbrowser
            webbrowser.open(url)

    window.expose(DesktopAPI())

    print("[CommentBoost] 앱 창 열기...")
    webview.start(debug=False)
    print("[CommentBoost] 앱 종료")


if __name__ == "__main__":
    main()
