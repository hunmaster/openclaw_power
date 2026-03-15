"""
라이선스 서버 - Flask API
독립 서버로 배포 가능 (포트 5100)
"""

import os
import functools
from datetime import datetime
from flask import Flask, request, jsonify, render_template

from models import (
    init_db,
    create_customer,
    get_customer,
    list_customers,
    create_license,
    get_license_by_key,
    list_licenses,
    revoke_license,
    bind_device,
    unbind_device,
    get_token_balance,
    consume_tokens,
    add_tokens,
    refill_monthly_tokens,
    log_api_call,
)

app = Flask(__name__, template_folder="templates")

# 관리자 API 키 (환경변수로 설정)
ADMIN_API_KEY = os.environ.get("LICENSE_ADMIN_KEY", "change-me-in-production")


# ─── 미들웨어 ───

def admin_required(f):
    """관리자 API 키 검증 데코레이터"""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get("X-Admin-Key", "")
        if api_key != ADMIN_API_KEY:
            return jsonify({"error": "관리자 인증 실패"}), 403
        return f(*args, **kwargs)
    return decorated


def get_client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr)


# ═══════════════════════════════════════════
#  클라이언트 API (프로그램에서 호출)
# ═══════════════════════════════════════════

@app.route("/api/license/verify", methods=["POST"])
def api_verify_license():
    """
    라이선스 키 검증 + 디바이스 바인딩
    클라이언트가 프로그램 시작 시 호출
    """
    data = request.get_json() or {}
    license_key = data.get("license_key", "").strip()
    hardware_id = data.get("hardware_id", "").strip()
    hostname = data.get("hostname", "")
    ip = get_client_ip()

    if not license_key or not hardware_id:
        log_api_call(license_key, "/verify", ip, False, "키 또는 하드웨어ID 누락")
        return jsonify({"valid": False, "error": "라이선스 키와 하드웨어 ID가 필요합니다."}), 400

    # 라이선스 조회
    lic = get_license_by_key(license_key)
    if not lic:
        log_api_call(license_key, "/verify", ip, False, "존재하지 않는 키")
        return jsonify({"valid": False, "error": "유효하지 않은 라이선스 키입니다."}), 404

    # 상태 확인
    if lic["status"] != "active":
        log_api_call(license_key, "/verify", ip, False, f"비활성 상태: {lic['status']}")
        return jsonify({"valid": False, "error": f"라이선스가 비활성 상태입니다. ({lic['status']})"}), 403

    # 만료 확인
    if not lic["is_permanent"] and lic["expires_at"]:
        expires = datetime.fromisoformat(lic["expires_at"])
        if datetime.utcnow() > expires:
            log_api_call(license_key, "/verify", ip, False, "만료됨")
            return jsonify({"valid": False, "error": "라이선스가 만료되었습니다. 구독을 갱신해주세요."}), 403

    # 디바이스 바인딩
    try:
        binding, is_new = bind_device(lic["id"], hardware_id, ip, hostname)
    except ValueError as e:
        log_api_call(license_key, "/verify", ip, False, str(e))
        return jsonify({"valid": False, "error": str(e)}), 403

    # 토큰 잔액
    balance = get_token_balance(lic["id"])

    log_api_call(license_key, "/verify", ip, True, "검증 성공")

    return jsonify({
        "valid": True,
        "license": {
            "plan": lic["plan_display"],
            "plan_name": lic["plan_name"],
            "customer": lic["customer_name"],
            "expires_at": lic["expires_at"],
            "is_permanent": bool(lic["is_permanent"]),
            "max_accounts": lic["max_accounts"],
            "max_devices": lic["max_devices"],
        },
        "tokens": {
            "balance": balance["balance"] if balance else 0,
            "extra_token_price": lic["extra_token_price"],
        },
        "device": {
            "is_new": is_new,
            "hardware_id": hardware_id,
        },
    })


@app.route("/api/license/tokens/use", methods=["POST"])
def api_use_tokens():
    """토큰 소모 (클라이언트에서 작업 수행 시 호출)"""
    data = request.get_json() or {}
    license_key = data.get("license_key", "").strip()
    action = data.get("action", "")
    tokens = data.get("tokens", 0)
    description = data.get("description", "")
    ip = get_client_ip()

    if not license_key or not action or tokens <= 0:
        return jsonify({"error": "필수 파라미터 누락"}), 400

    lic = get_license_by_key(license_key)
    if not lic or lic["status"] != "active":
        log_api_call(license_key, "/tokens/use", ip, False, "유효하지 않은 라이선스")
        return jsonify({"error": "유효하지 않은 라이선스"}), 403

    try:
        remaining = consume_tokens(lic["id"], action, tokens, description)
        log_api_call(license_key, "/tokens/use", ip, True, f"{action}: -{tokens}")
        return jsonify({"success": True, "remaining": remaining})
    except ValueError as e:
        log_api_call(license_key, "/tokens/use", ip, False, str(e))
        return jsonify({"error": str(e)}), 402  # Payment Required


@app.route("/api/license/tokens/balance", methods=["POST"])
def api_token_balance():
    """토큰 잔액 조회"""
    data = request.get_json() or {}
    license_key = data.get("license_key", "").strip()

    lic = get_license_by_key(license_key)
    if not lic:
        return jsonify({"error": "유효하지 않은 라이선스"}), 404

    balance = get_token_balance(lic["id"])
    return jsonify({
        "balance": balance["balance"] if balance else 0,
        "plan": lic["plan_display"],
    })


@app.route("/api/license/tokens/purchase", methods=["POST"])
def api_purchase_tokens():
    """고객 토큰 충전 요청 (결제 완료 후 호출)"""
    data = request.get_json() or {}
    license_key = data.get("license_key", "").strip()
    tokens = int(data.get("tokens", 0))
    payment_key = data.get("payment_key", "")  # 결제 검증 키
    amount = int(data.get("amount", 0))  # 결제 금액
    ip = get_client_ip()

    if not license_key or tokens <= 0:
        return jsonify({"error": "필수 파라미터 누락"}), 400

    lic = get_license_by_key(license_key)
    if not lic or lic["status"] != "active":
        log_api_call(license_key, "/tokens/purchase", ip, False, "유효하지 않은 라이선스")
        return jsonify({"error": "유효하지 않은 라이선스"}), 403

    new_balance = add_tokens(lic["id"], tokens, amount)
    log_api_call(license_key, "/tokens/purchase", ip, True, f"+{tokens} tokens, ₩{amount}")

    return jsonify({
        "success": True,
        "balance": new_balance,
        "purchased": tokens,
        "amount": amount,
    })


@app.route("/api/license/usage-history", methods=["POST"])
def api_usage_history():
    """고객 토큰 사용 내역 조회"""
    data = request.get_json() or {}
    license_key = data.get("license_key", "").strip()

    lic = get_license_by_key(license_key)
    if not lic:
        return jsonify({"error": "유효하지 않은 라이선스"}), 404

    from models import get_db
    conn = get_db()

    # 최근 사용 내역 50건
    usage = conn.execute(
        "SELECT action, tokens_used, description, used_at FROM token_usage "
        "WHERE license_id = ? ORDER BY used_at DESC LIMIT 50",
        (lic["id"],),
    ).fetchall()

    # 최근 충전 내역 20건
    purchases = conn.execute(
        "SELECT tokens, price, purchased_at FROM token_purchases "
        "WHERE license_id = ? ORDER BY purchased_at DESC LIMIT 20",
        (lic["id"],),
    ).fetchall()

    conn.close()

    return jsonify({
        "usage": [dict(r) for r in usage],
        "purchases": [dict(r) for r in purchases],
    })


@app.route("/api/license/heartbeat", methods=["POST"])
def api_heartbeat():
    """주기적 하트비트 (프로그램 실행 중 30분마다 호출)"""
    data = request.get_json() or {}
    license_key = data.get("license_key", "").strip()
    hardware_id = data.get("hardware_id", "").strip()
    ip = get_client_ip()

    lic = get_license_by_key(license_key)
    if not lic or lic["status"] != "active":
        return jsonify({"valid": False, "error": "라이선스 비활성"}), 403

    # 만료 확인
    if not lic["is_permanent"] and lic["expires_at"]:
        expires = datetime.fromisoformat(lic["expires_at"])
        if datetime.utcnow() > expires:
            return jsonify({"valid": False, "error": "라이선스 만료"}), 403

    # 디바이스 확인
    if hardware_id:
        try:
            bind_device(lic["id"], hardware_id, ip)
        except ValueError:
            return jsonify({"valid": False, "error": "디바이스 인증 실패"}), 403

    balance = get_token_balance(lic["id"])
    return jsonify({
        "valid": True,
        "tokens_remaining": balance["balance"] if balance else 0,
    })


# ═══════════════════════════════════════════
#  관리자 API (관리자 대시보드에서 호출)
# ═══════════════════════════════════════════

# ─── 고객 관리 ───

@app.route("/api/admin/customers", methods=["GET"])
@admin_required
def api_list_customers():
    return jsonify(list_customers())


@app.route("/api/admin/customers", methods=["POST"])
@admin_required
def api_create_customer():
    data = request.get_json() or {}
    email = data.get("email", "").strip()
    name = data.get("name", "").strip()
    phone = data.get("phone", "")
    company = data.get("company", "")
    memo = data.get("memo", "")

    if not email or not name:
        return jsonify({"error": "이메일과 이름은 필수입니다."}), 400

    try:
        customer = create_customer(email, name, phone, company, memo)
        return jsonify(customer), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ─── 라이선스 관리 ───

@app.route("/api/admin/licenses", methods=["GET"])
@admin_required
def api_list_licenses():
    customer_id = request.args.get("customer_id")
    return jsonify(list_licenses(customer_id))


@app.route("/api/admin/licenses", methods=["POST"])
@admin_required
def api_create_license():
    """라이선스 발급"""
    data = request.get_json() or {}
    customer_id = data.get("customer_id", "").strip()
    plan_id = data.get("plan_id", "starter").strip()
    months = data.get("months", 1)

    if not customer_id:
        return jsonify({"error": "고객 ID가 필요합니다."}), 400

    try:
        lic = create_license(customer_id, plan_id, months)
        return jsonify(lic), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/admin/licenses/<license_id>/revoke", methods=["POST"])
@admin_required
def api_revoke_license(license_id):
    revoke_license(license_id)
    return jsonify({"message": "라이선스가 해지되었습니다."})


@app.route("/api/admin/licenses/<license_id>/tokens/add", methods=["POST"])
@admin_required
def api_add_tokens(license_id):
    """토큰 수동 추가"""
    data = request.get_json() or {}
    tokens = data.get("tokens", 0)
    price = data.get("price", 0)

    if tokens <= 0:
        return jsonify({"error": "토큰 수량이 필요합니다."}), 400

    new_balance = add_tokens(license_id, tokens, price)
    return jsonify({"balance": new_balance})


@app.route("/api/admin/licenses/<license_id>/refill", methods=["POST"])
@admin_required
def api_refill_tokens(license_id):
    """월간 토큰 리필"""
    refill_monthly_tokens(license_id)
    balance = get_token_balance(license_id)
    return jsonify({"balance": balance["balance"] if balance else 0})


@app.route("/api/admin/licenses/<license_id>/devices", methods=["GET"])
@admin_required
def api_list_devices(license_id):
    from models import get_db
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM device_bindings WHERE license_id = ? ORDER BY bound_at DESC",
        (license_id,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/admin/devices/<binding_id>/unbind", methods=["POST"])
@admin_required
def api_unbind_device(binding_id):
    unbind_device(binding_id)
    return jsonify({"message": "디바이스 바인딩이 해제되었습니다."})


# ─── 통계 ───

@app.route("/api/admin/stats", methods=["GET"])
@admin_required
def api_stats():
    from models import get_db
    conn = get_db()

    total_customers = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    total_licenses = conn.execute("SELECT COUNT(*) FROM licenses WHERE status = 'active'").fetchone()[0]
    total_revenue = conn.execute(
        "SELECT COALESCE(SUM(price), 0) FROM token_purchases"
    ).fetchone()[0]

    # 플랜별 라이선스 수
    plan_stats = conn.execute(
        "SELECT p.display_name, COUNT(l.id) as count "
        "FROM plans p LEFT JOIN licenses l ON p.id = l.plan_id AND l.status = 'active' "
        "GROUP BY p.id"
    ).fetchall()

    # 최근 API 로그
    recent_logs = conn.execute(
        "SELECT * FROM api_logs ORDER BY logged_at DESC LIMIT 50"
    ).fetchall()

    conn.close()

    return jsonify({
        "total_customers": total_customers,
        "active_licenses": total_licenses,
        "total_token_revenue": total_revenue,
        "plan_stats": [dict(r) for r in plan_stats],
        "recent_logs": [dict(r) for r in recent_logs],
    })


# ─── 플랜 조회 ───

@app.route("/api/plans", methods=["GET"])
def api_list_plans():
    from models import get_db
    conn = get_db()
    rows = conn.execute("SELECT * FROM plans WHERE active = 1 ORDER BY monthly_price").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ─── 헬스체크 ───

@app.route("/api/health", methods=["GET"])
def api_health():
    return jsonify({"status": "ok", "server": "license-server", "version": "1.0.0"})


@app.route("/admin")
def admin_page():
    return render_template("admin.html")


# ═══════════════════════════════════════════

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("LICENSE_PORT", 5100))
    print(f"라이선스 서버 시작: http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
