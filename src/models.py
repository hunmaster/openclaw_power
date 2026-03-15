"""사용자 데이터베이스 모델."""
import json
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    VALID_PLANS = ["Free", "Starter", "Business", "Agency", "Enterprise"]

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    nickname = db.Column(db.String(50), nullable=True)
    license_key = db.Column(db.String(255), nullable=True)
    plan = db.Column(db.String(50), nullable=False, default="Free")
    is_active_user = db.Column(db.Boolean, default=True)
    agreed_terms = db.Column(db.Boolean, default=False)
    agreed_at = db.Column(db.DateTime, nullable=True)
    setup_completed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        return self.is_active_user

    # 관계
    youtube_accounts = db.relationship("YouTubeAccount", backref="owner", lazy="dynamic")
    settings = db.relationship("UserSettings", backref="owner", uselist=False, lazy="joined")

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "nickname": self.nickname,
            "plan": self.plan or "Free",
            "license_key": (self.license_key or "")[:8] + "..." if self.license_key else None,
            "setup_completed": self.setup_completed,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login": self.last_login.isoformat() if self.last_login else None,
        }


class YouTubeAccount(db.Model):
    """유저별 유튜브 계정 저장 (영속성)."""
    __tablename__ = "youtube_accounts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    account_email = db.Column(db.String(255), nullable=False)
    account_password = db.Column(db.String(500), nullable=True)
    label = db.Column(db.String(100), nullable=True)
    account_type = db.Column(db.String(50), default="google")
    cookies_saved = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.account_email,
            "label": self.label or self.account_email.split("@")[0],
            "account_type": self.account_type,
            "cookies_saved": self.cookies_saved,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def to_account_dict(self):
        """자동화 실행용 딕셔너리 (비밀번호 포함)."""
        return {
            "email": self.account_email,
            "password": self.account_password or "",
            "label": self.label or self.account_email.split("@")[0],
            "account_type": self.account_type or "sub",
        }


class UserSettings(db.Model):
    """유저별 설정 영속 저장 (JSON)."""
    __tablename__ = "user_settings"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True, index=True)
    settings_json = db.Column(db.Text, default="{}")
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 설정 기본값
    DEFAULTS = {
        "NOTION_API_TOKEN": "",
        "NOTION_DATABASE_ID": "",
        "MAX_COMMENTS_PER_DAY": "20",
        "COMMENT_INTERVAL_SEC": "180",
        "SAME_VIDEO_INTERVAL_MIN": "30",
        "SMM_LIKE_QUANTITY": "20",
        "SMM_LIKE_AUTO_MAX": "500",
        "HEADLESS": "false",
        "ADB_IP_CHANGE_ENABLED": "false",
        "ADB_PATH": "adb",
        "ADB_AIRPLANE_WAIT": "4",
        "ADB_AUTO_ETHERNET": "true",
        "ADB_ETHERNET_NAME": "이더넷",
    }

    def get_settings(self):
        """설정 딕셔너리 반환 (기본값 병합)."""
        try:
            saved = json.loads(self.settings_json or "{}")
        except (json.JSONDecodeError, TypeError):
            saved = {}
        merged = dict(self.DEFAULTS)
        merged.update(saved)
        return merged

    def get(self, key, default=None):
        """개별 설정값 반환."""
        settings = self.get_settings()
        return settings.get(key, default or self.DEFAULTS.get(key, ""))

    def update_settings(self, updates):
        """설정 업데이트 (딕셔너리)."""
        try:
            current = json.loads(self.settings_json or "{}")
        except (json.JSONDecodeError, TypeError):
            current = {}
        current.update(updates)
        self.settings_json = json.dumps(current, ensure_ascii=False)

    def to_dict(self):
        return self.get_settings()


class CommentTracking(db.Model):
    """유저별 댓글 트래킹 데이터 영속 저장."""
    __tablename__ = "comment_tracking"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    video_url = db.Column(db.String(500), nullable=False)
    video_title = db.Column(db.String(500), nullable=True)
    comment_text = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default="tracking")  # tracking, exposed, lost
    last_checked = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "video_url": self.video_url,
            "video_title": self.video_title,
            "comment_text": self.comment_text,
            "status": self.status,
            "last_checked": self.last_checked.isoformat() if self.last_checked else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class LikeOrder(db.Model):
    """좋아요 주문 이력 (SMM 주문 추적)."""
    __tablename__ = "like_orders"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    order_id = db.Column(db.String(50), nullable=False)  # SMM 주문 ID
    comment_url = db.Column(db.String(500), nullable=False)
    quantity = db.Column(db.Integer, default=0)
    tier = db.Column(db.String(50), default="standard")
    cost = db.Column(db.Integer, default=0)  # 원 단위
    status = db.Column(db.String(50), default="Pending")  # Pending, In progress, Completed, Partial, Canceled
    remains = db.Column(db.Integer, nullable=True)  # 남은 수량
    source = db.Column(db.String(50), default="manual")  # manual, auto, boost
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "order_id": self.order_id,
            "comment_url": self.comment_url,
            "quantity": self.quantity,
            "tier": self.tier,
            "cost": self.cost,
            "status": self.status,
            "remains": self.remains,
            "source": self.source,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class UserActivityLog(db.Model):
    """유저 활동 로그 (가입, 로그인, 탈퇴 등)."""
    __tablename__ = "user_activity_log"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True, index=True)
    email = db.Column(db.String(255), nullable=True)
    action = db.Column(db.String(50), nullable=False)  # register, login, logout, deactivate
    detail = db.Column(db.String(500), nullable=True)
    ip_address = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "email": self.email,
            "action": self.action,
            "detail": self.detail,
            "ip_address": self.ip_address,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
