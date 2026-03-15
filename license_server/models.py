"""
라이선스 서버 - DB 모델
SQLite 기반, 나중에 PostgreSQL로 전환 가능
"""

import sqlite3
import os
import uuid
import hashlib
import secrets
import time
from datetime import datetime, timedelta

DB_PATH = os.environ.get("LICENSE_DB_PATH", os.path.join(os.path.dirname(__file__), "license.db"))


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """DB 테이블 초기화"""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS customers (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            phone TEXT,
            company TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            memo TEXT
        );

        CREATE TABLE IF NOT EXISTS plans (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            display_name TEXT NOT NULL,
            monthly_price INTEGER NOT NULL,
            tokens_per_month INTEGER NOT NULL,
            max_accounts INTEGER NOT NULL,
            max_devices INTEGER NOT NULL,
            is_permanent INTEGER NOT NULL DEFAULT 0,
            extra_token_price INTEGER NOT NULL DEFAULT 30000,
            active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS licenses (
            id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            license_key TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            activated_at TEXT,
            expires_at TEXT,
            is_permanent INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (customer_id) REFERENCES customers(id),
            FOREIGN KEY (plan_id) REFERENCES plans(id)
        );

        CREATE TABLE IF NOT EXISTS device_bindings (
            id TEXT PRIMARY KEY,
            license_id TEXT NOT NULL,
            hardware_id TEXT NOT NULL,
            ip_address TEXT,
            hostname TEXT,
            bound_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_seen TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (license_id) REFERENCES licenses(id)
        );

        CREATE TABLE IF NOT EXISTS token_usage (
            id TEXT PRIMARY KEY,
            license_id TEXT NOT NULL,
            action TEXT NOT NULL,
            tokens_used INTEGER NOT NULL,
            description TEXT,
            used_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (license_id) REFERENCES licenses(id)
        );

        CREATE TABLE IF NOT EXISTS token_balance (
            license_id TEXT PRIMARY KEY,
            balance INTEGER NOT NULL DEFAULT 0,
            last_refill TEXT,
            FOREIGN KEY (license_id) REFERENCES licenses(id)
        );

        CREATE TABLE IF NOT EXISTS token_purchases (
            id TEXT PRIMARY KEY,
            license_id TEXT NOT NULL,
            tokens INTEGER NOT NULL,
            price INTEGER NOT NULL,
            purchased_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (license_id) REFERENCES licenses(id)
        );

        CREATE TABLE IF NOT EXISTS api_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            license_key TEXT,
            endpoint TEXT,
            ip_address TEXT,
            success INTEGER,
            message TEXT,
            logged_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.commit()

    # 기본 플랜 삽입
    _insert_default_plans(conn)
    conn.close()


def _insert_default_plans(conn):
    """기본 구독 플랜 삽입 (없는 경우만)"""
    existing = conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
    if existing > 0:
        return

    plans = [
        ("starter", "Starter", "Starter", 100000, 3000, 3, 1, 0, 30000),
        ("business", "Business", "Business", 290000, 10000, 10, 2, 0, 25000),
        ("agency", "Agency", "Agency", 590000, 30000, 30, 5, 0, 20000),
        ("enterprise", "Enterprise", "Enterprise", 9900000, 999999999, 9999, 9999, 1, 0),
    ]
    conn.executemany(
        "INSERT INTO plans (id, name, display_name, monthly_price, tokens_per_month, max_accounts, max_devices, is_permanent, extra_token_price) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        plans,
    )
    conn.commit()


def generate_license_key():
    """라이선스 키 생성: XXXX-XXXX-XXXX-XXXX 형식"""
    raw = secrets.token_hex(16)
    parts = [raw[i:i+4].upper() for i in range(0, 16, 4)]
    return "-".join(parts)


def generate_hardware_id():
    """클라이언트에서 보내는 하드웨어 ID 생성용 (클라이언트 측 호출)"""
    import platform
    info = f"{platform.node()}-{platform.machine()}-{platform.processor()}"
    return hashlib.sha256(info.encode()).hexdigest()[:32]


# ─── 고객 관리 ───

def create_customer(email, name, phone=None, company=None, memo=None):
    conn = get_db()
    cid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO customers (id, email, name, phone, company, memo) VALUES (?, ?, ?, ?, ?, ?)",
        (cid, email, name, phone, company, memo),
    )
    conn.commit()
    customer = conn.execute("SELECT * FROM customers WHERE id = ?", (cid,)).fetchone()
    conn.close()
    return dict(customer)


def get_customer(customer_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_customers():
    conn = get_db()
    rows = conn.execute("SELECT * FROM customers ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── 라이선스 관리 ───

def create_license(customer_id, plan_id, months=1):
    """라이선스 발급"""
    conn = get_db()
    plan = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    if not plan:
        conn.close()
        raise ValueError(f"플랜을 찾을 수 없습니다: {plan_id}")

    lid = str(uuid.uuid4())
    key = generate_license_key()
    now = datetime.utcnow()
    is_permanent = plan["is_permanent"]

    if is_permanent:
        expires_at = None
    else:
        expires_at = (now + timedelta(days=30 * months)).isoformat()

    conn.execute(
        "INSERT INTO licenses (id, customer_id, plan_id, license_key, status, activated_at, expires_at, is_permanent) VALUES (?, ?, ?, ?, 'active', ?, ?, ?)",
        (lid, customer_id, plan_id, key, now.isoformat(), expires_at, is_permanent),
    )

    # 토큰 잔액 초기화
    initial_tokens = plan["tokens_per_month"] if not is_permanent else 999999999
    conn.execute(
        "INSERT INTO token_balance (license_id, balance, last_refill) VALUES (?, ?, ?)",
        (lid, initial_tokens, now.isoformat()),
    )

    conn.commit()
    lic = conn.execute("SELECT * FROM licenses WHERE id = ?", (lid,)).fetchone()
    conn.close()
    return dict(lic)


def get_license_by_key(license_key):
    conn = get_db()
    row = conn.execute(
        "SELECT l.*, p.name as plan_name, p.display_name as plan_display, "
        "p.tokens_per_month, p.max_accounts, p.max_devices, p.extra_token_price, "
        "c.email as customer_email, c.name as customer_name "
        "FROM licenses l "
        "JOIN plans p ON l.plan_id = p.id "
        "JOIN customers c ON l.customer_id = c.id "
        "WHERE l.license_key = ?",
        (license_key,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_licenses(customer_id=None):
    conn = get_db()
    if customer_id:
        rows = conn.execute(
            "SELECT l.*, p.display_name as plan_display, c.email as customer_email, c.name as customer_name "
            "FROM licenses l JOIN plans p ON l.plan_id = p.id JOIN customers c ON l.customer_id = c.id "
            "WHERE l.customer_id = ? ORDER BY l.created_at DESC",
            (customer_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT l.*, p.display_name as plan_display, c.email as customer_email, c.name as customer_name "
            "FROM licenses l JOIN plans p ON l.plan_id = p.id JOIN customers c ON l.customer_id = c.id "
            "ORDER BY l.created_at DESC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def revoke_license(license_id):
    conn = get_db()
    conn.execute("UPDATE licenses SET status = 'revoked' WHERE id = ?", (license_id,))
    conn.commit()
    conn.close()


def upgrade_license_plan(license_key, new_plan_id, months=1):
    """라이선스 플랜 업그레이드 (결제 후 호출)"""
    conn = get_db()
    lic = conn.execute(
        "SELECT * FROM licenses WHERE license_key = ?", (license_key,)
    ).fetchone()
    if not lic:
        conn.close()
        return None, "라이선스를 찾을 수 없습니다."

    plan = conn.execute("SELECT * FROM plans WHERE id = ?", (new_plan_id,)).fetchone()
    if not plan:
        conn.close()
        return None, f"플랜을 찾을 수 없습니다: {new_plan_id}"

    now = datetime.utcnow()
    is_permanent = plan["is_permanent"]
    if is_permanent:
        expires_at = None
    else:
        expires_at = (now + timedelta(days=30 * months)).isoformat()

    # 플랜 변경 + 만료일 갱신 + 활성화
    conn.execute(
        "UPDATE licenses SET plan_id = ?, expires_at = ?, is_permanent = ?, status = 'active' WHERE id = ?",
        (new_plan_id, expires_at, is_permanent, lic["id"]),
    )

    # 토큰 잔액 리필 (새 플랜 기준)
    new_tokens = plan["tokens_per_month"] if not is_permanent else 999999999
    existing_balance = conn.execute(
        "SELECT * FROM token_balance WHERE license_id = ?", (lic["id"],)
    ).fetchone()
    if existing_balance:
        conn.execute(
            "UPDATE token_balance SET balance = ?, last_refill = ? WHERE license_id = ?",
            (new_tokens, now.isoformat(), lic["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO token_balance (license_id, balance, last_refill) VALUES (?, ?, ?)",
            (lic["id"], new_tokens, now.isoformat()),
        )

    conn.commit()
    updated = conn.execute(
        "SELECT l.*, p.name as plan_name, p.display_name as plan_display "
        "FROM licenses l JOIN plans p ON l.plan_id = p.id WHERE l.id = ?",
        (lic["id"],),
    ).fetchone()
    conn.close()
    return dict(updated), None


# ─── 디바이스 바인딩 ───

def bind_device(license_id, hardware_id, ip_address=None, hostname=None):
    """디바이스를 라이선스에 바인딩"""
    conn = get_db()

    # 이미 바인딩된 디바이스인지 확인
    existing = conn.execute(
        "SELECT * FROM device_bindings WHERE license_id = ? AND hardware_id = ? AND active = 1",
        (license_id, hardware_id),
    ).fetchone()

    if existing:
        # 이미 바인딩됨 → last_seen 업데이트
        conn.execute(
            "UPDATE device_bindings SET last_seen = datetime('now'), ip_address = ? WHERE id = ?",
            (ip_address, existing["id"]),
        )
        conn.commit()
        conn.close()
        return dict(existing), False  # (binding, is_new)

    # 바인딩 수 확인
    lic = conn.execute(
        "SELECT l.*, p.max_devices FROM licenses l JOIN plans p ON l.plan_id = p.id WHERE l.id = ?",
        (license_id,),
    ).fetchone()

    active_count = conn.execute(
        "SELECT COUNT(*) FROM device_bindings WHERE license_id = ? AND active = 1",
        (license_id,),
    ).fetchone()[0]

    if active_count >= lic["max_devices"]:
        conn.close()
        raise ValueError(f"최대 디바이스 수 초과 ({lic['max_devices']}대)")

    # 새 바인딩
    bid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO device_bindings (id, license_id, hardware_id, ip_address, hostname, last_seen) "
        "VALUES (?, ?, ?, ?, ?, datetime('now'))",
        (bid, license_id, hardware_id, ip_address, hostname),
    )
    conn.commit()
    binding = conn.execute("SELECT * FROM device_bindings WHERE id = ?", (bid,)).fetchone()
    conn.close()
    return dict(binding), True


def unbind_device(binding_id):
    conn = get_db()
    conn.execute("UPDATE device_bindings SET active = 0 WHERE id = ?", (binding_id,))
    conn.commit()
    conn.close()


# ─── 토큰 관리 ───

def get_token_balance(license_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM token_balance WHERE license_id = ?", (license_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def consume_tokens(license_id, action, tokens, description=None):
    """토큰 소모. 잔액 부족 시 ValueError 발생."""
    conn = get_db()
    bal = conn.execute("SELECT balance FROM token_balance WHERE license_id = ?", (license_id,)).fetchone()

    if not bal:
        conn.close()
        raise ValueError("토큰 잔액 정보 없음")

    if bal["balance"] < tokens:
        conn.close()
        raise ValueError(f"토큰 부족 (잔액: {bal['balance']}, 필요: {tokens})")

    conn.execute(
        "UPDATE token_balance SET balance = balance - ? WHERE license_id = ?",
        (tokens, license_id),
    )
    conn.execute(
        "INSERT INTO token_usage (id, license_id, action, tokens_used, description) VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), license_id, action, tokens, description),
    )
    conn.commit()

    new_bal = conn.execute("SELECT balance FROM token_balance WHERE license_id = ?", (license_id,)).fetchone()
    conn.close()
    return new_bal["balance"]


def add_tokens(license_id, tokens, price=0):
    """토큰 추가 구매"""
    conn = get_db()
    conn.execute(
        "UPDATE token_balance SET balance = balance + ? WHERE license_id = ?",
        (tokens, license_id),
    )
    conn.execute(
        "INSERT INTO token_purchases (id, license_id, tokens, price) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), license_id, tokens, price),
    )
    conn.commit()
    new_bal = conn.execute("SELECT balance FROM token_balance WHERE license_id = ?", (license_id,)).fetchone()
    conn.close()
    return new_bal["balance"]


def refill_monthly_tokens(license_id):
    """월간 토큰 리필 (구독 갱신 시 호출)"""
    conn = get_db()
    lic = conn.execute(
        "SELECT l.*, p.tokens_per_month FROM licenses l JOIN plans p ON l.plan_id = p.id WHERE l.id = ?",
        (license_id,),
    ).fetchone()

    if not lic:
        conn.close()
        return

    conn.execute(
        "UPDATE token_balance SET balance = balance + ?, last_refill = datetime('now') WHERE license_id = ?",
        (lic["tokens_per_month"], license_id),
    )
    conn.commit()
    conn.close()


# ─── API 로그 ───

def log_api_call(license_key, endpoint, ip_address, success, message=None):
    conn = get_db()
    conn.execute(
        "INSERT INTO api_logs (license_key, endpoint, ip_address, success, message) VALUES (?, ?, ?, ?, ?)",
        (license_key, endpoint, ip_address, success, message),
    )
    conn.commit()
    conn.close()
