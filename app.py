"""
YouTube 댓글 자동화 - 웹 대시보드 (Flask)

기능:
- 대시보드: 작업 현황, 계정 상태, SMM 잔액
- 작업 관리: 노션 DB 작업 목록 조회/실행
- 자동화 실행: 백그라운드에서 댓글 작업 실행
- SMM 관리: 서비스 조회, 주문 상태 확인
- 설정: 환경 변수 조회/수정
"""

import os
import sys
import json
import threading
from datetime import datetime

from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

load_dotenv()

from src.notion_client import NotionManager
from src.proxy_manager import ProxyManager
from src.fingerprint import FingerprintManager
from src.safety_rules import SafetyRules
from src.smm_client import SMMClient

app = Flask(__name__)

# 글로벌 상태
automation_state = {
    "running": False,
    "current_task": None,
    "progress": 0,
    "total": 0,
    "logs": [],
    "results": {"success": 0, "fail": 0, "skip": 0, "likes": 0},
}
automation_lock = threading.Lock()


def add_log(message, level="info"):
    """로그 메시지를 추가합니다."""
    automation_state["logs"].append({
        "time": datetime.now().strftime("%H:%M:%S"),
        "message": message,
        "level": level,
    })
    # 최근 200개만 유지
    if len(automation_state["logs"]) > 200:
        automation_state["logs"] = automation_state["logs"][-200:]


def load_accounts():
    """계정 파일 로드."""
    accounts_file = os.getenv("ACCOUNTS_FILE", "config/accounts.json")
    if os.path.exists(accounts_file):
        with open(accounts_file, "r") as f:
            return json.load(f)
    return []


# ──────────────────────────── 페이지 라우트 ────────────────────────────

@app.route("/")
def dashboard():
    """메인 대시보드."""
    return render_template("dashboard.html")


# ──────────────────────────── API 라우트 ────────────────────────────

@app.route("/api/status")
def api_status():
    """현재 자동화 상태를 반환합니다."""
    return jsonify(automation_state)


@app.route("/api/dashboard")
def api_dashboard():
    """대시보드 데이터를 반환합니다."""
    safety_rules = SafetyRules()
    accounts = load_accounts()
    smm = SMMClient()

    # 계정별 현황
    account_stats = []
    for acc in accounts:
        label = acc.get("label", acc.get("email", "unknown"))
        status = safety_rules.get_account_status(label)
        account_stats.append({
            "label": label,
            "email": acc.get("email", ""),
            "type": acc.get("account_type", "unknown"),
            "today_count": status["today_count"],
            "max_count": status["max_count"],
            "remaining": status["remaining"],
        })

    # SMM 잔액
    smm_balance = None
    if smm.enabled:
        try:
            smm_balance = smm.get_balance()
        except Exception:
            smm_balance = None

    # 프록시 상태
    proxy = ProxyManager()

    return jsonify({
        "accounts": account_stats,
        "account_count": len(accounts),
        "smm_enabled": smm.enabled,
        "smm_balance": smm_balance,
        "proxy_status": proxy.get_status(),
        "settings": {
            "max_comments_per_day": int(os.getenv("MAX_COMMENTS_PER_DAY", "20")),
            "comment_interval_sec": int(os.getenv("COMMENT_INTERVAL_SEC", "180")),
            "same_video_interval_min": int(os.getenv("SAME_VIDEO_INTERVAL_MIN", "30")),
            "smm_like_quantity": int(os.getenv("SMM_LIKE_QUANTITY", "10")),
        },
    })


@app.route("/api/tasks")
def api_tasks():
    """노션 DB에서 대기 중인 작업 목록을 가져옵니다."""
    try:
        notion = NotionManager()
        tasks = notion.get_pending_tasks()
        return jsonify({"tasks": tasks, "count": len(tasks)})
    except Exception as e:
        return jsonify({"error": str(e), "tasks": [], "count": 0}), 500


@app.route("/api/run", methods=["POST"])
def api_run():
    """자동화를 백그라운드에서 시작합니다."""
    with automation_lock:
        if automation_state["running"]:
            return jsonify({"error": "이미 실행 중입니다."}), 409

        automation_state["running"] = True
        automation_state["progress"] = 0
        automation_state["logs"] = []
        automation_state["results"] = {"success": 0, "fail": 0, "skip": 0, "likes": 0}

    thread = threading.Thread(target=_run_automation, daemon=True)
    thread.start()

    return jsonify({"message": "자동화가 시작되었습니다."})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    """자동화를 중지합니다."""
    automation_state["running"] = False
    add_log("사용자가 자동화를 중지했습니다.", "warning")
    return jsonify({"message": "중지 요청이 전송되었습니다."})


@app.route("/api/smm/services")
def api_smm_services():
    """SMM 서비스 목록을 조회합니다."""
    try:
        smm = SMMClient()
        if not smm.enabled:
            return jsonify({"error": "SMM이 비활성 상태입니다.", "services": []}), 400

        services = smm.get_services()
        # YouTube 관련 서비스 필터링
        youtube_services = [
            s for s in services
            if "youtube" in s.get("name", "").lower()
            or "youtube" in s.get("category", "").lower()
        ]
        return jsonify({
            "services": youtube_services,
            "all_count": len(services),
            "youtube_count": len(youtube_services),
        })
    except Exception as e:
        return jsonify({"error": str(e), "services": []}), 500


@app.route("/api/smm/balance")
def api_smm_balance():
    """SMM 잔액을 조회합니다."""
    try:
        smm = SMMClient()
        balance = smm.get_balance()
        return jsonify({"balance": balance})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    """현재 설정값을 반환합니다."""
    return jsonify({
        "NOTION_DATABASE_ID": os.getenv("NOTION_DATABASE_ID", ""),
        "MAX_COMMENTS_PER_DAY": os.getenv("MAX_COMMENTS_PER_DAY", "20"),
        "COMMENT_INTERVAL_SEC": os.getenv("COMMENT_INTERVAL_SEC", "180"),
        "SAME_VIDEO_INTERVAL_MIN": os.getenv("SAME_VIDEO_INTERVAL_MIN", "30"),
        "HEADLESS": os.getenv("HEADLESS", "false"),
        "USE_PROXY": os.getenv("USE_PROXY", "false"),
        "SMM_ENABLED": os.getenv("SMM_ENABLED", "false"),
        "SMM_LIKE_QUANTITY": os.getenv("SMM_LIKE_QUANTITY", "10"),
        "SMM_LIKE_SERVICE_ID": os.getenv("SMM_LIKE_SERVICE_ID", ""),
    })


@app.route("/api/accounts", methods=["GET"])
def api_get_accounts():
    """계정 목록을 반환합니다."""
    accounts = load_accounts()
    safe_accounts = []
    for acc in accounts:
        safe_accounts.append({
            "email": acc.get("email", ""),
            "label": acc.get("label", ""),
            "account_type": acc.get("account_type", ""),
        })
    return jsonify({"accounts": safe_accounts})


@app.route("/api/accounts", methods=["POST"])
def api_add_account():
    """계정을 추가합니다."""
    data = request.get_json()
    if not data or not data.get("email") or not data.get("password"):
        return jsonify({"error": "이메일과 비밀번호는 필수입니다."}), 400

    accounts = load_accounts()
    new_account = {
        "email": data["email"],
        "password": data["password"],
        "account_type": data.get("account_type", "sub"),
        "label": data.get("label", data["email"].split("@")[0]),
    }

    # 중복 확인
    for acc in accounts:
        if acc.get("email") == new_account["email"]:
            return jsonify({"error": "이미 등록된 이메일입니다."}), 409

    accounts.append(new_account)
    _save_accounts(accounts)
    return jsonify({"message": "계정이 추가되었습니다.", "account": {
        "email": new_account["email"],
        "label": new_account["label"],
        "account_type": new_account["account_type"],
    }})


@app.route("/api/accounts/<email>", methods=["DELETE"])
def api_delete_account(email):
    """계정을 삭제합니다."""
    accounts = load_accounts()
    original_len = len(accounts)
    accounts = [a for a in accounts if a.get("email") != email]

    if len(accounts) == original_len:
        return jsonify({"error": "해당 계정을 찾을 수 없습니다."}), 404

    _save_accounts(accounts)
    return jsonify({"message": f"계정 {email}이 삭제되었습니다."})


def _save_accounts(accounts):
    """계정 목록을 파일에 저장합니다."""
    accounts_file = os.getenv("ACCOUNTS_FILE", "config/accounts.json")
    os.makedirs(os.path.dirname(accounts_file), exist_ok=True)
    with open(accounts_file, "w", encoding="utf-8") as f:
        json.dump(accounts, f, indent=2, ensure_ascii=False)


# ──────────────────────────── 자동화 실행 ────────────────────────────

def _run_automation():
    """백그라운드에서 자동화를 실행합니다."""
    import time
    from src.youtube_bot import YouTubeBot

    try:
        add_log("자동화 시작", "info")

        notion = NotionManager()
        proxy_manager = ProxyManager()
        fingerprint_manager = FingerprintManager()
        safety_rules = SafetyRules()
        smm_client = SMMClient()
        accounts = load_accounts()

        if not accounts:
            add_log("계정이 없습니다. config/accounts.json을 확인하세요.", "error")
            automation_state["running"] = False
            return

        tasks = notion.get_pending_tasks()
        if not tasks:
            add_log("대기 중인 작업이 없습니다.", "warning")
            automation_state["running"] = False
            return

        automation_state["total"] = len(tasks)
        add_log(f"총 {len(tasks)}개 작업 발견", "info")

        delay_ip_change = int(os.getenv("DELAY_AFTER_IP_CHANGE", "3"))
        comment_interval = int(os.getenv("COMMENT_INTERVAL_SEC", "180"))
        prev_account_label = None

        for i, task in enumerate(tasks):
            if not automation_state["running"]:
                add_log("사용자에 의해 중지됨", "warning")
                break

            automation_state["progress"] = i + 1
            automation_state["current_task"] = task.get("youtube_url", "")[:60]

            # 계정 결정
            account = None
            account_label = task.get("account", "")
            for acc in accounts:
                if acc.get("label") == account_label or acc.get("email", "").startswith(account_label):
                    account = acc
                    break
            if not account:
                account = accounts[0]

            current_label = account.get("label", account.get("email", "unknown"))

            # 대기 시간
            if prev_account_label and prev_account_label != current_label:
                add_log(f"계정 전환: {prev_account_label} → {current_label} (IP 변경 {delay_ip_change}초 대기)", "info")
                time.sleep(delay_ip_change)
            elif prev_account_label == current_label and i > 0:
                add_log(f"같은 계정 연속 - {comment_interval}초 대기", "info")
                time.sleep(comment_interval)

            # 안전 규칙 검사
            passed, reason = safety_rules.check_all_rules(
                current_label, task["youtube_url"], task["comment_text"]
            )
            if not passed:
                add_log(f"[건너뜀] {reason}", "warning")
                automation_state["results"]["skip"] += 1
                prev_account_label = current_label
                continue

            # 프록시 설정
            proxy_config = None
            if account.get("account_type") == "sub":
                proxy_url = proxy_manager.get_proxy_for_account(current_label)
                if proxy_url:
                    proxy_config = proxy_manager.parse_proxy_for_playwright(proxy_url)

            # 브라우저 실행 및 댓글 작성
            bot = YouTubeBot(
                proxy_config=proxy_config,
                fingerprint_manager=fingerprint_manager,
                account_label=current_label,
            )
            try:
                add_log(f"작업 {i+1}/{len(tasks)}: {task['youtube_url'][:50]}...", "info")
                bot.start_browser()

                login_ok = bot.login_youtube(account["email"], account["password"])
                if not login_ok:
                    add_log(f"로그인 실패: {current_label}", "error")
                    automation_state["results"]["fail"] += 1
                    notion.update_task_error(task["page_id"], "로그인 실패")
                    continue

                comment_url = bot.post_comment(task["youtube_url"], task["comment_text"])

                if comment_url:
                    notion.update_task_result(task["page_id"], comment_url, status="완료")
                    safety_rules.record_comment(current_label, task["youtube_url"], task["comment_text"])
                    automation_state["results"]["success"] += 1
                    add_log(f"댓글 성공: {comment_url[:60]}", "success")

                    # 좋아요 구매
                    if smm_client.enabled and comment_url:
                        like_result = smm_client.order_likes(comment_url)
                        if like_result["success"]:
                            automation_state["results"]["likes"] += 1
                            add_log(f"좋아요 주문 (ID: {like_result['order_id']})", "success")
                        else:
                            add_log(f"좋아요 실패: {like_result.get('error', '')}", "warning")
                else:
                    automation_state["results"]["fail"] += 1
                    notion.update_task_error(task["page_id"], "댓글 작성 실패")
                    add_log("댓글 작성 실패", "error")

            except Exception as e:
                automation_state["results"]["fail"] += 1
                add_log(f"오류: {str(e)}", "error")
            finally:
                bot.close_browser()
                prev_account_label = current_label

        add_log(
            f"완료! 성공: {automation_state['results']['success']}, "
            f"실패: {automation_state['results']['fail']}, "
            f"건너뜀: {automation_state['results']['skip']}, "
            f"좋아요: {automation_state['results']['likes']}",
            "info",
        )

    except Exception as e:
        add_log(f"치명적 오류: {str(e)}", "error")
    finally:
        automation_state["running"] = False
        automation_state["current_task"] = None


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
