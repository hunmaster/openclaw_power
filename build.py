"""
CommentBoost 데스크탑 앱 빌드 스크립트 (PyInstaller)

사용법:
    python build.py

결과물:
    dist/CommentBoost/ (폴더) → zip으로 압축하여 배포
"""

import os
import sys
import subprocess
import shutil
import json
import time
import tempfile

APP_NAME = "CommentBoost"
ENTRY_POINT = "desktop.py"
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION_FILE = os.path.join(_SCRIPT_DIR, "version.json")

# PyInstaller에 포함할 데이터 파일/폴더
DATA_FILES = [
    ("templates", "templates"),
    ("static", "static"),
    ("version.json", "."),
]

# 숨겨진 import (PyInstaller가 자동 감지 못하는 모듈)
HIDDEN_IMPORTS = [
    "flask",
    "flask_login",
    "flask_sqlalchemy",
    "sqlalchemy",
    "dotenv",
    "requests",
    "notion_client",
    "rich",
    "webview",
]

# 제외할 모듈 (빌드 크기 줄이기)
EXCLUDES = [
    # tkinter는 업데이트 팝업에서 사용하므로 제외하면 안 됨
    "matplotlib",
    "numpy",
    "pandas",
    "scipy",
    "PIL",
    "cv2",
    "pytest",
]


def _remove_readonly_recursive(path):
    """Windows에서 READONLY/SYSTEM/HIDDEN 속성 재귀 해제 (삭제 전 필수)"""
    import stat
    for root, dirs, files in os.walk(path):
        for name in files:
            fp = os.path.join(root, name)
            try:
                os.chmod(fp, stat.S_IWRITE)
            except Exception:
                pass
        for name in dirs:
            dp = os.path.join(root, name)
            try:
                os.chmod(dp, stat.S_IWRITE)
            except Exception:
                pass
    try:
        os.chmod(path, stat.S_IWRITE)
    except Exception:
        pass


def get_version():
    """version.json에서 버전 정보 읽기"""
    try:
        with open(VERSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("version", "1.0.0")
    except FileNotFoundError:
        return "1.0.0"


def build():
    """PyInstaller로 .exe 빌드"""
    # 스크립트 위치 기준으로 작업 디렉토리 설정
    os.chdir(_SCRIPT_DIR)
    version = get_version()
    print(f"[빌드] {APP_NAME} v{version} 빌드 시작...")

    # 이전 빌드 정리 (사용자 데이터 보존)
    # dist/CommentBoost/data/ 는 사용자 DB가 있으므로 절대 삭제하지 않음
    user_data_dir = os.path.join("dist", APP_NAME, "data")
    user_data_backup = None
    if os.path.exists(user_data_dir):
        user_data_backup = os.path.join(tempfile.gettempdir(), f"commentboost_data_backup_{int(time.time())}")
        shutil.copytree(user_data_dir, user_data_backup)
        print(f"[빌드] 사용자 데이터 백업 완료: {user_data_backup}")

    for d in ["build", "dist"]:
        if os.path.exists(d):
            for attempt in range(3):
                try:
                    # Windows: READONLY 속성 해제 후 삭제
                    if sys.platform == "win32":
                        _remove_readonly_recursive(d)
                    shutil.rmtree(d)
                    print(f"[빌드] {d}/ 폴더 정리")
                    break
                except PermissionError as e:
                    if attempt < 2:
                        print(f"[빌드] {d}/ 폴더가 잠겨있습니다. 재시도 {attempt + 1}/3... (3초 대기)")
                        # Windows에서 잠긴 파일 해제 시도
                        if sys.platform == "win32":
                            subprocess.run(
                                ["taskkill", "/F", "/IM", f"{APP_NAME}.exe"],
                                capture_output=True,
                            )
                        time.sleep(3)
                    else:
                        print(f"[빌드] 오류: {d}/ 폴더 삭제 불가 - {e}")
                        print(f"[빌드] {APP_NAME}.exe가 실행 중이면 종료 후 다시 시도하세요.")
                        sys.exit(1)
                except Exception as e:
                    print(f"[빌드] {d}/ 폴더 정리 실패: {e}")
                    break

    # 임시 distpath 사용 (Windows에서 백신/인덱서가 .pyd 파일을 잠그는 COLLECT 단계 PermissionError 방지)
    tmp_dist = tempfile.mkdtemp(prefix="commentboost_dist_")
    print(f"[빌드] 임시 출력 경로: {tmp_dist}")

    # PyInstaller 명령 구성
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--noconfirm",
        "--clean",
        # 임시 경로로 출력 (COLLECT 단계 PermissionError 방지)
        "--distpath", tmp_dist,
        # 폴더 모드 (onedir) - Playwright 브라우저 포함 위해
        "--onedir",
        # 콘솔 창 숨기기
        "--noconsole",
        # 앱 아이콘
        "--icon", "app_icon.ico",
    ]

    # 데이터 파일 추가
    for src, dst in DATA_FILES:
        if os.path.exists(src):
            cmd.extend(["--add-data", f"{src}{os.pathsep}{dst}"])

    # config, data 디렉토리는 런타임에 생성하므로 포함하지 않음
    # .env는 사용자가 직접 설정

    # 숨겨진 import
    for mod in HIDDEN_IMPORTS:
        cmd.extend(["--hidden-import", mod])

    # 제외 모듈
    for mod in EXCLUDES:
        cmd.extend(["--exclude-module", mod])

    # 엔트리포인트
    cmd.append(ENTRY_POINT)

    print(f"[빌드] 명령: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=os.path.dirname(os.path.abspath(__file__)))

    if result.returncode != 0:
        # 임시 폴더 정리
        shutil.rmtree(tmp_dist, ignore_errors=True)
        print("[빌드] 빌드 실패!")
        sys.exit(1)

    # 임시 출력을 dist/로 이동
    final_dist = os.path.join("dist", APP_NAME)
    tmp_app_dir = os.path.join(tmp_dist, APP_NAME)
    os.makedirs("dist", exist_ok=True)
    shutil.move(tmp_app_dir, final_dist)
    shutil.rmtree(tmp_dist, ignore_errors=True)
    print(f"[빌드] 빌드 결과를 dist/{APP_NAME}/으로 이동 완료")

    dist_dir = final_dist

    # src/ 폴더 복사 (소스 코드)
    src_dst = os.path.join(dist_dir, "src")
    if os.path.exists("src"):
        shutil.copytree("src", src_dst, dirs_exist_ok=True)
        print("[빌드] src/ 폴더 복사 완료")

    # app.py 복사 (Flask 앱)
    shutil.copy2("app.py", dist_dir)
    print("[빌드] app.py 복사 완료")

    # .env.example 복사
    if os.path.exists(".env.example"):
        shutil.copy2(".env.example", os.path.join(dist_dir, ".env.example"))

    # config/, data/ 디렉토리 생성
    os.makedirs(os.path.join(dist_dir, "config"), exist_ok=True)
    os.makedirs(os.path.join(dist_dir, "data"), exist_ok=True)

    # 사용자 데이터 복원 (빌드 전 백업했던 data/ 폴더)
    if user_data_backup and os.path.exists(user_data_backup):
        restored_data_dir = os.path.join(dist_dir, "data")
        shutil.copytree(user_data_backup, restored_data_dir, dirs_exist_ok=True)
        shutil.rmtree(user_data_backup, ignore_errors=True)
        print("[빌드] 사용자 데이터 복원 완료 (users.db, 설정 등 보존)")

    # config 예제 파일 복사
    for f in ["accounts.example.json", "proxies.example.txt"]:
        src_path = os.path.join("config", f)
        if os.path.exists(src_path):
            shutil.copy2(src_path, os.path.join(dist_dir, "config", f))

    # version.json을 EXE 옆에 복사 (updater가 참조)
    if os.path.exists(VERSION_FILE):
        shutil.copy2(VERSION_FILE, os.path.join(dist_dir, "version.json"))
        print("[빌드] version.json 복사 완료")

    # install_adb.bat 복사
    if os.path.exists("install_adb.bat"):
        shutil.copy2("install_adb.bat", os.path.join(dist_dir, "install_adb.bat"))
        print("[빌드] install_adb.bat 복사 완료")

    # app_icon.ico 복사 (EXE 아이콘은 PyInstaller --icon으로 설정됨, 폴더 아이콘은 기본 유지)
    if os.path.exists("app_icon.ico"):
        shutil.copy2("app_icon.ico", os.path.join(dist_dir, "app_icon.ico"))

    # 업데이트 배포용 ZIP 자동 생성
    # ZIP에 사용자 데이터(DB 등)가 포함되면 업데이트 시 빈 DB로 덮어쓸 수 있으므로 제외
    dist_data_dir = os.path.join(dist_dir, "data")
    dist_data_backup_for_zip = None
    if os.path.exists(dist_data_dir) and os.listdir(dist_data_dir):
        dist_data_backup_for_zip = os.path.join(tempfile.gettempdir(), f"commentboost_zip_data_{int(time.time())}")
        shutil.move(dist_data_dir, dist_data_backup_for_zip)
        os.makedirs(dist_data_dir, exist_ok=True)  # 빈 data/ 폴더 유지

    zip_name = "commentboost-latest"
    zip_path = shutil.make_archive(
        os.path.join("dist", zip_name), "zip", "dist", APP_NAME
    )
    print(f"[빌드] 업데이트 ZIP 생성: {zip_path}")

    # ZIP 생성 후 사용자 데이터 복원
    if dist_data_backup_for_zip and os.path.exists(dist_data_backup_for_zip):
        shutil.rmtree(dist_data_dir, ignore_errors=True)
        shutil.move(dist_data_backup_for_zip, dist_data_dir)

    # landing/releases/ 에 자동 복사 (Fly.io 배포 시 포함됨)
    releases_dir = os.path.join("landing", "releases")
    os.makedirs(releases_dir, exist_ok=True)
    release_dest = os.path.join(releases_dir, f"{zip_name}.zip")
    shutil.copy2(zip_path, release_dest)
    print(f"[빌드] 릴리즈 복사: {release_dest}")

    # landing/version.json 자동 동기화
    landing_ver_path = os.path.join("landing", "version.json")
    with open(VERSION_FILE, "r", encoding="utf-8") as f:
        ver_data = json.load(f)
    ver_data["download_url"] = f"{zip_name}.zip"
    with open(landing_ver_path, "w", encoding="utf-8") as f:
        json.dump(ver_data, f, ensure_ascii=False, indent=4)
    print(f"[빌드] landing/version.json 동기화: v{ver_data.get('version')}")

    # 완료 안내
    print()
    print("=" * 60)
    print(f"[빌드] 완료: dist/{APP_NAME}/")
    print("=" * 60)
    print()
    print("배포 방법:")
    print(f"  1. cd landing && fly deploy   (Fly.io 랜딩 서버 배포)")
    print(f"  2. 사용자가 EXE 실행 → 자동 업데이트 팝업 표시")
    print()
    print("수동 배포:")
    print(f"  dist/{zip_name}.zip 파일을 직접 배포해도 됩니다.")
    print()
    print("주의: Playwright Chromium은 사용자 PC에서 최초 실행 시 자동 설치됩니다.")


if __name__ == "__main__":
    build()
