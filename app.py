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
import time
import threading
from datetime import datetime

from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

load_dotenv(override=True)

from src.notion_client import NotionManager
from src.proxy_manager import ProxyManager
from src.fingerprint import FingerprintManager
from src.safety_rules import SafetyRules
from src.smm_client import SMMClient
from src.adb_ip_changer import ADBIPChanger

app = Flask(__name__)

# 글로벌 상태
automation_state = {
    "running": False,
    "current_task": None,
    "progress": 0,
    "total": 0,
    "logs": [],
    "results": {"success": 0, "fail": 0, "skip": 0, "likes": 0, "duplicate": 0},
    "test_mode": False,
    "limit": 0,
    "full_auto": False,
}
automation_lock = threading.Lock()

# 대댓글 자동화 상태
reply_state = {
    "running": False,
    "current_task": None,
    "progress": 0,
    "total": 0,
    "results": {"success": 0, "fail": 0, "skip": 0},
}
reply_lock = threading.Lock()

# 작업 목록 캐시 (매번 노션 API 전체 조회 방지) — 멀티 키 지원
# { "status:date": {"tasks": [...], "fetched_at": timestamp}, ... }
_task_cache = {}
_task_cache_ttl = 300  # 5분 (탭 전환 캐시용)
_task_cache_lock = threading.Lock()


def _filter_tasks_from_cache(all_tasks, status_filter):
    """'전체' 캐시 데이터에서 개별 탭용 데이터를 파생합니다 (상태만 필터링)."""
    checkbox_filters = {
        "댓글완료":      lambda t: t.get("comment_done") and not t.get("reply_done"),
        "대댓글완료":    lambda t: t.get("reply_done"),
        "좋아요작업완료": lambda t: t.get("like_done"),
    }

    if status_filter in checkbox_filters:
        return [t for t in all_tasks if checkbox_filters[status_filter](t)]
    else:
        return [t for t in all_tasks if t.get("status") == status_filter]


def _apply_date_filter(tasks, date_filter):
    """날짜 필터를 캐시된 데이터에 적용합니다."""
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))

    if date_filter.startswith("since:"):
        since_str = date_filter.split(":", 1)[1]
        since_dt = datetime.strptime(since_str, "%Y-%m-%d").replace(tzinfo=KST)
        return [t for t in tasks if t.get("last_edited") and
                datetime.fromisoformat(t["last_edited"].replace("Z", "+00:00")) >= since_dt]
    else:
        day_start = datetime.strptime(date_filter, "%Y-%m-%d").replace(tzinfo=KST)
        day_end = day_start + timedelta(days=1)
        return [t for t in tasks if t.get("last_edited") and
                day_start <= datetime.fromisoformat(t["last_edited"].replace("Z", "+00:00")) < day_end]


def _apply_sort(tasks, sort_param):
    """정렬을 적용합니다. sort_param: 'field:asc' 또는 'field:desc'"""
    if not sort_param or not tasks:
        return tasks

    parts = sort_param.split(":")
    field = parts[0] if parts else "last_edited"
    direction = parts[1] if len(parts) > 1 else "desc"
    reverse = (direction == "desc")

    def sort_key(t):
        val = t.get(field)
        if val is None:
            return ""
        return str(val)

    try:
        return sorted(tasks, key=sort_key, reverse=reverse)
    except Exception:
        return tasks


# 작업 목록 로딩 진행 상태 (UI 프로그레스 바용)
_loading_state = {
    "active": False,
    "loaded": 0,
    "message": "",
}
_loading_lock = threading.Lock()


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
        with open(accounts_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


# ──────────────────────────── 페이지 라우트 ────────────────────────────

@app.route("/")
def dashboard():
    """메인 대시보드."""
    return render_template("dashboard.html")


# ──────────────────────────── API 라우트 ────────────────────────────

@app.route("/api/loading-status")
def api_loading_status():
    """작업 목록 로딩 진행 상태를 반환합니다."""
    with _loading_lock:
        return jsonify({
            "active": _loading_state["active"],
            "loaded": _loading_state["loaded"],
            "message": _loading_state["message"],
        })


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

    # 상태별 카운트 (전체 DB 1회 조회)
    status_counts = {}
    try:
        notion = NotionManager()
        status_counts = notion.count_all_statuses()
    except Exception:
        pass

    return jsonify({
        "accounts": account_stats,
        "account_count": len(accounts),
        "pending_count": status_counts.get("댓글작업전", 0),
        "reply_done_count": status_counts.get("댓글완료", 0),
        "like_pending_count": status_counts.get("대댓글완료", 0),
        "status_counts": status_counts,
        "smm_enabled": smm.enabled,
        "smm_balance": smm_balance,
        "proxy_status": proxy.get_status(),
        "settings": {
            "max_comments_per_day": int(os.getenv("MAX_COMMENTS_PER_DAY", "20")),
            "comment_interval_sec": int(os.getenv("COMMENT_INTERVAL_SEC", "180")),
            "same_video_interval_min": int(os.getenv("SAME_VIDEO_INTERVAL_MIN", "30")),
            "smm_like_quantity": int(os.getenv("SMM_LIKE_QUANTITY", "20")),
        },
    })


@app.route("/api/tasks")
def api_tasks():
    """노션 DB에서 작업 목록을 가져옵니다.
    ?status=&date=YYYY-MM-DD&page=1&search=&sort=last_edited:desc&refresh=1 파라미터 지원.
    캐시: 상태별 1회 로드 → 날짜/검색/정렬은 캐시에서 즉시 처리 (노션 재호출 없음)
    """
    try:
        status_filter = request.args.get("status", "댓글작업전")
        date_filter = request.args.get("date", None)
        # 전체 리스트는 날짜 필터 무시
        if status_filter == "전체":
            date_filter = None
        search_query = request.args.get("search", "").strip()
        sort_param = request.args.get("sort", "last_edited:desc")  # 기본: 최근 작업순
        page = int(request.args.get("page", 1))
        force_refresh = request.args.get("refresh", "0") == "1"
        page_size = 100

        # 캐시 키: 상태만 사용 (날짜/검색/정렬은 캐시 후 필터링)
        cache_key = status_filter

        # 캐시 확인 (유효한 캐시가 있으면 노션 API 호출 생략)
        tasks = None
        cached_ago = 0
        from_cache = False
        with _task_cache_lock:
            entry = _task_cache.get(cache_key)
            if (not force_refresh
                    and entry
                    and entry["tasks"] is not None
                    and (time.time() - entry["fetched_at"]) < _task_cache_ttl):
                tasks = list(entry["tasks"])  # 원본 보호용 복사
                cached_ago = int(time.time() - entry["fetched_at"])
                from_cache = True

            # "전체" 캐시에서 개별 탭 데이터 파생 (불필요한 재수집 방지)
            if tasks is None and not force_refresh and status_filter != "전체":
                all_entry = _task_cache.get("전체")
                if (all_entry and all_entry["tasks"] is not None
                        and (time.time() - all_entry["fetched_at"]) < _task_cache_ttl):
                    all_tasks = all_entry["tasks"]
                    tasks = _filter_tasks_from_cache(all_tasks, status_filter)
                    cached_ago = int(time.time() - all_entry["fetched_at"])
                    from_cache = True

        # 캐시 미스 → 노션에서 가져오기
        if tasks is None:
            def on_progress(loaded, message=""):
                with _loading_lock:
                    _loading_state["active"] = True
                    _loading_state["loaded"] = loaded
                    _loading_state["message"] = message

            with _loading_lock:
                _loading_state["active"] = True
                _loading_state["loaded"] = 0
                _loading_state["message"] = "노션 데이터 불러오는 중..."

            try:
                notion = NotionManager()
                if status_filter == "전체":
                    tasks = notion.get_all_tasks(progress_callback=on_progress)
                else:
                    # 날짜 없이 전체 상태 로드 (캐시 후 날짜는 필터링)
                    tasks = notion.get_tasks_by_status(status_filter, date_filter=None, progress_callback=on_progress)
            finally:
                with _loading_lock:
                    _loading_state["active"] = False
                    _loading_state["loaded"] = len(tasks) if tasks else 0
                    _loading_state["message"] = ""

            # 캐시 저장 (상태별, 날짜 무관)
            now = time.time()
            with _task_cache_lock:
                _task_cache[cache_key] = {"tasks": tasks, "fetched_at": now}
                # "전체" 로드 시 개별 탭 캐시도 자동 생성
                if status_filter == "전체" and tasks:
                    for st in ["댓글작업전", "댓글완료", "대댓글완료", "좋아요작업완료", "중복", "에러"]:
                        _task_cache[st] = {
                            "tasks": _filter_tasks_from_cache(tasks, st),
                            "fetched_at": now,
                        }
                # 오래된 캐시 정리 (12개 초과 시 가장 오래된 것 제거)
                if len(_task_cache) > 12:
                    oldest_key = min(_task_cache, key=lambda k: _task_cache[k]["fetched_at"])
                    del _task_cache[oldest_key]
            cached_ago = 0
            tasks = list(tasks)  # 복사

        # 날짜 필터 적용 (캐시된 데이터에서 즉시 필터링)
        if date_filter and tasks:
            tasks = _apply_date_filter(tasks, date_filter)

        # 검색 필터 적용
        if search_query:
            q = search_query.lower()
            tasks = [t for t in tasks if
                     q in (t.get("youtube_url") or "").lower() or
                     q in (t.get("comment_text") or "").lower() or
                     q in (t.get("video_title") or "").lower() or
                     q in (t.get("account") or "").lower() or
                     q in (t.get("brand") or "").lower()]

        # 정렬 적용
        tasks = _apply_sort(tasks, sort_param)

        # 페이지네이션
        total_count = len(tasks)
        total_pages = max(1, (total_count + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paged_tasks = tasks[start_idx:end_idx]

        return jsonify({
            "tasks": paged_tasks,
            "count": len(paged_tasks),
            "total_count": total_count,
            "page": page,
            "total_pages": total_pages,
            "status": status_filter,
            "date": date_filter,
            "search": search_query,
            "sort": sort_param,
            "from_cache": from_cache,
            "cached_ago": cached_ago,
        })
    except Exception as e:
        return jsonify({"error": str(e), "tasks": [], "count": 0}), 500


@app.route("/api/tasks/counts")
def api_task_counts():
    """각 탭별 작업 개수를 반환합니다 (캐시 기반, "전체" 캐시에서 파생)."""
    try:
        statuses = ["댓글작업전", "댓글완료", "대댓글완료", "좋아요작업완료", "중복", "에러"]
        counts = {}

        with _task_cache_lock:
            # "전체" 캐시가 있으면 거기서 파생
            all_entry = _task_cache.get("전체")
            if all_entry and all_entry["tasks"] and (time.time() - all_entry["fetched_at"]) < _task_cache_ttl:
                all_tasks = all_entry["tasks"]
                counts["전체"] = len(all_tasks)
                for st in statuses:
                    counts[st] = len(_filter_tasks_from_cache(all_tasks, st))
            else:
                # 개별 캐시에서 수집
                for st in statuses:
                    entry = _task_cache.get(st)
                    if entry and entry["tasks"] and (time.time() - entry["fetched_at"]) < _task_cache_ttl:
                        counts[st] = len(entry["tasks"])

        return jsonify({"counts": counts})
    except Exception as e:
        return jsonify({"counts": {}, "error": str(e)})


@app.route("/api/notion/debug")
def api_notion_debug():
    """노션 DB 구조를 확인합니다 (디버깅용)."""
    try:
        from notion_client import Client
        load_dotenv(override=True)
        token = os.getenv("NOTION_API_TOKEN", "")
        db_id = os.getenv("NOTION_DATABASE_ID", "")

        if not token or not db_id:
            return jsonify({"error": "NOTION_API_TOKEN 또는 NOTION_DATABASE_ID 미설정"}), 400

        client = Client(auth=token)

        # DB 메타데이터 조회 (컬럼 구조)
        db_info = client.databases.retrieve(database_id=db_id)
        properties = {}
        for name, prop in db_info.get("properties", {}).items():
            properties[name] = {
                "type": prop.get("type", "unknown"),
                "id": prop.get("id", ""),
            }

        # 댓글작업전 상태의 샘플 3개 조회
        col_status = os.getenv("NOTION_COLUMN_STATUS", "상태")
        try:
            response = client.databases.query(
                database_id=db_id,
                page_size=3,
                filter={"property": col_status, "select": {"equals": "댓글작업전"}},
            )
        except Exception:
            try:
                response = client.databases.query(
                    database_id=db_id,
                    page_size=3,
                    filter={"property": col_status, "status": {"equals": "댓글작업전"}},
                )
            except Exception:
                response = client.databases.query(database_id=db_id, page_size=3)
        sample_pages = []
        for page in response.get("results", []):
            page_data = {"id": page["id"]}
            for name, prop in page.get("properties", {}).items():
                prop_type = prop.get("type", "")
                if prop_type == "title":
                    titles = prop.get("title", [])
                    page_data[name] = "".join(t.get("plain_text", "") for t in titles)
                elif prop_type == "rich_text":
                    texts = prop.get("rich_text", [])
                    page_data[name] = "".join(t.get("plain_text", "") for t in texts)
                elif prop_type == "url":
                    page_data[name] = prop.get("url", "")
                elif prop_type == "status":
                    status = prop.get("status")
                    page_data[name] = status.get("name", "") if status else ""
                elif prop_type == "select":
                    select = prop.get("select")
                    page_data[name] = select.get("name", "") if select else ""
                else:
                    page_data[name] = f"[{prop_type}]"
            sample_pages.append(page_data)

        # 현재 .env 파일에서 직접 컬럼명 읽기 (캐시된 환경변수 무시)
        env_columns = {}
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    env_columns[key.strip()] = val.strip()

        expected = {
            "영상 링크": env_columns.get("NOTION_COLUMN_YOUTUBE_URL", "영상 링크"),
            "댓글 원고": env_columns.get("NOTION_COLUMN_COMMENT_TEXT", "댓글 원고"),
            "상태": env_columns.get("NOTION_COLUMN_STATUS", "상태"),
            "댓글 계정": env_columns.get("NOTION_COLUMN_ACCOUNT", "댓글 계정"),
            "댓글 url": env_columns.get("NOTION_COLUMN_COMMENT_RESULT_URL", "댓글 url"),
        }

        # 매칭 확인
        actual_names = set(properties.keys())
        matching = {}
        for label, col_name in expected.items():
            matching[label] = {
                "expected": col_name,
                "found": col_name in actual_names,
            }

        return jsonify({
            "db_title": db_info.get("title", [{}])[0].get("plain_text", "Untitled") if db_info.get("title") else "Untitled",
            "properties": properties,
            "expected_columns": matching,
            "total_pages": len(response.get("results", [])),
            "sample_pages": sample_pages,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/run", methods=["POST"])
def api_run():
    """자동화를 백그라운드에서 시작합니다. limit 파라미터로 테스트 실행 가능."""
    with automation_lock:
        if automation_state["running"]:
            return jsonify({"error": "이미 실행 중입니다."}), 409

        automation_state["running"] = True
        automation_state["progress"] = 0
        automation_state["logs"] = []
        automation_state["results"] = {"success": 0, "fail": 0, "skip": 0, "likes": 0, "duplicate": 0}

    data = request.get_json(silent=True) or {}
    limit = data.get("limit", 0)  # 0 = 전체, 1~N = 테스트 건수
    selected_ids = data.get("selected_ids", [])  # 선택된 page_id 목록
    full_auto = data.get("full_auto", False)  # Full Auto 모드
    test_mode = limit > 0

    automation_state["test_mode"] = test_mode
    automation_state["limit"] = limit
    automation_state["full_auto"] = full_auto

    if full_auto:
        thread = threading.Thread(
            target=_run_full_auto, daemon=True
        )
    else:
        thread = threading.Thread(
            target=_run_automation, args=(limit, selected_ids), daemon=True
        )
    thread.start()

    if full_auto:
        mode_text = "Full Auto (무한 반복)"
    elif selected_ids:
        mode_text = f"선택 실행 ({len(selected_ids)}건)"
    elif test_mode:
        mode_text = f"테스트 모드 ({limit}건)"
    else:
        mode_text = "전체 실행"
    return jsonify({"message": f"자동화가 시작되었습니다. [{mode_text}]"})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    """자동화를 중지합니다."""
    automation_state["running"] = False
    add_log("사용자가 자동화를 중지했습니다.", "warning")
    return jsonify({"message": "중지 요청이 전송되었습니다."})


@app.route("/api/reply/run", methods=["POST"])
def api_reply_run():
    """대댓글 자동화를 백그라운드에서 시작합니다."""
    with reply_lock:
        if reply_state["running"]:
            return jsonify({"error": "대댓글 자동화가 이미 실행 중입니다."}), 409
        if automation_state["running"]:
            return jsonify({"error": "댓글 자동화가 실행 중입니다. 완료 후 시도하세요."}), 409

        reply_state["running"] = True
        reply_state["progress"] = 0
        reply_state["results"] = {"success": 0, "fail": 0, "skip": 0}

    data = request.get_json(silent=True) or {}
    limit = data.get("limit", 0)

    thread = threading.Thread(target=_run_reply_automation, args=(limit,), daemon=True)
    thread.start()

    mode = f"테스트 ({limit}건)" if limit > 0 else "전체 실행"
    return jsonify({"message": f"대댓글 자동화가 시작되었습니다. [{mode}]"})


@app.route("/api/reply/stop", methods=["POST"])
def api_reply_stop():
    """대댓글 자동화를 중지합니다."""
    reply_state["running"] = False
    add_log("[대댓글] 사용자가 대댓글 자동화를 중지했습니다.", "warning")
    return jsonify({"message": "대댓글 자동화 중지 요청이 전송되었습니다."})


@app.route("/api/reply/status")
def api_reply_status():
    """대댓글 자동화 상태를 반환합니다."""
    return jsonify({
        "running": reply_state["running"],
        "progress": reply_state["progress"],
        "total": reply_state["total"],
        "current_task": reply_state["current_task"],
        "results": reply_state["results"],
    })


@app.route("/api/reply/preview")
def api_reply_preview():
    """대댓글 대기 작업 목록을 미리보기합니다."""
    try:
        notion = NotionManager()
        tasks = notion.get_reply_pending_tasks()
        items = []
        for t in tasks:
            items.append({
                "page_id": t.get("page_id", ""),
                "video_title": t.get("video_title", ""),
                "youtube_url": t.get("youtube_url", ""),
                "account": t.get("account", ""),
                "comment_preview": (t.get("comment_text", "")[:30] + "...") if len(t.get("comment_text", "")) > 30 else t.get("comment_text", ""),
                "reply_preview": (t.get("reply_text", "")[:30] + "...") if len(t.get("reply_text", "")) > 30 else t.get("reply_text", ""),
                "result_url": t.get("result_url", ""),
            })
        return jsonify({"tasks": items, "count": len(items)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/duplicate-scan", methods=["POST"])
def api_duplicate_scan():
    """사전 중복 스캔: 대기 작업의 영상 링크를 전체 DB 완료 목록과 비교합니다."""
    try:
        notion = NotionManager()
        tasks = notion.get_pending_tasks()
        if not tasks:
            return jsonify({"duplicates": [], "clean_count": 0, "message": "대기 작업이 없습니다."})

        clean_tasks, duplicate_tasks = notion.check_duplicates(tasks)

        # 중복 목록 상세 정보
        dup_details = []
        for t in duplicate_tasks:
            dup_details.append({
                "page_id": t.get("page_id", ""),
                "youtube_url": t.get("youtube_url", ""),
                "video_title": t.get("video_title", ""),
                "account": t.get("account", ""),
                "comment_preview": (t.get("comment_text", "")[:40] + "...") if len(t.get("comment_text", "")) > 40 else t.get("comment_text", ""),
            })

        return jsonify({
            "duplicates": dup_details,
            "duplicate_count": len(duplicate_tasks),
            "clean_count": len(clean_tasks),
            "total_count": len(tasks),
            "message": f"스캔 완료: 전체 {len(tasks)}건 중 중복 {len(duplicate_tasks)}건 발견"
                       + (f" → '중복' 상태로 변경됨" if duplicate_tasks else " (중복 없음)"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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


def _mask_key(value, visible=4):
    """API 키를 마스킹합니다. 앞 4자만 보여줌."""
    if not value or len(value) <= visible:
        return value
    return value[:visible] + "*" * (len(value) - visible)


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    """현재 설정값을 반환합니다."""
    return jsonify({
        "NOTION_API_TOKEN": _mask_key(os.getenv("NOTION_API_TOKEN", "")),
        "NOTION_DATABASE_ID": os.getenv("NOTION_DATABASE_ID", ""),
        "SMM_API_KEY": _mask_key(os.getenv("SMM_API_KEY", "")),
        "SMM_ENABLED": os.getenv("SMM_ENABLED", "false"),
        "SMM_LIKE_SERVICE_ID": os.getenv("SMM_LIKE_SERVICE_ID", "4001"),
        "SMM_LIKE_QUANTITY": os.getenv("SMM_LIKE_QUANTITY", "20"),
        "MAX_COMMENTS_PER_DAY": os.getenv("MAX_COMMENTS_PER_DAY", "20"),
        "COMMENT_INTERVAL_SEC": os.getenv("COMMENT_INTERVAL_SEC", "180"),
        "SAME_VIDEO_INTERVAL_MIN": os.getenv("SAME_VIDEO_INTERVAL_MIN", "30"),
        "HEADLESS": os.getenv("HEADLESS", "false"),
        "USE_PROXY": os.getenv("USE_PROXY", "false"),
        "ADB_IP_CHANGE_ENABLED": os.getenv("ADB_IP_CHANGE_ENABLED", "false"),
        "ADB_PATH": os.getenv("ADB_PATH", "adb"),
        "ADB_AIRPLANE_WAIT": os.getenv("ADB_AIRPLANE_WAIT", "4"),
        "ADB_AUTO_ETHERNET": os.getenv("ADB_AUTO_ETHERNET", "true"),
        "ADB_ETHERNET_NAME": os.getenv("ADB_ETHERNET_NAME", "이더넷"),
    })


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    """설정을 .env 파일에 저장합니다."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "데이터가 없습니다."}), 400

        # 저장 가능한 키 목록
        allowed_keys = {
            "NOTION_API_TOKEN", "NOTION_DATABASE_ID",
            "SMM_API_KEY", "SMM_ENABLED", "SMM_LIKE_SERVICE_ID", "SMM_LIKE_QUANTITY",
            "MAX_COMMENTS_PER_DAY", "COMMENT_INTERVAL_SEC", "SAME_VIDEO_INTERVAL_MIN",
            "HEADLESS", "USE_PROXY",
            "ADB_IP_CHANGE_ENABLED", "ADB_PATH", "ADB_AIRPLANE_WAIT",
            "ADB_AUTO_ETHERNET", "ADB_ETHERNET_NAME",
        }

        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

        # 기존 .env 파일 읽기
        env_lines = []
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                env_lines = f.readlines()

        # 업데이트할 키-값 수집 (마스킹된 값은 건너뜀)
        updates = {}
        for key, value in data.items():
            if key not in allowed_keys:
                continue
            # 마스킹된 값(***포함)은 기존 값 유지
            if "*" in str(value):
                continue
            updates[key] = str(value)

        # .env 파일 업데이트
        updated_keys = set()
        new_lines = []
        for line in env_lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in updates:
                    new_lines.append(f"{key}={updates[key]}\n")
                    updated_keys.add(key)
                    # os.environ도 갱신
                    os.environ[key] = updates[key]
                    continue
            new_lines.append(line)

        # 새로운 키 추가 (기존에 없던 키)
        for key, value in updates.items():
            if key not in updated_keys:
                new_lines.append(f"{key}={value}\n")
                os.environ[key] = value

        # .env 파일 저장
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

        return jsonify({"message": "설정이 저장되었습니다."})
    except Exception as e:
        return jsonify({"error": f"설정 저장 중 오류: {str(e)}"}), 500


@app.route("/api/check-connections", methods=["GET"])
def api_check_connections():
    """모든 API 연동 상태를 확인합니다."""
    results = {}

    # 1. .env 파일 존재 여부
    env_exists = os.path.exists(os.path.join(os.path.dirname(__file__), ".env"))
    results["env_file"] = {"ok": env_exists, "message": ".env 파일 존재" if env_exists else ".env 파일이 없습니다. .env.example을 복사하세요."}

    # 2. Notion API 연결 테스트
    notion_token = os.getenv("NOTION_API_TOKEN", "")
    notion_db_id = os.getenv("NOTION_DATABASE_ID", "")
    if not notion_token or not notion_db_id:
        results["notion"] = {"ok": False, "message": "NOTION_API_TOKEN 또는 NOTION_DATABASE_ID 미설정"}
    else:
        try:
            notion = NotionManager()
            tasks = notion.get_pending_tasks()
            results["notion"] = {"ok": True, "message": f"연결 성공! 대기 작업 {len(tasks)}개"}
        except Exception as e:
            results["notion"] = {"ok": False, "message": f"연결 실패: {str(e)}"}

    # 3. SMM Kings API 연결 테스트
    smm_enabled = os.getenv("SMM_ENABLED", "false").lower() == "true"
    smm_api_key = os.getenv("SMM_API_KEY", "")
    if not smm_enabled:
        results["smm"] = {"ok": None, "message": "SMM 비활성 (SMM_ENABLED=false)"}
    elif not smm_api_key:
        results["smm"] = {"ok": False, "message": "SMM_API_KEY 미설정"}
    else:
        try:
            smm = SMMClient()
            balance = smm.get_balance()
            if balance is not None:
                service_id = os.getenv("SMM_LIKE_SERVICE_ID", "")
                msg = f"연결 성공! 잔액: ${balance:.2f}"
                if not service_id:
                    msg += " (SMM_LIKE_SERVICE_ID 미설정 - 서비스 조회 필요)"
                results["smm"] = {"ok": True, "message": msg}
            else:
                results["smm"] = {"ok": False, "message": "API 응답 없음 (API 키 확인 필요)"}
        except Exception as e:
            results["smm"] = {"ok": False, "message": f"연결 실패: {str(e)}"}

    # 4. 계정 파일 확인
    accounts = load_accounts()
    if accounts:
        results["accounts"] = {"ok": True, "message": f"계정 {len(accounts)}개 로드됨"}
    else:
        results["accounts"] = {"ok": False, "message": "등록된 계정이 없습니다"}

    # 5. ADB IP 변경 상태
    adb_enabled = os.getenv("ADB_IP_CHANGE_ENABLED", "false").lower() == "true"
    if not adb_enabled:
        results["adb"] = {"ok": None, "message": "ADB IP 변경 비활성 (ADB_IP_CHANGE_ENABLED=false)"}
    else:
        try:
            adb_changer = ADBIPChanger()
            connected, info = adb_changer.check_device()
            if connected:
                ip = adb_changer.get_current_ip()
                msg = f"디바이스 연결됨: {info}"
                if ip:
                    msg += f" (IP: {ip})"
                results["adb"] = {"ok": True, "message": msg}
            else:
                results["adb"] = {"ok": False, "message": info}
        except Exception as e:
            results["adb"] = {"ok": False, "message": f"ADB 오류: {str(e)}"}

    # 6. 프록시 상태
    use_proxy = os.getenv("USE_PROXY", "false").lower() == "true"
    if not use_proxy:
        results["proxy"] = {"ok": None, "message": "프록시 비활성 (USE_PROXY=false)"}
    else:
        try:
            proxy = ProxyManager()
            status = proxy.get_status()
            results["proxy"] = {"ok": True, "message": f"프록시 활성: {status}"}
        except Exception as e:
            results["proxy"] = {"ok": False, "message": f"프록시 오류: {str(e)}"}

    return jsonify(results)


@app.route("/api/adb/test", methods=["POST"])
def api_adb_test():
    """ADB 연결을 단계별로 테스트합니다 (활성 여부 무관)."""
    steps = []

    # UI에서 전달된 ADB 경로 사용 (저장 전이라도 테스트 가능)
    data = request.get_json() or {}
    adb_path = data.get("adb_path", os.getenv("ADB_PATH", "adb"))

    changer = ADBIPChanger()
    changer.adb_path = adb_path
    changer.enabled = True  # 테스트는 항상 활성 상태로

    # 1단계: ADB 실행 가능 여부
    import subprocess
    try:
        result = subprocess.run(
            [adb_path, "version"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            version = result.stdout.strip().split("\n")[0]
            steps.append({"step": "ADB 실행 확인", "ok": True, "message": version})
        else:
            steps.append({"step": "ADB 실행 확인", "ok": False, "message": "ADB 실행 실패"})
            return jsonify({"steps": steps})
    except FileNotFoundError:
        hint = "ADB 경로를 전체 경로로 입력하세요 (예: D:\\platform-tools\\adb.exe)"
        steps.append({"step": "ADB 실행 확인", "ok": False, "message": f"ADB를 찾을 수 없습니다: {adb_path} → {hint}"})
        return jsonify({"steps": steps})
    except Exception as e:
        steps.append({"step": "ADB 실행 확인", "ok": False, "message": str(e)})
        return jsonify({"steps": steps})

    # 2단계: 디바이스 연결 확인
    connected, info = changer.check_device()
    if connected:
        steps.append({"step": "디바이스 연결", "ok": True, "message": f"연결됨: {info}"})
    else:
        steps.append({"step": "디바이스 연결", "ok": False, "message": info})
        return jsonify({"steps": steps})

    # 3단계: 현재 IP 확인
    ip = changer.get_current_ip()
    if ip:
        steps.append({"step": "현재 IP 확인", "ok": True, "message": f"IP: {ip}"})
    else:
        steps.append({"step": "현재 IP 확인", "ok": None, "message": "IP 확인 불가 (비행기모드 토글은 가능)"})

    # 4단계: 비행기모드 제어 확인 (cmd connectivity 방식)
    output, code = changer._run_adb("shell", "cmd connectivity airplane-mode")
    if code == 0:
        mode = output.strip()
        steps.append({"step": "비행기모드 제어", "ok": True, "message": f"cmd connectivity 지원됨 (현재: {mode})"})
    else:
        # fallback: settings 방식 확인
        output2, code2 = changer._run_adb("shell", "settings get global airplane_mode_on")
        if code2 == 0:
            steps.append({"step": "비행기모드 제어", "ok": True, "message": f"settings 방식 사용 가능"})
        else:
            steps.append({"step": "비행기모드 제어", "ok": False, "message": "비행기모드 제어 접근 실패"})

    return jsonify({"steps": steps})


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
    try:
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
    except Exception as e:
        return jsonify({"error": f"계정 추가 중 오류: {str(e)}"}), 500


@app.route("/api/accounts/<email>", methods=["DELETE"])
def api_delete_account(email):
    """계정을 삭제합니다."""
    try:
        accounts = load_accounts()
        original_len = len(accounts)
        accounts = [a for a in accounts if a.get("email") != email]

        if len(accounts) == original_len:
            return jsonify({"error": "해당 계정을 찾을 수 없습니다."}), 404

        _save_accounts(accounts)
        return jsonify({"message": f"계정 {email}이 삭제되었습니다."})
    except Exception as e:
        return jsonify({"error": f"계정 삭제 중 오류: {str(e)}"}), 500


@app.route("/api/accounts/test-login", methods=["POST"])
def api_test_login():
    """계정의 YouTube 로그인을 테스트합니다."""
    import time as _time
    from src.youtube_bot import YouTubeBot

    data = request.get_json()
    if not data or not data.get("email"):
        return jsonify({"error": "이메일이 필요합니다."}), 400

    email = data["email"]
    accounts = load_accounts()
    account = None
    for acc in accounts:
        if acc.get("email") == email:
            account = acc
            break

    if not account:
        return jsonify({"error": "해당 계정을 찾을 수 없습니다."}), 404

    start_time = _time.time()
    bot = YouTubeBot()
    # 테스트는 headless로 강제
    bot.headless = True

    try:
        bot.start_browser()
        login_ok = bot.login_youtube(account["email"], account["password"])
        elapsed = round(_time.time() - start_time, 1)

        if login_ok:
            return jsonify({"success": True, "message": f"로그인 성공 ({elapsed}초)", "elapsed": elapsed})
        else:
            return jsonify({"success": False, "message": f"로그인 실패 ({elapsed}초)", "elapsed": elapsed})
    except Exception as e:
        elapsed = round(_time.time() - start_time, 1)
        return jsonify({"success": False, "message": f"오류: {str(e)} ({elapsed}초)", "elapsed": elapsed})
    finally:
        bot.close_browser()


# 수동 로그인 상태 관리
manual_login_state = {
    "active": False,
    "email": None,
    "status": "idle",  # idle, waiting, success, failed
    "message": "",
}
manual_login_bot = None


@app.route("/api/accounts/manual-login", methods=["POST"])
def api_manual_login():
    """수동 로그인 - 브라우저를 열어 사용자가 직접 로그인합니다."""
    global manual_login_bot

    from src.youtube_bot import YouTubeBot

    data = request.get_json()
    if not data or not data.get("email"):
        return jsonify({"error": "이메일이 필요합니다."}), 400

    # 이전 세션이 남아있으면 강제 정리
    if manual_login_state["active"]:
        print("[manual_login] 이전 세션 강제 정리")
        try:
            if manual_login_bot:
                manual_login_bot.close_browser()
        except Exception:
            pass
        manual_login_bot = None
        manual_login_state["active"] = False
        manual_login_state["status"] = "idle"

    email = data["email"]
    accounts = load_accounts()
    account = None
    for acc in accounts:
        if acc.get("email") == email:
            account = acc
            break

    if not account:
        return jsonify({"error": "해당 계정을 찾을 수 없습니다."}), 404

    manual_login_state["active"] = True
    manual_login_state["email"] = email
    manual_login_state["status"] = "waiting"
    manual_login_state["message"] = "브라우저에서 로그인을 완료해주세요..."

    def _do_manual_login():
        global manual_login_bot
        try:
            label = account.get("label", email.split("@")[0])
            bot = YouTubeBot(account_label=label)
            bot.headless = False  # 화면 보이기 필수
            manual_login_bot = bot

            bot.start_browser()
            print(f"[manual_login] 브라우저 시작됨, email={email}")
            login_ok = bot.manual_login(email=email, timeout=300)
            print(f"[manual_login] 결과: {login_ok}")

            if login_ok:
                manual_login_state["status"] = "success"
                manual_login_state["message"] = "로그인 성공! 쿠키가 저장되었습니다."
            else:
                manual_login_state["status"] = "failed"
                manual_login_state["message"] = "로그인 시간 초과 또는 실패"
        except Exception as e:
            print(f"[manual_login] 오류: {e}")
            manual_login_state["status"] = "failed"
            manual_login_state["message"] = f"오류: {str(e)}"
        finally:
            try:
                if manual_login_bot:
                    manual_login_bot.close_browser()
                    manual_login_bot = None
            except Exception:
                pass
            manual_login_state["active"] = False
            print(f"[manual_login] 완료, status={manual_login_state['status']}")

    thread = threading.Thread(target=_do_manual_login, daemon=True)
    thread.start()

    return jsonify({"message": "브라우저가 열렸습니다. 로그인을 완료해주세요."})


@app.route("/api/accounts/manual-login/status")
def api_manual_login_status():
    """수동 로그인 진행 상태를 반환합니다."""
    return jsonify(manual_login_state)


@app.route("/api/accounts/manual-login/confirm", methods=["POST"])
def api_manual_login_confirm():
    """사용자가 로그인 완료를 수동으로 확인합니다. 쿠키를 저장하고 브라우저를 닫습니다."""
    global manual_login_bot

    if not manual_login_bot:
        return jsonify({"error": "진행 중인 수동 로그인이 없습니다."}), 400

    try:
        bot = manual_login_bot
        # YouTube로 이동하여 YouTube 쿠키도 저장
        try:
            bot.page.goto(
                "https://www.youtube.com",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            import time as _time
            _time.sleep(3)
        except Exception:
            pass
        bot.save_cookies()

        manual_login_state["status"] = "success"
        manual_login_state["message"] = "로그인 성공! 쿠키가 저장되었습니다."

        try:
            bot.close_browser()
        except Exception:
            pass
        manual_login_bot = None
        manual_login_state["active"] = False

        return jsonify({"message": "로그인 확인 완료! 쿠키가 저장되었습니다."})
    except Exception as e:
        return jsonify({"error": f"확인 중 오류: {str(e)}"}), 500


@app.route("/api/accounts/login-status/<email>")
def api_login_status(email):
    """계정의 저장된 쿠키 상태를 확인합니다."""
    from src.youtube_bot import YouTubeBot
    import re as _re

    accounts = load_accounts()
    account = None
    for acc in accounts:
        if acc.get("email") == email:
            account = acc
            break

    if not account:
        return jsonify({"has_cookies": False, "message": "계정 없음"})

    label = account.get("label", email.split("@")[0])
    safe_label = _re.sub(r'[^\w\-]', '_', label)
    cookie_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "sessions")
    cookie_path = os.path.join(cookie_dir, f"{safe_label}.json")

    if os.path.exists(cookie_path):
        mtime = os.path.getmtime(cookie_path)
        from datetime import datetime
        saved_time = datetime.fromtimestamp(mtime).strftime("%m/%d %H:%M")
        return jsonify({"has_cookies": True, "message": f"쿠키 저장됨 ({saved_time})"})

    return jsonify({"has_cookies": False, "message": "쿠키 없음"})


def _save_accounts(accounts):
    """계정 목록을 파일에 저장합니다."""
    accounts_file = os.getenv("ACCOUNTS_FILE", "config/accounts.json")
    os.makedirs(os.path.dirname(accounts_file), exist_ok=True)
    with open(accounts_file, "w", encoding="utf-8") as f:
        json.dump(accounts, f, indent=2, ensure_ascii=False)


# ──────────────────────────── 자동화 실행 ────────────────────────────

def _run_automation(limit=0, selected_ids=None):
    """백그라운드에서 자동화를 실행합니다. limit>0이면 해당 건수만 테스트 실행."""
    import time
    from src.youtube_bot import YouTubeBot

    selected_ids = selected_ids or []
    test_mode = limit > 0

    try:
        if selected_ids:
            mode_label = f"[선택 실행 {len(selected_ids)}건]"
        elif test_mode:
            mode_label = f"[테스트 {limit}건]"
        else:
            mode_label = "[전체 실행]"
        add_log(f"자동화 시작 {mode_label}", "info")

        notion = NotionManager()
        proxy_manager = ProxyManager()
        fingerprint_manager = FingerprintManager()
        safety_rules = SafetyRules()
        smm_client = SMMClient()
        accounts = load_accounts()

        # ADB IP 변경 활성 시 유선 인터넷 비활성화 (USB 테더링으로 전환)
        # Full Auto 모드에서는 _run_full_auto에서 관리하므로 건너뜀
        adb_changer = ADBIPChanger()
        _ethernet_disabled = False
        if adb_changer.enabled and adb_changer.auto_ethernet and not automation_state.get("full_auto"):
            automation_state["current_task"] = "[준비] 유선 인터넷 비활성화 중..."
            add_log("유선 인터넷 비활성화 → USB 테더링으로 전환 중...", "info")
            ok, msg = adb_changer.disable_ethernet()
            if ok:
                _ethernet_disabled = True
                add_log(f"유선 비활성화 완료: {msg}", "success")
            else:
                add_log(f"유선 비활성화 실패: {msg} (관리자 권한으로 실행 필요)", "warning")

        if not accounts:
            add_log("계정이 없습니다. config/accounts.json을 확인하세요.", "error")
            automation_state["running"] = False
            return

        tasks = notion.get_pending_tasks()
        if not tasks:
            add_log("대기 중인 작업이 없습니다.", "warning")
            automation_state["running"] = False
            return

        # 중복 영상 체크 (이미 댓글 완료된 영상 필터링)
        automation_state["total"] = len(tasks)
        automation_state["current_task"] = "[준비] 중복 영상 체크 중..."
        add_log(f"대기 작업 {len(tasks)}건 로드 → 중복 영상 체크 중...", "info")
        tasks, duplicate_tasks = notion.check_duplicates(tasks)
        if duplicate_tasks:
            dup_count = len(duplicate_tasks)
            add_log(f"중복 영상 {dup_count}건 발견 → '중복' 상태로 변경됨", "warning")
            automation_state["results"]["duplicate"] = dup_count
        if not tasks:
            add_log("중복 제외 후 대기 작업이 없습니다.", "warning")
            automation_state["running"] = False
            automation_state["current_task"] = None
            return

        # 선택 실행: 선택된 page_id만 필터링
        if selected_ids:
            selected_set = set(selected_ids)
            tasks = [t for t in tasks if t.get("page_id") in selected_set]
            if not tasks:
                add_log("선택된 작업을 찾을 수 없습니다.", "warning")
                automation_state["running"] = False
                return
            add_log(f"선택된 {len(tasks)}건 작업 실행", "info")
        # 테스트 모드: 지정 건수만 처리
        elif test_mode and len(tasks) > limit:
            add_log(f"테스트 모드: 전체 {len(tasks)}건 중 {limit}건만 실행", "warning")
            tasks = tasks[:limit]

        automation_state["total"] = len(tasks)
        add_log(f"총 {len(tasks)}개 작업 {'(테스트)' if test_mode else ''} 발견", "info")

        delay_ip_change = int(os.getenv("DELAY_AFTER_IP_CHANGE", "3"))
        comment_interval = int(os.getenv("COMMENT_INTERVAL_SEC", "180"))
        prev_account_label = None

        for i, task in enumerate(tasks):
            if not automation_state["running"]:
                add_log("사용자에 의해 중지됨", "warning")
                break

            automation_state["progress"] = i + 1
            task_url_short = task.get("youtube_url", "")[:50]
            automation_state["current_task"] = task_url_short

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
                # ADB 비행기모드로 IP 변경
                adb_changer = ADBIPChanger()
                if adb_changer.enabled:
                    old_ip = adb_changer.get_current_ip()
                    add_log(f"계정 전환: {prev_account_label} → {current_label} | 현재 IP: {old_ip or '확인불가'}", "info")
                    automation_state["current_task"] = f"[IP 변경] 비행기모드 토글 중..."
                    success, msg = adb_changer.toggle_airplane_mode()
                    new_ip = adb_changer.get_current_ip()
                    if success:
                        add_log(f"IP 변경 완료: {old_ip or '?'} → {new_ip or '?'} ({msg})", "success")
                    else:
                        add_log(f"IP 변경 실패: {msg} | IP: {new_ip or '확인불가'} (계속 진행)", "warning")
                else:
                    add_log(f"계정 전환: {prev_account_label} → {current_label} (IP 변경 {delay_ip_change}초 대기)", "info")
                    time.sleep(delay_ip_change)
            elif prev_account_label == current_label and i > 0:
                add_log(f"같은 계정 연속 - {comment_interval}초 대기", "info")
                time.sleep(comment_interval)

            # 안전 규칙 검사 (테스트/선택 실행 모드에서는 시간 간격 규칙 건너뜀)
            skip = test_mode or bool(selected_ids)
            passed, reason = safety_rules.check_all_rules(
                current_label, task["youtube_url"], task["comment_text"],
                skip_interval=skip,
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
                automation_state["current_task"] = f"[브라우저 시작] {task_url_short}"
                add_log(f"작업 {i+1}/{len(tasks)}: {task['youtube_url'][:50]}...", "info")
                bot.start_browser()

                automation_state["current_task"] = f"[로그인 중] {current_label}"
                login_ok = bot.login_youtube(account["email"], account["password"])
                if not login_ok:
                    add_log(f"로그인 실패: {current_label}", "error")
                    automation_state["results"]["fail"] += 1
                    notion.update_task_error(task["page_id"], "로그인 실패")
                    continue

                automation_state["current_task"] = f"[댓글 작성 중] {task_url_short}"
                comment_url = bot.post_comment(task["youtube_url"], task["comment_text"])

                if comment_url:
                    # ── 1단계: 댓글 작성 완료 ──
                    automation_state["current_task"] = f"[1/3 댓글완료] {task_url_short}"
                    add_log(f"[1/3 댓글작성 완료] {comment_url}", "success")

                    # ── 2단계: 즉시 노션에 댓글완료 + URL 반영 ──
                    automation_state["current_task"] = f"[2/3 노션반영] {task_url_short}"
                    add_log("[2/3 노션 반영 중] 상태→댓글완료, 댓글 URL 저장...", "info")
                    notion_ok, notion_err = notion.update_task_result(task["page_id"], comment_url, status="댓글완료")
                    if notion_ok:
                        add_log("[2/3 노션 반영 완료] 댓글완료 + URL 저장 성공", "success")
                    else:
                        add_log(f"[2/3 노션 반영 실패] {notion_err}", "warning")

                    # ── 3단계: 즉시 좋아요 주문 (상태는 댓글완료 유지 → 대댓글 대기) ──
                    if smm_client.enabled:
                        automation_state["current_task"] = f"[3/3 좋아요] {task_url_short}"
                        add_log(f"[3/3 좋아요 주문 중] {comment_url[:50]}...", "info")
                        single = smm_client.order_likes(comment_url)
                        if single.get("success"):
                            automation_state["results"]["likes"] += 1
                            add_log(f"[3/3 좋아요 주문 성공] 주문ID: {single.get('order_id')}", "success")
                            # 좋아요 완료 체크박스 체크
                            try:
                                notion.update_like_checkbox(task["page_id"])
                            except Exception:
                                pass
                        else:
                            err = single.get("error", "?")
                            add_log(f"[3/3 좋아요 주문 실패] {err}", "warning")
                            if "incorrect_service" in str(err).lower():
                                add_log(
                                    f"서비스 ID '{smm_client.service_id}' 유효하지 않음. "
                                    "SMM 좋아요 탭 → 서비스 조회에서 올바른 ID 확인 필요",
                                    "error",
                                )
                    else:
                        add_log("[3/3 좋아요] SMM 비활성화 - 건너뜀", "info")
                    # 상태는 '댓글완료' 유지 (추후 대댓글 자동화에서 처리)

                    safety_rules.record_comment(current_label, task["youtube_url"], task["comment_text"])
                    automation_state["results"]["success"] += 1
                    automation_state["current_task"] = f"[완료] {task_url_short}"
                    add_log(f"--- 작업 {i+1}/{len(tasks)} 완료 ---", "success")
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

        summary_prefix = "[테스트 완료]" if test_mode else "[전체 완료]"
        dup = automation_state['results'].get('duplicate', 0)
        dup_text = f", 중복: {dup}" if dup > 0 else ""
        add_log(
            f"{summary_prefix} 성공: {automation_state['results']['success']}, "
            f"실패: {automation_state['results']['fail']}, "
            f"건너뜀: {automation_state['results']['skip']}, "
            f"좋아요: {automation_state['results']['likes']}{dup_text}",
            "success" if automation_state['results']['success'] > 0 else "warning",
        )

    except Exception as e:
        add_log(f"치명적 오류: {str(e)}", "error")
    finally:
        # ADB IP 변경 사용 시 유선 인터넷 복원
        if _ethernet_disabled:
            add_log("유선 인터넷 복원 중...", "info")
            ok, msg = adb_changer.enable_ethernet()
            if ok:
                add_log(f"유선 인터넷 복원 완료: {msg}", "success")
            else:
                add_log(f"유선 복원 실패: {msg} (수동으로 복원 필요)", "warning")

        # Full Auto 모드에서는 _run_full_auto가 running을 관리하므로 여기서 끄지 않음
        if not automation_state.get("full_auto"):
            automation_state["running"] = False
            automation_state["current_task"] = None


def _run_full_auto():
    """Full Auto 모드: 대기 작업이 없을 때까지 반복 실행합니다."""
    import time

    round_num = 0
    total_success = 0
    total_fail = 0
    total_skip = 0
    total_likes = 0

    # Full Auto 모드에서는 여기서 유선 제어 (라운드별 반복 방지)
    adb_changer = ADBIPChanger()
    _fa_ethernet_disabled = False
    if adb_changer.enabled and adb_changer.auto_ethernet:
        add_log("유선 인터넷 비활성화 → USB 테더링으로 전환 중...", "info")
        ok, msg = adb_changer.disable_ethernet()
        if ok:
            _fa_ethernet_disabled = True
            add_log(f"유선 비활성화 완료: {msg}", "success")
        else:
            add_log(f"유선 비활성화 실패: {msg}", "warning")

    try:
        add_log("=== Full Auto 모드 시작 ===", "info")
        add_log("대기 작업이 없을 때까지 계속 실행합니다. 중지 버튼으로 멈출 수 있습니다.", "info")

        while automation_state["running"]:
            round_num += 1
            add_log(f"── 라운드 {round_num} 시작 ──", "info")

            # 매 라운드마다 결과 리셋
            automation_state["progress"] = 0
            automation_state["results"] = {"success": 0, "fail": 0, "skip": 0, "likes": 0, "duplicate": 0}

            # 자동화 실행 (전체 모드)
            _run_automation(limit=0)

            # 라운드 결과 누적
            r = automation_state["results"]
            total_success += r["success"]
            total_fail += r["fail"]
            total_skip += r["skip"]
            total_likes += r["likes"]

            add_log(
                f"── 라운드 {round_num} 완료: 성공 {r['success']}, 실패 {r['fail']}, "
                f"건너뜀 {r['skip']}, 좋아요 {r['likes']} ──",
                "success" if r["success"] > 0 else "warning",
            )

            if not automation_state["running"]:
                break

            # 다음 라운드 전 대기 작업 확인
            automation_state["current_task"] = "[재수집 중] 노션 대기 작업 확인..."
            try:
                notion = NotionManager()
                pending = notion.count_pending_tasks()
                add_log(f"남은 대기 작업: {pending}건", "info")
            except Exception as e:
                add_log(f"대기 작업 확인 실패: {e}", "warning")
                pending = 0

            if pending == 0:
                add_log("대기 작업이 없습니다. Full Auto 종료.", "success")
                break

            # 잠시 대기 후 다음 라운드
            add_log(f"10초 후 라운드 {round_num + 1} 시작...", "info")
            for _ in range(10):
                if not automation_state["running"]:
                    break
                time.sleep(1)

        add_log(
            f"=== Full Auto 종료 (총 {round_num}라운드) ===\n"
            f"총 성공: {total_success}, 실패: {total_fail}, "
            f"건너뜀: {total_skip}, 좋아요: {total_likes}",
            "success",
        )

    except Exception as e:
        add_log(f"Full Auto 오류: {str(e)}", "error")
    finally:
        # Full Auto 종료 시 유선 복원
        if _fa_ethernet_disabled:
            add_log("유선 인터넷 복원 중...", "info")
            ok, msg = adb_changer.enable_ethernet()
            if ok:
                add_log(f"유선 인터넷 복원 완료: {msg}", "success")
            else:
                add_log(f"유선 복원 실패: {msg}", "warning")

        automation_state["running"] = False
        automation_state["full_auto"] = False
        automation_state["current_task"] = None


def _run_reply_automation(limit=0):
    """백그라운드에서 대댓글 자동화를 실행합니다."""
    import time
    from src.youtube_bot import YouTubeBot

    test_mode = limit > 0

    try:
        mode_label = f"[대댓글 테스트 {limit}건]" if test_mode else "[대댓글 전체 실행]"
        add_log(f"=== 대댓글 자동화 시작 {mode_label} ===", "info")

        notion = NotionManager()
        proxy_manager = ProxyManager()
        fingerprint_manager = FingerprintManager()
        accounts = load_accounts()

        if not accounts:
            add_log("[대댓글] 계정이 없습니다.", "error")
            reply_state["running"] = False
            return

        tasks = notion.get_reply_pending_tasks()
        if not tasks:
            add_log("[대댓글] 대댓글 대기 작업이 없습니다. (댓글완료 + 대댓글 원고 필요)", "warning")
            reply_state["running"] = False
            return

        if test_mode and len(tasks) > limit:
            add_log(f"[대댓글] 테스트 모드: 전체 {len(tasks)}건 중 {limit}건만 실행", "warning")
            tasks = tasks[:limit]

        reply_state["total"] = len(tasks)
        add_log(f"[대댓글] 총 {len(tasks)}개 작업 발견", "info")

        comment_interval = int(os.getenv("COMMENT_INTERVAL_SEC", "180"))
        delay_ip_change = int(os.getenv("DELAY_AFTER_IP_CHANGE", "3"))
        prev_account_label = None

        for i, task in enumerate(tasks):
            if not reply_state["running"]:
                add_log("[대댓글] 사용자에 의해 중지됨", "warning")
                break

            reply_state["progress"] = i + 1
            task_url_short = task.get("youtube_url", "")[:50]
            reply_state["current_task"] = task_url_short

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
                add_log(f"[대댓글] 계정 전환: {prev_account_label} → {current_label} ({delay_ip_change}초 대기)", "info")
                time.sleep(delay_ip_change)
            elif prev_account_label == current_label and i > 0:
                add_log(f"[대댓글] 같은 계정 연속 - {comment_interval}초 대기", "info")
                time.sleep(comment_interval)

            # 프록시 설정
            proxy_config = None
            if account.get("account_type") == "sub":
                proxy_url = proxy_manager.get_proxy_for_account(current_label)
                if proxy_url:
                    proxy_config = proxy_manager.parse_proxy_for_playwright(proxy_url)

            bot = YouTubeBot(
                proxy_config=proxy_config,
                fingerprint_manager=fingerprint_manager,
                account_label=current_label,
            )
            try:
                reply_state["current_task"] = f"[브라우저 시작] {task_url_short}"
                add_log(f"[대댓글] 작업 {i+1}/{len(tasks)}: {task['youtube_url'][:50]}...", "info")
                bot.start_browser()

                reply_state["current_task"] = f"[로그인 중] {current_label}"
                login_ok = bot.login_youtube(account["email"], account["password"])
                if not login_ok:
                    add_log(f"[대댓글] 로그인 실패: {current_label}", "error")
                    reply_state["results"]["fail"] += 1
                    continue

                comment_url = task.get("result_url", "")
                reply_text = task.get("reply_text", "")

                reply_state["current_task"] = f"[대댓글 작성 중] {task_url_short}"
                add_log(f"[대댓글] 대댓글 작성 중: {comment_url[:50]}...", "info")
                success = bot.post_reply(comment_url, reply_text)

                if success:
                    add_log(f"[대댓글] 대댓글 작성 성공", "success")

                    # 노션 업데이트: 상태→대댓글완료 + 대댓글 완료 체크박스
                    reply_state["current_task"] = f"[노션 반영] {task_url_short}"
                    ok, err = notion.update_reply_result(task["page_id"])
                    if ok:
                        add_log("[대댓글] 노션 반영 완료: 대댓글완료", "success")
                    else:
                        add_log(f"[대댓글] 노션 반영 실패: {err}", "warning")

                    reply_state["results"]["success"] += 1
                    add_log(f"[대댓글] --- 작업 {i+1}/{len(tasks)} 완료 ---", "success")
                else:
                    reply_state["results"]["fail"] += 1
                    add_log("[대댓글] 대댓글 작성 실패", "error")

            except Exception as e:
                reply_state["results"]["fail"] += 1
                add_log(f"[대댓글] 오류: {str(e)}", "error")
            finally:
                bot.close_browser()
                prev_account_label = current_label

        add_log(
            f"[대댓글 완료] 성공: {reply_state['results']['success']}, "
            f"실패: {reply_state['results']['fail']}, "
            f"건너뜀: {reply_state['results']['skip']}",
            "success" if reply_state['results']['success'] > 0 else "warning",
        )

    except Exception as e:
        add_log(f"[대댓글] 자동화 오류: {str(e)}", "error")
    finally:
        reply_state["running"] = False
        reply_state["current_task"] = None


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
